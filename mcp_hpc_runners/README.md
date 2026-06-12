# mcp_hpc_runners

Two MCP servers — `vasp-mcp` and `lammps-mcp` — that expose VASP and LAMMPS
to MCP clients (Claude Code, OpenClaw, etc.) on a node without a queue
system. Both servers share a small SQLite-backed job registry so a parent
agent can submit, poll, and reconcile multiple parallel calculations.

## Layout

```
mcp_hpc_runners/
├── common/
│   ├── __init__.py
│   ├── _runner.py        # subprocess + sqlite + walltime + status reconciliation
│   └── parsers.py        # OUTCAR / OSZICAR / LAMMPS log parsers
├── vasp_mcp/
│   └── mcp_server.py     # vasp-mcp FastMCP server
├── lammps_mcp/
│   └── mcp_server.py     # lammps-mcp FastMCP server
├── requirements.txt
└── README.md
```

## Tools

### vasp-mcp

| Tool | Purpose |
|---|---|
| `run_vasp_calc(work_dir? or inputs?, flavor='std', np=8, walltime_s=3600)` | Submit a job (background). Returns `{job_id, work_dir, ...}`. |
| `get_job_status(job_id)` | Reconcile pid + return current status. |
| `wait_for_job(job_id, timeout_s=60)` | Block up to N seconds for completion. |
| `kill_job(job_id)` | SIGTERM then SIGKILL the process group. |
| `list_vasp_jobs(status?, limit=20)` | List recent VASP jobs. |
| `tail_vasp_log(job_id, kind='stdout', lines=200)` | Read tail of stdout.log/stderr.log. |
| `parse_outcar_tool(job_id? or path?)` | Energies, forces, stress, convergence. |
| `parse_oszicar_tool(job_id? or path?)` | Ionic-step / total-energy history. |
| `vasp_mcp_status()` | Inspect server config and binary presence. |

### lammps-mcp

| Tool | Purpose |
|---|---|
| `run_lammps(input_file? or input_text?, extra_files?, flavor='mpi', np=8)` | Submit a LAMMPS run. |
| `get_lammps_job_status / wait_for_lammps_job / kill_lammps_job / list_lammps_jobs / tail_lammps_log_tool` | Same shape as VASP. |
| `parse_lammps_log_tool(job_id? or path?)` | Thermo blocks, loop time, n_atoms. |
| `lammps_mcp_status()` | Inspect config / binary presence. |

## Concurrency model

- Per-engine concurrency cap, default 2, override with `MCP_HPC_CONCURRENCY=N`.
- Trying to submit more than the cap returns `{"ok": false, "error": "concurrency limit reached…"}`.
- Reasoning: nodec2 has 48 cores and no queue. With `np=8` and cap=2 we
  reserve enough headroom to keep the box responsive and let the agent
  schedule new work as previous jobs finish.

## Config (env vars)

| Var | Default | Notes |
|---|---|---|
| `MCP_HPC_BASE` | `~/mcp_runs` | Where job dirs and `jobs.sqlite` live. |
| `MCP_HPC_CONCURRENCY` | `2` | Per-engine cap. |
| `VASP_BIN_DIR` | `/data/home/wzu25zj/vasp.6.4.2/bin` | |
| `LAMMPS_BIN_DIR` | `/data/home/wzu25zj/lammps-22Jul2025/src` | |
| `MPIRUN` | `mpirun` | Resolved via `INTEL_MPI_VARS` source-able script. |
| `INTEL_MPI_VARS` | `/data/home/wzu25zj/intel/oneapi/setvars.sh` | sourced before launch. |
| `VASP_DEFAULT_NP` | `8` | |
| `LAMMPS_DEFAULT_NP` | `8` | |
| `VASP_DEFAULT_WALLTIME_S` | `3600` | |
| `LAMMPS_DEFAULT_WALLTIME_S` | `1800` | |

## Deployment to nodec2

```bash
# from the local repo root
scp -r mcp_hpc_runners/ wzu25zj@10.12.1.182:~/mcp_hpc_runners/
ssh wzu25zj@10.12.1.182 "scp -r ~/mcp_hpc_runners nodec2:~/"
ssh wzu25zj@10.12.1.182 "ssh nodec2 '/data/home/wzu25zj/miniconda3/bin/pip install -r ~/mcp_hpc_runners/requirements.txt'"
```

## Register with Claude Code (Windows)

```powershell
claude mcp add vasp -s user --transport stdio -- `
    ssh wzu25zj@10.12.1.182 `
    "ssh nodec2 /data/home/wzu25zj/miniconda3/bin/python /data/home/wzu25zj/mcp_hpc_runners/vasp_mcp/mcp_server.py"

claude mcp add lammps -s user --transport stdio -- `
    ssh wzu25zj@10.12.1.182 `
    "ssh nodec2 /data/home/wzu25zj/miniconda3/bin/python /data/home/wzu25zj/mcp_hpc_runners/lammps_mcp/mcp_server.py"
```
