# Agora Milestones

## M1 (Completed)

- Core room engine, persistence, command parser, dashboard, and initial CLI drivers landed.
- Legacy 5-phase flow (`opening/challenge/cross-exam/revision/verdict`) is now retired.

## M2 (Completed)

### Scope delivered

- Per-room driver sessions across `claude-code-new`, `codex`, `gemini-cli`, and `fake`:
  - `start_session(room_id, system_frame)`
  - `send_in_session(room_id, user_message)`
  - `close_session(room_id)`
  - `has_session(room_id)`
- Session persistence and rehydration:
  - `C:\Users\chris\PROJECTS\agora\data\driver-state\<driver-id>\sessions\<room-id>.json`
- Gemini CLI debate mode revived with `--yolo` + `--resume <uuid>` and explicit no-tool system framing.
- M2 phase model with mode support:
  - `positions` (parallel)
  - `contrarian` (parallel)
  - `debate` (serial; rounds = `max_total_rounds - 3`)
  - `verdict` (parallel)
- New room parameter `max_total_rounds` (min 4).
- M2 prompt templates:
  - room frame (session bootstrap)
  - delta turn message (session continuation)
- New endpoints:
  - `POST /api/rooms/start`
  - `POST /api/rooms/{id}/archive`
  - `DELETE /api/rooms/{id}`
- New websocket event:
  - `participant.thinking` with `{room_id, participant_id, in_flight}`
- Dashboard overhaul:
  - `+ New Debate` modal
  - transcript header controls (`Pause/Resume/Stop/Inject/Archive`)
  - typing indicators
  - archived rooms collapsed at bottom
  - per-room delete action
  - command help tooltip
- Cleanup:
  - removed deprecated M1 `DEFAULT_PHASES`
  - removed old probe rooms from `data/rooms`

### Validation targets

- `pytest` updated for M2 behavior and expanded coverage.
- End-to-end local run uses `http://127.0.0.1:8789`.
