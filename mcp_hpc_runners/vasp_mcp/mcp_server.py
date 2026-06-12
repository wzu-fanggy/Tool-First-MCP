"""
mcp_hpc_runners/vasp_mcp/mcp_server.py

VASP MCP server. Submits VASP runs as background processes (no SLURM on the
target box), tracks them in a SQLite-backed registry, and exposes
parse_outcar / parse_oszicar helpers.

Designed for nodec2 layout:
  vasp binaries: /data/home/wzu25zj/vasp.6.4.2/bin/{vasp_std, vasp_gam, vasp_ncl}
  Intel oneAPI MPI: /data/home/wzu25zj/intel/oneapi/mpi/<ver>/bin/mpirun
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# Make the sibling 'common' package importable regardless of cwd
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
from common.parsers import parse_outcar, parse_oszicar  # noqa: E402

# Import the vaspkit-based input file generator
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _vaspkit_gen import generate_from_poscar  # noqa: E402

from mcp.server.fastmcp import FastMCP  # noqa: E402

# ---------------------------------------------------------------------------
# Site config (override via env vars)
# ---------------------------------------------------------------------------
VASP_BIN_DIR = Path(os.environ.get(
    "VASP_BIN_DIR", "/data/home/wzu25zj/vasp.6.4.2/bin"
))
MPIRUN = os.environ.get("MPIRUN", "mpirun")
DEFAULT_NP = int(os.environ.get("VASP_DEFAULT_NP", "8"))
DEFAULT_WALLTIME = int(os.environ.get("VASP_DEFAULT_WALLTIME_S", "3600"))
ENGINE = "vasp"

# Ensure Intel oneAPI MPI is on PATH (`source setvars.sh` equivalent)
INTEL_MPI_VARS = os.environ.get(
    "INTEL_MPI_VARS",
    "/data/home/wzu25zj/intel/oneapi/setvars.sh",
)

# Intel oneAPI library paths (needed at runtime for MKL + compiler libs)
INTEL_MKL_LIB = "/data/home/wzu25zj/intel/oneapi/mkl/2025.3/lib"
INTEL_COMPILER_LIB = "/data/home/wzu25zj/intel/oneapi/compiler/2025.3/lib"
INTEL_MPI_LIB = "/data/home/wzu25zj/intel/oneapi/mpi/2021.15/lib"

# POTCAR reference tree (official pseudopotentials from VASP)
POTCAR_DIR = Path("/data/home/wzu25zj/vasp_peb")

# Default env overrides passed to every VASP subprocess
VASP_ENV_OVERRIDES = {
    "LD_LIBRARY_PATH": f"{INTEL_MKL_LIB}:{INTEL_COMPILER_LIB}:{INTEL_MPI_LIB}",
    "OMP_NUM_THREADS": "1",
    "I_MPI_FABRICS": "shm",
}


def _flavor_to_bin(flavor: str) -> Path:
    f = flavor.lower().strip()
    if f not in {"std", "gam", "ncl"}:
        raise ValueError(f"unknown VASP flavor: {flavor}")
    return VASP_BIN_DIR / f"vasp_{f}"


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "vasp-mcp",
    instructions=(
        "VASP density-functional calculation runner. Submit a job with "
        "run_vasp_calc(work_dir or inputs={INCAR,KPOINTS,POSCAR,POTCAR}, np, "
        "flavor); poll with get_job_status / wait_for_job; parse results with "
        "parse_outcar_tool / parse_oszicar_tool. Jobs run in background; this "
        "MCP returns immediately with a job_id."
    ),
)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
@mcp.tool()
def run_vasp_calc(
    work_dir: str | None = None,
    inputs: dict[str, str] | None = None,
    flavor: str = "std",
    np: int = DEFAULT_NP,
    walltime_s: int = DEFAULT_WALLTIME,
    note: str | None = None,
) -> dict[str, Any]:
    """Submit a VASP run as a background process.

    Args:
        work_dir: Existing directory containing INCAR/KPOINTS/POSCAR/POTCAR.
            If None, ``inputs`` MUST be provided and a fresh directory under
            ~/mcp_runs/vasp/<job_id>/ is created and populated from it.
        inputs: Mapping of input filename -> contents. Used only when
            work_dir is None. Typical keys: "INCAR", "KPOINTS", "POSCAR",
            "POTCAR".
        flavor: One of {"std", "gam", "ncl"}.
        np: MPI rank count.
        walltime_s: Hard wall-clock cap (seconds). When exceeded, the runner
            sends SIGTERM then SIGKILL and marks the job 'timeout'.
        note: Free-form note stored alongside the job for later inspection.

    Returns:
        Job dict including id, status='running', work_dir, stdout/stderr paths.
    """
    if work_dir is None and not inputs:
        return {"ok": False, "error": "must provide either work_dir or inputs"}
    if work_dir is not None and inputs:
        return {"ok": False, "error": "provide only one of work_dir / inputs"}

    bin_path = _flavor_to_bin(flavor)
    if not bin_path.exists():
        return {"ok": False, "error": f"VASP binary not found: {bin_path}"}

    # Build cmd: source intel mpi vars (if available) then mpirun -np N vasp_xxx
    # The runner already wraps with bash -lc, so the leading 'source' is fine.
    # Prepend LD_LIBRARY_PATH so MKL/compiler libs are found.
    lib_path = VASP_ENV_OVERRIDES["LD_LIBRARY_PATH"]
    cmd_str = (
        f"source {INTEL_MPI_VARS} >/dev/null 2>&1 || true; "
        f"export LD_LIBRARY_PATH={lib_path}:$LD_LIBRARY_PATH; "
        f"{MPIRUN} -np {np} {bin_path}"
    )
    cmd = ["bash", "-lc", cmd_str]

    extra_files = inputs if inputs else None
    target = Path(work_dir).expanduser().resolve() if work_dir else None

    # If only POSCAR is provided, use vaspkit to auto-generate
    # INCAR, KPOINTS, and POTCAR.
    if extra_files is not None and "POSCAR" in extra_files:
        has_incar = "INCAR" in extra_files
        has_kpoints = "KPOINTS" in extra_files
        has_potcar = "POTCAR" in extra_files

        # If any of INCAR / KPOINTS / POTCAR is missing, use vaspkit
        if not (has_incar and has_kpoints and has_potcar):
            try:
                gen = generate_from_poscar(extra_files["POSCAR"])
            except Exception as e:
                return {"ok": False,
                        "error": f"vaspkit auto-generation failed: {type(e).__name__}: {e}"}
            if not has_incar:
                extra_files["INCAR"] = gen["INCAR"]
            if not has_kpoints:
                extra_files["KPOINTS"] = gen["KPOINTS"]
            if not has_potcar:
                extra_files["POTCAR"] = gen["POTCAR"]

    try:
        rec: JobRecord = submit(
            engine=ENGINE,
            cmd=cmd,
            work_dir=target,
            np=np,
            flavor=flavor,
            walltime_s=walltime_s,
            extra_files=extra_files,
            env_overrides=VASP_ENV_OVERRIDES,
            note=note,
        )
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, **rec.to_dict()}


@mcp.tool()
def get_job_status(job_id: str) -> dict[str, Any]:
    """Return the current status of a VASP job (reconciles with OS pid)."""
    try:
        rec = get_status(job_id)
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, **rec.to_dict()}


@mcp.tool()
def wait_for_job(job_id: str, timeout_s: float = 60.0) -> dict[str, Any]:
    """Block up to timeout_s seconds for the job to leave 'running'. Returns
    the latest record either way. Safe to call repeatedly."""
    try:
        rec = wait_for(job_id, timeout_s=timeout_s)
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, **rec.to_dict()}


@mcp.tool()
def kill_job(job_id: str) -> dict[str, Any]:
    """Terminate a running VASP job (SIGTERM then SIGKILL)."""
    try:
        rec = kill(job_id)
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, **rec.to_dict()}


@mcp.tool()
def list_vasp_jobs(status: str | None = None, limit: int = 20) -> dict[str, Any]:
    """List recent VASP jobs (optionally filtered by status)."""
    recs = list_jobs(engine=ENGINE, status_filter=status, limit=limit)
    return {"ok": True, "count": len(recs), "jobs": [r.to_dict() for r in recs]}


@mcp.tool()
def tail_vasp_log(job_id: str, kind: str = "stdout", lines: int = 200) -> dict[str, Any]:
    """Read the tail of stdout.log or stderr.log for a job."""
    try:
        text = tail_log(job_id, kind=kind, n=lines)
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "kind": kind, "lines": lines, "text": text}


@mcp.tool()
def parse_outcar_tool(job_id: str | None = None, path: str | None = None) -> dict[str, Any]:
    """Parse VASP OUTCAR for energies, forces, stress, convergence.

    Args:
        job_id: If given, read OUTCAR from the job's work_dir.
        path: Or pass an explicit OUTCAR path.
    """
    target = _resolve_artifact_path(job_id, path, "OUTCAR")
    if isinstance(target, dict):
        return target
    return parse_outcar(target)


@mcp.tool()
def parse_oszicar_tool(job_id: str | None = None, path: str | None = None) -> dict[str, Any]:
    """Parse VASP OSZICAR for ionic-step / total-energy history."""
    target = _resolve_artifact_path(job_id, path, "OSZICAR")
    if isinstance(target, dict):
        return target
    return parse_oszicar(target)


def _resolve_artifact_path(
    job_id: str | None, path: str | None, fname: str
) -> Path | dict[str, Any]:
    if path:
        return Path(path)
    if not job_id:
        return {"ok": False, "error": "must pass either job_id or path"}
    try:
        rec = get_status(job_id)
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    return Path(rec.work_dir) / fname


@mcp.tool()
def vasp_mcp_status() -> dict[str, Any]:
    """Report this MCP server's local environment / config."""
    return {
        "engine": ENGINE,
        "vasp_bin_dir": str(VASP_BIN_DIR),
        "vasp_bin_dir_exists": VASP_BIN_DIR.exists(),
        "binaries_present": [
            f for f in ("vasp_std", "vasp_gam", "vasp_ncl")
            if (VASP_BIN_DIR / f).exists()
        ],
        "mpirun": MPIRUN,
        "default_np": DEFAULT_NP,
        "default_walltime_s": DEFAULT_WALLTIME,
        "concurrency_cap": DEFAULT_CONCURRENCY,
        "intel_mpi_vars_path": INTEL_MPI_VARS,
        "intel_mpi_vars_present": Path(INTEL_MPI_VARS).exists(),
    }


if __name__ == "__main__":
    mcp.run()
