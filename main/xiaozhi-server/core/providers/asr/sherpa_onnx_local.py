import time
import wave
import os
import sys
import io
import glob as _glob
from config.logger import setup_logging
from typing import Optional, Tuple, List
from core.providers.asr.dto.dto import InterfaceType
from core.providers.asr.base import ASRProviderBase

import numpy as np
import sherpa_onnx

from modelscope.hub.file_download import model_file_download

TAG = __name__
logger = setup_logging()


# 捕获标准输出
class CaptureOutput:
    def __enter__(self):
        self._output = io.StringIO()
        self._original_stdout = sys.stdout
        sys.stdout = self._output

    def __exit__(self, exc_type, exc_value, traceback):
        sys.stdout = self._original_stdout
        self.output = self._output.getvalue()
        self._output.close()

        # 将捕获到的内容通过 logger 输出
        if self.output:
            logger.bind(tag=TAG).info(self.output.strip())


class ASRProvider(ASRProviderBase):
    def __init__(self, config: dict, delete_audio_file: bool):
        super().__init__()
        self.interface_type = InterfaceType.LOCAL
        self.model_dir = config.get("model_dir")
        self.output_dir = config.get("output_dir")
        self.model_type = config.get("model_type", "sense_voice")  # 支持 paraformer, zipformer_vi
        self.delete_audio_file = delete_audio_file

        # 确保输出目录存在
        os.makedirs(self.output_dir, exist_ok=True)

        # 初始化模型文件路径
        if self.model_type == "zipformer_vi":
            # Zipformer Vietnamese uses 3-file Transducer architecture
            # Prefer int8 quantized files (smaller, faster) when available
            def _find_onnx(prefix):
                files = _glob.glob(os.path.join(self.model_dir, f"{prefix}*.onnx"))
                int8 = [f for f in files if ".int8." in f]
                return int8[0] if int8 else (files[0] if files else None)

            self.encoder_path = _find_onnx("encoder")
            self.decoder_path = _find_onnx("decoder")
            self.joiner_path = _find_onnx("joiner")
            tokens_path = os.path.join(self.model_dir, "tokens.txt")

            if not (self.encoder_path and self.decoder_path and self.joiner_path and os.path.exists(tokens_path)):
                logger.bind(tag=TAG).info("Zipformer Vietnamese model files not found, downloading...")
                self._download_zipformer_vi_model()

                self.encoder_path = _find_onnx("encoder")
                self.decoder_path = _find_onnx("decoder")
                self.joiner_path = _find_onnx("joiner")

                if not (self.encoder_path and self.decoder_path and self.joiner_path and os.path.exists(tokens_path)):
                    raise FileNotFoundError(
                        f"Zipformer Vietnamese model files missing in {self.model_dir} after download. "
                        f"Expected: encoder*.onnx, decoder*.onnx, joiner*.onnx, tokens.txt"
                    )
            self.tokens_path = tokens_path
            logger.bind(tag=TAG).info(
                f"Zipformer Vietnamese model: encoder={os.path.basename(self.encoder_path)}, "
                f"decoder={os.path.basename(self.decoder_path)}, "
                f"joiner={os.path.basename(self.joiner_path)}"
            )
        else:
            # SenseVoice or Paraformer — single model file
            model_files = {
                "model.int8.onnx": os.path.join(self.model_dir, "model.int8.onnx"),
                "tokens.txt": os.path.join(self.model_dir, "tokens.txt"),
            }

            try:
                for file_name, file_path in model_files.items():
                    if not os.path.isfile(file_path):
                        logger.bind(tag=TAG).info(f"正在下载模型文件: {file_name}")
                        model_file_download(
                            model_id="pengzhendong/sherpa-onnx-sense-voice-zh-en-ja-ko-yue",
                            file_path=file_name,
                            local_dir=self.model_dir,
                        )

                        if not os.path.isfile(file_path):
                            raise FileNotFoundError(f"模型文件下载失败: {file_path}")

                self.model_path = model_files["model.int8.onnx"]
                self.tokens_path = model_files["tokens.txt"]

            except Exception as e:
                logger.bind(tag=TAG).error(f"模型文件处理失败: {str(e)}")
                raise

        with CaptureOutput():
            if self.model_type == "zipformer_vi":
                self.model = sherpa_onnx.OfflineRecognizer.from_transducer(
                    encoder=self.encoder_path,
                    decoder=self.decoder_path,
                    joiner=self.joiner_path,
                    tokens=self.tokens_path,
                    num_threads=2,
                    sample_rate=16000,
                    feature_dim=80,
                    decoding_method="modified_beam_search",
                    max_active_paths=8,
                    debug=False,
                )
            elif self.model_type == "paraformer":
                self.model = sherpa_onnx.OfflineRecognizer.from_paraformer(
                    paraformer=self.model_path,
                    tokens=self.tokens_path,
                    num_threads=2,
                    sample_rate=16000,
                    feature_dim=80,
                    decoding_method="greedy_search",
                    debug=False,
                )
            else:  # sense_voice
                self.model = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                    model=self.model_path,
                    tokens=self.tokens_path,
                    num_threads=2,
                    sample_rate=16000,
                    feature_dim=80,
                    decoding_method="greedy_search",
                    debug=False,
                    use_itn=True,
                )

    def _download_zipformer_vi_model(self):
        """Download Zipformer Vietnamese model from HuggingFace."""
        import urllib.request
        import json

        dir_name = os.path.basename(self.model_dir)
        if "30m" in dir_name.lower():
            repo_id = "hynt/Zipformer-30M-RNNT-6000h"
        else:
            repo_id = "csukuangfj/sherpa-onnx-zipformer-vi-2025-04-20"

        logger.bind(tag=TAG).info(f"Downloading Zipformer Vietnamese model from {repo_id}...")

        # Get file list from HuggingFace API
        api_url = f"https://huggingface.co/api/models/{repo_id}"
        try:
            with urllib.request.urlopen(api_url, timeout=30) as resp:
                model_info = json.loads(resp.read())
            repo_files = [s["rfilename"] for s in model_info.get("siblings", [])]
            onnx_files = [f for f in repo_files if f.endswith(".onnx")]
            txt_files = [f for f in repo_files if f in ("tokens.txt", "bpe.model", "config.json")]
            files_to_download = txt_files + onnx_files
        except Exception as e:
            logger.bind(tag=TAG).warning(f"Could not list repo files, using known filenames: {e}")
            if "30m" in dir_name.lower():
                files_to_download = [
                    "tokens.txt", "config.json", "bpe.model",
                    "encoder-epoch-20-avg-10.int8.onnx",
                    "decoder-epoch-20-avg-10.int8.onnx",
                    "joiner-epoch-20-avg-10.int8.onnx",
                ]
            else:
                files_to_download = [
                    "tokens.txt", "config.json", "bpe.model",
                    "encoder-epoch-12-avg-8.onnx",
                    "decoder-epoch-12-avg-8.onnx",
                    "joiner-epoch-12-avg-8.onnx",
                ]

        os.makedirs(self.model_dir, exist_ok=True)

        for filename in files_to_download:
            target_path = os.path.join(self.model_dir, filename)
            if os.path.exists(target_path):
                continue
            download_url = f"https://huggingface.co/{repo_id}/resolve/main/{filename}"
            try:
                logger.bind(tag=TAG).info(f"Downloading {filename}...")
                urllib.request.urlretrieve(download_url, target_path)
                logger.bind(tag=TAG).info(f"Downloaded {filename}")
            except Exception as e:
                logger.bind(tag=TAG).error(f"Failed to download {filename}: {e}")
                raise

        # Some repos rename tokens.txt to config.json — fix that
        tokens_path = os.path.join(self.model_dir, "tokens.txt")
        config_json_path = os.path.join(self.model_dir, "config.json")
        if not os.path.exists(tokens_path) and os.path.exists(config_json_path):
            os.rename(config_json_path, tokens_path)
            logger.bind(tag=TAG).info("Renamed config.json -> tokens.txt")

    def read_wave(self, wave_filename: str) -> Tuple[np.ndarray, int]:
        """
        Args:
        wave_filename:
            Path to a wave file. It should be single channel and each sample should
            be 16-bit. Its sample rate does not need to be 16kHz.
        Returns:
        Return a tuple containing:
        - A 1-D array of dtype np.float32 containing the samples, which are
        normalized to the range [-1, 1].
        - sample rate of the wave file
        """

        with wave.open(wave_filename) as f:
            assert f.getnchannels() == 1, f.getnchannels()
            assert f.getsampwidth() == 2, f.getsampwidth()  # it is in bytes
            num_samples = f.getnframes()
            samples = f.readframes(num_samples)
            samples_int16 = np.frombuffer(samples, dtype=np.int16)
            samples_float32 = samples_int16.astype(np.float32)

            samples_float32 = samples_float32 / 32768
            return samples_float32, f.getframerate()

    def requires_file(self) -> bool:
        return True

    async def speech_to_text(
        self, opus_data: List[bytes], session_id: str, audio_format="opus", artifacts=None
    ) -> Tuple[Optional[str], Optional[str]]:
        """语音转文本主处理逻辑"""
        file_path = None
        try:
            if artifacts is None:
                return "", None
            file_path = artifacts.file_path

            start_time = time.time()
            s = self.model.create_stream()
            samples, sample_rate = self.read_wave(file_path)
            s.accept_waveform(sample_rate, samples)
            self.model.decode_stream(s)
            text = s.result.text
            logger.bind(tag=TAG).debug(
                f"语音识别耗时: {time.time() - start_time:.3f}s | 结果: {text}"
            )

            return text, file_path

        except Exception as e:
            logger.bind(tag=TAG).error(f"语音识别失败: {e}", exc_info=True)
            return "", file_path
