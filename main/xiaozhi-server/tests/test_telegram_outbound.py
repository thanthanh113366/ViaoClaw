import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestTelegramOutbound(unittest.TestCase):
    def test_split_respects_max_length(self):
        from core.agent.outbound import split_telegram_message

        text = "a" * 5000
        chunks = split_telegram_message(text, 4096)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(len(chunks[0]), 4096)
        self.assertEqual(len(chunks[1]), 904)

    def test_escape_html(self):
        from core.agent.outbound import escape_telegram_html

        self.assertEqual(escape_telegram_html("<b>&"), "&lt;b&gt;&amp;")

    def test_flush_sends_split_messages(self):
        from core.agent.outbound import TelegramOutbound

        bot = MagicMock()
        bot.send_message = AsyncMock()
        tg_cfg = {"max_message_length": 10, "parse_mode": "HTML"}
        outbound = TelegramOutbound(bot, 123, tg_cfg)
        outbound.on_first("s1")
        outbound.on_chunk("s1", "hello world!")
        outbound.on_last("s1")

        import asyncio

        asyncio.run(outbound.flush())
        self.assertEqual(bot.send_message.await_count, 2)


if __name__ == "__main__":
    unittest.main()
