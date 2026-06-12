"""
mcp_hpc_runners/lammps_mcp/mcp_server.py

LAMMPS MCP server. Submits LAMMPS runs as background processes, tracks them
in the same SQLite registry the VASP MCP uses, and exposes a parser for
the LAMMPS log.

nodec2 layout:
  /data/home/wzu25zj/lammps-22Jul2025/src/lmp_mpi    (MPI build)
  /data/home/wzu25zj/lammps-22Jul2025/src/lmp_serial (single-rank fallback)
"""
from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common._runner import (  # noqa: E402
    DEFAULT_CONCURRENCY,
    JobRecord,
    get_status,
    kill,
    list_jobs,
    submit,
    tail_log,
    wait_for,
)
from common.parsers import parse_lammps_log  # noqa: E402

from mcp.server.fastmcp import FastMCP  # noqa: E402

# ---------------------------------------------------------------------------
# Site config
# ---------------------------------------------------------------------------
LAMMPS_BIN_DIR = Path(os.environ.get(
    "LAMMPS_BIN_DIR", "/data/home/wzu25zj/lammps-22Jul2025/src"
))
LMP_MPI = Path(os.environ.get("LMP_MPI", str(LAMMPS_BIN_DIR / "lmp_mpi")))
LMP_SERIAL = Path(os.environ.get("LMP_SERIAL", str(LAMMPS_BIN_DIR / "lmp_serial")))
MPIRUN = os.environ.get("MPIRUN", "mpirun")
DEFAULT_NP = int(os.environ.get("LAMMPS_DEFAULT_NP", "8"))
DEFAULT_WALLTIME = int(os.environ.get("LAMMPS_DEFAULT_WALLTIME_S", "1800"))
INTEL_MPI_VARS = os.environ.get(
    "INTEL_MPI_VARS",
    "/data/home/wzu25zj/intel/oneapi/setvars.sh",
)

# Intel oneAPI library paths (needed at runtime for MKL + MPI libs)
INTEL_MKL_LIB = "/data/home/wzu25zj/intel/oneapi/mkl/2025.3/lib"
INTEL_COMPILER_LIB = "/data/home/wzu25zj/intel/oneapi/compiler/2025.3/lib"
INTEL_MPI_LIB = "/data/home/wzu25zj/intel/oneapi/mpi/2021.15/lib"

# Default env overrides passed to every LAMMPS subprocess
LAMMPS_ENV_OVERRIDES = {
    "LD_LIBRARY_PATH": f"{INTEL_MKL_LIB}:{INTEL_COMPILER_LIB}:{INTEL_MPI_LIB}",
    "OMP_NUM_THREADS": "1",
    "I_MPI_FABRICS": "shm",
}

# Search paths for potential / data files that the LAMMPS input may reference.
# When extra_files is missing a file like "Ni_u3.eam", we scan these dirs.
LAMMPS_POTENTIAL_DIRS = [
    Path("/data/home/wzu25zj/lammps-22Jul2025/examples"),
    Path("/data/home/wzu25zj/lammps-22Jul2025/potentials"),
]

ENGINE = "lammps"


# ---------------------------------------------------------------------------
# Helpers: local potential-file resolution
# ---------------------------------------------------------------------------
def _is_potential_file(name: str) -> bool:
    """Heuristic: return True if *name* looks like a potential / data file."""
    if "/" in name or "\\" in name:
        return False  # path, not a bare filename
    # Skip LAMMPS keywords
    skip = {"*", "NULL", ""}
    if name in skip:
        return False
    # Typical potential extensions (eam, eam.alloy, snap, etc.)
    ext = Path(name).suffix.lower()
    pot_exts = {".eam", ".alloy", ".eam.alloy", ".eam.fs", ".snapparam",
                ".snapcoeff", ".sw", ".tersoff", ".rebo", ".airebo",
                ".lmp", ".data", ".dat", ".pot", ".bop", ".bcs",
                ".hipot", ".flare", ".pace", ".yace"}
    if ext in pot_exts:
        return True
    # No extension but not a known keyword → likely a data file
    if ext == "":
        return True
    return False


