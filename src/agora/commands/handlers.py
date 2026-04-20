from __future__ import annotations

from dataclasses import dataclass

from agora.commands.parser import ParsedCommand, parse_command
from agora.engine.room import RoomEngine


@dataclass(slots=True)
class CommandContext:
    active_room_id: str | None = None


@dataclass(slots=True)
class CommandHandler:
    engine: RoomEngine
    context: CommandContext

    async def handle(self, text: str, room_id: str | None = None) -> dict[str, object]:
        command = parse_command(text)
        selected_room = room_id or self.context.active_room_id

        if command.name == "new":
            room = await self.engine.create_room(topic=command.args[0])
            self.context.active_room_id = room.id
            return {"ok": True, "room_id": room.id}

        if command.name == "attach":
            self.context.active_room_id = command.args[0]
            return {"ok": True, "room_id": command.args[0]}

        if command.name == "list":
            return {"ok": True, "rooms": self.engine.list_rooms()}

        if command.name == "drivers":
            payload = [
                {
                    "id": driver.id,
                    "kind": driver.kind,
                    "token_ceiling": driver.token_ceiling,
                }
                for driver in self.engine.drivers.values()
            ]
            return {"ok": True, "drivers": payload}

        if selected_room is None:
            raise ValueError("No active room selected")

        if command.name == "participants":
            await self.engine.set_participants(selected_room, list(command.args))
            return {"ok": True}
        if command.name == "rounds":
            raw_value = command.args[0]
            extend = raw_value.startswith("+")
            value = int(raw_value[1:] if extend else raw_value)
            await self.engine.set_rounds(selected_room, value=value, extend=extend)
            return {"ok": True}
        if command.name == "start":
            await self.engine.start(selected_room)
            return {"ok": True}
        if command.name == "pause":
            await self.engine.pause(selected_room)
            return {"ok": True}
        if command.name == "resume":
            await self.engine.resume(selected_room)
            return {"ok": True}
        if command.name == "stop":
            await self.engine.stop(selected_room)
            return {"ok": True}
        if command.name == "inject":
            await self.engine.inject(selected_room, command.args[0])
            return {"ok": True}
        if command.name == "to":
            room = self.engine.rooms[selected_room]
            if room.status == "done":
                await self.engine.follow_up(selected_room, command.args[0], command.args[1])
            else:
                await self.engine.addressed_note(selected_room, command.args[0], command.args[1])
            return {"ok": True}
        if command.name == "phase":
            await self.engine.set_phase(selected_room, command.args[0])
            return {"ok": True}
        if command.name == "synthesize":
            model = command.args[0] if command.args else None
            text = await self.engine.synthesize(selected_room, model)
            return {"ok": True, "summary": text}

        raise ValueError(f"Unhandled command: {command.name}")
