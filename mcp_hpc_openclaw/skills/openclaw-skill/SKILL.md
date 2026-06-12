---
name: openclaw-agent-skill
description: |
  Standard operating procedure for delegating tasks to the OpenClaw
  autonomous agent on the remote HPC server (nodec2) via MCP. The
  bridge talks to the OpenClaw Gateway over WebSocket and exposes
  five tools.
triggers:
  - openclaw
  - HPC
  - 远程服务器
  - autonomous agent
  - 自主执行
  - delegate to openclaw
tools_required:
  - ask_openclaw_tool
  - list_openclaw_sessions_tool
  - openclaw_identity_tool
  - openclaw_status_tool
  - reset_openclaw_tool
---

# OpenClaw Agent Skill

> **MANDATORY: Always use `ask_openclaw_tool` for tasks delegated to OpenClaw.**
> Never simulate or fabricate what OpenClaw "would" return. Call the tool. Use its response.

This skill defines the standard workflow for delegating tasks to the
OpenClaw autonomous agent running on nodec2. The MCP bridge speaks to the
gateway at `ws://127.0.0.1:18789/api/v1/rpc` and is registered with
Claude Code as `openclaw-agent`.

## When to use this skill

Use the OpenClaw tools when the user asks to:

- Run experiments or scripts on the HPC server
- Execute multi-step autonomous tasks (file ops, code execution, search)
- Query the state of the remote server (GPU usage, running jobs, disk space)
- Delegate any task that benefits from autonomous planning + execution

## Tool reference

| Tool | When to call |
|---|---|
| `ask_openclaw_tool(message, session_key?, timeout_seconds?)` | Default verb. Sends a task; returns `{status, response, session_key, ...}`. Pass `session_key` returned from a previous call to continue the same conversation. |
| `openclaw_identity_tool()` | The right way to check whether the remote agent is reachable. Returns the agent identity. |
| `list_openclaw_sessions_tool()` | List all existing sessions on the agent. Use to find a session to resume. |
| `openclaw_status_tool()` | Reports this bridge's local WebSocket state ONLY. Lazy-connect: returns `disconnected` right after startup even when the agent is healthy. NEVER use this to decide whether the agent is up. |
| `reset_openclaw_tool()` | Tear down the WS connection. Call when `ask_openclaw_tool` returns `status: error` repeatedly. |

## Critical: how to check whether OpenClaw is online

**Wrong:** call `openclaw_status_tool` and conclude the agent is down because
the response says `disconnected`.

**Right:** call `openclaw_identity_tool`. If it returns `status: success` with
an `agent` field, OpenClaw is reachable. If it returns `status: error`, the
gateway or network is actually broken; report the error verbatim to the user.

The bridge only opens its WebSocket when a tool that needs the agent is called,
so `openclaw_status_tool` will say `disconnected` until the first real call.
This is by design.

## MANDATORY tool-call rule

- Always call `ask_openclaw_tool` for OpenClaw tasks; do not summarize from memory.
- If the tool returns `status: error`, surface the error to the user; do not invent a success.
- If `status: timeout`, the agent run may still be in progress. The session_key
  is preserved; either re-ask with the same session_key (likely picks up the
  finished response) or wait and call again.

## Standard workflow

### Step 1 — (optional) confirm agent reachability

If you genuinely need to verify connectivity before sending a task, call
`openclaw_identity_tool`. It triggers a real handshake and returns the agent's
identity on success.

DO NOT call `openclaw_status_tool` for this purpose. That tool reports the
bridge's own WS state, which is lazily opened — it will say `disconnected`
right after startup even when the agent is perfectly healthy.

### Step 2 — Delegate the task

```
ask_openclaw_tool(message="<task description>", timeout_seconds=180)
```

Write a clear, specific task in natural language. OpenClaw will plan and
execute autonomously and stream its answer back; the bridge waits for the
final frame and returns the assistant's last text.

For long-running tasks (training, big builds), pass `timeout_seconds=600`
or even larger.

### Step 3 — Continue a conversation (optional)

If you need follow-up turns referencing prior context, pass the
`session_key` returned by the previous call:

```
ask_openclaw_tool(
    message="Now do the same thing for X",
    session_key="agent:main:dashboard:<uuid>",
)
```

### Step 4 — Error handling

| Returned `status` | Action |
|---|---|
| `success` | Use `response` |
| `timeout` | Try again with longer `timeout_seconds`, or look at `events_seen` to gauge progress |
| `error` | Read `error` field. If it mentions `connection torn down` or `gateway not connected`, call `reset_openclaw_tool()` and retry once |

## Task formulation

**Good:**
```
列出 ~/dreams_to_MCP 目录下所有 .py 文件，并显示每个文件的行数
```

**Bad:**
```
看看那个目录
```

**Good:**
```
检查所有 GPU 的使用情况，包括显存、温度和正在跑的进程
```

**Bad:**
```
GPU 怎么样
```

## Anti-patterns

- ❌ Fabricating a response without calling the tool
- ❌ Ignoring the `status` field
- ❌ Using `timeout_seconds` shorter than the task likely needs
- ❌ Sending multiple tasks in one message (send one clear task)
- ❌ Calling `reset_openclaw_tool` proactively — only after errors

## Example

**User:** 帮我看看远程服务器上 GPU 的使用情况

**Correct flow:**

```
ask_openclaw_tool(
    message="请检查当前所有 GPU 的使用情况，包括显存占用、温度和运行的进程",
    timeout_seconds=120,
)
```

Then summarize `response` for the user.