def _find_potential_on_server(name: str) -> Path | None:
    """Search LAMMPS_POTENTIAL_DIRS for a file with the given name."""
    name = name.strip()
    for base in LAMMPS_POTENTIAL_DIRS:
        if not base.exists():
            continue
        for found in base.rglob(name):
            if found.is_file():
                return found
    return None


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "lammps-mcp",
    instructions=(
        "LAMMPS molecular-dynamics runner. Submit a job with run_lammps "
        "(input_file path or input_text), poll with get_lammps_job_status / "
        "wait_for_lammps_job, parse log with parse_lammps_log_tool. "
        "Jobs run in background; this MCP returns immediately with a job_id."
    ),
)


@mcp.tool()
def run_lammps(
    input_file: str | None = None,
    input_text: str | None = None,
    extra_files: dict[str, str] | None = None,
    work_dir: str | None = None,
    flavor: str = "mpi",
    np: int = DEFAULT_NP,
    walltime_s: int = DEFAULT_WALLTIME,
    note: str | None = None,
) -> dict[str, Any]:
    """Submit a LAMMPS run as a background process.

    Args:
        input_file: Path to an existing LAMMPS input deck (e.g. in.lmp).
            Used as-is (the binary is launched with ``-in <input_file>``);
            mutually exclusive with input_text.
        input_text: Inline LAMMPS input contents. Written to ``in.lmp`` in
            the work directory.
        extra_files: Optional mapping name -> contents (e.g. data files,
            potential files) staged into the work directory.
        work_dir: If provided, run inside this directory; otherwise a fresh
            ~/mcp_runs/lammps/<job_id>/ is created.
        flavor: "mpi" (uses lmp_mpi via mpirun) or "serial" (lmp_serial).
        np: MPI rank count when flavor='mpi'.
        walltime_s: Hard wall-clock cap (seconds).
        note: Free-form note attached to the job.

    Returns:
        Job dict including id, status, work_dir, stdout/stderr paths.
    """
    if not input_file and not input_text:
        return {"ok": False, "error": "must provide input_file or input_text"}
    if input_file and input_text:
        return {"ok": False, "error": "provide only one of input_file / input_text"}

    # Guard: if input_file looks like a Windows path (starts with drive letter)
    # the caller probably meant to pass input_text instead.
    if input_file:
        p = Path(input_file)
        has_windows_drive = (
            len(input_file) >= 2
            and input_file[1] == ":"
            and input_file[0].isalpha()
        )
        if has_windows_drive or not p.exists():
            return {
                "ok": False,
                "error": (
                    f"input_file '{input_file}' does not exist on this server. "
                    "You are likely passing a local Windows/macOS path. "
                    "Use `input_text` to send the file content as a string "
                    "instead of `input_file`."
                ),
            }

    flavor_l = flavor.lower().strip()
    if flavor_l not in {"mpi", "serial"}:
        return {"ok": False, "error": f"unknown LAMMPS flavor: {flavor}"}
    bin_path = LMP_MPI if flavor_l == "mpi" else LMP_SERIAL
    if not bin_path.exists():
        return {"ok": False, "error": f"LAMMPS binary not found: {bin_path}"}

    files: dict[str, str] = {}
    if extra_files:
        # Guard: extra_files values should be file contents, not paths.
        # Detect if any value looks like a local file path.
        for fname, content in extra_files.items():
            is_path_like = (
                isinstance(content, str)
                and (
                    len(content) > 10
                    and (content[1:3] == ":\\" or content.startswith("/"))
                    and Path(content).exists()
                )
            )
            if is_path_like:
                # The value is probably a local path; read it
                try:
                    files[fname] = Path(content).read_text()
                except Exception:
                    # Fall back to using the content as-is (may cause error)
                    files[fname] = content
            else:
                files[fname] = content

    if input_text:
        files["in.lmp"] = input_text
        in_arg = "in.lmp"
    else:
        in_arg = str(Path(input_file).expanduser().resolve())  # type: ignore[arg-type]

    # Auto-resolve missing extra_files from local LAMMPS potential dirs.
    # Scan the input text for filenames referenced by pair_coeff / include
    # and check if they exist on the server.
    _input_body = input_text if input_text else (Path(in_arg).read_text() if Path(in_arg).exists() else "")
    if _input_body:
        for line in _input_body.splitlines():
            stripped = line.strip()
            # Look for:  pair_coeff  * *  filename
            #            include      filename
            #            read_data    filename
            for kw in ("pair_coeff", "include", "read_data"):
                if stripped.lower().startswith(kw):
                    parts = stripped.split()
                    # pair_coeff * * Ni_u3.eam ... -> the filename is often parts[2] or parts[3]
                    for p in parts:
                        p = p.strip()
                        if p and p not in files and _is_potential_file(p):
                            found = _find_potential_on_server(p)
                            if found:
                                files[p] = found.read_text()
                                break


    lib_path = LAMMPS_ENV_OVERRIDES["LD_LIBRARY_PATH"]
    if flavor_l == "mpi":
        cmd_str = (
            f"source {INTEL_MPI_VARS} >/dev/null 2>&1 || true; "
            f"export LD_LIBRARY_PATH={lib_path}:$LD_LIBRARY_PATH; "
            f"{MPIRUN} -np {np} {shlex.quote(str(bin_path))} "
            f"-in {shlex.quote(in_arg)}"
        )
    else:
        cmd_str = (
            f"export LD_LIBRARY_PATH={lib_path}:$LD_LIBRARY_PATH; "
            f"{shlex.quote(str(bin_path))} -in {shlex.quote(in_arg)}"
        )
    cmd = ["bash", "-lc", cmd_str]

    target = Path(work_dir).expanduser().resolve() if work_dir else None

    try:
        rec: JobRecord = submit(
            engine=ENGINE,
            cmd=cmd,
            work_dir=target,
            np=np if flavor_l == "mpi" else 1,
            flavor=flavor_l,
            walltime_s=walltime_s,
            extra_files=files,
            env_overrides=LAMMPS_ENV_OVERRIDES,
            note=note,
        )
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, **rec.to_dict()}


