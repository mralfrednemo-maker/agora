# Agora M3 — Critic+TERMINATE Style, Auto-Verdict, Post-Done Composer

**Version:** 0.1
**Author:** Newton
**Date:** 2026-04-20
**Status:** APPROVED by Christo — ready for implementation

M3 builds on M2. Engine, per-room sessions, driver contracts, and M1/M2 fixes stay. M3 adds: a second debate style, per-style round caps, automatic post-debate verdict document, and a "Follow-up" composer that keeps participant sessions usable after a debate ends.

---

## 1. Debate Styles

### 1.1 Style selector

A `style` field on every room: `"ein-mdp" | "critic-terminate"`. Default: `"ein-mdp"`.

Selected in the New Debate modal via a new dropdown **above** the "Convergence check" field. The convergence check default auto-selects based on style (`agree-marker` for Ein-MDP; `terminate-majority` for Critic+TERMINATE) but is user-overridable.

### 1.2 Ein-MDP (unchanged)

Existing M2 4-phase sequence. Round cap **5** (unchanged).

### 1.3 Critic + TERMINATE (new)

Based on AutoGen GroupChat with Critic role + explicit TERMINATE voting.

**Phase sequence:**

| # | Name | Mode | Instruction |
|---|---|---|---|
| 1 | `positions` | parallel | State your opening position on the brief. If the brief is ambiguous, state your working interpretation and proceed. 400 words max. End with `TERMINATE` if you believe no further debate is needed. |
| 2 | `critic` | serial (round-robin) | Your role this round: name the single strongest unresolved objection to each opponent's position. Be specific. 150 words per objection. End with `TERMINATE` if you are satisfied nothing material remains. |
| 3 | `debate` | serial (round-robin) | Respond to the latest Critic objection against you. Defend where attacked; concede where right. 300 words max. End with `TERMINATE` if no residual disagreement. |
| 4 | `synthesis` | parallel | Summarise the shared position you and the opponents now hold. Identify residual disagreements if any. 400 words max. End with `TERMINATE`. |

Phases 2 and 3 alternate if cap allows. Phase 3 may run N times (bounded by the round cap). Phase 4 always runs.

**Role assignment:** no explicit Critic role per participant — every participant is told to critique in phase 2. Simpler, no role-matrix UI burden. Future work: dedicated Critic role.

**Convergence check: `terminate-majority`** — after each completed round, count replies ending with the token `TERMINATE` (case-sensitive, last line only). When `ceil(N_participants / 2)` or more replies have `TERMINATE` in the same round, the debate terminates and moves to the synthesis phase.

**Round cap: 15.** Lifted from the 5-cap for this style only. Enforced in `config/phases.py`:

```python
STYLE_ROUND_CAPS = {
    "ein-mdp": 5,
    "critic-terminate": 15,
}
```

### 1.4 Dynamic modal slider

The slider's `max` attribute updates when the style dropdown changes. Default values per style:

- Ein-MDP: min 4, max 5, default 5
- Critic+TERMINATE: min 4, max 15, default 8

### 1.5 Stuck-debate warning

If round 8 of a Critic+TERMINATE debate completes with **zero** TERMINATE votes across all participants' replies in that round, broadcast a WS event:

```json
{"type": "debate.warning", "room_id": "...", "detail": "round 8/15 with no TERMINATE votes"}
```

Dashboard shows an amber banner above the transcript: "Debate has not seen any termination votes — consider /inject or /stop."

---

## 2. Auto-verdict

### 2.1 Trigger

When a room transitions to `status = done` (via convergence or round cap), the engine automatically fires one follow-up turn into the **selected** participant's existing session:

```
The debate has concluded. Produce a final verdict document in markdown. Include:
- The brief (restated verbatim).
- Shared conclusions (bulleted).
- Residual disagreements (bulleted, or "None.").
- Condensed reasoning trail (numbered list of turns that moved the debate).
Do not introduce new arguments. Synthesise what was said.
Max 600 words.
```

Reply saved to `C:\Users\chris\PROJECTS\agora\data\rooms\<room-id>\verdict.md`.

### 2.2 Selection rule — who writes?

Deterministic:

1. Among participants whose **last verdict line was exactly `AGREE` or `TERMINATE`**, pick the one with the longest verdict text (character count minus whitespace).
2. If none match, pick the participant with the most structured verdict (longest text overall).
3. Tiebreak by participant id (alphabetical).

### 2.3 Dashboard rendering

When `verdict.md` exists, render it as a **locked card pinned to the top of the transcript** with a header: **"Verdict by [participant-id] (one of N participants)"** to prevent false-neutrality implication.

### 2.4 Regenerate

Button **"Regenerate verdict"** in the transcript header → opens participant dropdown → on select, fires the same synthesis prompt into the chosen participant's session, overwrites `verdict.md`, re-broadcasts.

### 2.5 Opt-out

Room has `auto_verdict: bool` flag (default `true`). Set to `false` in the New Debate modal via a checkbox: **"Auto-generate verdict when debate ends"** (checked by default). When false, no auto-verdict is fired; the user can still click **Regenerate** manually.

---

## 3. Follow-up composer

### 3.1 Surface

When a room has `status = done`, the transcript header shows a new block below the other buttons:

```
[ Ask a participant ▼ claude-code-new-1 ] [ textarea                         ] [ Send ]
```

### 3.2 Behaviour

