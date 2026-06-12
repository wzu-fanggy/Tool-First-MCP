# OpenClaw Gateway WebSocket Protocol — Reverse-Engineering Notes

Reverse-engineered from the gateway shipped with `openclaw@2026.4.22`,
verified against a live gateway running on `nodec2:127.0.0.1:18789`.

## Endpoint

- WS URL: `ws://127.0.0.1:18789/api/v1/rpc`
- Sibling HTTP control: `http://127.0.0.1:18791` (GET `/` returns gateway status JSON, requires Bearer token)
- Health probe: `GET http://127.0.0.1:18789/healthz` → `{"ok":true,"status":"live"}`

## Frame envelopes

```jsonc
// Server → Client (push)
{"type":"event","event":"<name>","payload":{...}, "seq":<n>?, "ts":<ms>?}

// Client → Server (call)
{"type":"req", "id":"<uuid>", "method":"<dotted.name>", "params":{...}}

// Server → Client (call response)
{"type":"res", "id":"<uuid>", "ok":<bool>, "payload":{...}, "error":{"code":"...","message":"..."}?}
```

`id` MUST be a UUID-like string; numeric ids are rejected with `1008 invalid request frame`.

## Handshake

1. Server pushes immediately on connect:
   ```json
   {"type":"event","event":"connect.challenge","payload":{"nonce":"<uuid>","ts":<ms>}}
   ```
2. Client must respond with the very first request being `connect`:
   ```json
   {
     "type":"req",
     "id":"<uuid>",
     "method":"connect",
     "params":{
       "minProtocol":3,
       "maxProtocol":3,
       "client":{
         "id":"gateway-client",
         "version":"0.1.0",
         "platform":"linux",
         "mode":"backend",
         "instanceId":"<uuid>"
       },
       "role":"operator",
       "scopes":[
         "operator.admin","operator.read","operator.write",
         "operator.approvals","operator.pairing","operator.talk.secrets"
       ],
       "caps":["tool-events"],
       "auth":{"token":"<shared-secret-from-openclaw.json:gateway.auth.token>"},
       "userAgent":"openclaw-mcp-bridge/0.1",
       "locale":"en-US"
     }
   }
   ```
3. Server replies with `hello-ok` payload containing `auth.role`, `auth.scopes`,
   `features.methods`, `features.events`, `snapshot.*`.

### Auth modes

The gateway supports several auth methods. We use **shared-secret token** mode:

- `gateway.auth.mode = "token"` in `~/.openclaw/openclaw.json`
- `gateway.auth.token` is a 48-char hex secret
- Client passes it on the wire as `params.auth.token`
- Token alone does NOT grant scopes

### Scope grant rules (the key gotcha)

The server clears any client-declared `scopes` whenever
`shouldClearUnboundScopesForMissingDeviceIdentity` is true. Under shared-secret
mode without a paired device that's almost always true — **except** when
`shouldSkipLocalBackendSelfPairing` returns true:

- `client.id == "gateway-client"` and `client.mode == "backend"`
- Connection is loopback (`isLoopbackAddress`)
- No proxy or browser-origin headers
- shared-secret auth is OK

When that exception applies, the server preserves the client-declared scopes,
producing `auth.scopes = [<everything you declared>]` in `hello-ok`.

This is the only known way to obtain operator scopes without holding the
device's private key (paired devices use HMAC-signed nonces; the private key
is not exported by the gateway).

## Useful methods (a small relevant subset)

| Method | Scope | Purpose |
|---|---|---|
| `agent.identity.get` | read | Returns `{agentId, name, avatar, emoji}` for the active agent |
| `sessions.list` | read | List all existing sessions (key, kind, status, etc.) |
| `sessions.create` | write | Create a new session, returns `{ok, key, sessionId, entry, runStarted}` |
| `chat.send` | write | Send a user message, kicks off an agent run |
| `chat.history` | read | Read history for a session |
| `chat.abort` | write | Abort running response |
| `sessions.delete` | admin | Remove a session |

### `sessions.create` params

`{}` works (uses defaults: agentId="main"). Returns:
```json
{"ok":true,"key":"agent:main:dashboard:<uuid>","sessionId":"<uuid>","entry":{"sessionFile":"...jsonl"},"runStarted":false}
```

### `chat.send` params

```json
{"sessionKey":"<from sessions.create>","message":"<plain text>","idempotencyKey":"<uuid>"}
```

Response is the *acknowledgement*, not the answer:
```json
{"ok":true,"payload":{"runId":"<uuid>","status":"started"}}
```

The actual reply streams in via events.

## Streaming response model

Multiple event types fire during a chat run, all carry `sessionKey` and `runId`:

- `agent` `stream:lifecycle phase:start|end` — run boundaries
- `agent` `stream:assistant data:{text, delta}` — incremental text
- `chat` `state:delta message:{role:"assistant", content:[{type:"text", text}]}` — accumulated message
- `chat` `state:final message:...` — terminal frame, this is the answer

Treat `chat state=final` as the canonical "done" signal.

## Verified against live gateway (2026-05-20)

- Connect with declared scopes: `auth.scopes` echoed in full → ✓
- `agent.identity.get` → `{"agentId":"main","name":"小金","avatar":"🐟"}` → ✓
- `sessions.create` → returned key + sessionId → ✓
- `chat.send "Reply with the single word: pong"` → streamed back `pong` and a `chat state=final` → ✓
