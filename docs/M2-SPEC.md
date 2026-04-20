# Agora M2 — Per-Room Sessions, Ein-MDP Phase Structure, UX Overhaul

**Version:** 0.1
**Author:** Newton
**Date:** 2026-04-20
**Status:** APPROVED by Christo — ready for implementation

M2 supersedes M1's phase sequence and driver contract. The engine, persistence, command grammar, and tests from M1 remain; the **phase model**, **driver session lifecycle**, **template**, and **dashboard** change.

---

## 1. Summary of changes

| Area | M1 | M2 |
|---|---|---|
| Driver sessions | Fresh subprocess per turn (Claude Code); per-driver session (Codex) | **Per-room session** on every driver |
| Phase 1 behaviour | Round-robin | **Parallel** — all participants reply independently |
| Phase 2 behaviour | Round-robin | **Parallel contrarian** — each attacks all opponents' openings |
| Phase 3+ | Round-robin | Round-robin debate (unchanged) |
| Final phase | Round-robin verdict | **Parallel verdict** — clean simultaneous AGREE/DISAGREE |
| Total rounds | Hardcoded 5 | **User-selectable**, minimum 4 |
| Template rule on ambiguity | "Do not ask clarifying questions" | **"State your working interpretation, then proceed"** |
| Dashboard bootstrap | Type `/new <topic>` + `/participants …` + `/start` | **"New Debate" modal** — topic + checkboxes + slider + one button |
| Gemini CLI | Unusable for debate | **Revived** via `--yolo --resume <uuid>` + debate-framing system prompt |

---

## 2. Per-room session lifecycle

### 2.1 Driver contract (extended)

```python
class Driver(Protocol):
    id: str
    kind: str
    display_name: str
    token_ceiling: int

    async def health_check(self) -> tuple[bool, str]: ...
    async def start_session(self, room_id: str, system_frame: str) -> str:
        """Create a new conversation session for this (driver, room) pair.
        Returns the session id. Sends the system_frame as the first message,
        captures and returns the driver's reply. Persists the session id.
        """
    async def send_in_session(self, room_id: str, user_message: str) -> DriverReply:
        """Send a message into an existing session, return the reply."""
    async def close_session(self, room_id: str) -> None:
        """Best-effort cleanup. Safe to call on a missing session."""
    async def has_session(self, room_id: str) -> bool: ...
```

The old `send(prompt)` method is deprecated. M2 drivers must implement the session trio.

### 2.2 Session id persistence

Per-driver-instance directory:
```
data/driver-state/<driver-id>/sessions/<room-id>.json
    { "session_id": "...", "created_at": "...", "driver_kind": "..." }
```

On gateway restart, drivers rehydrate these files into an in-memory map; if a file is missing or the session id is rejected by the CLI, the driver raises `DriverError("session expired")` and the engine can decide whether to recreate.

### 2.3 Driver-specific implementations

#### 2.3.1 `claude-code-new`
- **First message (start_session):** `claude --print --verbose --output-format stream-json` with `system_frame + "\n\n" + user_message` piped to stdin. Parse the stream-json init event for `session_id`. Persist.
- **Subsequent (send_in_session):** `claude --resume <session_id> --print --verbose --output-format stream-json`. Prompt caching across resumes is a key M2 cost reduction.
- **CWD:** `data/driver-state/claude-code-new-1/room-<room-id>/` — separate CWD per room avoids JSONL collisions.

#### 2.3.2 `codex`
- **First message:** `node codex-companion.mjs task --model gpt-5.4 --read-only --fresh "<system_frame + user_message>"`. Parse returned resume id. Persist per room.
- **Subsequent:** `--resume <id> --read-only` with the user_message only.

#### 2.3.3 `gemini-cli`
- **First message:** `gemini --yolo --model gemini-2.5-pro --prompt "<system_frame + user_message>"`. Parse Gemini CLI output / session log to extract the session UUID. Persist per room.
- **Subsequent:** `gemini --yolo --resume <uuid> --prompt "<user_message>"`.
- **System frame must explicitly forbid tool use:** "You are a debate participant. Do not read files, do not run shell commands, do not inspect the workspace. Answer the debate brief as a commentator. All arguments must be in plain text."
- If Gemini still emits tool-use text in any turn, log a warning and include the raw output in the transcript entry. Do not silently retry.

