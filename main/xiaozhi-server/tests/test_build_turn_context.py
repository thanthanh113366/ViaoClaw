import os
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestBuildTurnContext(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        mock_logger = MagicMock()
        with patch("config.logger.setup_logging", return_value=mock_logger):
            from core.agent import session as session_mod
            from core.agent.context import build_turn_context
            from core.utils.dialogue import Dialogue

            cls._session_mod = session_mod
            cls._build_turn_context = build_turn_context
            cls._Dialogue = Dialogue

    def test_build_turn_context_uses_session_key(self):
        from core.agent.context import build_turn_context

        conn = MagicMock()
        conn.config = {"xiaoclaw": {"agent": {"enabled": True}}}
        conn.dialogue = self._Dialogue()
        conn.llm = MagicMock()
        conn.memory = None
        conn.func_handler = None
        conn.intent_type = "nointent"
        conn.loop = MagicMock()
        conn.client_abort = False

        session = self._session_mod.ChatSession(
            "xiaozhi:aa:bb:cc:dd:ee:ff",
            channel="xiaozhi",
            device_id="aa:bb:cc:dd:ee:ff",
        )
        runtime = MagicMock()
        runtime.func_handler = MagicMock()
        runtime.conn_proxy = MagicMock()
        outbound = MagicMock()

        ctx = build_turn_context(
            session=session,
            outbound=outbound,
            runtime=runtime,
            conn=conn,
        )
        self.assertEqual(ctx.llm_session_id, "xiaozhi:aa:bb:cc:dd:ee:ff")
        self.assertIs(ctx.dialogue, session.dialogue)

    def test_build_turn_context_without_conn(self):
        from core.agent.context import build_turn_context

        session = self._session_mod.ChatSession(
            "telegram:123",
            channel="telegram",
            device_id="123",
            llm=MagicMock(),
            intent_type="function_call",
        )
        runtime = MagicMock()
        runtime.func_handler = MagicMock()
        runtime.conn_proxy = MagicMock()
        runtime.config = {"xiaoclaw": {"agent": {"enabled": True}}}
        runtime.memory = None
        runtime.intent_type = "function_call"
        runtime.loop = MagicMock()
        outbound = MagicMock()

        ctx = build_turn_context(
            session=session,
            outbound=outbound,
            runtime=runtime,
            conn=None,
        )
        self.assertEqual(ctx.llm_session_id, "telegram:123")
        self.assertIsNone(ctx.voice_conn)
        self.assertFalse(ctx.abort_check())


if __name__ == "__main__":
    unittest.main()
