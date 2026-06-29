import os
import uuid
from datetime import datetime
from config.logger import setup_logging
from core.providers.tts.base import TTSProviderBase

TAG = __name__
logger = setup_logging()


class TTSProvider(TTSProviderBase):
    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)
        self.audio_file_type = config.get("format", "wav")
        self.output_file = config.get("output_dir", "tmp/")
        self.mode = config.get("mode", "turbo")
        self.voice = config.get("voice", "Bích Ngọc (Nữ - Miền Bắc)")
        self.ref_audio = config.get("ref_audio", None)
        self._tts = None

    def _get_tts(self):
        if self._tts is None:
            from vieneu import Vieneu
            logger.bind(tag=TAG).info(f"Loading VieNeu-TTS mode={self.mode}")
            self._tts = Vieneu(mode=self.mode)
            logger.bind(tag=TAG).info("VieNeu-TTS loaded successfully")
        return self._tts

    def generate_filename(self, extension=".wav"):
        return os.path.join(
            self.output_file,
            f"tts-{datetime.now().date()}@{uuid.uuid4().hex}{extension}",
        )

    async def text_to_speak(self, text, output_file):
        try:
            tts = self._get_tts()

            kwargs = {"text": text}
            if self.ref_audio and os.path.exists(self.ref_audio):
                kwargs["ref_audio"] = self.ref_audio
            elif self.voice:
                kwargs["voice"] = self.voice

            audio = tts.infer(**kwargs)

            if output_file:
                os.makedirs(os.path.dirname(output_file), exist_ok=True)
                tts.save(audio, output_file)
            else:
                tmp_file = self.generate_filename()
                tts.save(audio, tmp_file)
                with open(tmp_file, "rb") as f:
                    audio_bytes = f.read()
                os.remove(tmp_file)
                return audio_bytes
        except Exception as e:
            error_msg = f"VieNeu-TTS请求失败: {e}"
            logger.bind(tag=TAG).error(error_msg)
            raise Exception(error_msg)
