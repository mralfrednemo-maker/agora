from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import Any

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

# Styles defined in the M3 spec
SPEC_STYLES = ("ein-mdp", "critic-terminate", "primary-pair")
from agora.commands.handlers import CommandContext, CommandHandler
from agora.drivers.chatgpt_web import ChatGPTWebDriver
from agora.drivers.claude_code_new import ClaudeCodeNewDriver
from agora.drivers.anthropic_code import AnthropicCodeDriver
from agora.drivers.claude_web import ClaudeWebDriver
from agora.drivers.codex import CodexDriver
from agora.drivers.base import DriverError
from agora.drivers.gemini_cli import GeminiCliDriver
from agora.drivers.gemini_web import GeminiWebDriver
from agora.drivers.openclaw import OpenClawDriver
from agora.engine.live_handover import LiveHandoverService
from agora.engine.room import RoomEngine
from agora.ops.admin import OpsManager
from agora.ops.engine_tools import (
    register_engine_tools,
    register_telegram_tools,
    register_whatsapp_tools,
)
from agora.persistence.live_handover_store import LiveHandoverStore
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
    style: str = "ein-mdp"
    auto_verdict: bool = True
    target_file: str | None = None
    dod_file: str | None = None
    ui_mode: str | None = None
    role_assignments: list[dict[str, object]] | None = None
    workflow_notes: str | None = None


class RegenerateBody(BaseModel):
    participant_id: str | None = None


class FollowUpBody(BaseModel):
    participant_id: str
    text: str


class CommandBody(BaseModel):
    text: str


class AttachLiveLinkBody(BaseModel):
    label: str
    driver_id: str
    external_session_ref: str


class LiveHandoverBody(BaseModel):
    goal: str
    interviewer_link_id: str
    source_link_id: str
    max_interview_turns: int = 3
    max_total_wakes: int = 8
    max_invalid_outputs_per_agent: int = 2
    max_runtime_minutes: int = 10


class AskAgentBody(BaseModel):
    question: str
    source_driver_id: str
    source_session_ref: str
    source_label: str | None = None
    interviewer_driver_id: str = "codex-1"
    interviewer_session_ref: str | None = None
    interviewer_label: str | None = None
    max_interview_turns: int = 1
    max_total_wakes: int = 4
    max_invalid_outputs_per_agent: int = 2
    max_runtime_minutes: int = 10


