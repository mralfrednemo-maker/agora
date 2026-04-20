from __future__ import annotations

from typing import Any

from agora.engine.room import RoomEngine
from agora.ops import telegram_client, whatsapp_client
from agora.ops.tools import ToolRegistry, ToolSpec


def register_engine_tools(registry: ToolRegistry, engine: RoomEngine) -> None:
    async def _list_debates(_: dict[str, Any]) -> dict[str, Any]:
        rooms: list[dict[str, Any]] = []
        for rid, room in engine.rooms.items():
            if rid == "ops":
                continue
            rooms.append(
                {
                    "id": room.id,
                    "topic": room.topic,
                    "status": room.status,
                    "phase": room.current_phase().name,
                    "round": room.current_round,
                    "participants": [p.id for p in room.participants],
                    "style": getattr(room, "style", "ein-mdp"),
                }
            )
        return {"ok": True, "rooms": rooms}

    async def _get_debate(args: dict[str, Any]) -> dict[str, Any]:
        room_id = str(args.get("room_id", ""))
        if room_id not in engine.rooms:
            return {"ok": False, "error": "room not found"}
        snapshot = engine.room_snapshot(room_id)
        return {"ok": True, "room": snapshot}

    async def _create_debate(args: dict[str, Any]) -> dict[str, Any]:
        topic = str(args.get("topic", "")).strip()
        if not topic:
            return {"ok": False, "error": "topic required"}
        participants = args.get("participants") or ["claude-code-new-1", "codex-1"]
        if not isinstance(participants, list) or not participants:
            return {"ok": False, "error": "participants must be a non-empty list of driver ids"}
        style = str(args.get("style", "ein-mdp"))
        max_total_rounds = int(args.get("max_total_rounds", 5))
        auto_verdict = bool(args.get("auto_verdict", True))
        convergence = "terminate-majority" if style == "critic-terminate" else "agree-marker"
        try:
            room = await engine.create_room(
                topic=topic,
                convergence_name=convergence,
                max_total_rounds=max_total_rounds,
                style=style,
                auto_verdict=auto_verdict,
            )
            await engine.set_participants(room.id, list(participants))
            await engine.start(room.id)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "room_id": room.id}

    async def _pause_debate(args: dict[str, Any]) -> dict[str, Any]:
        rid = str(args.get("room_id", ""))
        if rid not in engine.rooms:
            return {"ok": False, "error": "room not found"}
        await engine.pause(rid)
        return {"ok": True}

    async def _resume_debate(args: dict[str, Any]) -> dict[str, Any]:
        rid = str(args.get("room_id", ""))
        if rid not in engine.rooms:
            return {"ok": False, "error": "room not found"}
        await engine.resume(rid)
        return {"ok": True}

    async def _stop_debate(args: dict[str, Any]) -> dict[str, Any]:
        rid = str(args.get("room_id", ""))
        if rid not in engine.rooms:
            return {"ok": False, "error": "room not found"}
        await engine.stop(rid)
        return {"ok": True}

    async def _inject_debate(args: dict[str, Any]) -> dict[str, Any]:
        rid = str(args.get("room_id", ""))
        text = str(args.get("text", "")).strip()
        if not rid or not text:
            return {"ok": False, "error": "room_id and text required"}
        if rid not in engine.rooms:
            return {"ok": False, "error": "room not found"}
        await engine.inject(rid, text)
        return {"ok": True}

    registry.register(
        ToolSpec(
            name="list_debates",
            description="List all active and completed debates in Agora",
            args_schema="{}",
            func=_list_debates,
        )
    )
    registry.register(
        ToolSpec(
            name="get_debate",
            description="Get full state and transcript of a specific debate",
            args_schema='{"room_id": "uuid"}',
            func=_get_debate,
        )
    )
    registry.register(
        ToolSpec(
            name="create_debate",
            description=(
                "Start a new debate. Defaults: participants=[claude-code-new-1, codex-1], "
                "style=ein-mdp, max_total_rounds=5. Valid styles: ein-mdp (cap 5), critic-terminate (cap 15)."
            ),
            args_schema=(
                '{"topic": "str", "participants": ["driver_id", ...] (optional), '
                '"style": "ein-mdp"|"critic-terminate" (optional), '
                '"max_total_rounds": int (optional)}'
            ),
            func=_create_debate,
        )
    )
    registry.register(
        ToolSpec(
            name="pause_debate",
            description="Pause an active debate (halts after current turn)",
            args_schema='{"room_id": "uuid"}',
            func=_pause_debate,
        )
    )
    registry.register(
        ToolSpec(
            name="resume_debate",
            description="Resume a paused debate",
            args_schema='{"room_id": "uuid"}',
            func=_resume_debate,
        )
    )
    registry.register(
        ToolSpec(
            name="stop_debate",
            description="Stop a debate permanently (cannot be resumed)",
            args_schema='{"room_id": "uuid"}',
            func=_stop_debate,
        )
    )
    registry.register(
        ToolSpec(
            name="inject_debate",
            description="Inject an instruction for the next round of a running debate",
            args_schema='{"room_id": "uuid", "text": "str"}',
            func=_inject_debate,
        )
    )


