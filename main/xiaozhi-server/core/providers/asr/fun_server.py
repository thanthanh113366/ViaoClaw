import ssl
import json
import uuid
import asyncio
import websockets
import opuslib_next

from config.logger import setup_logging
from core.providers.asr.base import ASRProviderBase
from core.providers.asr.utils import lang_tag_filter
from core.providers.asr.dto.dto import InterfaceType
from typing import Optional, Tuple, List, TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()


class ASRProvider(ASRProviderBase):
    def __init__(self, config: dict, delete_audio_file: bool):
        super().__init__()
        self.interface_type = InterfaceType.STREAM
        self.config = config
        self.text = ""
        self.decoder = opuslib_next.Decoder(16000, 1)
        self.asr_ws = None
        self.forward_task = None
        self.is_processing = False
        self._is_stopping = False
        self.server_ready = False

        self.host = config.get("host", "127.0.0.1")
        self.port = config.get("port", 10095)
        self.api_key = config.get("api_key", "none")
        self.is_ssl = str(config.get("is_ssl", False)).lower() in (
            "true",
            "1",
            "yes",
        )
        self.mode = config.get("mode", "2pass")
        self.chunk_size = config.get("chunk_size", [5, 10, 5])
        self.chunk_interval = int(config.get("chunk_interval", 10))
        self.itn = str(config.get("itn", False)).lower() in ("true", "1", "yes")
        self.output_dir = config.get("output_dir", "tmp/")
        self.delete_audio_file = delete_audio_file

        self.uri = (
            f"wss://{self.host}:{self.port}"
            if self.is_ssl
            else f"ws://{self.host}:{self.port}"
        )
        self.ssl_context = ssl.SSLContext() if self.is_ssl else None
        if self.ssl_context:
            self.ssl_context.check_hostname = False
            self.ssl_context.verify_mode = ssl.CERT_NONE

    def _auth_headers(self):
        if self.api_key and self.api_key != "none":
            return {"Authorization": "Bearer; {}".format(self.api_key)}
        return None

    def _build_start_message(self, session_id: str) -> str:
        return json.dumps(
            {
                "mode": self.mode,
                "chunk_size": self.chunk_size,
                "chunk_interval": self.chunk_interval,
                "wav_name": session_id,
                "is_speaking": True,
                "itn": self.itn,
                "audio_fs": 16000,
            },
            ensure_ascii=False,
        )

    async def open_audio_channels(self, conn):
        await super().open_audio_channels(conn)

    async def receive_audio(self, conn: "ConnectionHandler", audio, audio_have_voice):
        await super().receive_audio(conn, audio, audio_have_voice)

        if (
            audio_have_voice
            and not self.is_processing
            and not self.asr_ws
            and not self._is_stopping
        ):
            try:
                await self._start_recognition(conn)
            except Exception as e:
                logger.bind(tag=TAG).error(f"建立FunASR连接失败: {e}")
                await self._cleanup(conn)
                return

        if self.asr_ws and self.is_processing and self.server_ready and not self._is_stopping:
            try:
                pcm_frame = self.decoder.decode(audio, 960)
                await self.asr_ws.send(pcm_frame)
            except Exception as e:
                logger.bind(tag=TAG).warning(f"发送音频失败: {e}")
                await self._cleanup(conn)

    async def _start_recognition(self, conn: "ConnectionHandler"):
        headers = self._auth_headers()
        self.asr_ws = await websockets.connect(
            self.uri,
            additional_headers=headers,
            subprotocols=["binary"],
            ping_interval=None,
            ping_timeout=None,
            close_timeout=10,
            ssl=self.ssl_context,
            max_size=1000000000,
        )

        self.is_processing = True
        self.server_ready = False
        self.text = ""
        session_id = conn.session_id or uuid.uuid4().hex

        start_msg = self._build_start_message(session_id)
        await self.asr_ws.send(start_msg)
        logger.bind(tag=TAG).debug(f"已发送FunASR配置: {start_msg}")

        self.server_ready = True
        self.forward_task = asyncio.create_task(self._forward_asr_results(conn))

        if conn.asr_audio:
            for cached_audio in conn.asr_audio[-10:]:
                try:
                    pcm_frame = self.decoder.decode(cached_audio, 960)
                    await self.asr_ws.send(pcm_frame)
                except Exception as e:
                    logger.bind(tag=TAG).warning(f"发送缓存音频失败: {e}")
                    break

    async def _forward_asr_results(self, conn: "ConnectionHandler"):
        try:
            while self.asr_ws and not conn.stop_event.is_set():
                audio_data = conn.asr_audio
                try:
                    response = await self.asr_ws.recv()
                    if isinstance(response, bytes):
                        continue

                    result = json.loads(response)
                    logger.bind(tag=TAG).debug(f"收到FunASR结果: {result}")

                    mode = result.get("mode", "")
                    text = result.get("text", "") or ""
                    is_final = bool(result.get("is_final", False))

                    if text:
                        text = lang_tag_filter(text)

                    if mode == "2pass-online" or (
                        mode.endswith("-online") and not is_final
                    ):
                        if text and conn.client_listen_mode != "manual":
                            self.text = text
                        continue

                    if mode in ("2pass-offline", "offline") or is_final:
                        if not text:
                            if (
                                conn.client_listen_mode == "manual"
                                and conn.client_voice_stop
                                and len(audio_data) > 0
                            ):
                                await self.handle_voice_stop(conn, audio_data)
                            break

                        logger.bind(tag=TAG).info(f"识别到文本: {text}")

                        if conn.client_listen_mode == "manual":
                            if self.text:
                                self.text += text
                            else:
                                self.text = text
                            if conn.client_voice_stop and len(audio_data) > 0:
                                await self.handle_voice_stop(conn, audio_data)
                            break
                        else:
                            self.text = text
                            if len(audio_data) > 15:
                                await self.handle_voice_stop(conn, audio_data)
                            break

                except websockets.ConnectionClosed:
                    logger.bind(tag=TAG).info("FunASR WebSocket连接已关闭")
                    break
                except Exception as e:
                    logger.bind(tag=TAG).error(f"处理FunASR结果失败: {e}")
                    break
        except Exception as e:
            logger.bind(tag=TAG).error(f"FunASR结果转发失败: {e}")
        finally:
            await self._cleanup(conn)
            conn.reset_audio_states()

    async def _send_stop_request(self):
        self._is_stopping = True
        if self.asr_ws:
            try:
                stop_msg = json.dumps({"is_speaking": False}, ensure_ascii=False)
                await self.asr_ws.send(stop_msg)
                logger.bind(tag=TAG).debug(f"已发送停止消息: {stop_msg}")
            except Exception as e:
                logger.bind(tag=TAG).debug(f"发送停止消息失败: {e}")

    def stop_ws_connection(self):
        if self.asr_ws:
            asyncio.create_task(self.asr_ws.close())
            self.asr_ws = None
        self.is_processing = False
        self.server_ready = False
        self._is_stopping = False

    async def _cleanup(self, conn: "ConnectionHandler" = None):
        self.is_processing = False
        self.server_ready = False
        self._is_stopping = False

        if self.forward_task:
            self.forward_task.cancel()
            try:
                await self.forward_task
            except asyncio.CancelledError:
                pass
            self.forward_task = None

        if self.asr_ws:
            try:
                await asyncio.wait_for(self.asr_ws.close(), timeout=2.0)
            except Exception as e:
                logger.bind(tag=TAG).debug(f"关闭WebSocket失败: {e}")
            finally:
                self.asr_ws = None

    async def speech_to_text(
        self, opus_data: List[bytes], session_id: str, audio_format="opus", artifacts=None
    ) -> Tuple[Optional[str], Optional[str]]:
        result = self.text
        self.text = ""
        return result, None

    async def close(self):
        await self._cleanup()
        if hasattr(self, "decoder") and self.decoder is not None:
            try:
                del self.decoder
                self.decoder = None
                logger.bind(tag=TAG).debug("FunASR decoder resources released")
            except Exception as e:
                logger.bind(tag=TAG).debug(f"释放FunASR decoder资源时出错: {e}")
