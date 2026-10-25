from __future__ import annotations

import asyncio
import locale
import logging
import os
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import settings
from database.session import init_db
from database import repo
from bot.handlers import router as bot_router
from services.scheduler import run_scheduler
from services.web_api import start_web_server
from services.site_sync import _scan_tasks
from services.listing_details import reconcile_inventory_details


def _configure_utf8_runtime() -> None:
    # Force UTF-8 I/O so logs and Telegram payloads keep emoji characters intact.
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    for loc in ("C.UTF-8", "en_US.UTF-8"):
        try:
            locale.setlocale(locale.LC_ALL, loc)
            break
        except locale.Error:
            continue


_configure_utf8_runtime()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("main")


async def main():
    init_db()
    merged = await asyncio.to_thread(repo.reconcile_duplicate_listings)
    if merged:
        log.info("Grouped %s duplicate property publications", merged)
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(bot_router)

    scheduler_task = asyncio.create_task(run_scheduler(bot), name="scheduler")
    web_runner = await start_web_server(bot)
    details_task=asyncio.create_task(reconcile_inventory_details(),name="property-verification")

    log.info("Bot is starting...")
    try:
        await dp.start_polling(bot)
    finally:
        if web_runner:
            await web_runner.cleanup()
        for task in _scan_tasks.values():
            task.cancel()
        scheduler_task.cancel()
        details_task.cancel()
        try:
            await details_task
        except asyncio.CancelledError:
            pass
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
