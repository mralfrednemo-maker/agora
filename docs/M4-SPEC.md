# Agora M4 — Admin Agent, Voice I/O, Telegram, WhatsApp

**Version:** 0.1
**Author:** Newton
**Date:** 2026-04-20
**Status:** APPROVED by Christo — in implementation

M4 delivers the "chief of staff" layer: a persistent admin LLM that Christo talks to (voice + text), with tools for creating debates, sending/receiving Telegram and WhatsApp messages, and reporting back.

---

## 1. Admin Agent ("Ops")

### 1.1 What it is

A persistent Claude Code session (one per gateway instance) living in a dedicated **Ops Room**. Only Christo + the admin speak here. The admin has access to structured tools — it can create debates, query them, send messages on Telegram/WhatsApp, read inboxes, and relay to Christo.

### 1.2 Ops Room

- Special room type: `kind = "ops"`, fixed id `ops` (not UUID — singleton).
- Participants: one — `admin-1` (a `ClaudeCodeNewDriver` instance dedicated to ops).
- Style: N/A (no debate phases). Ops is a free-form conversation room.
- Transcript persists to `data/rooms/ops/transcript.jsonl` like any other room.
- Cannot be deleted or archived.

### 1.3 Conversation flow

- User types or speaks → goes into the admin's session as a user message.
- Admin replies → streamed back to dashboard → TTS'd if voice mode active.
- Admin may embed tool-use blocks in its reply (see §1.4). Gateway parses, dispatches, feeds results back as follow-up user messages.

### 1.4 Tool-use protocol

Claude Code (no API) doesn't support structured tool use natively. Admin uses a tagged-block convention the gateway parses:

```
<tool name="tool_name">
{"arg1": "value1", "arg2": "value2"}
</tool>
```

- Admin's system prompt lists available tools and their schemas.
- Gateway scans each admin reply for `<tool>` blocks, extracts JSON, validates, executes.
- Result posted back into admin's session as:
  ```
  <tool-result name="tool_name">
  {"ok": true, "data": {...}}
  </tool-result>
  ```
- Admin may chain tools across turns.

### 1.5 Available tools (M4)

| Tool | Args | Returns |
|---|---|---|
| `create_debate` | `{topic, style, participants, max_total_rounds}` | `{room_id}` |
| `list_debates` | `{}` | `[{id, topic, status, phase, round}, ...]` |
| `get_debate` | `{room_id}` | full room state + transcript |
| `pause_debate` | `{room_id}` | `{ok}` |
| `stop_debate` | `{room_id}` | `{ok}` |
| `inject_debate` | `{room_id, text}` | `{ok}` |
| `tg_send` | `{text}` | `{ok, message_id}` |
| `tg_list_recent` | `{limit}` | `[{ts, from, text}, ...]` |
| `wa_send` | `{contact, text}` | `{ok, message_id}` |
| `wa_list_contacts` | `{}` | `[{id, name, phone}, ...]` |
| `wa_list_recent` | `{limit}` | `[{ts, from, contact, text}, ...]` |
| `now` | `{}` | `{iso}` — current time |

### 1.6 Admin system prompt (frame)

Sent once via `start_session`:

```
You are "Ops", Christo's chief of staff. You help him run debates, send messages,
and stay on top of incoming communications.

You have tools. Invoke them by emitting blocks in this exact format:

<tool name="tool_name">
{json args}
</tool>

A tool result will come back in the next user turn as <tool-result> blocks.

Do not invent tool names; only use ones listed here: ...
When Christo asks something you can answer without tools, answer directly.
Be concise. Use bullets when listing. No filler.
```

---

## 2. Voice I/O

### 2.1 Mic capture (browser)

- **Push-to-talk** button in Ops panel. Holding `Space` (or tap-and-hold button) records.
- Uses `MediaRecorder` API — captures Opus in WebM container.
- On release, posts blob to `POST /api/ops/transcribe` (multipart/form-data).

### 2.2 STT (server)

