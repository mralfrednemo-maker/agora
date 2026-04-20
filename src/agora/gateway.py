from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import Any, Literal

# Windows' default mimetypes registry serves .js as text/plain, which Chrome
# rejects for ES modules. Register the correct types at import time.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("text/css", ".css")

import uvicorn
from fastapi import FastAPI, File, Header, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agora.config.phases import MIN_TOTAL_ROUNDS, STYLE_ROUND_CAPS
from agora.commands.handlers import CommandContext, CommandHandler
from agora.drivers.chatgpt_web import ChatGPTWebDriver
from agora.drivers.claude_code_new import ClaudeCodeNewDriver
from agora.drivers.claude_web import ClaudeWebDriver
from agora.drivers.codex import CodexDriver
from agora.drivers.gemini_cli import GeminiCliDriver
from agora.drivers.gemini_web import GeminiWebDriver
from agora.drivers.openclaw import OpenClawDriver
from agora.engine.room import RoomEngine
from agora.ops.admin import OpsManager
from agora.ops.engine_tools import (
    register_engine_tools,
    register_telegram_tools,
    register_whatsapp_tools,
)
from agora.persistence.store import RoomStore
from agora.web.api import ApiService
from agora.web.ws import WsHub


class CreateRoomBody(BaseModel):
    topic: str


class StartRoomBody(BaseModel):
    topic: str
    participants: list[str]
    max_total_rounds: int
    convergence: str = "agree-marker"
    style: Literal["ein-mdp", "critic-terminate"] = "ein-mdp"
    auto_verdict: bool = True


class RegenerateBody(BaseModel):
    participant_id: str | None = None


class FollowUpBody(BaseModel):
    participant_id: str
    text: str


class CommandBody(BaseModel):
    text: str


class OpsMessageBody(BaseModel):
    text: str


class OpsModelBody(BaseModel):
    model: str


def validate_max_total_rounds(style: str, max_total_rounds: int) -> None:
    if max_total_rounds < MIN_TOTAL_ROUNDS:
        raise HTTPException(status_code=400, detail=f"max_total_rounds must be >= {MIN_TOTAL_ROUNDS}")
    cap = STYLE_ROUND_CAPS.get(style, STYLE_ROUND_CAPS["ein-mdp"])
    if max_total_rounds > cap:
        raise HTTPException(
            status_code=400,
            detail=f"max_total_rounds must be <= {cap} for style '{style}'",
        )


