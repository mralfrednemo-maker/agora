from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket


@dataclass(slots=True)
class WsHub:
    clients: set[WebSocket] = field(default_factory=set)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        text = json.dumps(payload)
        stale: list[WebSocket] = []
        for ws in self.clients:
            try:
                await ws.send_text(text)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.disconnect(ws)
