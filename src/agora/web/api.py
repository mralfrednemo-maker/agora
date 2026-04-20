from __future__ import annotations

from dataclasses import dataclass

from agora.commands.handlers import CommandHandler
from agora.engine.room import RoomEngine


@dataclass(slots=True)
class ApiService:
    engine: RoomEngine
    commands: CommandHandler
    driver_health: dict[str, dict[str, object]]

    async def list_rooms(self) -> list[dict[str, object]]:
        return self.engine.list_rooms()

    async def get_room(self, room_id: str) -> dict[str, object]:
        return self.engine.room_snapshot(room_id)

    async def create_room(self, topic: str) -> dict[str, object]:
        room = await self.engine.create_room(topic=topic)
        self.commands.context.active_room_id = room.id
        return {"room_id": room.id}

    async def start_room(
        self,
        topic: str,
        participants: list[str],
        max_total_rounds: int,
        convergence: str,
        style: str,
        auto_verdict: bool,
    ) -> dict[str, object]:
        room = await self.engine.create_room(
            topic=topic,
            convergence_name=convergence,
            max_total_rounds=max_total_rounds,
            style=style,
            auto_verdict=auto_verdict,
        )
        await self.engine.set_participants(room.id, participants)
        await self.engine.start(room.id)
        self.commands.context.active_room_id = room.id
        return {"room_id": room.id}

    async def archive_room(self, room_id: str) -> dict[str, object]:
        await self.engine.archive_room(room_id)
        return {"ok": True}

    async def delete_room(self, room_id: str) -> dict[str, object]:
        await self.engine.delete_room(room_id)
        return {"ok": True}

    async def command(self, room_id: str, text: str) -> dict[str, object]:
        return await self.commands.handle(text=text, room_id=room_id)

    async def regenerate_verdict(self, room_id: str, participant_id: str | None) -> dict[str, object]:
        verdict, author = await self.engine.regenerate_verdict(room_id=room_id, participant_id=participant_id)
        return {"verdict": verdict, "author": author}

    async def follow_up(self, room_id: str, participant_id: str, text: str) -> dict[str, object]:
        message = await self.engine.follow_up(room_id=room_id, participant_id=participant_id, text=text)
        return {"message": message.to_dict()}

    async def list_drivers(self) -> dict[str, object]:
        rows = []
        for driver in self.engine.drivers.values():
            health = self.driver_health.get(driver.id, {"ok": True, "detail": "ok"})
            rows.append(
                {
                    "id": driver.id,
                    "kind": driver.kind,
                    "token_ceiling": driver.token_ceiling,
                    "health": health,
                }
            )
        return {"drivers": rows}
