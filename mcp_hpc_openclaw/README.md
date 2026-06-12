# mcp_hpc_openclaw

把远程 HPC 服务器（nodec2）上的 OpenClaw 自主 Agent 包装成 MCP 工具服务器，
让 Claude Code 这类 MCP 客户端可以直接对它发任务。

## 架构

```
本地 Claude Code (Windows)
    │  MCP stdio
    ▼
登录节点 nodeM1 (10.12.1.182)
    │  ssh
    ▼
计算节点 nodec2
    └── mcp_server.py
            │  WebSocket
            ▼
        OpenClaw Gateway @ 127.0.0.1:18789  ←── /api/v1/rpc
            │
            └── OpenClaw Agent "main" / model deepseek-chat
```

桥用 WebSocket 直连同机的 gateway，不经 SSH 隧道、不经伪终端。
认证走 gateway 的 shared-secret token，配合 `client.id=gateway-client + mode=backend`
触发 loopback 自配对豁免，因此不需要持有设备私钥。

## 工具

| 工具 | 作用 |
|---|---|
| `ask_openclaw_tool(message, session_key=None, timeout_seconds=180)` | 把任务发给 OpenClaw，等流式回答的 final 帧 |
| `list_openclaw_sessions_tool()` | 列出 agent 上现存所有会话 |
| `openclaw_identity_tool()` | 返回 agent 身份（agentId / name / avatar） |
| `openclaw_status_tool()` | 看本桥的 gateway 连接状态和已授 scopes |
| `reset_openclaw_tool()` | 断开重连（连接异常时用） |

`ask_openclaw_tool` 的返回字典：

```json
{
  "status": "success",         // "success" / "timeout" / "error"
  "response": "pong",          // agent 的纯文本回答
  "session_key": "agent:main:dashboard:<uuid>",
  "session_created": true,
  "run_id": "<uuid>",
  "events_seen": 7,
  "agent": "OpenClaw",
  "gateway": "ws://127.0.0.1:18789/api/v1/rpc"
}
```

## 部署步骤

### 1. 上传到 nodec2

```bash
scp -r mcp_hpc_openclaw/ wzu25zj@10.12.1.182:~/mcp_hpc_openclaw/
ssh wzu25zj@10.12.1.182 "scp -r ~/mcp_hpc_openclaw nodec2:~/"
```

### 2. 装依赖（在 nodec2 的 conda env 里）

```bash
ssh wzu25zj@10.12.1.182 "ssh nodec2 '/data/home/wzu25zj/miniconda3/bin/pip install -r ~/mcp_hpc_openclaw/requirements.txt'"
```

### 3. 验证 gateway 可达

```bash
ssh wzu25zj@10.12.1.182 "ssh nodec2 'curl -s http://127.0.0.1:18789/healthz'"
# 应返回 {"ok":true,"status":"live"}
```

### 4. 在本地 Claude Code 注册（Windows PowerShell）

```powershell
claude mcp add openclaw -s user --transport stdio -- `
    ssh wzu25zj@10.12.1.182 `
    "ssh nodec2 /data/home/wzu25zj/miniconda3/bin/python /data/home/wzu25zj/mcp_hpc_openclaw/mcp_server.py"
```

注意是**双层 ssh**：先到 nodeM1，再跳到 nodec2 启动 server。这是因为 OpenClaw
gateway 只监听 nodec2 的 127.0.0.1:18789，不暴露到外网；MCP 桥必须跑在 nodec2 上。

### 5. 验证

在 Claude Code 里执行 `/mcp`，应看到：

```
openclaw-agent · connected · 5 tools
```

## 配置（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `OPENCLAW_GATEWAY_URL` | `ws://127.0.0.1:18789/api/v1/rpc` | gateway 入口 |
| `OPENCLAW_GATEWAY_TOKEN` | 内置 | shared-secret token，来源是 `~/.openclaw/openclaw.json` 里 `gateway.auth.token` |

## 常见问题

**Q: tools/call 报 `connected but server granted empty scopes`**

说明本桥到 gateway 不在同一台机器（loopback 检查失败）或 client.mode 不对。
本桥默认走 `client.id=gateway-client, mode=backend`，必须运行在 gateway 同一台
nodec2 上才能拿到 scopes。

**Q: connect 报 `gateway auth failed`**

token 不对。检查 `~/.openclaw/openclaw.json` 里 `gateway.auth.token` 是否变了；
gateway 每次轮换 token 后需要更新 `OPENCLAW_GATEWAY_TOKEN`。

**Q: ask_openclaw_tool 返回 `status: timeout`**

agent 还在跑但 timeout_seconds 用完了。把 timeout 调大（比如 300 或 600）；
连接没断，agent 会继续跑完，下一次 ask 还能用同一个 `session_key` 接着对话。

**Q: 想看协议细节？**

读同目录的 `PROTOCOL_NOTES.md`，那是从 gateway 自己的代码反向得到的协议事实卡。