- The dropdown lists all participants in the room.
- On **Send**, the engine routes the message into the selected participant's existing session via `send_in_session`.
- Reply appears as a regular chat bubble at the bottom of the transcript, tagged **`[follow-up]`** in the meta line.
- Follow-up exchanges persist in `transcript.jsonl` with a new role: `"follow_up"` (distinct from `"participant"` so they don't confuse convergence checks on archived rooms).

### 3.3 Backend

New endpoint:

```
POST /api/rooms/{room_id}/follow-up
Body: { "participant_id": "...", "text": "..." }
Response: { "message": {...} }
```

Works only when `status = done`. Returns 409 if the room is still running (use `/to` instead).

### 3.4 `/to` command on done rooms

Lift the existing `status == "running"` guard for `/to <participant_id> <text>` — it now also works when `status = done`, behaving identically to the follow-up composer.

---

## 4. AGREE / TERMINATE summary bar

When `status = done`, display a summary bar at the very top of the transcript (above the verdict card if present):

```
┌────────────────────────────────────────────────────────────────┐
│ Converged round 4 of 5 · 2/2 AGREE · 0 TERMINATE · Style: ein-mdp │
└────────────────────────────────────────────────────────────────┘
```

Counts are computed by:

- **AGREE:** participants whose final verdict (last turn of the last phase) ends with exactly `AGREE`.
- **TERMINATE:** participants whose final verdict ends with `TERMINATE`.
- **Converged round X of Y:** current_round when the engine transitioned to `done`, out of `max_total_rounds`.

---

## 5. Backend changes

### 5.1 `POST /api/rooms/start` body additions

```python
class StartRoomBody(BaseModel):
    topic: str
    participants: list[str]
    max_total_rounds: int
    convergence: str = "agree-marker"
    style: Literal["ein-mdp", "critic-terminate"] = "ein-mdp"   # NEW
    auto_verdict: bool = True                                    # NEW
```

### 5.2 Per-style cap enforcement

```python
from agora.config.phases import STYLE_ROUND_CAPS
cap = STYLE_ROUND_CAPS.get(body.style, 5)
if body.max_total_rounds > cap:
    raise HTTPException(400, detail=f"max_total_rounds must be <= {cap} for style '{body.style}'")
```

Replace the existing `TEMPORARY_MAX_TOTAL_ROUNDS = 5` check with this per-style lookup. Keep `MIN_TOTAL_ROUNDS = 4`.

### 5.3 `POST /api/rooms/{id}/regenerate-verdict`

```python
class RegenerateBody(BaseModel):
    participant_id: str | None = None   # None = default selection rule

Response: { "verdict": "<markdown>", "author": "<participant-id>" }
```

### 5.4 `POST /api/rooms/{id}/follow-up` — see §3.3.

### 5.5 New convergence check

`terminate-majority` — parse each reply's last non-empty line. If trimmed value equals `TERMINATE`, count it. Return `True` when count >= `ceil(N / 2)` in the latest completed round.

---

## 6. Engine changes

1. `Room` gets `style: str`, `auto_verdict: bool`, `verdict_author: str | None`, `verdict_text: str | None`.
2. `phases_for_style(style, max_total_rounds)` replaces `phases_for_total_rounds()`; existing default mapping kept for backward compat on legacy rooms.
3. Engine loop detects `status` transition to `done` and, if `auto_verdict=True`, fires the verdict prompt once. Verdict reply saved to `verdict.md` and to `Room.verdict_text`.
4. Follow-up turns are appended to the transcript with `role="follow_up"` and do NOT trigger convergence checks.
5. Room rehydration on gateway restart restores `verdict.md` from disk into `Room.verdict_text` if present.

---

## 7. Tests

- Unit: `terminate-majority` convergence — at 2/3 replies with TERMINATE → fires; at 1/3 → no.
- Unit: per-style cap — request with `style=critic-terminate, max_total_rounds=16` is rejected; `=15` is accepted.
- Unit: `phases_for_style("critic-terminate", 8)` returns 4 phases with correct modes and debate rounds = 8 - 3.
- Integration: a 3-fake-driver Critic+TERMINATE run with preset TERMINATE markers in round 3 terminates after round 3 and produces a verdict.md.
- Integration: follow-up composer — send a message, receive a reply, verify it lands in transcript with role="follow_up" and does not retrigger convergence.

---

## 8. Exit criteria

1. `pytest` passes all new + existing tests (expect ≥ 70 total).
2. Modal style dropdown live; slider adjusts cap based on selection.
3. Run a Critic+TERMINATE debate end-to-end with 2 participants; reach organic TERMINATE in < 8 rounds; verdict.md auto-generated and displayed.
4. Regenerate verdict works; different participant's version overwrites cleanly.
5. Follow-up composer sends a question to the selected participant; reply appears; transcript preserves it.
6. Summary bar displays correct AGREE / TERMINATE counts on a done room.
7. Stuck-debate warning banner appears on a Critic debate that hits round 8 without any TERMINATE votes (use an integration test with a fake driver that never outputs TERMINATE).

---

## 9. Non-goals (unchanged)

All M1/M2 non-goals still apply. In addition:

- No Convergence Funnel style — dropped per Christo 2026-04-20.
- No dedicated Critic role per participant — every participant critiques. Role matrix deferred.
- No embeddings-based convergence (reply-stability) — deferred.
- No confidence-score style (Reconcile) — deferred.
