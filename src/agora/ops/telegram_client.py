from __future__ import annotations

from typing import Any

import httpx

from agora.ops.config import TELEGRAM_BRIDGE_URL


async def send(text: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(f"{TELEGRAM_BRIDGE_URL}/agora/send", json={"text": text})
        resp.raise_for_status()
        return resp.json()


async def list_recent(limit: int = 10) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(f"{TELEGRAM_BRIDGE_URL}/agora/recent", params={"limit": limit})
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []


async def health() -> bool:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{TELEGRAM_BRIDGE_URL}/agora/health")
            return resp.status_code == 200
    except Exception:  # noqa: BLE001
        return False
