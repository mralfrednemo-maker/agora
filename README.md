# Agora M2

Agora M2 is a local FastAPI gateway + dashboard for multi-LLM debate rooms with:
- Per-room driver sessions (`start_session` / `send_in_session` / `close_session` / `has_session`)
- Four-phase M2 flow: `positions` -> `contrarian` -> `debate` -> `verdict`
- Mixed phase modes (`parallel` + `serial`)
- New dashboard UX with `+ New Debate` modal and transcript-header controls

## Requirements

- Python 3.11+
- Windows paths and local execution

## Setup

```powershell
cd C:\Users\chris\PROJECTS\agora
pip install -e .
```

## Run

```powershell
python -m agora.gateway
```

Open: `http://127.0.0.1:8789`

## Dashboard Flow (No Commands Required)

1. Click `+ New Debate`.
2. Enter topic.
3. Select participants.
4. Set rounds (`4`-`20`) and convergence mode.
5. Click `Start Debate`.

Controls in transcript header:
- `Pause` / `Resume`
- `Stop`
- `Inject`
- `Archive`

Rooms pane:
- Newest-first active rooms
- Archived rooms collapsed at bottom
- Per-room delete button (hard delete)

## Power Commands

Command box remains available:

`/new`, `/participants`, `/start`, `/pause`, `/resume`, `/stop`, `/inject`, `/to`, `/rounds`, `/phase`, `/synthesize`, `/list`, `/attach`, `/drivers`

## Session Persistence

Per-driver per-room session state:

`C:\Users\chris\PROJECTS\agora\data\driver-state\<driver-id>\sessions\<room-id>.json`

Room persistence:

`C:\Users\chris\PROJECTS\agora\data\rooms\<room-id>\`

## Tests

```powershell
pytest
```

## Operational notes

- **Rotate an OpenAI key:** the `.env` file is read once at gateway import. After editing `.env`, restart the gateway for the new key to take effect. `get()` in `agora.ops.config` still reads `os.environ` first, so `set OPENAI_API_KEY=new` in the same shell before `python -m agora.gateway` works without an .env edit.
- **Bridge authentication:** set `AGORA_BRIDGE_TOKEN=<secret>` in both bridges' environment and in Agora's `.env`. Bridges send `X-Bridge-Token` on webhook POSTs; Agora rejects mismatches. Leaving it unset is acceptable for single-user dev on 127.0.0.1 only — any local process can otherwise post fake inbound events.
- **Transcript size:** `data/rooms/<room-id>/transcript.jsonl` grows unbounded. A single debate room rarely exceeds a few MB but long-lived ops rooms can. Delete or archive old rooms via the dashboard when noisy.
