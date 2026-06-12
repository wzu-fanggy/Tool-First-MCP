"""
Light-weight parsers for VASP OUTCAR/OSZICAR and LAMMPS log.
No numpy / no pymatgen — keeps the deployment footprint tiny.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# VASP OSZICAR
# ---------------------------------------------------------------------------
# Sample line:
#   1 F= -.55487412E+02 E0= -.55487412E+02  d E =-.554874E+02
_OSZ_FE = re.compile(
    r"^\s*(?P<step>\d+)\s+F=\s*([-\d.E+]+)\s+E0=\s*([-\d.E+]+)\s+d E\s*=\s*([-\d.E+]+)"
)


def parse_oszicar(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": f"OSZICAR not found at {p}"}
    steps: list[dict[str, float]] = []
    last_scf_lines = 0
    final = None
    for line in p.read_text(errors="replace").splitlines():
        m = _OSZ_FE.match(line)
        if m:
            step = int(m.group("step"))
            f = float(m.group(2)); e0 = float(m.group(3)); de = float(m.group(4))
            steps.append({"ionic_step": step, "F": f, "E0": e0, "dE": de})
            final = {"ionic_step": step, "F": f, "E0": e0, "dE": de}
        elif "DAV:" in line or "RMM:" in line:
            last_scf_lines += 1
    return {
        "ok": True,
        "ionic_steps": steps,
        "n_ionic_steps": len(steps),
        "final": final,
        "approx_scf_lines": last_scf_lines,
    }


# ---------------------------------------------------------------------------
# VASP OUTCAR
# ---------------------------------------------------------------------------
_OUT_FREE_E = re.compile(r"free\s+energy\s+TOTEN\s*=\s*([-\d.E+]+)\s*eV")
_OUT_E_ENT = re.compile(r"energy\s+without\s+entropy\s*=\s*([-\d.E+]+)")
_OUT_FERMI = re.compile(r"E-fermi\s*:\s*([-\d.E+]+)")
_OUT_NIONS = re.compile(r"NIONS\s*=\s*(\d+)")
_OUT_NELECT = re.compile(r"NELECT\s*=\s*([\d.]+)")
_OUT_REACHED = re.compile(r"reached required accuracy")
_OUT_TOTAL_CPU = re.compile(r"Total CPU time used \(sec\)\s*:\s*([\d.]+)")
_OUT_FORCE_MAX = re.compile(r"^\s+POSITION\s+TOTAL-FORCE", re.MULTILINE)
_OUT_STRESS = re.compile(
    r"in kB\s+([-\d.E+]+)\s+([-\d.E+]+)\s+([-\d.E+]+)\s+([-\d.E+]+)\s+([-\d.E+]+)\s+([-\d.E+]+)"
)


def parse_outcar(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": f"OUTCAR not found at {p}"}
    text = p.read_text(errors="replace")

    # Last total energies
    free_es = [float(m.group(1)) for m in _OUT_FREE_E.finditer(text)]
    no_ent_es = [float(m.group(1)) for m in _OUT_E_ENT.finditer(text)]
    fermis = [float(m.group(1)) for m in _OUT_FERMI.finditer(text)]
    cputs = [float(m.group(1)) for m in _OUT_TOTAL_CPU.finditer(text)]
    stress_blocks = list(_OUT_STRESS.finditer(text))

    n_ions = None
    m_n = _OUT_NIONS.search(text)
    if m_n:
        n_ions = int(m_n.group(1))
    n_elect = None
    m_e = _OUT_NELECT.search(text)
    if m_e:
        try:
            n_elect = float(m_e.group(1))
        except Exception:
            n_elect = None

    converged = bool(_OUT_REACHED.search(text))

    # Pull max force from the LAST force block, if present
    force_blocks = list(_OUT_FORCE_MAX.finditer(text))
    max_force: float | None = None
    if force_blocks:
        last_block_start = force_blocks[-1].end()
        # Read up to the next blank line cluster or limit
        chunk = text[last_block_start: last_block_start + 8000]
        # Each force line has 6 numbers; take cols 4-6 magnitudes
        forces: list[float] = []
        for line in chunk.splitlines():
            parts = line.strip().split()
            if len(parts) == 6:
                try:
                    fx, fy, fz = float(parts[3]), float(parts[4]), float(parts[5])
                    forces.append((fx * fx + fy * fy + fz * fz) ** 0.5)
                except ValueError:
                    continue
        if forces:
            max_force = max(forces)

    final_stress = None
    if stress_blocks:
        last = stress_blocks[-1]
        final_stress = {
            "xx": float(last.group(1)),
            "yy": float(last.group(2)),
            "zz": float(last.group(3)),
            "xy": float(last.group(4)),
            "yz": float(last.group(5)),
            "zx": float(last.group(6)),
        }

    return {
        "ok": True,
        "n_ions": n_ions,
        "n_elect": n_elect,
        "converged": converged,
        "final_free_energy_eV": free_es[-1] if free_es else None,
        "final_energy_no_entropy_eV": no_ent_es[-1] if no_ent_es else None,
        "final_fermi_eV": fermis[-1] if fermis else None,
        "max_force_eV_per_A": max_force,
        "final_stress_kbar": final_stress,
        "n_energy_records": len(free_es),
        "total_cpu_time_s": cputs[-1] if cputs else None,
    }


# ---------------------------------------------------------------------------
# LAMMPS log parser
# ---------------------------------------------------------------------------
_LMP_THERMO_HDR = re.compile(r"^Per MPI rank|^Step\s+", re.MULTILINE)
_LMP_RUN_DONE = re.compile(r"^Loop time of\s+([\d.]+)\s+on\s+(\d+)\s+procs", re.MULTILINE)
_LMP_TOTAL_WALL = re.compile(r"Total wall time:\s+([\d:]+)")
_LMP_NATOMS = re.compile(r"^\s*(?:Created|Reading)\s+(\d+)\s+atoms", re.MULTILINE)


def parse_lammps_log(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": f"log file not found at {p}"}
    text = p.read_text(errors="replace")

    # Find each "Step ..." thermo block, parse columns and rows until Loop/'\n\n'.
    blocks: list[dict[str, Any]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("Step ") or (
            line.lstrip().startswith("Step") and "Temp" in line
        ):
            cols = line.split()
            data: list[list[float]] = []
            j = i + 1
            while j < len(lines):
                row = lines[j].strip()
                if not row or row.startswith("Loop time") or row.startswith("ERROR"):
                    break
                parts = row.split()
                if len(parts) != len(cols):
                    break
                try:
                    data.append([float(x) for x in parts])
                except ValueError:
                    break
                j += 1
            if data:
                blocks.append({
                    "columns": cols,
                    "rows": data,
                    "n_rows": len(data),
                    "first": dict(zip(cols, data[0])),
                    "last": dict(zip(cols, data[-1])),
                })
            i = j
        else:
            i += 1

    loop_match = _LMP_RUN_DONE.search(text)
    total_wall = _LMP_TOTAL_WALL.search(text)
    natoms_match = _LMP_NATOMS.search(text)

    return {
        "ok": True,
        "thermo_blocks": blocks,
        "n_thermo_blocks": len(blocks),
        "loop_time_s": float(loop_match.group(1)) if loop_match else None,
        "n_procs": int(loop_match.group(2)) if loop_match else None,
        "total_wall_time": total_wall.group(1) if total_wall else None,
        "n_atoms": int(natoms_match.group(1)) if natoms_match else None,
        "final_thermo": blocks[-1]["last"] if blocks else None,
    }
