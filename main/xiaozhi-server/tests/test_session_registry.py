import os
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.utils.dialogue import Message


class TestSessionRegistry(unittest.TestCase):
    _registry_cls = None

    @classmethod
    def setUpClass(cls):
        mock_logger = MagicMock()
        with patch("config.logger.setup_logging", return_value=mock_logger):
            from core.agent import session_registry as sr_mod
            from core.agent.session import ChatSessionFactory

            cls._registry_cls = sr_mod.SessionRegistry
            cls._factory = ChatSessionFactory

    def test_get_or_create_reuses_session(self):
        registry = self._registry_cls({})
        s1 = registry.get_or_create(
            "xiaozhi:aa:bb:cc:dd:ee:ff",
            channel="xiaozhi",
            device_id="aa:bb:cc:dd:ee:ff",
        )
        s1.dialogue.put(Message(role="user", content="hello"))
        s2 = registry.get_or_create(
            "xiaozhi:aa:bb:cc:dd:ee:ff",
            channel="xiaozhi",
            device_id="aa:bb:cc:dd:ee:ff",
        )
        self.assertIs(s1, s2)
        self.assertEqual(len(s2.dialogue.dialogue), 1)

    def test_different_keys_isolated(self):
        registry = self._registry_cls({})
        a = registry.get_or_create(
            "xiaozhi:device-a", channel="xiaozhi", device_id="device-a"
        )
        b = registry.get_or_create(
            "xiaozhi:device-b", channel="xiaozhi", device_id="device-b"
        )
        a.dialogue.put(Message(role="user", content="a"))
        self.assertEqual(len(b.dialogue.dialogue), 0)

    def test_evict_idle(self):
        registry = self._registry_cls({})
        registry.get_or_create(
            "xiaozhi:old", channel="xiaozhi", device_id="old"
        )
        registry._last_active["xiaozhi:old"] = 0
        evicted = registry.evict_idle(max_age_seconds=1)
        self.assertEqual(evicted, 1)
        self.assertIsNone(registry.get("xiaozhi:old"))


if __name__ == "__main__":
    unittest.main()
