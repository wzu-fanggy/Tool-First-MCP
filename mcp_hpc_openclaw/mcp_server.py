"""
mcp_hpc_openclaw/mcp_server.py
将远程 nodec2 上的 OpenClaw Agent 通过 WebSocket Gateway 包装为 MCP 工具服务器。

部署位置：登录节点 nodeM1 (10.12.1.182) 或本地（通过 SSH 隧道）。
依赖：
    pip install mcp websockets

详细协议见同目录 PROTOCOL_NOTES.md。
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import websockets
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# 配置（可由环境变量覆盖）
# ---------------------------------------------------------------------------
GATEWAY_URL = os.environ.get("OPENCLAW_GATEWAY_URL", "ws://127.0.0.1:18789/api/v1/rpc")
GATEWAY_TOKEN = os.environ.get(
    "OPENCLAW_GATEWAY_TOKEN",
    "9c6d8cd1a3e734bb4f8afc58947e19aaa4642e4d8fb4d8e1",
)

CLIENT_ID = "gateway-client"     # 必须配合 mode=backend 才能保留 scopes
CLIENT_MODE = "backend"
DEFAULT_SCOPES = [
    "operator.admin",
    "operator.read",
    "operator.write",
    "operator.approvals",
    "operator.pairing",
    "operator.talk.secrets",
]

# 单次 chat 等待终态最长时间
DEFAULT_CHAT_TIMEOUT_S = 180
# RPC 调用响应等待
RPC_TIMEOUT_S = 30


# ---------------------------------------------------------------------------
# 后台 asyncio loop（FastMCP 同步工具内部需要发异步调用，统一用一个独立线程跑）
# ---------------------------------------------------------------------------
class _Background:
    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        def runner() -> None:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self._ready.set()
            self.loop.run_forever()

        self._thread = threading.Thread(target=runner, name="openclaw-bg", daemon=True)
        self._thread.start()
        self._ready.wait()

    def run(self, coro):
        """在后台 loop 跑一段协程并阻塞等待结果。"""
        self.start()
        assert self.loop is not None
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return fut.result()


_bg = _Background()


# ---------------------------------------------------------------------------
# OpenClaw 网关客户端 — 持久长连接 + 请求/响应 + 事件路由
# ---------------------------------------------------------------------------
@dataclass
class _PendingRequest:
    future: asyncio.Future
    method: str


class OpenclawClient:
    def __init__(self, url: str, token: str) -> None:
        self.url = url
        self.token = token
        self.ws: websockets.WebSocketClientProtocol | None = None
        self.connected_event: asyncio.Event = asyncio.Event()
        self._reader_task: asyncio.Task | None = None
        self._connect_lock: asyncio.Lock | None = None
        self._pending: dict[str, _PendingRequest] = {}
        # 每个 sessionKey 对应一组事件队列，由谁请求谁消费
        self._stream_queues: dict[str, list[asyncio.Queue]] = {}
        self.hello: dict[str, Any] | None = None
        self.connection_id: str | None = None

    # -- lifecycle --

    async def ensure_connected(self) -> None:
        if self._connect_lock is None:
            self._connect_lock = asyncio.Lock()
        async with self._connect_lock:
            if self.ws and self.ws.state.name == "OPEN":
                return
            await self._connect()

    async def _connect(self) -> None:
        # 重置状态
        self.connected_event.clear()
        self._pending.clear()
        # 注意：不清 _stream_queues —— 调用者持有的队列仍可能被复用

        ws = await websockets.connect(self.url, ping_interval=None, max_size=8 * 1024 * 1024)
        self.ws = ws

        # 先收 challenge
        challenge_raw = await asyncio.wait_for(ws.recv(), timeout=10)
        challenge = json.loads(challenge_raw)
        if challenge.get("event") != "connect.challenge":
            raise RuntimeError(f"unexpected first frame: {challenge_raw[:200]}")

        # 启动读取 loop（也会处理 connect 自己的 res）
        self._reader_task = asyncio.create_task(self._read_loop(), name="openclaw-reader")

        # 发 connect
        connect_id = str(uuid.uuid4())
        connect_fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[connect_id] = _PendingRequest(future=connect_fut, method="connect")
        await ws.send(json.dumps({
            "type": "req",
            "id": connect_id,
            "method": "connect",
            "params": {
                "minProtocol": 3,
                "maxProtocol": 3,
                "client": {
                    "id": CLIENT_ID,
                    "version": "openclaw-mcp/0.1",
                    "platform": "linux",
                    "mode": CLIENT_MODE,
                    "instanceId": str(uuid.uuid4()),
                },
                "role": "operator",
                "scopes": DEFAULT_SCOPES,
                "caps": ["tool-events"],
                "auth": {"token": self.token},
                "userAgent": "openclaw-mcp-bridge/0.1",
                "locale": "en-US",
            },
        }))

        try:
            res = await asyncio.wait_for(connect_fut, timeout=15)
        except Exception:
            await self._teardown()
            raise

        if not res.get("ok"):
            await self._teardown()
            raise RuntimeError(f"connect rejected: {res.get('error')}")

        self.hello = res.get("payload") or {}
        self.connection_id = ((self.hello.get("server") or {}).get("connId"))
        scopes = ((self.hello.get("auth") or {}).get("scopes")) or []
        if not scopes:
            await self._teardown()
            raise RuntimeError(
                "connected but server granted empty scopes (likely "
                "shouldSkipLocalBackendSelfPairing didn't apply — check we are on loopback "
                "with mode=backend)."
            )
        self.connected_event.set()

    async def _teardown(self) -> None:
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
        ws = self.ws
        self.ws = None
        if ws:
            try:
                await ws.close()
            except Exception:
                pass
        # fail every pending future
        for rid, p in list(self._pending.items()):
            if not p.future.done():
                p.future.set_exception(RuntimeError("connection torn down"))
            self._pending.pop(rid, None)
        self.connected_event.clear()

    # -- IO --

    async def _read_loop(self) -> None:
        ws = self.ws
        assert ws is not None
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                kind = msg.get("type")
                if kind == "res":
                    rid = msg.get("id")
                    pending = self._pending.pop(rid, None)
                    if pending and not pending.future.done():
                        pending.future.set_result(msg)
                elif kind == "event":
                    self._dispatch_event(msg)
                # 'req' from server (none expected) — ignore
        except websockets.ConnectionClosed:
            pass
        finally:
            await self._teardown()

    def _dispatch_event(self, msg: dict[str, Any]) -> None:
        payload = msg.get("payload") or {}
        sk = payload.get("sessionKey") or (payload.get("session") or {}).get("key")
        if not sk:
            return
        queues = list(self._stream_queues.get(sk, []))
        for q in queues:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                # if a consumer is too slow, drop oldest
                try:
                    q.get_nowait()
                    q.put_nowait(msg)
                except Exception:
                    pass

    # -- public API --

    async def call(self, method: str, params: dict[str, Any], timeout: float = RPC_TIMEOUT_S) -> dict[str, Any]:
        await self.ensure_connected()
        rid = str(uuid.uuid4())
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[rid] = _PendingRequest(future=fut, method=method)
        assert self.ws is not None
        await self.ws.send(json.dumps({
            "type": "req",
            "id": rid,
            "method": method,
            "params": params,
        }))
        try:
            res = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            raise
        if not res.get("ok"):
            err = res.get("error") or {}
            raise RuntimeError(f"{method} failed: {err.get('code')} {err.get('message')}")
        return res.get("payload") or {}

    def subscribe_session(self, session_key: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1024)
        self._stream_queues.setdefault(session_key, []).append(q)
        return q

    def unsubscribe_session(self, session_key: str, q: asyncio.Queue) -> None:
        bucket = self._stream_queues.get(session_key, [])
        if q in bucket:
            bucket.remove(q)
        if not bucket:
            self._stream_queues.pop(session_key, None)


_client: OpenclawClient | None = None


def _get_client() -> OpenclawClient:
    global _client
    if _client is None:
        _client = OpenclawClient(GATEWAY_URL, GATEWAY_TOKEN)
    return _client


# ---------------------------------------------------------------------------
# 高层 RPC：发一条消息并等到 final
# ---------------------------------------------------------------------------
async def _ask_async(message: str, *, session_key: str | None, timeout: float) -> dict[str, Any]:
    client = _get_client()
    await client.ensure_connected()

    # 1. 准备 sessionKey
    created_new = False
    if session_key is None:
        payload = await client.call("sessions.create", {})
        session_key = payload["key"]
        created_new = True

    # 2. 订阅事件
    q = client.subscribe_session(session_key)
    try:
        # 3. 发送
        idem = str(uuid.uuid4())
        send_payload = await client.call("chat.send", {
            "sessionKey": session_key,
            "message": message,
            "idempotencyKey": idem,
        })
        run_id = send_payload.get("runId")

        # 4. 收事件直到 chat state=final
        deadline = asyncio.get_event_loop().time() + timeout
        final_text = ""
        final_seen = False
        events_seen = 0
        while True:
            remain = deadline - asyncio.get_event_loop().time()
            if remain <= 0:
                break
            try:
                msg = await asyncio.wait_for(q.get(), timeout=remain)
            except asyncio.TimeoutError:
                break
            events_seen += 1
            ev = msg.get("event")
            payload = msg.get("payload") or {}
            if payload.get("runId") and run_id and payload.get("runId") != run_id:
                continue
            if ev == "chat":
                state = payload.get("state")
                msg_obj = payload.get("message") or {}
                if state == "final":
                    final_text = _extract_text(msg_obj)
                    final_seen = True
                    break

        return {
            "status": "success" if final_seen else "timeout",
            "response": final_text,
            "session_key": session_key,
            "session_created": created_new,
            "run_id": run_id,
            "events_seen": events_seen,
            "agent": "OpenClaw",
            "gateway": GATEWAY_URL,
        }
    finally:
        client.unsubscribe_session(session_key, q)


def _extract_text(message_obj: dict[str, Any]) -> str:
    """从 chat state=final 的 message 对象里抽取纯文本。"""
    if not message_obj:
        return ""
    if isinstance(message_obj.get("text"), str):
        return message_obj["text"]
    content = message_obj.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, dict):
                t = part.get("type")
                if t == "text" and isinstance(part.get("text"), str):
                    out.append(part["text"])
                elif "text" in part and isinstance(part["text"], str):
                    out.append(part["text"])
        return "".join(out)
    return ""


# ---------------------------------------------------------------------------
# MCP 服务器
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "openclaw-agent",
    instructions=(
        "Bridge to the OpenClaw autonomous agent running on a remote HPC server. "
        "Use ask_openclaw_tool to delegate complex, multi-step, or compute-intensive "
        "tasks. OpenClaw can autonomously plan and execute. The bridge talks to the "
        "gateway over a persistent WebSocket session; consecutive calls reuse the same "
        "OpenClaw session unless you pass session_key=None to start a fresh one."
    ),
)


@mcp.tool()
def ask_openclaw_tool(
    message: str,
    session_key: str | None = None,
    timeout_seconds: int = DEFAULT_CHAT_TIMEOUT_S,
) -> dict[str, Any]:
    """Send a task to the remote OpenClaw agent and return its final reply.

    Args:
        message: Natural-language task for OpenClaw.
        session_key: Optional existing session key (returned in a previous call).
            Pass None to create a fresh session.
        timeout_seconds: Hard cap (default 180s) for waiting on the streamed final reply.

    Returns:
        dict with keys: status, response, session_key, session_created, run_id,
        events_seen, agent, gateway. status is "success" or "timeout".
    """
    try:
        return _bg.run(_ask_async(message, session_key=session_key, timeout=float(timeout_seconds)))
    except Exception as e:
        return {
            "status": "error",
            "error": f"{type(e).__name__}: {e}",
        }


@mcp.tool()
def list_openclaw_sessions_tool() -> dict[str, Any]:
    """List existing OpenClaw sessions on the remote agent."""
    async def _do():
        client = _get_client()
        await client.ensure_connected()
        payload = await client.call("sessions.list", {})
        # Trim to lightweight summary
        slim = []
        for s in payload.get("sessions", []) or []:
            slim.append({
                "key": s.get("key"),
                "kind": s.get("kind"),
                "displayName": s.get("displayName"),
                "status": s.get("status"),
                "updatedAt": s.get("updatedAt"),
                "model": s.get("model"),
                "modelProvider": s.get("modelProvider"),
            })
        return {
            "status": "success",
            "count": payload.get("count"),
            "defaults": payload.get("defaults"),
            "sessions": slim,
        }
    try:
        return _bg.run(_do())
    except Exception as e:
        return {"status": "error", "error": f"{type(e).__name__}: {e}"}


@mcp.tool()
def openclaw_identity_tool() -> dict[str, Any]:
    """Return the active OpenClaw agent's identity ({agentId, name, avatar})."""
    async def _do():
        client = _get_client()
        await client.ensure_connected()
        payload = await client.call("agent.identity.get", {})
        return {"status": "success", "agent": payload, "gateway": GATEWAY_URL}
    try:
        return _bg.run(_do())
    except Exception as e:
        return {"status": "error", "error": f"{type(e).__name__}: {e}"}


