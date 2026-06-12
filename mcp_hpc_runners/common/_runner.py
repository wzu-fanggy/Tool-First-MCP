"""
mcp_hpc_runners/common/_runner.py

Shared job-runner backend for vasp-mcp and lammps-mcp.

Responsibilities:
  - Maintain a SQLite-backed job registry under ~/mcp_runs/jobs.sqlite.
  - Spawn an mpirun subprocess inside ~/mcp_runs/<engine>/<job_id>/, write its
    pid + start time + work_dir into the registry, redirect stdout/stderr to
    files inside the work_dir.
  - Provide list / status / wait / kill primitives.
  - Be safe to call concurrently from multiple MCP processes (sqlite handles
    short-lived locking).

Intentionally minimal: no SLURM, no cgroup throttling. nodec2 has 48 cores +
no queue system, so we just respect a configurable concurrency cap per engine.
"""
from __future__ import annotations

import json
import os
import shlex
import signal
import sqlite3
import subprocess
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
BASE_DIR = Path(os.environ.get("MCP_HPC_BASE", str(Path.home() / "mcp_runs")))
DB_PATH = BASE_DIR / "jobs.sqlite"

DEFAULT_CONCURRENCY = int(os.environ.get("MCP_HPC_CONCURRENCY", "2"))


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    engine        TEXT NOT NULL,            -- 'vasp' | 'lammps'
    flavor        TEXT,                     -- 'std' | 'gam' | 'ncl' | 'mpi' | 'serial'
    work_dir      TEXT NOT NULL,
    cmd           TEXT NOT NULL,
    np            INTEGER NOT NULL,
    pid           INTEGER,
    status        TEXT NOT NULL,            -- 'running' | 'done' | 'failed' | 'killed' | 'timeout'
    returncode    INTEGER,
    started_at    REAL NOT NULL,
    ended_at      REAL,
    walltime_s    INTEGER,
    note          TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_engine_status ON jobs(engine, status);
