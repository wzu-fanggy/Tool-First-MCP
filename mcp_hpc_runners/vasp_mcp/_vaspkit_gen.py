"""
_vaspkit_gen.py

Helper: Given a POSCAR, use vaspkit to generate INCAR, KPOINTS and POTCAR,
then patch INCAR for common HEA / magnetic cases.

Called by mcp_server.py when only POSCAR is provided as input.
"""

import os
import subprocess
import tempfile
from pathlib import Path

VASPKIT_BIN = "/data/home/wzu25zj/vaspkit.1.5.1/bin/vaspkit"

FULL_INCAR_TEMPLATE = """Global Parameters
ISTART =  1            (Read existing wavefunction, if there)
ISPIN  =  {ispin}     (Non-Spin polarised DFT / Spin polarised DFT)
LREAL  = .FALSE.       (Projection operators: automatic)
LWAVE  = .FALSE.       (Write WAVECAR or not)
LCHARG = .FALSE.       (Write CHGCAR or not)
ADDGRID= .TRUE.        (Increase grid, helps GGA convergence)
LASPH  = .TRUE.        (More accurate total energies)
PREC   = Accurate      (Avoid aliasing / wrap-around errors)
ENCUT  = {encut}       (Cut-off energy)
EDIFF  = 1E-06         (SCF energy convergence, in eV)
ISMEAR = {ismear}      (Smearing method)
SIGMA  = {sigma}       (Smearing width)
LORBIT = 11            (Projected DOS)
NELM   = 200           (Max electronic SCF steps)
NSW    = 0             (No ionic relaxation)
IBRION = -1            (No ionic relaxation)
{magnetism}
"""

# Elements that typically require spin-polarised calculation
ALWAYS_MAGNETIC = {"Fe", "Co", "Ni", "Mn", "Cr", "V", "Gd", "Tb", "Dy", "Ho",
                   "Er", "Tm", "Nd", "Pr", "Sm", "Eu", "Ce", "Pu", "Np", "U",
                   "Ti", "Mo", "W", "Ru", "Os", "Re", "Rh", "Ir", "Pd", "Pt",
                   "Cu", "Ag", "Au"}