#### 2.3.4 `fake` (test driver)
- Maintains an in-process dict `{room_id → list of queued replies}`.
- `start_session` returns `"fake-session-<room_id>"`, consumes the first reply.
- `send_in_session` consumes the next.

---

## 3. Phase engine — parallel and serial phases

### 3.1 Phase definition (extended)

```python
@dataclass(frozen=True, slots=True)
class Phase:
    name: str                   # "positions", "contrarian", "debate", "verdict"
    mode: Literal["parallel", "serial"]
    instruction_template: str
    max_rounds: int             # for "debate" this is user-selectable
    include_opponents: bool     # whether to inject opponents' last replies
```

### 3.2 Engine loop (amended)

For each phase:

- **If `mode == "parallel"`:** render per-participant prompts, then
  `await asyncio.gather(*[p.send_in_session(room_id, prompt_i) for p in participants])`.
  All replies land, appended to transcript in participant order (by id) for determinism. Single broadcast per participant as each `gather` entry resolves.
- **If `mode == "serial"`:** existing round-robin. For each round, each participant in order: render, send, append, broadcast.

Convergence is checked **only after serial rounds** — parallel phases always produce exactly `max_rounds * len(participants)` entries.

### 3.3 Default phase sequence (new hardcoded)

```python
DEFAULT_PHASES_M2 = [
    Phase(
        name="positions",
        mode="parallel",
        max_rounds=1,
        include_opponents=False,
        instruction_template=(
            "State your opening position on the brief. "
            "If the brief is ambiguous, state your working interpretation at the top and proceed — "
            "do not refuse to take a position, do not stall. "
            "Max 400 words. No hedging."
        ),
    ),
    Phase(
        name="contrarian",
        mode="parallel",
        max_rounds=1,
        include_opponents=True,
        instruction_template=(
            "You have now seen all opponents' opening positions (below). "
            "For each opponent, state the single strongest objection you can raise against their opening. "
            "One specific objection per opponent, named. Max 150 words per objection."
        ),
    ),
    Phase(
        name="debate",
        mode="serial",
        max_rounds=1,  # set per-room by the user
        include_opponents=True,
        instruction_template=(
            "Respond to opponents' latest points. Defend your position where attacked. "
            "Concede where they are right. Identify residual disagreements explicitly. "
            "Max 400 words."
        ),
    ),
    Phase(
        name="verdict",
        mode="parallel",
        max_rounds=1,
        include_opponents=True,
        instruction_template=(
            "State: (a) what you now agree with, naming each participant; "
            "(b) what you still disagree on and why. "
            "End your message with a final line reading exactly `AGREE` or `DISAGREE: <one-line reason>`."
        ),
    ),
]
```

### 3.4 Round budget

**User input:** a single `max_total_rounds` integer, minimum **4**. Allocation:

```
total              = user-selected, min 4
positions rounds   = 1
contrarian rounds  = 1
verdict rounds     = 1
debate rounds      = max(1, total - 3)
```

Engine sets `DEFAULT_PHASES_M2[2].max_rounds = total - 3` for each room. Convergence can terminate the debate phase early; verdict phase always runs.

---

## 4. Template — per-session messages

### 4.1 First message (sent via `start_session`)

```
[AGORA ROOM FRAME]
You are a participant in a structured debate room called "Agora".
Other participants: {{ participants }}.
Your identity in this room: {{ self.display_name }} ({{ self.kind }}).

Rules:
- If the brief is ambiguous, state your working interpretation at the top and proceed.
- Do not ask the room for clarification — commit to your interpretation.
- Keep arguments concrete and falsifiable.
- Address opponents by their room id when replying to them.

[BRIEF]
{{ topic }}

[PHASE — {{ phase.name }}]
{{ phase.instruction_template }}

Respond now.
```

### 4.2 Subsequent messages (sent via `send_in_session`)

Participants' own session memory carries everything prior. Engine only injects deltas:

```
[PHASE — {{ phase.name }}, round {{ round_number }}]

{% if include_opponents %}
Opponents' latest contributions:
{% for p in opponents %}
--- {{ p.display_name }} ---
{{ p.last_content }}
{% endfor %}
{% endif %}

{{ phase.instruction_template }}

Respond now.
```

### 4.3 Rationale

Per-room session memory means each LLM's own cache holds the accumulated thread. The engine pushes only the new turn, saving both tokens on the re-render side and prompt-caching hits on the driver side.

