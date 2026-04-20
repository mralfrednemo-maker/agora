# Agora — Architecture

**Version:** 0.1 (draft)
**Author:** Newton
**Date:** 2026-04-20
**Status:** DRAFT — pending Christo sign-off

---

## 1. Purpose

Agora is a local gateway and dashboard that lets two or more LLMs hold a structured, round-based conversation on a topic and reach agreement or explicit disagreement. The human (Christo) is the only moderator. The system spends tokens on actual discussion between participants and zero tokens on moderation.

## 2. Non-Goals

The following are explicitly **out of scope** for this architecture. They may be built as separate layers later, but they must not leak into the core design.

- **Voice I/O** (microphone, STT, TTS).
- **WhatsApp / Telegram integrations.**
- **An "admin agent" that auto-moderates rooms.** Christo moderates.
- **Automatic turn selection by an LLM.** Turn order is deterministic.
- **Side channels / private sidebars between participants.**
- **Cross-room supervision.** Each room is isolated.
- **Attaching to a live, actively-prompted Claude Code session.** Only `--resume` based attach is supported.
- **Outbound calls, notifications, or any network egress other than LLM providers.**

## 3. Glossary

| Term | Meaning |
|---|---|
| **Gateway** | The single Python process that runs the engine, drivers, HTTP/WebSocket API, and serves the dashboard. |
| **Participant** | A logical entity that can answer a prompt. May be a CLI subprocess, a browser tab, a Docker container, etc. |
| **Driver** | The adapter code for one participant type. Implements `send(prompt) -> reply`. |
| **Room** | A stateful debate instance: participants + transcript + phase + round counter. |
| **Round** | One full cycle where each participant speaks exactly once, in fixed order. |
| **Phase** | A labelled category of rounds with a specific instruction template (e.g. Opening, Challenge, Verdict). |
| **Transcript** | Append-only ordered list of messages in a room. Persisted as JSONL. |
| **Prompt template** | Deterministic string template rendered per turn. Contains brief, role, prior contributions, phase instruction. |
| **Convergence check** | Deterministic function over the last round's replies. Returns `converged: bool`. |
| **Command** | A text directive typed by Christo in the dashboard. Parsed by regex. |

## 4. System Diagram

```
+------------------------------------------------------------+
|                        BROWSER (localhost)                 |
|   +----------------------------------------------------+   |
|   |                Dashboard (HTML + JS)               |   |
|   |   Rooms list | Transcript view | Command input     |   |
|   +---------------------+------------------------------+   |
+-------------------------|----------------------------------+
                          | WebSocket + HTTP
+-------------------------v----------------------------------+
|                    GATEWAY (Python, FastAPI)               |
|                                                            |
|   +------------------+   +-------------------------+       |
|   |   HTTP / WS API  |-->|   Command parser (code) |       |
|   +------------------+   +-------------------------+       |
|            |                          |                    |
|            v                          v                    |
|   +------------------+   +-------------------------+       |
|   |   Room Engine    |<->|   Transcript store       |      |
|   |   (state machine)|   |   (JSONL per room)       |      |
|   +--------+---------+   +-------------------------+       |
|            |                                               |
|            v                                               |
|   +------------------+                                     |
|   |  Driver Registry |                                     |
|   +--------+---------+                                     |
|            |                                               |
|   +--------+----------+----------+----------+-----+        |
|   v        v          v          v          v     v        |
| [CC-new] [CC-resume] [Codex] [Gemini-CLI] [CDP] [OC]       |
+------------------------------------------------------------+
          |         |         |          |        |    |
     subprocess  subprocess  subprocess  subprocess CDP docker
                                                   :9222 exec
```

## 5. Driver Contract

Every driver implements this interface:

```python
class Driver(Protocol):
    id: str                 # unique per participant instance
    kind: str               # "claude-code-new" | "claude-code-resume" | "codex"
                            # | "gemini-cli" | "chatgpt-web" | "claude-web"
                            # | "gemini-web" | "openclaw"
    display_name: str

    async def start(self) -> None: ...
    async def send(self, prompt: str) -> str: ...
    async def stop(self) -> None: ...
    async def health(self) -> bool: ...
```

Rules:

1. `send()` is request-response. It blocks until a full reply is available.
2. `send()` must be reentrant-safe only at the room-engine level (engine guarantees one in-flight `send` per driver at a time).
3. Drivers may maintain internal conversation state (e.g. a resumable session ID). They should NOT assume the engine sends the full transcript — the engine decides what context each prompt includes.
4. `start()` and `stop()` are idempotent.
5. If `send()` fails, the driver raises. The engine decides retry policy.
6. Drivers never log to stdout; they emit events on a per-driver logger.

