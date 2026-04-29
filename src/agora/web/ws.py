from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket


@dataclass(slots=True)
class WsHub:
    clients: set[WebSocket] = field(default_factory=set)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self.clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        # Synchronous discard is safe; broadcast copies the set under the lock.
        self.clients.discard(ws)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        text = json.dumps(payload)
        stale: list[WebSocket] = []
        async with self._lock:
            clients = list(self.clients)
        for ws in clients:
            try:
                await ws.send_text(text)
            except Exception:
                stale.append(ws)
        if stale:
            async with self._lock:
                for ws in stale:
                    self.clients.discard(ws)
