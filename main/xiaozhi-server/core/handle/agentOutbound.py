import uuid

from config.logger import setup_logging
from core.providers.tts.dto.dto import ContentType, SentenceType, TTSMessageDTO

TAG = __name__
logger = setup_logging()


class VoiceOutbound:
    """Stream LLM output → conn.tts.tts_text_queue (FIRST/MIDDLE/LAST)."""

    def __init__(self, conn):
        self.conn = conn
        self._current_sentence_id: str | None = None

    def on_first(self, sentence_id: str) -> None:
        self._current_sentence_id = sentence_id
        self.conn.sentence_id = sentence_id
        self.conn.tts.tts_text_queue.put(
            TTSMessageDTO(
                sentence_id=sentence_id,
                sentence_type=SentenceType.FIRST,
                content_type=ContentType.ACTION,
            )
        )

    def on_chunk(self, sentence_id: str, text: str) -> None:
        if not text:
            return
        self.conn.tts.tts_text_queue.put(
            TTSMessageDTO(
                sentence_id=sentence_id,
                sentence_type=SentenceType.MIDDLE,
                content_type=ContentType.TEXT,
                content_detail=text,
            )
        )

    def on_last(self, sentence_id: str) -> None:
        self.conn.tts.tts_text_queue.put(
            TTSMessageDTO(
                sentence_id=sentence_id,
                sentence_type=SentenceType.LAST,
                content_type=ContentType.ACTION,
            )
        )

    def speak_full(self, text: str) -> None:
        sentence_id = str(uuid.uuid4().hex)
        self.on_first(sentence_id)
        self.on_chunk(sentence_id, text)
        self.on_last(sentence_id)