## 6. Driver Catalog

### 6.1 Claude Code (new session) — `claude-code-new`

- **Mechanism:** spawn `claude` CLI as subprocess. Stream prompt via stdin. Parse reply via Claude Code SDK's `query()` if using Python bindings, or stdout protocol if raw.
- **Preferred path:** Python `claude-code-sdk` if installed; otherwise wrap `claude --print --output-format stream-json`.
- **Pitfalls:** first-run boot cost (~1-2s); working-directory must be set per-session; avoid concurrent sessions sharing the same cwd JSONL.
- **Status:** existing pattern (Telegram bridge).

### 6.2 Claude Code (resume existing) — `claude-code-resume`

- **Mechanism:** spawn with `claude --resume <session-id>`. Session JSONL is the shared state with the original terminal.
- **Pitfalls:** if the original terminal is actively prompted at the same moment, both processes append to the same JSONL → race. Driver must serialize access via a file lock (`.lock` sibling file) and refuse to send if the file has been modified since last read.
- **Status:** new for M2. Not in M1.

### 6.3 Codex CLI — `codex`

- **Mechanism:** shell out via existing `codex-companion.mjs task`. Pass `--resume <id>` or `--fresh`.
- **Pitfalls:** storage rule preamble must be included in every prompt (see `TOOL-PROMPTING-GUIDE.md`). Codex writes files by default — drivers must pass `--read-only` unless the room explicitly authorises writes.
- **Status:** existing via `cx` shortcut.

### 6.4 Gemini CLI — `gemini-cli`

- **Mechanism:** spawn `gemini` subprocess. New session per spawn (or `--resume` if supported).
- **Pitfalls:** model resets to Flash on new session — set model explicitly. Rate limits per account.
- **Status:** new driver for M1 (small wrapper).

### 6.5 ChatGPT Web — `chatgpt-web` (CDP)

- **Mechanism:** attach to existing Chrome via Chrome DevTools Protocol on port 9222. Drive a logged-in chatgpt.com tab. Type into composer, click send, poll DOM for complete reply, scrape text.
- **Pitfalls:** Cloudflare challenges, DOM selector drift, long-response streaming detection. Existing `test_chatgpt_upload.py` / ein-selenium already solves these.
- **Status:** existing code to be wrapped as driver for M3.

### 6.6 Claude Web — `claude-web` (CDP)

- Same as 6.5 against claude.ai. `chrome-automation-profile-3` required for uc mode. Status: M3.

### 6.7 Gemini Web — `gemini-web` (CDP)

- Same pattern against gemini.google.com. Status: M3.

### 6.8 OpenClaw agent — `openclaw`