---

## 5. Dashboard UX overhaul

### 5.1 "New Debate" modal (replaces `/new`)

Top of the page: a primary button **"+ New Debate"**. Opens a modal:

| Field | Control | Default |
|---|---|---|
| Topic | Textarea, 3 rows, required, max 1000 chars | (empty) |
| Participants | Checkbox list of available drivers (from `/api/drivers`). Each row shows id, kind, health dot | claude-code-new-1 + codex-1 pre-checked |
| Max total rounds | Slider 4–20 with live label: "Total rounds: N (positions 1 + contrarian 1 + debate N-3 + verdict 1)" | 6 |
| Convergence check | Dropdown: agree-marker / consensus-prefix / none | agree-marker |
| | | |
| **[Start Debate]** button | POSTs the compound request, activates room, closes modal | — |

Backend: add `POST /api/rooms/start` that takes `{topic, participants, max_total_rounds, convergence}` and executes create + participants + start atomically. `/new` command stays for power users.

### 5.2 Transcript header

Above the first bubble:

```
┌───────────────────────────────────────────────────────────────┐
│  Phase: Debate — Round 3 of 5                                 │
│  Next up: codex-1 (thinking…)                                 │
│  Participants: claude-code-new-1 · codex-1 · gemini-cli-1     │
│  [Pause] [Stop] [Inject…] [Archive]                           │
└───────────────────────────────────────────────────────────────┘
```

Controls:
- **Pause / Resume / Stop** — replace the equivalent `/pause`, `/resume`, `/stop` commands.
- **Inject** — opens a small inline text input; Enter sends `/inject <text>`.
- **Archive** — soft-hides the room from the main rooms pane (moves to collapsed "Archived" section). Uses a new `POST /api/rooms/{id}/archive` endpoint; sets a flag in `room.json`, does not delete.

### 5.3 Typing indicator

While a driver's `send_in_session` is in flight, broadcast a new WS event:
```json
{"type": "participant.thinking", "room_id": "...", "participant_id": "...", "in_flight": true}
```
Dashboard shows a pulsing "…" bubble for that participant. Event also fires on reply arrival (`in_flight: false`).

### 5.4 Rooms pane

- Sort newest-first by `updated_at` (already landed in M1.5 fix).
- Each room card:
  - Short id (8 chars)
  - Topic truncated to 2 lines
  - Phase + round line
  - Status dot: running (green pulse) / paused (yellow) / done (grey) / idle (blue)
- **Archived** section at the bottom, collapsed by default, click to expand.
- Delete icon on hover → confirmation → `DELETE /api/rooms/{id}` (hard delete; removes room directory).

### 5.5 Command input

Keep the command box for power users. Add a small help tooltip `?` next to it explaining that most actions are available via buttons in the transcript header.

---

## 6. Cleanup of M1 leftovers

- Delete the "test probe" and "Test topic about foo" rooms from `data/rooms/` as part of M2 provisioning (or archive them).
- Legacy `DEFAULT_PHASES` (M1 5-phase) is removed from `config/phases.py`; the M2 sequence replaces it. Tests that reference the M1 phase names are updated accordingly.

---

## 7. Exit criteria

1. `pytest` passes the M2 test suite end-to-end, including:
   - Parallel phase with fake driver completes in `O(max(latency_i))` not `O(sum(latency_i))`.
   - Session id persisted and survives gateway restart; second turn resumes cleanly.
   - User-selectable `max_total_rounds = 4` produces exactly 1 positions + 1 contrarian + 1 debate + 1 verdict.
   - Convergence on `AGREE` markers fires after the verdict phase and terminates the room as `done`.
2. Gateway starts cleanly on `127.0.0.1:8789`. Driver health endpoint reports all three drivers ok.
3. Christo clicks **"+ New Debate"**, fills the form, hits **Start**, and watches a full 4-round debate finish without any command typing. Three participants active (including Gemini CLI).
4. Pause + Inject + Resume from the transcript header all work without touching the command box.
5. Gemini CLI stays on-topic for the full debate — no "I'll read the README" drift.

---

## 8. Non-goals (unchanged from M1)

All M1 non-goals still apply. Specifically:
- No voice, no WhatsApp/Telegram, no admin agent, no authentication.
- No side channels, no LLM-picked turn order.
- Browser (CDP) drivers deferred to M3.