@mcp.tool()
def get_lammps_job_status(job_id: str) -> dict[str, Any]:
    try:
        rec = get_status(job_id)
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, **rec.to_dict()}


@mcp.tool()
def wait_for_lammps_job(job_id: str, timeout_s: float = 60.0) -> dict[str, Any]:
    try:
        rec = wait_for(job_id, timeout_s=timeout_s)
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, **rec.to_dict()}


@mcp.tool()
def kill_lammps_job(job_id: str) -> dict[str, Any]:
    try:
        rec = kill(job_id)
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, **rec.to_dict()}


@mcp.tool()
def list_lammps_jobs(status: str | None = None, limit: int = 20) -> dict[str, Any]:
    recs = list_jobs(engine=ENGINE, status_filter=status, limit=limit)
    return {"ok": True, "count": len(recs), "jobs": [r.to_dict() for r in recs]}


@mcp.tool()
def tail_lammps_log_tool(job_id: str, kind: str = "stdout", lines: int = 200) -> dict[str, Any]:
    """Tail stdout.log or stderr.log for a LAMMPS job (the engine output and
    any errors during launch)."""
    try:
        text = tail_log(job_id, kind=kind, n=lines)
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "kind": kind, "lines": lines, "text": text}


@mcp.tool()
def parse_lammps_log_tool(job_id: str | None = None, path: str | None = None) -> dict[str, Any]:
    """Parse a LAMMPS log file: thermo blocks, loop time, n_atoms, walltime.

    Args:
        job_id: Resolves to <work_dir>/log.lammps (or stdout.log fallback).
        path: Or pass the log path directly.
    """
    log_path: Path | None = None
    if path:
        log_path = Path(path)
    elif job_id:
        try:
            rec = get_status(job_id)
        except KeyError as e:
            return {"ok": False, "error": str(e)}
        candidate = Path(rec.work_dir) / "log.lammps"
        if not candidate.exists():
            candidate = Path(rec.work_dir) / "stdout.log"
        log_path = candidate
    else:
        return {"ok": False, "error": "must pass job_id or path"}
    return parse_lammps_log(log_path)


@mcp.tool()
def lammps_mcp_status() -> dict[str, Any]:
    return {
        "engine": ENGINE,
        "lammps_bin_dir": str(LAMMPS_BIN_DIR),
        "lmp_mpi_path": str(LMP_MPI),
        "lmp_mpi_present": LMP_MPI.exists(),
        "lmp_serial_path": str(LMP_SERIAL),
        "lmp_serial_present": LMP_SERIAL.exists(),
        "mpirun": MPIRUN,
        "default_np": DEFAULT_NP,
        "default_walltime_s": DEFAULT_WALLTIME,
        "concurrency_cap": DEFAULT_CONCURRENCY,
        "intel_mpi_vars_path": INTEL_MPI_VARS,
        "intel_mpi_vars_present": Path(INTEL_MPI_VARS).exists(),
    }


if __name__ == "__main__":
    mcp.run()
