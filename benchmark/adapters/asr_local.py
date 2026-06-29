"""Local Sherpa ASR adapter for B2 WER benchmark."""

from __future__ import annotations

import os
import sys
import wave
from pathlib import Path
from typing import Any

import numpy as np


def _init_sherpa_asr(model_dir: str, model_type: str = "zipformer_vi"):
    """Initialize Sherpa ONNX ASR model."""
    import sherpa_onnx

    if model_type == "zipformer_vi":
        import glob as _glob

        def _find_onnx(prefix):
            files = _glob.glob(os.path.join(model_dir, f"{prefix}*.onnx"))
            int8 = [f for f in files if ".int8." in f]
            return int8[0] if int8 else (files[0] if files else None)

        encoder_path = _find_onnx("encoder")
        decoder_path = _find_onnx("decoder")
        joiner_path = _find_onnx("joiner")
        tokens_path = os.path.join(model_dir, "tokens.txt")

        if not (encoder_path and decoder_path and joiner_path and os.path.exists(tokens_path)):
            raise FileNotFoundError(
                f"Zipformer model files missing in {model_dir}. "
                f"Expected: encoder*.onnx, decoder*.onnx, joiner*.onnx, tokens.txt"
            )

        return sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=encoder_path,
            decoder=decoder_path,
            joiner=joiner_path,
            tokens=tokens_path,
            num_threads=2,
            sample_rate=16000,
            feature_dim=80,
            decoding_method="modified_beam_search",
            max_active_paths=8,
            debug=False,
        )
    else:
        raise ValueError(f"Unsupported model_type: {model_type}")


def _read_wave(wave_filename: str):
    """Read WAV file and return (samples, sample_rate)."""
    with wave.open(wave_filename) as f:
        assert f.getnchannels() == 1, f"Expected mono WAV, got {f.getnchannels()} channels"
        assert f.getsampwidth() == 2, f"Expected 16-bit WAV, got {f.getsampwidth() * 8}-bit"
        num_samples = f.getnframes()
        samples = f.readframes(num_samples)
        samples_int16 = np.frombuffer(samples, dtype=np.int16)
        samples_float32 = samples_int16.astype(np.float32) / 32768
        return samples_float32, f.getframerate()


def transcribe_local_sherpa(
    audio_path: str | Path,
    *,
    model_dir: str,
    model_type: str = "zipformer_vi",
) -> str:
    """Transcribe a WAV file using local Sherpa ONNX ASR."""
    path = Path(audio_path)
    if not path.is_file():
        raise FileNotFoundError(f"WAV not found: {path}")

    recognizer = _init_sherpa_asr(model_dir, model_type)
    samples, sample_rate = _read_wave(str(path))

    stream = recognizer.create_stream()
    stream.accept_waveform(sample_rate, samples)
    recognizer.decode_stream(stream)
    return stream.result.text


def transcribe_local_sherpa_with_time(
    audio_path: str | Path,
    *,
    model_dir: str,
    model_type: str = "zipformer_vi",
) -> tuple[str, float]:
    """Transcribe and return (text, elapsed_seconds)."""
    import time

    start = time.time()
    text = transcribe_local_sherpa(audio_path, model_dir=model_dir, model_type=model_type)
    elapsed = time.time() - start
    return text, elapsed