class AgentMessageBody(BaseModel):
    to_link_id: str
    body: str
    from_link_id: str | None = None
    subject: str | None = None
    requires_ack: bool = True


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
    live_handover_store = LiveHandoverStore(base_dir / "data" / "live-handover")

    drivers: dict[str, Any] = {
        "anthropic-code-1": AnthropicCodeDriver(id="anthropic-code-1", display_name="Anthropic Code"),
        "claude-code-new-1": ClaudeCodeNewDriver(
            id="claude-code-new-1",
            display_name="Claude MiniMax",
            model="MiniMax-M2.7-highspeed",
        ),
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
        model="MiniMax-M2.7-highspeed",
    )
    ops_model_choice = "claude-opus-4-7"

    ws_hub = WsHub()
    engine = RoomEngine(store=store, drivers=drivers, emit=ws_hub.broadcast)
    commands = CommandHandler(engine=engine, context=CommandContext())
    live_handover = LiveHandoverService(store=live_handover_store, drivers=drivers)
    api = ApiService(engine=engine, commands=commands, driver_health={}, live_handover=live_handover)
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
            try:
                ok, detail = await driver.health_check()
                api.driver_health[driver.id] = {"ok": ok, "detail": detail}
            except Exception as exc:
                api.driver_health[driver.id] = {"ok": False, "detail": str(exc)}
        try:
            await admin_driver.health_check()
        except Exception as exc:
            # Log but don't crash startup; ops will report its own health state.
            print(f"Admin driver health check failed: {exc}")
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
        if body.style not in SPEC_STYLES and body.style != "exhaustion-loop":
            raise HTTPException(
                status_code=400,
                detail=f"Unknown style '{body.style}'. Known styles: {list(SPEC_STYLES)}",
            )
        for driver_id in body.participants:
            if driver_id not in drivers:
                raise HTTPException(status_code=400, detail=f"Unknown participant: {driver_id}")
        if not body.participants:
            raise HTTPException(status_code=400, detail="participants cannot be empty")
        if body.style == "exhaustion-loop":
            if len(body.participants) != 3:
                raise HTTPException(
                    status_code=400,
                    detail=f"Exhaustion Loop requires exactly 3 participants (claude-code-new, gemini-cli, codex). Got {len(body.participants)}.",
                )
            kinds = {drivers[did].kind for did in body.participants}
            required = {"claude-code-new", "gemini-cli", "codex"}
            missing = required - kinds
            if missing:
                raise HTTPException(
                    status_code=400,
                    detail=f"Exhaustion Loop requires one each of claude-code-new, gemini-cli, and codex participants. Missing kinds: {sorted(missing)}",
                )
        validate_max_total_rounds(body.style, body.max_total_rounds)
        return await api.start_room(
            topic=body.topic,
            participants=body.participants,
            max_total_rounds=body.max_total_rounds,
            convergence=body.convergence,
            style=body.style,
            auto_verdict=body.auto_verdict,
            target_file=body.target_file,
            dod_file=body.dod_file,
            ui_mode=body.ui_mode,
            role_assignments=body.role_assignments,
            workflow_notes=body.workflow_notes,
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

    @app.get("/api/fs/ls")
    async def list_fs(path: str = ".") -> dict[str, object]:
        return await api.list_filesystem(path)

    @app.get("/api/live-links")
    async def list_live_links() -> dict[str, object]:
        return await api.list_live_links()

    @app.post("/api/live-links/attach")
    async def attach_live_link(body: AttachLiveLinkBody) -> dict[str, object]:
        try:
            return await api.attach_live_link(
                label=body.label,
                driver_id=body.driver_id,
                external_session_ref=body.external_session_ref,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except DriverError as exc:
            raise HTTPException(status_code=502, detail=f"driver failed: {exc}") from exc

    @app.post("/api/live-handover/workflows")
    async def run_live_handover(body: LiveHandoverBody) -> dict[str, object]:
        try:
            return await api.run_live_handover(
                goal=body.goal,
                interviewer_link_id=body.interviewer_link_id,
                source_link_id=body.source_link_id,
                max_interview_turns=body.max_interview_turns,
                max_total_wakes=body.max_total_wakes,
                max_invalid_outputs_per_agent=body.max_invalid_outputs_per_agent,
                max_runtime_minutes=body.max_runtime_minutes,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except DriverError as exc:
            raise HTTPException(status_code=502, detail=f"driver failed: {exc}") from exc

    @app.get("/api/live-handover/workflows/{workflow_id}")
    async def get_live_handover_workflow(workflow_id: str) -> dict[str, object]:
        try:
            return await api.get_live_handover_workflow(workflow_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/live-handover/workflows/{workflow_id}/audit")
    async def get_live_handover_audit(workflow_id: str) -> dict[str, object]:
        try:
            return await api.get_live_handover_audit(workflow_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/ask-agent")
    async def ask_agent(body: AskAgentBody) -> dict[str, object]:
        try:
            return await api.ask_agent(
                question=body.question,
                source_driver_id=body.source_driver_id,
                source_session_ref=body.source_session_ref,
                source_label=body.source_label,
                interviewer_driver_id=body.interviewer_driver_id,
                interviewer_session_ref=body.interviewer_session_ref,
                interviewer_label=body.interviewer_label,
                max_interview_turns=body.max_interview_turns,
                max_total_wakes=body.max_total_wakes,
                max_invalid_outputs_per_agent=body.max_invalid_outputs_per_agent,
                max_runtime_minutes=body.max_runtime_minutes,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except DriverError as exc:
            raise HTTPException(status_code=502, detail=f"driver failed: {exc}") from exc

    @app.post("/api/agent-messages")
    async def send_agent_message(body: AgentMessageBody) -> dict[str, object]:
        try:
            return await api.send_agent_message(
                from_link_id=body.from_link_id,
                to_link_id=body.to_link_id,
                subject=body.subject,
                body=body.body,
                requires_ack=body.requires_ack,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/agent-messages")
    async def list_agent_messages(
        to_link_id: str | None = None,
        status: str | None = None,
        include_terminal: bool = True,
        limit: int = 50,
    ) -> dict[str, object]:
        try:
            return await api.list_agent_messages(
                to_link_id=to_link_id,
                status=status,
                include_terminal=include_terminal,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/agent-messages/{message_id}/read")
    async def mark_agent_message_read(message_id: str) -> dict[str, object]:
        try:
            return await api.mark_agent_message_read(message_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/agent-messages/{message_id}/ack")
    async def acknowledge_agent_message(message_id: str) -> dict[str, object]:
        try:
            return await api.acknowledge_agent_message(message_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/agent-links/{to_link_id}/process-inbox-once")
    async def process_agent_inbox_once(to_link_id: str) -> dict[str, object]:
        try:
            return await api.process_agent_inbox_once(to_link_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except DriverError as exc:
            raise HTTPException(status_code=502, detail=f"driver failed: {exc}") from exc

    # ---- Ops (admin agent) -----------------------------------------------

    MAX_OPS_TEXT = 32_000
    MAX_UPLOAD_BYTES = 25 * 1024 * 1024

    # 200K-context models only. The 1M Opus variant (`claude-opus-4-7[1m]`) is
    # REFUSED because mid-session switching from 1M to a 200K model produces
    # unpredictable truncation. Keeping all choices at a shared 200K ceiling
    # means the admin can swap cost tiers freely without memory surprises.
    # See: tech-library/claude-code/opus-1m-context-switching-pitfall.md
    ALLOWED_OPS_MODELS = {
        "claude-haiku-4-5": "MiniMax-M2.7",
        "claude-sonnet-4-6": "MiniMax-M2.7",
        "claude-opus-4-7": "MiniMax-M2.7-highspeed",
    }

    @app.get("/api/ops")
    async def ops_get() -> dict[str, object]:
        snap = await ops.snapshot()
        snap["model"] = ops_model_choice
        snap["backend_model"] = getattr(admin_driver, "model", None) or ALLOWED_OPS_MODELS[ops_model_choice]
        snap["allowed_models"] = list(ALLOWED_OPS_MODELS.keys())
        return snap

    @app.post("/api/ops/model")
    async def ops_set_model(body: OpsModelBody) -> dict[str, object]:
        nonlocal ops_model_choice
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
        ops_model_choice = choice
        await ops.reset_session()
        return {"ok": True, "model": choice, "backend_model": admin_driver.model}

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
    host = os.environ.get("AGORA_BIND_HOST", "127.0.0.1")
    port = int(os.environ.get("AGORA_PORT", "8890"))
    uvicorn.run("agora.gateway:app", host=host, port=port, reload=False)