def build_app() -> FastAPI:
    app = FastAPI(title="Agora Gateway")
    base_dir = Path("C:/Users/chris/PROJECTS/agora")
    static_dir = base_dir / "src" / "agora" / "web" / "static"
    store = RoomStore(base_dir=base_dir / "data" / "rooms")

    drivers: dict[str, Any] = {
        "claude-code-new-1": ClaudeCodeNewDriver(id="claude-code-new-1", display_name="Claude Code New"),
        "codex-1": CodexDriver(id="codex-1", display_name="Codex"),
        "gemini-cli-1": GeminiCliDriver(id="gemini-cli-1", display_name="Gemini CLI"),
        "chatgpt-web-1": ChatGPTWebDriver(),
        "claude-web-1": ClaudeWebDriver(),
        "gemini-web-1": GeminiWebDriver(),
    }
    # OpenClaw agents are opt-in per session (each has a named agent).
    for oc_agent, oc_display in (
        ("turing", "OpenClaw Turing"),
        ("daedalus", "OpenClaw Daedalus"),
        ("hermes", "OpenClaw Hermes"),
        ("themis", "OpenClaw Themis"),
        ("socrates", "OpenClaw Socrates"),
        ("athena", "OpenClaw Athena"),
        ("descartes", "OpenClaw Descartes"),
        ("prism", "OpenClaw Prism"),
        ("ikarus", "OpenClaw Ikarus"),
        ("inspector", "OpenClaw Inspector"),
    ):
        oc_id = f"openclaw-{oc_agent}-1"
        drivers[oc_id] = OpenClawDriver(id=oc_id, display_name=oc_display, agent=oc_agent)
    # Admin starts on Haiku 4.5 (200K cap, cheap). User can switch to Sonnet or
    # Opus 200K via the dashboard dropdown. Opus 1M is refused — see
    # tech-library/claude-code/opus-1m-context-switching-pitfall.md.
    admin_driver = ClaudeCodeNewDriver(
        id="admin-1",
        display_name="Ops Admin",
        model="claude-haiku-4-5-20251001",
    )

    ws_hub = WsHub()
    engine = RoomEngine(store=store, drivers=drivers, emit=ws_hub.broadcast)
    commands = CommandHandler(engine=engine, context=CommandContext())
    api = ApiService(engine=engine, commands=commands, driver_health={})
    ops = OpsManager.create(driver=admin_driver, emit=ws_hub.broadcast)
    register_engine_tools(ops.registry, engine)
    register_telegram_tools(ops.registry)
    register_whatsapp_tools(ops.registry)

    app.state.engine = engine
    app.state.commands = commands
    app.state.api = api
    app.state.ws_hub = ws_hub
    app.state.ops = ops
    app.state.admin_driver = admin_driver

    @app.on_event("startup")
    async def startup() -> None:
        for driver in drivers.values():
            ok, detail = await driver.health_check()
            api.driver_health[driver.id] = {"ok": ok, "detail": detail}
        await admin_driver.health_check()
        await engine.restore_rooms()

    @app.get("/")
    async def root() -> FileResponse:
        return FileResponse(
            static_dir / "index.html",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/app.mjs")
    async def app_module() -> FileResponse:
        return FileResponse(
            static_dir / "app.js",
            media_type="text/javascript",
            headers={"Cache-Control": "no-store"},
        )

    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/api/rooms")
    async def list_rooms() -> list[dict[str, object]]:
        return await api.list_rooms()

    @app.get("/api/rooms/{room_id}")
    async def get_room(room_id: str) -> dict[str, object]:
        if room_id not in engine.rooms:
            raise HTTPException(status_code=404, detail="room not found")
        return await api.get_room(room_id)

    @app.post("/api/rooms")
    async def create_room(body: CreateRoomBody) -> dict[str, str]:
        return await api.create_room(topic=body.topic)

    @app.post("/api/rooms/start")
    async def start_room(body: StartRoomBody) -> dict[str, object]:
        for driver_id in body.participants:
            if driver_id not in drivers:
                raise HTTPException(status_code=400, detail=f"Unknown participant: {driver_id}")
        if not body.participants:
            raise HTTPException(status_code=400, detail="participants cannot be empty")
        validate_max_total_rounds(body.style, body.max_total_rounds)
        return await api.start_room(
            topic=body.topic,
            participants=body.participants,
            max_total_rounds=body.max_total_rounds,
            convergence=body.convergence,
            style=body.style,
            auto_verdict=body.auto_verdict,
        )

    @app.post("/api/rooms/{room_id}/regenerate-verdict")
    async def regenerate_verdict(room_id: str, body: RegenerateBody) -> dict[str, object]:
        if room_id not in engine.rooms:
            raise HTTPException(status_code=404, detail="room not found")
        room = engine.rooms[room_id]
        if room.status != "done":
            raise HTTPException(status_code=409, detail="room must be done before regenerating verdict")
        try:
            return await api.regenerate_verdict(room_id, body.participant_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/rooms/{room_id}/follow-up")
    async def follow_up(room_id: str, body: FollowUpBody) -> dict[str, object]:
        if room_id not in engine.rooms:
            raise HTTPException(status_code=404, detail="room not found")
        room = engine.rooms[room_id]
        if room.status != "done":
            raise HTTPException(status_code=409, detail="room is still running; use /to instead")
        try:
            return await api.follow_up(room_id=room_id, participant_id=body.participant_id, text=body.text)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/rooms/{room_id}/command")
    async def command(room_id: str, body: CommandBody) -> dict[str, object]:
        if room_id not in engine.rooms:
            raise HTTPException(status_code=404, detail="room not found")
        try:
            return await api.command(room_id=room_id, text=body.text)
        except Exception as exc:
            await ws_hub.broadcast({"type": "error", "detail": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/rooms/{room_id}/archive")
    async def archive(room_id: str) -> dict[str, object]:
        if room_id not in engine.rooms:
            raise HTTPException(status_code=404, detail="room not found")
        return await api.archive_room(room_id)

    @app.delete("/api/rooms/{room_id}")
    async def delete_room(room_id: str) -> dict[str, object]:
        if room_id not in engine.rooms:
            raise HTTPException(status_code=404, detail="room not found")
        return await api.delete_room(room_id)

    @app.get("/api/drivers")
    async def drivers_endpoint() -> dict[str, object]:
        return await api.list_drivers()

    # ---- Ops (admin agent) -----------------------------------------------

    MAX_OPS_TEXT = 32_000
    MAX_UPLOAD_BYTES = 25 * 1024 * 1024

    # 200K-context models only. The 1M Opus variant (`claude-opus-4-7[1m]`) is
    # REFUSED because mid-session switching from 1M to a 200K model produces
    # unpredictable truncation. Keeping all choices at a shared 200K ceiling
    # means the admin can swap cost tiers freely without memory surprises.
    # See: tech-library/claude-code/opus-1m-context-switching-pitfall.md
    ALLOWED_OPS_MODELS = {
        "claude-haiku-4-5": "claude-haiku-4-5-20251001",
        "claude-sonnet-4-6": "claude-sonnet-4-6",
        "claude-opus-4-7": "claude-opus-4-7",
    }

    @app.get("/api/ops")
    async def ops_get() -> dict[str, object]:
        snap = await ops.snapshot()
        # Map the CLI model back to its label; "default" is not exposed anymore.
        current = getattr(admin_driver, "model", None)
        label = None
        for lbl, cli in ALLOWED_OPS_MODELS.items():
            if cli == current:
                label = lbl
                break
        snap["model"] = label or "claude-haiku-4-5"
        snap["allowed_models"] = list(ALLOWED_OPS_MODELS.keys())
        return snap

    @app.post("/api/ops/model")
    async def ops_set_model(body: OpsModelBody) -> dict[str, object]:
        choice = (body.model or "").strip()
        # Belt-and-braces: reject any 1M-context model string even if it snuck
        # into the allowlist. Admin must stay on a 200K ceiling.
        if "[1m]" in choice.lower() or choice.lower().endswith("-1m"):
            raise HTTPException(
                status_code=400,
                detail="1M-context models are not permitted for the ops admin (context-switch pitfall)",
            )
        if choice not in ALLOWED_OPS_MODELS:
            raise HTTPException(
                status_code=400,
                detail=f"unknown model '{choice}'; allowed: {list(ALLOWED_OPS_MODELS.keys())}",
            )
        admin_driver.model = ALLOWED_OPS_MODELS[choice]
        return {"ok": True, "model": choice, "cli_model": admin_driver.model}

    @app.post("/api/ops/message")
    async def ops_message(body: OpsMessageBody) -> dict[str, object]:
        text = (body.text or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="text required")
        if len(text) > MAX_OPS_TEXT:
            raise HTTPException(status_code=413, detail=f"text exceeds {MAX_OPS_TEXT} chars")
        msg = await ops.handle_user_text(text)
        return {"ok": True, "seq": msg.seq}

    @app.post("/api/ops/transcribe")
    async def ops_transcribe(audio: UploadFile = File(...)) -> dict[str, object]:
        from agora.ops.voice import transcribe, VoiceNotConfigured, MAX_STT_BYTES
        ct = (audio.content_type or "").lower()
        if ct and not (ct.startswith("audio/") or ct == "application/octet-stream"):
            raise HTTPException(status_code=415, detail=f"unsupported content-type: {ct}")
        data = await audio.read()
        if len(data) > MAX_STT_BYTES:
            raise HTTPException(status_code=413, detail=f"audio exceeds {MAX_STT_BYTES} bytes")
        if len(data) == 0:
            raise HTTPException(status_code=400, detail="empty audio upload")
        try:
            text = await transcribe(data, filename=audio.filename or "clip.webm")
        except VoiceNotConfigured as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"STT failed: {exc}") from exc
        return {"text": text}

    @app.get("/api/ops/tts")
    async def ops_tts(text: str) -> StreamingResponse:
        from agora.ops.voice import MAX_TTS_CHARS, VoiceNotConfigured, ensure_configured, synthesize_full
        if not text or not text.strip():
            raise HTTPException(status_code=400, detail="text required")
        if len(text) > MAX_TTS_CHARS:
            raise HTTPException(status_code=413, detail=f"text exceeds {MAX_TTS_CHARS} chars")
        try:
            ensure_configured()
            audio_bytes, media_type = await synthesize_full(text)
        except VoiceNotConfigured as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"TTS failed: {exc}") from exc

        async def one_shot() -> Any:
            yield audio_bytes

        return StreamingResponse(one_shot(), media_type=media_type)

    def _check_webhook_token(header_value: str | None) -> None:
        expected = os.environ.get("AGORA_BRIDGE_TOKEN") or ""
        if not expected:
            return  # token optional; localhost binding is the primary defense
        if header_value != expected:
            raise HTTPException(status_code=401, detail="invalid bridge token")

    @app.post("/api/ops/telegram/incoming")
    async def ops_tg_incoming(
        body: dict[str, object],
        x_bridge_token: str | None = Header(default=None, alias="X-Bridge-Token"),
    ) -> dict[str, object]:
        _check_webhook_token(x_bridge_token)
        text = str(body.get("text", "")).strip()
        sender = str(body.get("from", "Telegram"))
        if not text:
            raise HTTPException(status_code=400, detail="text required")
        if len(text) > MAX_OPS_TEXT:
            raise HTTPException(status_code=413, detail=f"text exceeds {MAX_OPS_TEXT} chars")
        await ops.deliver_system_event(f"[Telegram from {sender}]: {text}")
        return {"ok": True}

    @app.post("/api/ops/whatsapp/incoming")
    async def ops_wa_incoming(
        body: dict[str, object],
        x_bridge_token: str | None = Header(default=None, alias="X-Bridge-Token"),
    ) -> dict[str, object]:
        _check_webhook_token(x_bridge_token)
        text = str(body.get("text", "")).strip()
        sender = str(body.get("from", "WhatsApp"))
        if not text:
            raise HTTPException(status_code=400, detail="text required")
        if len(text) > MAX_OPS_TEXT:
            raise HTTPException(status_code=413, detail=f"text exceeds {MAX_OPS_TEXT} chars")
        await ops.deliver_system_event(f"[WhatsApp from {sender}]: {text}")
        return {"ok": True}

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws_hub.connect(ws)
        try:
            for room in engine.list_rooms():
                await ws.send_json({"type": "room.update", "room_id": room["id"], "state": room})
            while True:
                payload: dict[str, Any] = await ws.receive_json()
                if payload.get("type") != "command":
                    continue
                room_id = payload.get("room_id")
                text = payload.get("text")
                if not isinstance(text, str):
                    continue
                try:
                    result = await commands.handle(text=text, room_id=room_id)
                    await ws.send_json({"type": "command.result", "result": result})
                except Exception as exc:
                    await ws.send_json({"type": "error", "detail": str(exc)})
        except WebSocketDisconnect:
            ws_hub.disconnect(ws)

    return app


app = build_app()


if __name__ == "__main__":
    uvicorn.run("agora.gateway:app", host="127.0.0.1", port=8789, reload=False)