@mcp.tool()
def openclaw_status_tool() -> dict[str, Any]:
    """Report this bridge's WebSocket connection state to the OpenClaw gateway.

    IMPORTANT: This is the *bridge*'s state, not the *agent*'s health.
    The bridge uses lazy-connect: it only opens the WebSocket when a tool that
    actually needs the agent is called (ask_openclaw_tool, openclaw_identity_tool,
    list_openclaw_sessions_tool). So `status: disconnected` immediately after
    process start is normal — it does NOT mean the gateway or agent is down.

    To check whether the agent is reachable, call openclaw_identity_tool instead.
    """
    client = _get_client()
    is_open = bool(client.ws and client.ws.state.name == "OPEN")
    return {
        "status": "running" if is_open else "disconnected",
        "alive": is_open,
        "note": (
            "lazy-connect: 'disconnected' just means the bridge hasn't opened the WS "
            "yet. Call openclaw_identity_tool or ask_openclaw_tool to verify the agent."
        ),
        "gateway": GATEWAY_URL,
        "connection_id": client.connection_id,
        "scopes": ((client.hello or {}).get("auth") or {}).get("scopes"),
    }


@mcp.tool()
def reset_openclaw_tool() -> dict[str, Any]:
    """Tear down the current gateway connection; the next call will reconnect."""
    async def _do():
        global _client
        if _client is not None:
            await _client._teardown()
            _client = None
        return {"status": "success", "message": "OpenClaw connection reset."}
    try:
        return _bg.run(_do())
    except Exception as e:
        return {"status": "error", "error": f"{type(e).__name__}: {e}"}


if __name__ == "__main__":
    mcp.run()
