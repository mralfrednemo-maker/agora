from __future__ import annotations

import asyncio
import io
import mimetypes
import tempfile
from pathlib import Path

import httpx

from agora.ops import config as ops_config


OPENAI_BASE = "https://api.openai.com/v1"
MAX_TTS_CHARS = 4000
MAX_STT_BYTES = 25 * 1024 * 1024


class VoiceNotConfigured(Exception):
    pass


# ---------- backend selection ---------------------------------------------

def stt_backend() -> str:
    """'local' or 'openai'. Default 'local' if faster-whisper is importable,
    else 'openai'. Override via AGORA_STT_BACKEND env var.
    """
    requested = ops_config.get("AGORA_STT_BACKEND", "").strip().lower()
    if requested in {"local", "openai"}:
        return requested
    try:
        import faster_whisper  # noqa: F401
        return "local"
    except Exception:
        return "openai"


def tts_backend() -> str:
    """'windows' or 'openai'. Default 'windows' on Windows, else 'openai'.
    Override via AGORA_TTS_BACKEND env var.
    """
    requested = ops_config.get("AGORA_TTS_BACKEND", "").strip().lower()
    if requested in {"windows", "openai"}:
        return requested
    # Windows SAPI is available natively via PowerShell; prefer it when running on Windows.
    import sys
    return "windows" if sys.platform.startswith("win") else "openai"


# ---------- STT -----------------------------------------------------------

def _mime_for(filename: str) -> str:
    guess, _ = mimetypes.guess_type(filename)
    if guess and guess.startswith("audio/"):
        return guess
    return "audio/webm"


async def _transcribe_local(audio_bytes: bytes, filename: str) -> str:
    """Local faster-whisper transcription. Runs the CPU/GPU model in a thread
    to keep the event loop responsive. Model + device are env-configurable.
    """
    model_name = ops_config.get("AGORA_LOCAL_WHISPER_MODEL", "base")
    device = ops_config.get("AGORA_LOCAL_WHISPER_DEVICE", "auto")  # 'cpu' | 'cuda' | 'auto'
    compute_type = ops_config.get("AGORA_LOCAL_WHISPER_COMPUTE", "auto")

    try:
        from faster_whisper import WhisperModel
    except Exception as exc:  # noqa: BLE001
        raise VoiceNotConfigured(f"faster-whisper not installed: {exc}") from exc

    # Persist to a temp file — faster-whisper accepts path or ndarray; path is simplest.
    suffix = Path(filename).suffix or ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    def _run() -> str:
        kwargs: dict[str, object] = {}
        if device != "auto":
            kwargs["device"] = device
        if compute_type != "auto":
            kwargs["compute_type"] = compute_type
        model = WhisperModel(model_name, **kwargs)
        segments, _info = model.transcribe(tmp_path, beam_size=1)
        return "".join(segment.text for segment in segments).strip()

    try:
        return await asyncio.to_thread(_run)
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except OSError:
            pass


async def _transcribe_openai(audio_bytes: bytes, filename: str) -> str:
    key = ops_config.get("OPENAI_API_KEY")
    if not key:
        raise VoiceNotConfigured("OPENAI_API_KEY not set in agora/.env")
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


async def transcribe(audio_bytes: bytes, filename: str = "clip.webm") -> str:
    if len(audio_bytes) > MAX_STT_BYTES:
        raise ValueError(f"audio exceeds {MAX_STT_BYTES} bytes")
    backend = stt_backend()
    if backend == "local":
        try:
            return await _transcribe_local(audio_bytes, filename)
        except VoiceNotConfigured:
            # Fall through to OpenAI if a key is set.
            if ops_config.get("OPENAI_API_KEY"):
                return await _transcribe_openai(audio_bytes, filename)
            raise
    return await _transcribe_openai(audio_bytes, filename)


# ---------- TTS -----------------------------------------------------------

async def _synthesize_windows(text: str) -> bytes:
    """Windows SAPI via PowerShell System.Speech. Produces a WAV blob.
    Works out-of-the-box on Windows 10/11 with no pip dependencies.
    """
    import sys
    if not sys.platform.startswith("win"):
        raise VoiceNotConfigured("windows TTS requires Windows")

    voice = ops_config.get("AGORA_WINDOWS_VOICE", "").strip()  # optional voice name
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name

    try:
        # PowerShell one-liner: output to file, speak, done. Voice name optional.
        voice_clause = f"$s.SelectVoice('{voice}');" if voice else ""
        safe_text = text.replace("'", "''")  # PS single-quote escaping
        ps_script = (
            "Add-Type -AssemblyName System.Speech;"
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            f"{voice_clause}"
            f"$s.SetOutputToWaveFile('{wav_path}');"
            f"$s.Speak('{safe_text}');"
            "$s.Dispose();"
        )
        proc = await asyncio.create_subprocess_exec(
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps_script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=60.0)
        except (asyncio.TimeoutError, TimeoutError) as exc:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            raise VoiceNotConfigured("windows TTS timed out") from exc
        if proc.returncode != 0:
            raise VoiceNotConfigured(
                f"powershell SAPI exit {proc.returncode}: {stderr.decode('utf-8', errors='replace')[:300]}"
            )
        data = Path(wav_path).read_bytes()
        if not data:
            raise VoiceNotConfigured("windows TTS produced an empty wav")
        return data
    finally:
        try:
            Path(wav_path).unlink(missing_ok=True)
        except OSError:
            pass


async def _synthesize_openai(text: str, voice: str | None = None) -> bytes:
    key = ops_config.get("OPENAI_API_KEY")
    if not key:
        raise VoiceNotConfigured("OPENAI_API_KEY not set in agora/.env")
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


async def synthesize_full(text: str, voice: str | None = None) -> tuple[bytes, str]:
    """Return (audio_bytes, media_type). Validates everything up-front so HTTP
    headers are never flushed before we know the payload is good.
    """
    if len(text) > MAX_TTS_CHARS:
        raise ValueError(f"text exceeds {MAX_TTS_CHARS} chars")
    backend = tts_backend()
    if backend == "windows":
        try:
            wav = await _synthesize_windows(text)
            return wav, "audio/wav"
        except VoiceNotConfigured:
            # Fall back to OpenAI if a key is set.
            if ops_config.get("OPENAI_API_KEY"):
                mp3 = await _synthesize_openai(text, voice)
                return mp3, "audio/mpeg"
            raise
    mp3 = await _synthesize_openai(text, voice)
    return mp3, "audio/mpeg"


def ensure_configured() -> None:
    """Raise VoiceNotConfigured if neither backend can serve a request.

    Success criteria:
      - STT: at least one of {local faster-whisper importable, OPENAI_API_KEY set}
      - TTS: at least one of {Windows host, OPENAI_API_KEY set}
    """
    import sys
    stt_ok = False
    try:
        import faster_whisper  # noqa: F401
        stt_ok = True
    except Exception:
        pass
    if not stt_ok and not ops_config.get("OPENAI_API_KEY"):
        raise VoiceNotConfigured(
            "no STT backend available: install faster-whisper or set OPENAI_API_KEY"
        )
    tts_ok = sys.platform.startswith("win") or bool(ops_config.get("OPENAI_API_KEY"))
    if not tts_ok:
        raise VoiceNotConfigured(
            "no TTS backend available: run on Windows or set OPENAI_API_KEY"
        )