def register_telegram_tools(registry: ToolRegistry) -> None:
    async def _tg_send(args: dict[str, Any]) -> dict[str, Any]:
        text = str(args.get("text", "")).strip()
        if not text:
            return {"ok": False, "error": "text required"}
        try:
            result = await telegram_client.send(text)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, **({"message_id": result.get("message_id")} if isinstance(result, dict) else {})}

    async def _tg_recent(args: dict[str, Any]) -> dict[str, Any]:
        limit = int(args.get("limit", 10))
        try:
            messages = await telegram_client.list_recent(limit)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "messages": messages}

    registry.register(
        ToolSpec(
            name="tg_send",
            description="Send a Telegram message from the bot to Christo (the only allowed recipient)",
            args_schema='{"text": "str"}',
            func=_tg_send,
        )
    )
    registry.register(
        ToolSpec(
            name="tg_list_recent",
            description="Fetch recent Telegram messages (outbound + inbound) from the bot's ring buffer",
            args_schema='{"limit": int (optional, default 10)}',
            func=_tg_recent,
        )
    )


def register_whatsapp_tools(registry: ToolRegistry) -> None:
    async def _wa_send(args: dict[str, Any]) -> dict[str, Any]:
        contact = str(args.get("contact", "")).strip()
        text = str(args.get("text", "")).strip()
        if not contact or not text:
            return {"ok": False, "error": "contact and text required"}
        try:
            result = await whatsapp_client.send(contact, text)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, **({"message_id": result.get("message_id")} if isinstance(result, dict) else {})}

    async def _wa_contacts(_: dict[str, Any]) -> dict[str, Any]:
        try:
            contacts = await whatsapp_client.list_contacts()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "contacts": contacts}

    async def _wa_recent(args: dict[str, Any]) -> dict[str, Any]:
        limit = int(args.get("limit", 20))
        try:
            messages = await whatsapp_client.list_recent(limit)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "messages": messages}

    registry.register(
        ToolSpec(
            name="wa_send",
            description="Send a WhatsApp text message to a contact (phone number or JID)",
            args_schema='{"contact": "str", "text": "str"}',
            func=_wa_send,
        )
    )
    registry.register(
        ToolSpec(
            name="wa_list_contacts",
            description="List WhatsApp contacts discovered by the bridge (built from inbound messages)",
            args_schema="{}",
            func=_wa_contacts,
        )
    )
    registry.register(
        ToolSpec(
            name="wa_list_recent",
            description="Fetch recent WhatsApp messages (outbound + inbound) from the bridge",
            args_schema='{"limit": int (optional, default 20)}',
            func=_wa_recent,
        )
    )
