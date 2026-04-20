from __future__ import annotations

from typing import Any

import httpx

from agora.ops.config import WHATSAPP_BRIDGE_URL


async def send(contact: str, text: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{WHATSAPP_BRIDGE_URL}/send",
            json={"contact": contact, "text": text},
        )
        resp.raise_for_status()
        return resp.json()


async def list_contacts() -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{WHATSAPP_BRIDGE_URL}/contacts")
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []


async def list_recent(limit: int = 20) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{WHATSAPP_BRIDGE_URL}/recent", params={"limit": limit})
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []


async def health() -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{WHATSAPP_BRIDGE_URL}/health")
            if resp.status_code == 200:
                return resp.json()
            return {"ok": False, "connected": False}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "connected": False, "error": str(exc)}
