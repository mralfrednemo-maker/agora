from __future__ import annotations

import mimetypes

import httpx

from agora.ops import config as ops_config


OPENAI_BASE = "https://api.openai.com/v1"
MAX_TTS_CHARS = 4000
MAX_STT_BYTES = 25 * 1024 * 1024  # OpenAI Whisper hard limit


class VoiceNotConfigured(Exception):
    pass


def _require_key() -> str:
    key = ops_config.get("OPENAI_API_KEY")
    if not key:
        raise VoiceNotConfigured("OPENAI_API_KEY not set in agora/.env")
    return key


def ensure_configured() -> None:
    """Raise VoiceNotConfigured if the API key is missing. Safe to call eagerly."""
    _require_key()


def _mime_for(filename: str) -> str:
    guess, _ = mimetypes.guess_type(filename)
    if guess and guess.startswith("audio/"):
        return guess
    return "audio/webm"


async def transcribe(audio_bytes: bytes, filename: str = "clip.webm") -> str:
    if len(audio_bytes) > MAX_STT_BYTES:
        raise ValueError(f"audio exceeds {MAX_STT_BYTES} bytes")
    key = _require_key()
    mime = _mime_for(filename)
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{OPENAI_BASE}/audio/transcriptions",
            headers={"Authorization": f"Bearer {key}"},
            files={"file": (filename, audio_bytes, mime)},
            data={"model": ops_config.get("OPENAI_STT_MODEL", "whisper-1"), "response_format": "json"},
        )
        resp.raise_for_status()
        data = resp.json()
        return str(data.get("text", ""))


async def synthesize_full(text: str, voice: str | None = None) -> bytes:
    """Return MP3 bytes for the given text. Raises before any partial return."""
    if len(text) > MAX_TTS_CHARS:
        raise ValueError(f"text exceeds {MAX_TTS_CHARS} chars")
    key = _require_key()
    chosen_voice = voice or ops_config.get("OPENAI_TTS_VOICE", "alloy")
    body = {
        "model": ops_config.get("OPENAI_TTS_MODEL", "tts-1"),
        "voice": chosen_voice,
        "input": text,
        "response_format": "mp3",
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{OPENAI_BASE}/audio/speech",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=body,
        )
        resp.raise_for_status()
        return resp.content


# `synthesize` (async generator) was removed — gateway uses `synthesize_full`
# to surface errors synchronously before StreamingResponse headers are flushed.