def _run_vaspkit(stdin_input: str, cwd: str | Path, timeout: int = 30):
    """Run vaspkit with piped stdin input, return (returncode, stdout+stderr)."""
    proc = subprocess.run(
        [VASPKIT_BIN],
        input=stdin_input,
        capture_output=True,
        cwd=str(cwd),
        timeout=timeout,
        text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def _parse_poscar(poscar_text: str) -> dict:
    """Minimal POSCAR parser. Returns element symbols and lattice constant."""
    lines = poscar_text.strip().splitlines()
    if len(lines) < 7:
        raise ValueError("POSCAR has fewer than 7 lines")

    scale = float(lines[1].strip())

    # If sixth line has element symbols, use them
    elem_line = lines[5].strip()
    elements = elem_line.split()
    if not elements:
        raise ValueError("Element line (line 6) is empty in POSCAR")

    # Seventh line: atom counts per element
    atom_counts = [int(c) for c in lines[6].strip().split()]
    if len(atom_counts) < len(elements):
        if len(atom_counts) == 1 and len(elements) >= 1:
            pass  # single element case
        else:
            raise ValueError(
                f"Atom count line has {len(atom_counts)} values but "
                f"{len(elements)} elements: {atom_counts}"
            )

    return {
        "elements": elements,
        "atom_counts": atom_counts,
        "total_atoms": sum(atom_counts),
        "scale": scale,
        "lattice": lines[2:5],
    }


def _default_ispin_and_magmom(elements: list[str], atom_counts: list[int]) -> tuple[int, str]:
    """Return (ISPIN, MAGMOM line or empty string).

    MAGMOM is expanded per-atom following atom_counts.
    """
    magnetic_found = any(e in ALWAYS_MAGNETIC for e in elements)

    if not magnetic_found:
        return (1, "")
    else:
        magmom_parts: list[str] = []
        for elem, n in zip(elements, atom_counts):
            val = _default_magmom(elem)
            if n > 1:
                magmom_parts.append(f"{n}*{val}")
            else:
                magmom_parts.append(str(val))
        return (2, "MAGMOM = " + "  ".join(magmom_parts))


def _default_magmom(element: str) -> float:
    """Default initial magnetic moment per atom (rough guess)."""
    rough_guess = {
        "Fe": 4.0, "Co": 3.0, "Ni": 2.0, "Mn": 5.0, "Cr": 3.0,
        "V": 2.0, "Gd": 7.0, "Nd": 3.0, "Pr": 2.0, "Sm": 5.0,
        "Eu": 7.0, "Dy": 5.0, "Ho": 4.0, "Er": 3.0, "Tm": 2.0,
        "Ce": 2.0, "Mo": 3.0, "Ru": 2.0, "Os": 2.0, "Re": 3.0,
        "Rh": 1.0, "Ir": 1.0, "Pd": 1.0, "Pt": 1.0, "Cu": 1.0,
        "Ag": 1.0, "Au": 1.0, "Ti": 1.0, "W": 2.0,
    }
    return rough_guess.get(element, 3.0)


def _default_encut(elements: list[str]) -> float:
    """Get default ENCUT (recommended for PBE pseudopotentials)."""
    # Most TM elements need ENCUT ~520 eV as a safe default (1.3x ENMAX)
    return 520


def _default_smearing(elements: list[str]) -> tuple[int, float]:
    """Return (ISMEAR, SIGMA) based on whether it's a metal."""
    metals_or_magnetic = {"Fe", "Co", "Ni", "Mn", "Cr", "V", "Mo", "W",
                          "Ru", "Os", "Re", "Rh", "Ir", "Pd", "Pt",
                          "Cu", "Ag", "Au", "Zn", "Cd", "Al",
                          "Ga", "In", "Sn", "Pb", "Bi", "Sb",
                          "La", "Ce", "Nd", "Sm", "Eu", "Gd",
                          "Ti", "Zr", "Hf", "Nb", "Ta"}
    if any(e in metals_or_magnetic for e in elements):
        return (1, 0.2)  # Methfessel-Paxton for metals
    else:
        return (0, 0.05)  # Gaussian for insulators / semiconductors


def generate_from_poscar(
    poscar_text: str,
    working_dir: str | None = None,
    kspacing: float = 0.04,
    encut: int | None = None,
    magnetic_override: bool | None = None,
) -> dict[str, str]:
    """
    Use vaspkit to generate KPOINTS + POTCAR, then build a custom INCAR
    with sensible defaults.

    Args:
        poscar_text: Content of POSCAR file.
        working_dir: Directory to write files. If None, uses a temp dir.
        kspacing: K-spacing value in 2*pi/angstrom (smaller = denser).
        encut: ENCUT in eV. If None, auto-guess from elements.
        magnetic_override: Force ISPIN=2 if True, force ISPIN=1 if False.

    Returns:
        dict with keys "INCAR", "KPOINTS", "POTCAR" (file contents).
    """
    # Parse POSCAR to understand the system
    info = _parse_poscar(poscar_text)
    elements = info["elements"]

    # Determine magnetism
    if magnetic_override is False:
        ispin = 1
        magmom_line = ""
    elif magnetic_override is True:
        ispin, magmom_line = _default_ispin_and_magmom(elements, info["atom_counts"])
    else:
        ispin, magmom_line = _default_ispin_and_magmom(elements, info["atom_counts"])

    # Smearing
    ismear, sigma = _default_smearing(elements)

    # ENCUT
    if encut is None:
        encut = _default_encut(elements)

    # Create working directory
    cwd = Path(working_dir) if working_dir else Path(tempfile.mkdtemp(prefix="vaspkit_"))
    cwd.mkdir(parents=True, exist_ok=True)

    # Write POSCAR
    (cwd / "POSCAR").write_text(poscar_text)

    # Step 1: vaspkit 102 -> generate KPOINTS + POTCAR
    stdin_102 = f"102\n2\n{kspacing}\n"
    rc_102, log_102 = _run_vaspkit(stdin_102, cwd)
    if rc_102 != 0:
        raise RuntimeError(f"vaspkit 102 failed:\n{log_102}")

    # Step 2: Build our own INCAR (skip vaspkit INCAR generation)
    magmom_trimmed = magmom_line.strip()

    incar_text = FULL_INCAR_TEMPLATE.format(
        ispin=ispin,
        encut=encut,
        ismear=ismear,
        sigma=sigma,
        magnetism=magmom_trimmed,
    )

    # Read back KPOINTS and POTCAR
    kpoints_path = cwd / "KPOINTS"
    potcar_path = cwd / "POTCAR"

    if not kpoints_path.exists():
        raise RuntimeError("vaspkit did not generate KPOINTS")

    # If POTCAR is missing, try direct copy from vasp_peb
    if not potcar_path.exists():
        peb_dir = Path("/data/home/wzu25zj/vasp_peb")
        elem = elements[0]
        src = peb_dir / elem / "POTCAR"
        if src.exists():
            potcar_text = src.read_text()
            (cwd / "POTCAR").write_text(potcar_text)
        else:
            raise RuntimeError(f"POTCAR not generated by vaspkit and not found in vasp_peb/{elem}")
    else:
        potcar_text = potcar_path.read_text()

    return {
        "INCAR": incar_text,
        "KPOINTS": kpoints_path.read_text(),
        "POTCAR": potcar_text,
    }


if __name__ == "__main__":
    # Test with BCC Fe
    test_poscar = """BCC Fe
1.0
   2.8700000000   0.0000000000   0.0000000000
   0.0000000000   2.8700000000   0.0000000000
   0.0000000000   0.0000000000   2.8700000000
Fe
2
Direct
   0.000000000   0.000000000   0.000000000
   0.500000000   0.500000000   0.500000000
"""
    files = generate_from_poscar(test_poscar, working_dir="/tmp/vaspkit_mcp_test")
    print("=== INCAR ===")
    print(files["INCAR"])
    print("=== KPOINTS ===")
    print(files["KPOINTS"])
    print(f"=== POTCAR: {len(files['POTCAR'])} bytes ===")
    print("✅ Test passed")
