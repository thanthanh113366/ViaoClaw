import os
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestAgentService(unittest.TestCase):
    _service = None

    @classmethod
    def setUpClass(cls):
        mock_logger = MagicMock()
        with patch("config.logger.setup_logging", return_value=mock_logger):
            import core.agent.service as agent_service

            cls._service = agent_service

    def test_legacy_bridge_enabled(self):
        cfg = {"xiaoclaw": {"enabled": True}}
        self.assertTrue(self._service.is_bridge_enabled(cfg))
        self.assertFalse(self._service.is_agent_enabled(cfg))

    def test_agent_bridge_xor(self):
        cfg = {
            "xiaoclaw": {
                "bridge": {"enabled": True},
                "agent": {"enabled": True},
            }
        }
        with self.assertRaises(ValueError):
            self._service.validate_xiaoclaw_config(cfg)

    def test_telegram_disabled_by_default(self):
        cfg = {"xiaoclaw": {"agent": {"enabled": True}}}
        self.assertFalse(self._service.is_telegram_enabled(cfg))

    def test_telegram_enabled_requires_agent_and_token(self):
        cfg = {
            "xiaoclaw": {
                "agent": {"enabled": False},
                "telegram": {"enabled": True, "bot_token": "tok"},
            }
        }
        with self.assertRaises(ValueError):
            self._service.validate_xiaoclaw_config(cfg)

        cfg = {
            "xiaoclaw": {
                "agent": {"enabled": True},
                "telegram": {"enabled": True, "bot_token": ""},
            }
        }
        with self.assertRaises(ValueError):
            self._service.validate_xiaoclaw_config(cfg)

        cfg = {
            "xiaoclaw": {
                "agent": {"enabled": True},
                "telegram": {"enabled": True, "bot_token": "123:ABC"},
            }
        }
        self._service.validate_xiaoclaw_config(cfg)
        self.assertTrue(self._service.is_telegram_enabled(cfg))


if __name__ == "__main__":
    unittest.main()
