from __future__ import annotations

import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ["BOT_TOKEN"] = "test-token"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["ENABLE_GEOCODING"] = "false"
os.environ["AI_SCAM_ENABLED"] = "false"

from bot.handlers import cmd_ai_filter


class FakeMessage:
    def __init__(self):
        self.from_user = SimpleNamespace(id=42, username="free_user")
        self.answers: list[str] = []

    async def answer(self, text, **kwargs):
        self.answers.append(text)


class PremiumLimitTests(unittest.TestCase):
    def test_ai_filter_is_blocked_for_free_users(self):
        msg = FakeMessage()
        command = SimpleNamespace(args="Paris 11 1200 euros max")
        user = SimpleNamespace(id=1, telegram_id=42, username="free_user", is_premium=False)

        with patch("bot.handlers.repo.get_or_create_user", return_value=user), \
             patch("bot.handlers._panel", return_value=None), \
             patch("bot.handlers.ai_parse_filter") as ai_parse:
            asyncio.run(cmd_ai_filter(msg, command))

        ai_parse.assert_not_called()
        self.assertTrue(msg.answers)
        self.assertIn("réservé au PREMIUM", msg.answers[0])


if __name__ == "__main__":
    unittest.main()