- Endpoint `/api/ops/transcribe` forwards the audio blob to OpenAI Whisper API.
- Returns `{text}`. Dashboard renders the text into the ops composer and auto-submits.

### 2.3 TTS (server)

- Endpoint `/api/ops/tts?text=...` returns audio/mpeg from OpenAI's `tts-1` model (or `gpt-4o-mini-tts` when available), voice `alloy` by default.
- Streamed to the browser via `<audio>` element.
- Configurable: toggle "voice replies" on/off in ops panel. When on, every admin reply auto-plays.

### 2.4 Configuration

- `OPENAI_API_KEY` in `.env` (mandatory for voice).
- `OPENAI_TTS_VOICE` in `.env` (default `alloy`).
- `OPENAI_STT_MODEL` in `.env` (default `whisper-1`).

---

## 3. Telegram (reuse existing bridge)

### 3.1 Extend the existing bridge

Add to `C:\Users\chris\PROJECTS\telegram-bridge\src\`:

- **New HTTP adapter** listening on `127.0.0.1:9788`:
  - `POST /agora/send` — body `{text}`. Sends `text` from the bot to `ALLOWED_CHAT_ID`. Returns `{ok, message_id}`.
  - `GET /agora/recent?limit=N` — returns the last N inbound messages from `ALLOWED_CHAT_ID`.
  - `POST /agora/webhook` — Agora registers a callback URL; bridge POSTs each new inbound message there. Non-destructive of existing Claude Code handling.

- **Message log:** bridge stores every inbound + outbound message in `store/agora-messages.jsonl` (new file; does not touch existing session store).

### 3.2 Agora side

- `TelegramClient` in `src/agora/integrations/telegram.py` — thin wrapper around the bridge's HTTP endpoints.
- Tool dispatcher calls it for `tg_send`, `tg_list_recent`.
- Agora exposes `POST /api/ops/telegram/incoming` endpoint that the bridge webhook posts to. Incoming messages are pushed into the ops room as a system message: `[Telegram from Christo]: <text>`. Admin sees it on its next turn.

### 3.3 Security

- Bridge's HTTP adapter binds `127.0.0.1` only. No external reach.
- No auth beyond localhost — any process on the machine could call it. Acceptable for M4.

---

## 4. WhatsApp

### 4.1 New sidecar

Location: `C:\Users\chris\PROJECTS\whatsapp-bridge\` (separate Node project — isolated dependencies).

- **Library:** `@whiskeysockets/baileys` (the well-maintained fork).
- **First run:** CLI prints a QR code; Christo scans with WhatsApp → Linked Devices. Session credentials stored in `whatsapp-bridge/auth-state/`.
- **Subsequent runs:** restores session automatically. If session expired, re-prints QR.
- **HTTP adapter** listening on `127.0.0.1:9789`:
  - `POST /send` — body `{contact, text}`. `contact` is either a phone number (`+44...`) or a stored contact id.
  - `GET /contacts` — returns `[{id, name, phone}, ...]`.
  - `GET /recent?limit=N` — last N messages from any chat.
  - `POST /webhook` — Agora registers, bridge forwards inbound messages.
- **Message log:** `whatsapp-bridge/store/messages.jsonl`.

### 4.2 Agora side

- `WhatsAppClient` in `src/agora/integrations/whatsapp.py` — same pattern as Telegram.
- Same webhook → ops room relay as Telegram: `[WhatsApp from Alex]: <text>`.

### 4.3 What stays out of M4

- **Outbound voice calls** — Baileys' call support is read-only (receive only). Deferred.
- **Group chats** — send to individual contacts only in M4. Group support later.
- **Media messages (images, voice notes)** — text only in M4. Admin can describe a photo by asking Christo to describe it.

---

## 5. Dashboard — Ops Panel

### 5.1 Surface

New top-level tab `Ops` next to `Debates`. Activated by default if the admin is waiting for a reply.

```
+---------------------------------------------+
| [Debates]  [Ops]                            |
+---------------------------------------------+
| Ops — Christo's Agent                       |
|                                             |
| [Admin]: Hi Christo, two new Telegram       |
|          messages from Alex about the PR.   |
|          Want me to respond?                |
|                                             |
| [You]: Yes, tell him I'll review tonight.   |
|                                             |
| [Admin]: Sent.                              |
|                                             |
| [Admin]: <tool name="tg_send">...</tool>    |
|          → tool-result: {ok: true}          |
|                                             |
+---------------------------------------------+
| [🎤 Hold to talk]  [ Type a message… ] [▶]  |
| [☑ voice replies]                           |
+---------------------------------------------+
```

### 5.2 Tool-use rendering

- When a `<tool>` block appears in an admin reply, render it as a collapsible card: `[tool: tg_send]` clickable to see args + result.
- Regular prose rendered as bubbles.

### 5.3 Voice indicator

- While mic is recording: red dot + "Listening…".
- While waiting for admin reply: spinner.
- When reply arriving: bubble fades in + audio plays if voice-replies on.

---

## 6. Environment

New `C:\Users\chris\PROJECTS\agora\.env`:

```
OPENAI_API_KEY=sk-...
OPENAI_TTS_VOICE=alloy
OPENAI_STT_MODEL=whisper-1
TELEGRAM_BRIDGE_URL=http://127.0.0.1:9788
WHATSAPP_BRIDGE_URL=http://127.0.0.1:9789
ADMIN_AGENT_DRIVER=claude-code-new
```

---

## 7. Engine changes

1. **Singleton Ops Room** — on gateway startup, ensure `data/rooms/ops/` exists with `room.json` containing `{id: "ops", kind: "ops", status: "idle", participants: ["admin-1"]}`. Never expose this in the normal rooms list (`/api/rooms` filters it out). Dedicated endpoint `GET /api/ops`.
2. **New driver instance** `admin-1` — a `ClaudeCodeNewDriver` in the drivers registry, separate from `claude-code-new-1`. Different CWD so its session state is isolated.
3. **`send_to_ops(user_text)`** helper — appends to ops transcript, calls `admin-1.send_in_session("ops", user_text)`, parses tool blocks in reply, dispatches, appends tool-result as the next user message, loops until reply has no more tool blocks (or hits a sanity cap, default 8 tool calls per turn).
4. **Tool dispatcher** `src/agora/ops/tools.py` — registry of tool implementations. Each is an async callable taking a dict and returning a dict.

---

## 8. HTTP API additions

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/ops` | Ops room state + transcript |
| POST | `/api/ops/message` | Body `{text}` — user sends a message to admin |
| POST | `/api/ops/transcribe` | multipart audio → `{text}` |
| GET | `/api/ops/tts` | query `?text=` → audio/mpeg |
| POST | `/api/ops/telegram/incoming` | webhook from telegram-bridge |
| POST | `/api/ops/whatsapp/incoming` | webhook from whatsapp-bridge |
| WS events | `ops.message` | new ops message (same shape as room message.append) |

---

## 9. Exit criteria

1. `pytest` all green (new tests for tool dispatcher, tag parser).
2. Gateway starts; Ops room auto-created; dashboard `Ops` tab present.
3. Type "hi" to admin → reply within 5s.
4. Hold mic button, say "list my debates", release → STT transcribes, admin invokes `list_debates`, replies with the list, voice-reads it back.
5. Say "send Christo a Telegram message: test from ops" → admin invokes `tg_send` → your phone receives it.
6. Reply on Telegram → message lands in ops room as `[Telegram from Christo]: <text>`.
7. Same flow with WhatsApp: pair once via QR, then `wa_send` to a contact, inbound relay working.

---

## 10. Out of scope for M4

- WhatsApp outbound calls (not supported by Baileys).
- WhatsApp groups.
- WhatsApp media (photos, voice notes).
- Multi-user ops (one Christo, one admin — singleton).
- Admin ability to create/modify files (no Bash tool access — too dangerous without a sandbox layer).
- Phone call dialing from the admin (out of spec).
