# TODO — Post-M1

Items deferred from M1. None of these block M1 sign-off.

## Live-test findings (2026-04-20)

### Gemini CLI is a coding agent, not a chatbot
Real-run result: even with `--approval-mode plan`, Gemini CLI responds to any prompt by saying things like "I'm ready. What's the objective? I'll start by reading the README.md..." — it's hardwired as a repo-exploring coding agent. Not suitable as a debate participant.

**Decision:** drop `gemini-cli` from the debate-driver roster. Use Gemini Web (CDP driver, M3) for debate participation. Keep `gemini-cli` driver in code as a tool-execution driver for future use cases; mark it in driver health as "not recommended for debate".

### Dashboard bugs (surfaced by live test)

1. `/new` command cannot be submitted when no room is active. `app.js::submitCommand()` requires `activeRoomId` and posts to `/api/rooms/{id}/command` — but `/new` is a room-creation command and has no room yet. Needs a special-case path: if the command is `/new`, POST to `/api/rooms` directly.
2. After page reload, rooms list renders empty (0 `.room-item` divs) even though `/api/rooms` returns data. Likely an async error in the top-level `await initialLoad()` — add try/catch + console.error, investigate.

### Template fix validated
The previous (pre-M1-post-fix) template only passed same-phase prior-round replies. After fixing `room.py` to pass each peer's most-recent reply regardless of phase, Claude Code's Challenge-phase output explicitly addresses "On codex-1: Your answer is a one-sentence restatement…" — confirming cross-phase context now flows.

## Drivers

### gemini-cli — proper session resume

Current M1 implementation (`src/agora/drivers/gemini_cli.py`) has two bugs masked by fresh-per-send fallback:

1. Missing `--yolo` flag. Christo confirmed headless use requires `gemini --yolo --resume '<uuid>'`.
2. Uses `self.id` (the driver instance id) as the `--resume` argument — never a valid session UUID. On first send there is no prior session; on subsequent sends the UUID must be captured from the first invocation's output and persisted.

**Fix plan:**
- First send: `gemini --yolo --model <model>` with the prompt. Parse stdout to extract the session UUID.
- Persist UUID under `data/driver-state/<driver-id>/session.json`.
- Subsequent sends: `gemini --yolo --resume <uuid>`.
- When session persists, engine can optionally send only the delta — saves tokens per turn.

### claude-code-new — long-running session option

M1 spawns fresh `claude` subprocess per `send()`. Adds ~1-2s cold-start per turn. Consider a long-running subprocess with stream-json stdin/stdout.

### codex — resume id capture

Verify `codex-companion.mjs` resume id is captured and reused. Currently best-effort; validate against real Codex output.

## Engine

### Deprecated FastAPI startup hook

`gateway.py` uses `@app.on_event("startup")` — deprecated. Migrate to lifespan context manager.

### Dynamic driver instances

Gateway pre-registers exactly one instance of each kind. `/participants` can only attach those three. For multi-instance debates (e.g. two Codex participants) expose driver factory endpoints.

## Convergence

### Threshold-based checks

Current checks are binary (all match, or none). Consider quorum checks (N-of-M participants say AGREE).

## Dashboard

### Participant status chips (spec §14.1 optional)

Right-pane chips showing per-participant status (idle/thinking/replied/error) and last-turn latency. Not implemented in M1.

### Command history / autocomplete

Up-arrow history mentioned in the M1 spec; verify wired, polish.

## Dashboard follow-ups (post polish, 2026-04-20)

1. Preserve participant filter and active room in localStorage across browser refresh.
2. Replace full transcript re-render with incremental DOM append for very long rooms to reduce repaint cost.
