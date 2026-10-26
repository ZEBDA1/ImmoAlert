"""Refresh genuine provider usage off the request path, with bounded concurrency."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging

log=logging.getLogger(__name__)
_refresh_locks={}


def _age(value):
    if not value:
        return float("inf")
    try:
        return (datetime.now(timezone.utc)-datetime.fromisoformat(value.replace("Z","+00:00"))).total_seconds()
    except (ValueError,TypeError):
        return float("inf")


async def refresh_usage(provider, key_id):
    from services import provider_credentials as credentials
    lock=_refresh_locks.setdefault((provider,key_id),asyncio.Lock())
    async with lock:
        data=await asyncio.to_thread(credentials.list_providers)
        group=next((row for row in data["providers"] if row["id"]==provider),None)
        record=next((row for row in group["credentials"] if row["id"]==key_id),None) if group else None
        if record is None:
            raise ValueError("Clé introuvable.")
        if _age(record.get("checked_at"))<20:
            return record
        return await asyncio.to_thread(credentials.refresh_quota,provider,key_id)


async def monitor_quotas():
    from services import provider_credentials as credentials
    while True:
        try:
            data=await asyncio.to_thread(credentials.list_providers)
            for group in data["providers"]:
                if not group["enabled"]:
                    continue
                for record in group["credentials"]:
                    if record["enabled"] and _age(record.get("checked_at"))>=300:
                        try:
                            await refresh_usage(group["id"],record["id"])
                        except (ValueError,RuntimeError):
                            log.warning("Provider usage could not be refreshed (%s)",group["id"])
                        await asyncio.sleep(1)
        except Exception:
            # Never include provider responses or credential values in logs.
            log.warning("Provider quota monitor temporarily unavailable")
        await asyncio.sleep(60)
