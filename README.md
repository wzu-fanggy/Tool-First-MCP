# Tool-First-MCP

A collection of [Model Context Protocol (MCP)](https://modelcontextprotocol.io) servers for
**computational materials science**, built around a *tool-first* philosophy: the language model
performs **no** numerical estimation itself. Every quantity — composition rule, thermodynamic
equilibrium, simulation result — comes from an explicit, auditable tool call.

The toolset targets the **Co-Cr-Fe-Ni-V** high-entropy alloy (HEA) system and the HPC workflow
around it: empirical screening → CALPHAD phase equilibria → first-principles (VASP) and
molecular-dynamics (LAMMPS) calculations on a remote cluster.

## Components

| Component | What it does |
|---|---|
| **`hea_mcp_server.py`** | HEA design assistant MCP server. Empirical rules (VEC, δ, ΔHmix, ΔSmix, Ω), physical-property estimates (density, modulus, PBR), engineering checks (cost, toxicity), and strict CALPHAD phase equilibria via `pycalphad`. |
| **`mcp_hpc_runners/`** | Two MCP servers (`vasp-mcp`, `lammps-mcp`) that run VASP and LAMMPS jobs on a queue-less node, with a shared SQLite job registry for submit / poll / tail / parse. |
| **`mcp_hpc_openclaw/`** | A bridge that wraps a remote OpenClaw autonomous agent as MCP tools, so an MCP client can delegate tasks to it over WebSocket. |
| **`CoCrFeNiV.tdb`** | CALPHAD thermodynamic database (Co-Cr-Fe-Ni-V) consumed by the HEA server's `calc_phase_equilibrium` tool. |

## Repository layout

```
.
├── hea_mcp_server.py          # HEA rules + CALPHAD MCP server
├── hea_rules.py               # Empirical-rule and property calculations
├── hea_data.py                # Element property database + binary mixing enthalpies
├── CoCrFeNiV.tdb              # CALPHAD database (Co-Cr-Fe-Ni-V)
│
├── mcp_hpc_runners/           # VASP & LAMMPS MCP servers
│   ├── common/                # subprocess runner, SQLite registry, output parsers
│   ├── vasp_mcp/              # vasp-mcp server + VASPKIT input generation
│   ├── lammps_mcp/            # lammps-mcp server
│   ├── requirements.txt
│   └── README.md
│
└── mcp_hpc_openclaw/          # OpenClaw agent → MCP bridge
    ├── mcp_server.py
    ├── PROTOCOL_NOTES.md      # reverse-engineered gateway protocol notes
    ├── skills/                # OpenClaw skill definitions
    ├── requirements.txt
    └── README.md
```

## The HEA server

The HEA server is the centerpiece. Its design rule is strict: the model has **no** internal
knowledge of element properties or alloy thermodynamics, so any numerical answer must come from a
tool. Element data lives in `hea_data.py`; rule math lives in `hea_rules.py`.

Tools, grouped:

- **Electronic / geometric / thermodynamic** — `compute_vec`, `compute_delta`,
  `compute_mixing_enthalpy`, `compute_entropy`, `compute_omega`
- **Physical properties** — `estimate_density`, `estimate_modulus`, `compute_pilling_bedworth` (PBR)
- **Engineering** — `estimate_cost`, `check_toxicity`
- **Decision support** — `hume_rothery_check`, `full_screening`, `compare_compositions`,
  `suggest_substitutions`
- **Strict thermodynamics** — `calc_phase_equilibrium` (CALPHAD Gibbs-energy minimization, requires
  `pycalphad` + `CoCrFeNiV.tdb`; limited to Co/Cr/Fe/Ni/V)
- **Metadata** — `list_known_elements`

It also exposes resources (`hea://elements`, `hea://elements/{symbol}`) and a screening prompt.

### Running the HEA server

```bash
# optional: enables calc_phase_equilibrium
pip install pycalphad numpy
pip install "mcp[cli]"

python hea_mcp_server.py
```

If `pycalphad` or `CoCrFeNiV.tdb` is missing, the server still runs — only the strict CALPHAD tool
degrades gracefully and returns a friendly message instead of crashing.

### Registering with an MCP client

```jsonc
{
  "mcpServers": {
    "hea-rules": {
      "command": "python",
      "args": ["/absolute/path/to/hea_mcp_server.py"]
    }
  }
}
```

## HPC runners and OpenClaw bridge

`mcp_hpc_runners/` and `mcp_hpc_openclaw/` are deployment-oriented and target a specific remote
cluster (login node → compute node `nodec2`). Each has its own README with deployment steps,
tool tables, configuration env vars, and troubleshooting:

- [`mcp_hpc_runners/README.md`](mcp_hpc_runners/README.md) — VASP/LAMMPS servers, concurrency model, job registry
- [`mcp_hpc_openclaw/README.md`](mcp_hpc_openclaw/README.md) — OpenClaw bridge architecture and protocol

## Requirements

- Python 3.10+ (uses `from __future__ import annotations` and PEP 604 unions)
- `mcp` (FastMCP) for all servers
- HEA strict thermodynamics: `pycalphad`, `numpy`
- HPC runners: see `mcp_hpc_runners/requirements.txt`
- OpenClaw bridge: see `mcp_hpc_openclaw/requirements.txt`

## Notes and disclaimers

- Empirical-rule outputs (density, modulus, PBR, cost) are **first-order screening estimates**.
  They ignore lattice distortion, solid-solution strengthening, and processing cost. Use them to
  rank candidates, not to report final values.
- Element prices are 2024-scale estimates with large month-to-month swings; treat them as relative.
- CALPHAD results are only as good as the database coverage — cross-check against the empirical
  screening for compositions near the edge of the `CoCrFeNiV.tdb` validity range.