"""


def _conn() -> sqlite3.Connection:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=10.0)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------
@dataclass
class JobRecord:
    id: str
    engine: str
    flavor: str | None
    work_dir: str
    cmd: str
    np: int
    pid: int | None
    status: str
    returncode: int | None
    started_at: float
    ended_at: float | None
    walltime_s: int | None
    note: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "JobRecord":
        return cls(**{k: row[k] for k in row.keys()})

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Convenience: derived fields
        if self.ended_at and self.started_at:
            d["runtime_s"] = round(self.ended_at - self.started_at, 3)
        elif self.status == "running":
            d["runtime_s"] = round(time.time() - self.started_at, 3)
        # paths
        d["stdout_path"] = str(Path(self.work_dir) / "stdout.log")
        d["stderr_path"] = str(Path(self.work_dir) / "stderr.log")
        d["meta_path"] = str(Path(self.work_dir) / "meta.json")
        return d


# ---------------------------------------------------------------------------
# Status reconciliation
# ---------------------------------------------------------------------------
def _process_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    # On Linux, a finished but unreaped child is a zombie. /proc shows state.
    try:
        stat_path = Path(f"/proc/{pid}/status")
        if stat_path.exists():
            for line in stat_path.read_text().splitlines():
                if line.startswith("State:"):
                    state = line.split()[1]
                    # R running, S sleeping, D disk-sleep, T stopped, t tracing,
                    # Z zombie, X dead, I idle. Anything in {Z, X} means gone.
                    if state in {"Z", "X"}:
                        return False
                    return True
    except Exception:
        pass
    return True


def _job_completion_marker_present(work_dir: str) -> bool:
    """The runner writes exit_status.txt as the very last action of the wrapper.
    If that file exists, the job has finished regardless of pid state."""
    return (Path(work_dir) / "exit_status.txt").exists()


def _reconcile_status(rec: JobRecord) -> JobRecord:
    """If a job is marked running but has finished (either pid is gone or the
    exit-status marker file exists on disk), finalize it from disk."""
    if rec.status != "running":
        return rec
    # Completion marker takes precedence over pid liveness
    if _job_completion_marker_present(rec.work_dir):
        return _finalize_from_disk(rec.id, fallback_status="done")
    if _process_alive(rec.pid):
        # walltime check
        if rec.walltime_s and (time.time() - rec.started_at) > rec.walltime_s:
            try:
                os.kill(rec.pid, signal.SIGTERM)  # type: ignore[arg-type]
            except Exception:
                pass
            time.sleep(1.5)
            try:
                os.kill(rec.pid, signal.SIGKILL)  # type: ignore[arg-type]
            except Exception:
                pass
            return _finalize_from_disk(rec.id, fallback_status="timeout")
        return rec
    # process gone → finalize from on-disk marker
    return _finalize_from_disk(rec.id, fallback_status="done")


def _finalize_from_disk(job_id: str, fallback_status: str = "done") -> JobRecord:
    rec = _read_record(job_id)
    if rec is None:
        raise RuntimeError(f"job {job_id} unknown")
    if rec.status != "running":
        return rec
    work_dir = Path(rec.work_dir)
    rc_file = work_dir / "exit_status.txt"
    rc: int | None = None
    if rc_file.exists():
        try:
            rc = int(rc_file.read_text().strip())
        except Exception:
            rc = None
    final_status = fallback_status
    if rc is not None:
        final_status = "done" if rc == 0 else "failed"
    elif fallback_status == "done":
        # process gone but no exit-status file → assume crashed
        final_status = "failed"
    with _conn() as c:
        c.execute(
            "UPDATE jobs SET status=?, returncode=?, ended_at=? WHERE id=?",
            (final_status, rc, time.time(), job_id),
        )
    return _read_record(job_id)  # type: ignore[return-value]


def _read_record(job_id: str) -> JobRecord | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return JobRecord.from_row(row) if row else None


# ---------------------------------------------------------------------------
# Submit
# ---------------------------------------------------------------------------
def _running_count(engine: str) -> int:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, pid, started_at, status, walltime_s FROM jobs "
            "WHERE engine=? AND status='running'",
            (engine,),
        ).fetchall()
    n = 0
    for row in rows:
        rec = _read_record(row["id"])
        if rec is None:
            continue
        rec = _reconcile_status(rec)
        if rec.status == "running":
            n += 1
    return n


def _new_job_dir(engine: str, job_id: str) -> Path:
    p = BASE_DIR / engine / job_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def submit(
    *,
    engine: str,
    cmd: list[str],
    work_dir: Path | None = None,
    np: int = 1,
    flavor: str | None = None,
    walltime_s: int = 3600,
    extra_files: dict[str, str] | None = None,
    env_overrides: dict[str, str] | None = None,
    note: str | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> JobRecord:
    """Spawn ``cmd`` in ``work_dir`` (created under ~/mcp_runs/<engine>/<job_id>/
    if not provided) and return a JobRecord.

    Files in ``extra_files`` (mapping filename -> content) are written to
    ``work_dir`` before the process starts; useful for staging an INCAR/in.lmp.
    """
    if _running_count(engine) >= concurrency:
        raise RuntimeError(
            f"concurrency limit reached for {engine}: "
            f"{concurrency} job(s) already running. Wait or kill one."
        )

    job_id = uuid.uuid4().hex[:16]
    if work_dir is None:
        work_dir = _new_job_dir(engine, job_id)
    else:
        work_dir = Path(work_dir).expanduser().resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
    if extra_files:
        for name, content in extra_files.items():
            (work_dir / name).write_text(content)

    stdout_path = work_dir / "stdout.log"
    stderr_path = work_dir / "stderr.log"
    meta_path = work_dir / "meta.json"
    rc_path = work_dir / "exit_status.txt"

    # Wrap the command so we can capture exit status in a file even if MCP dies.
    cmd_str = " ".join(shlex.quote(c) for c in cmd)
    wrapper = (
        f"set -o pipefail; "
        f"({cmd_str}) > {shlex.quote(str(stdout_path))} 2> {shlex.quote(str(stderr_path))}; "
        f"echo $? > {shlex.quote(str(rc_path))}"
    )

    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    # Sane MPI defaults for Intel MPI on a 48-core box
    env.setdefault("OMP_NUM_THREADS", "1")
    env.setdefault("I_MPI_FABRICS", "shm")

    proc = subprocess.Popen(
        ["bash", "-lc", wrapper],
        cwd=str(work_dir),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        start_new_session=True,
    )

    started_at = time.time()
    meta = {
        "id": job_id,
        "engine": engine,
        "flavor": flavor,
        "cmd": cmd_str,
        "np": np,
        "pid": proc.pid,
        "started_at": started_at,
        "walltime_s": walltime_s,
        "note": note,
    }
    meta_path.write_text(json.dumps(meta, indent=2))

    with _conn() as c:
        c.execute(
            "INSERT INTO jobs(id, engine, flavor, work_dir, cmd, np, pid, status, "
            "started_at, walltime_s, note) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                job_id,
                engine,
                flavor,
                str(work_dir),
                cmd_str,
                np,
                proc.pid,
                "running",
                started_at,
                walltime_s,
                note,
            ),
        )

    rec = _read_record(job_id)
    assert rec is not None
    return rec


# ---------------------------------------------------------------------------
# Public ops
# ---------------------------------------------------------------------------
def get_status(job_id: str) -> JobRecord:
    rec = _read_record(job_id)
    if rec is None:
        raise KeyError(f"job {job_id} not found")
    return _reconcile_status(rec)


def wait_for(job_id: str, timeout_s: float = 60.0, poll_interval: float = 1.0) -> JobRecord:
    deadline = time.time() + timeout_s
    while True:
        rec = get_status(job_id)
        if rec.status != "running":
            return rec
        if time.time() >= deadline:
            return rec
        time.sleep(poll_interval)


def kill(job_id: str) -> JobRecord:
    rec = _read_record(job_id)
    if rec is None:
        raise KeyError(f"job {job_id} not found")
    if rec.status != "running":
        return rec
    if rec.pid:
        # SIGTERM the whole process group (we used start_new_session=True)
        try:
            os.killpg(rec.pid, signal.SIGTERM)
        except Exception:
            try:
                os.kill(rec.pid, signal.SIGTERM)
            except Exception:
                pass
        time.sleep(1.5)
        try:
            os.killpg(rec.pid, signal.SIGKILL)
        except Exception:
            try:
                os.kill(rec.pid, signal.SIGKILL)
            except Exception:
                pass
    with _conn() as c:
        c.execute(
            "UPDATE jobs SET status='killed', ended_at=?, returncode=NULL WHERE id=? AND status='running'",
            (time.time(), job_id),
        )
    return _read_record(job_id)  # type: ignore[return-value]


def list_jobs(
    *,
    engine: str | None = None,
    status_filter: str | None = None,
    limit: int = 20,
) -> list[JobRecord]:
    sql = "SELECT * FROM jobs"
    where: list[str] = []
    args: list[Any] = []
    if engine:
        where.append("engine = ?"); args.append(engine)
    if status_filter:
        where.append("status = ?"); args.append(status_filter)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY started_at DESC LIMIT ?"
    args.append(limit)
    with _conn() as c:
        rows = c.execute(sql, args).fetchall()
    out = [JobRecord.from_row(r) for r in rows]
    # Reconcile statuses lazily
    return [_reconcile_status(r) for r in out]


def tail_log(job_id: str, *, kind: str = "stdout", n: int = 200) -> str:
    rec = _read_record(job_id)
    if rec is None:
        raise KeyError(f"job {job_id} not found")
    fname = "stdout.log" if kind == "stdout" else "stderr.log"
    path = Path(rec.work_dir) / fname
    if not path.exists():
        return ""
    # Read up to n lines from the tail (small, fits in RAM for log files)
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            chunk = min(size, 64 * 1024)
            f.seek(size - chunk)
            data = f.read().decode("utf-8", errors="replace")
        return "\n".join(data.splitlines()[-n:])
    except Exception as e:
        return f"<failed to read {path}: {e}>"
