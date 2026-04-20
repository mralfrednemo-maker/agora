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
