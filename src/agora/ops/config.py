from __future__ import annotations

import os
from pathlib import Path

AGORA_ROOT = Path("C:/Users/chris/PROJECTS/agora")
ENV_PATH = AGORA_ROOT / ".env"


def _load_env_file() -> dict[str, str]:
    if not ENV_PATH.exists():
        return {}
    result: dict[str, str] = {}
    for line in ENV_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        result[key] = value
    return result


# NOTE: The .env file is read once at module import. To pick up key rotation
# (e.g. a new OPENAI_API_KEY) you must restart the gateway process. `get()`
# still falls through to os.environ first, so process-level env changes do
# take effect without restart if you set them there.
_env = _load_env_file()


def get(key: str, default: str = "") -> str:
    return os.environ.get(key) or _env.get(key) or default


def _get_int(key: str, default: int) -> int:
    raw = get(key, "")
    if not raw:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


# Snapshots at import. Prefer get() at call sites if rotation matters.
OPENAI_API_KEY = get("OPENAI_API_KEY")
OPENAI_TTS_VOICE = get("OPENAI_TTS_VOICE", "alloy")
OPENAI_TTS_MODEL = get("OPENAI_TTS_MODEL", "tts-1")
OPENAI_STT_MODEL = get("OPENAI_STT_MODEL", "whisper-1")
TELEGRAM_BRIDGE_URL = get("TELEGRAM_BRIDGE_URL", "http://127.0.0.1:9788")
WHATSAPP_BRIDGE_URL = get("WHATSAPP_BRIDGE_URL", "http://127.0.0.1:9789")
ADMIN_AGENT_DRIVER_ID = get("ADMIN_AGENT_DRIVER_ID", "admin-1")
ADMIN_AGENT_DRIVER_KIND = get("ADMIN_AGENT_DRIVER_KIND", "claude-code-new")

OPS_ROOM_ID = "ops"
# Ops state lives in a separate directory so the debate-room engine's
# rehydration scanner (which only looks at data/rooms/) ignores it.
OPS_ROOM_DIR = AGORA_ROOT / "data" / "ops"
MAX_TOOL_CALLS_PER_TURN = _get_int("MAX_TOOL_CALLS_PER_TURN", 8)