- **Mechanism:** `wsl docker exec openclaw-stack-openclaw-gateway-1 node openclaw.mjs agent --agent <id> --message "<msg>" --json --timeout 300`. Maintain session continuity via `--session-id`.
- **Pitfalls:** never CLI-ping `--agent main` (poisons Alfred's Telegram session — see feedback memory). Use specific agents or `--session-id <uuid>`.
- **Status:** existing, wrap as driver for M2.

## 7. Room Engine

### 7.1 State

```python
@dataclass
class Room:
    id: str                          # UUID
    topic: str                       # the brief
    participants: list[Driver]       # ordered, round-robin
    phase_sequence: list[Phase]      # the debate structure
    current_phase_index: int
    current_round: int               # within the phase
    max_rounds_per_phase: int
    max_total_rounds: int
    transcript: Transcript
    status: Literal["idle", "running", "paused", "done"]
    convergence: ConvergenceCheck
    injected_instruction: str | None # from /inject, consumed next turn
    created_at: datetime
    updated_at: datetime
```

### 7.2 Main loop (pseudocode)

```
while room.status == "running":
    phase = room.phase_sequence[room.current_phase_index]

    for participant in room.participants:
        if room.status != "running":
            break
        prompt = render_prompt(room, phase, participant)
        try:
            reply = await participant.send(prompt)
        except DriverError as e:
            transcript.append_error(participant.id, e)
            continue
        transcript.append(participant.id, reply)
        broadcast_to_dashboard(reply)

    room.current_round += 1
    room.injected_instruction = None   # one-shot

    if convergence.check(transcript, last_round=True):
        advance_phase_or_finish(room)
        continue

    if room.current_round >= room.max_rounds_per_phase:
        advance_phase_or_finish(room)
        continue

    if room.total_rounds() >= room.max_total_rounds:
        room.status = "done"
        break
```

`advance_phase_or_finish`: if there are more phases, move to next; otherwise mark done.

### 7.3 Guarantees

- One `send()` in flight per driver at a time (serialised by participant order).
- Transcript append is atomic (write + fsync) before broadcasting.
- Engine can be paused between participants, never mid-`send`.
- All state derivable from transcript + room config → resumable after crash.

## 8. Prompt Template

Rendered deterministically in code. No LLM involvement.

```
[BRIEF]
{topic}

[ROOM RULES]
- You are a participant in a debate. Other participants are listed below.
- This is round {round_number} of the {phase_name} phase.
- Do not ask clarifying questions. Respond to the brief with your best reasoning.
- Stay within {max_words} words unless the phase template says otherwise.

[PARTICIPANTS]
{for each participant: name + kind}

[YOUR IDENTITY]
You are {self.display_name} ({self.kind}).
{optional persona line from room config}

[PRIOR CONTRIBUTIONS — THIS ROUND]
{for each participant who has already spoken this round:
 --- {name} ---
 {their reply}
}

[YOUR LAST CONTRIBUTION — PREVIOUS ROUND]
{self.last_reply or "(none yet)"}

[OPPONENTS' LAST CONTRIBUTIONS — PREVIOUS ROUND]
{for each other participant:
 --- {name} ---
 {their last reply or "(none yet)"}
}

[PHASE INSTRUCTION]
{injected_instruction or phase.instruction_template}

Respond now.
```

The template is a single Jinja2 file. Any phase can override any block by providing its own template name.

## 9. Default Phase Sequence (M1)

Hardcoded. Matches ein-mdp.

| # | Name | Instruction template |
|---|---|---|
| 1 | Opening | "State your opening position on the brief. Max 300 words. No hedging, no disclaimers, no requests for clarification." |
| 2 | Challenge | "For each opponent, identify the single strongest flaw in their opening. One specific objection per opponent. Max 150 words per objection." |
| 3 | Cross-exam | "Defend your position against the objections raised against you. Concede points where opponents are right. Max 400 words." |
| 4 | Revision | "Revise your position if you have been moved. State residual disagreements explicitly. Max 400 words." |
| 5 | Verdict | "State: (a) what you now agree with, naming each participant; (b) what you still disagree on and why. End your message with a final line reading exactly `AGREE` or `DISAGREE: <one-line reason>`." |

`max_rounds_per_phase = 1` for phases 1, 2, 5. Phases 3 and 4 may run multiple rounds if convergence not met.

## 10. User Commands

Parsed from dashboard input by regex.

| Command | Grammar | Effect |
|---|---|---|
| `/new` | `/new <topic>` | Create new room with topic. Next message selects participants. |
| `/participants` | `/participants <id1> <id2> ...` | Attach named drivers to current room. |
| `/rounds` | `/rounds <N>` or `/rounds +<N>` | Set or extend max total rounds. |
| `/start` | `/start` | Begin execution. |
| `/pause` | `/pause` | Halt after current participant finishes current `send`. |
| `/resume` | `/resume` | Continue from pause. |
| `/stop` | `/stop` | Terminate room. Transcript preserved. |
| `/inject` | `/inject <text>` | Prepend text to next round's phase instruction. One-shot. |
| `/to` | `/to <participant_id> <text>` | Queue an addressed note. Other participants see the note but only the addressed one responds next turn. |
| `/phase` | `/phase <name>` | Skip to named phase. |
| `/synthesize` | `/synthesize [model]` | One LLM call to a cheap model producing a final summary. Default: haiku. |
| `/list` | `/list` | List rooms. |
| `/attach` | `/attach <room_id>` | Make that room the active view. |
| `/drivers` | `/drivers` | List available driver kinds. |

All commands are handled in Python code. None invoke an LLM.

## 11. Convergence Checks

Built-in, deterministic. Selected per room.

| Name | Logic |
|---|---|
| `agree-marker` | Every reply in last round ends with line `AGREE`. |
| `consensus-prefix` | Every reply in last round starts with `CONSENSUS:`. |
| `disagree-absent` | No reply in last round contains the token `DISAGREE`. |
| `none` | Never converges; stops only on `max_total_rounds` or `/stop`. |

Custom checks can be added as pure functions `(transcript) -> bool`. No LLM.

## 12. Context Budget Management

Per-participant context ceiling declared in driver config (e.g. 180k for Claude Code, 128k for ChatGPT Web, 900k for Gemini CLI). Before each `send`:

1. Render prompt fully.
2. Count tokens (tiktoken for OpenAI-style, approximate for others — hard cap = ceiling × 0.8).
3. If over: truncate oldest rounds from transcript, keeping:
   - Round 1 opening of every participant (always).
   - Last 2 completed rounds of every participant.
4. If still over: call summariser LLM **once** (configurable model, default `gpt-4o-mini` or `claude-haiku-4-5`) to compress rounds 2 through N-3 into one paragraph per participant.
5. Cache summary on the room; reuse until a new round overflows again.

One summariser call per overflow event. Not per turn.

## 13. Persistence

Per-room directory:

```
agora/data/rooms/<room_id>/
  room.json          # Room config + current state
  transcript.jsonl   # One line per message
  summary.json       # Cached context summary (if any)
  events.log         # Engine events (starts, pauses, errors)
```

`transcript.jsonl` line schema:

```json
{
  "seq": 42,
  "ts": "2026-04-20T13:45:02.812Z",
  "phase": "challenge",
  "round": 2,
  "participant_id": "gemini-cli-1",
  "participant_kind": "gemini-cli",
  "role": "participant",
  "content": "...",
  "tokens_in": 4211,
  "tokens_out": 602,
  "latency_ms": 8412,
  "error": null
}
```

Event log line schema: `{ts, level, event, detail}`. Events include: `room_created`, `room_started`, `phase_changed`, `paused`, `resumed`, `converged`, `stopped`, `driver_error`.

Room is resumable: on startup the gateway scans `data/rooms/`, rehydrates any room with status `paused` or `running` (treat `running` as `paused` on crash recovery).

## 14. Dashboard Contract

### 14.1 Surface (M1)

- Left pane: rooms list (id, topic, status, current phase/round).
- Center pane: transcript of active room, auto-scrolling, newest at bottom.
- Bottom pane: command input.
- Right pane (optional): participant status chips (idle / thinking / replied / error), last-turn latency.

No SPA framework. Single `index.html`, vanilla JS, WebSocket to `/ws`.

### 14.2 Messages (dashboard ↔ gateway)

Server → client (WebSocket):

```json
{"type": "room.update", "room_id": "...", "state": {...}}
{"type": "message.append", "room_id": "...", "message": {...}}
{"type": "participant.status", "room_id": "...", "participant_id": "...", "status": "..."}
{"type": "error", "detail": "..."}
```

Client → server (WebSocket or POST):

```json
{"type": "command", "room_id": "...", "text": "/start"}
```

HTTP endpoints:

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/rooms` | List rooms. |
| GET | `/api/rooms/{id}` | Get room state + transcript. |
| POST | `/api/rooms` | Create room. |
| POST | `/api/rooms/{id}/command` | Submit command. |
| GET | `/api/drivers` | List driver kinds + configured instances. |
| WS | `/ws` | Live updates. |

### 14.3 No authentication

Localhost bind only (`127.0.0.1`). Accept no external connections.

## 15. Out of Scope for M1 (explicit)

- Voice: mic capture, Whisper, TTS.
- Messaging: WhatsApp, Telegram, SMS, calls.
- Admin agent as voice front-end.
- Claude Code resume-attach driver.
- Browser (CDP) drivers: ChatGPT Web, Claude Web, Gemini Web.
- OpenClaw driver.
- Custom phase sequences from YAML.
- Side channels / sidebars.
- LLM-picked turn order.
- Authentication / multi-user.

These appear in later milestones (see `MILESTONES.md`).

## 16. Open Questions

1. **Gemini CLI session persistence.** Does `gemini` CLI support `--resume`? If not, each turn is a fresh process and the full transcript must be passed every time. Verify before M1.
2. **Claude Code SDK in Python.** Latest `claude-code-sdk` version and whether it exposes streaming JSON reliably on Windows. Verify before M1.
3. **Summariser default.** `haiku-4-5` (via Claude Code subscription, free) vs `gpt-4o-mini` (paid, small). Defer decision to first overflow event.
4. **Convergence on non-agreement.** Should `/stop on disagree` be a first-class convergence mode, or handled by `/rounds` cap? Default: handled by cap.
5. **Transcript length in prompt — single long string vs structured JSON?** Current spec is Markdown-ish. Consider XML-tagged for stricter parsing by participants.

Answers to these do not block doc sign-off; they are implementation details resolved in M1.

## 17. Change Log

| Date | Author | Change |
|---|---|---|
| 2026-04-20 | Newton | Initial draft (v0.1). |
