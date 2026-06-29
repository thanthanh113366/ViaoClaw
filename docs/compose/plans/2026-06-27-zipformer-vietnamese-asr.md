# Zipformer Vietnamese ASR Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Zipformer Vietnamese ASR model support to ViaoClaw's existing `sherpa_onnx_local.py` adapter, enabling offline Vietnamese-optimized speech recognition as an alternative to cloud-based GroqASR.

**Architecture:** Extend the existing `sherpa_onnx_local.py` provider with a new `zipformer_vi` model type that loads Transducer (RNN-T) models from HuggingFace. The Zipformer models use a 3-file architecture (encoder/decoder/joiner ONNX) instead of the single-file SenseVoice/Paraformer approach. No new files needed — all changes are in existing files.

**Tech Stack:** sherpa-onnx, Python, HuggingFace Hub (model download), existing ViaoClaw ASR provider framework.

---

## File Structure

| Action | File | Purpose |
|--------|------|---------|
| Modify | `main/xiaozhi-server/core/providers/asr/sherpa_onnx_local.py` | Add `zipformer_vi` model type with Transducer loading |
| Modify | `main/xiaozhi-server/data/.config.yaml` | Add `SherpaVietASR` config block + optional `SherpaViet30MASR` |
| Create | `main/xiaozhi-server/models/zipformer-30m-rnnt-6000h/` | Downloaded 30M model files (auto-created by code) |
| Create | `main/xiaozhi-server/models/sherpa-onnx-zipformer-vi-2025-04-20/` | Downloaded 68M model files (auto-created by code) |

---

### Task 1: Add `zipformer_vi` model type to `sherpa_onnx_local.py`

**Covers:** Core ASR provider extension — the main implementation task.

**Files:**
- Modify: `main/xiaozhi-server/core/providers/asr/sherpa_onnx_local.py:37-97`

**Context:** The current code handles two model types:
- `sense_voice`: `OfflineRecognizer.from_sense_voice(model=..., tokens=...)`
- `paraformer`: `OfflineRecognizer.from_paraformer(paraformer=..., tokens=...)`

Zipformer Vietnamese uses a **3-file Transducer architecture**:
- `encoder-*.onnx` (encoder)
- `decoder-*.onnx` (decoder)
- `joiner-*.onnx` (joiner)
- `tokens.txt`

It requires `OfflineRecognizer.from_transducer(encoder=..., decoder=..., joiner=..., tokens=...)`.

- [ ] **Step 1: Read current file**

Read `main/xiaozhi-server/core/providers/asr/sherpa_onnx_local.py` to understand the current `__init__` method (lines 37-97).

- [ ] **Step 2: Add model file detection for zipformer_vi**

In `__init__`, after the existing `model_files` dict (line 50-53), add logic to detect model type and set up the correct file paths. Replace lines 50-73 with:

```python
        # 初始化模型文件路径
        if self.model_type == "zipformer_vi":
            # Zipformer Vietnamese uses 3-file Transducer architecture
            # Auto-detect ONNX filenames in model_dir (supports both int8 and fp32)
            import glob as _glob
            encoder_files = _glob.glob(os.path.join(self.model_dir, "encoder*.onnx"))
            decoder_files = _glob.glob(os.path.join(self.model_dir, "decoder*.onnx"))
            joiner_files = _glob.glob(os.path.join(self.model_dir, "joiner*.onnx"))
            tokens_path = os.path.join(self.model_dir, "tokens.txt")

            if not (encoder_files and decoder_files and joiner_files and os.path.exists(tokens_path)):
                # Auto-download from HuggingFace if files missing
                logger.bind(tag=TAG).info("Zipformer Vietnamese model files not found, downloading...")
                self._download_zipformer_vi_model()

                # Re-scan after download
                encoder_files = _glob.glob(os.path.join(self.model_dir, "encoder*.onnx"))
                decoder_files = _glob.glob(os.path.join(self.model_dir, "decoder*.onnx"))
                joiner_files = _glob.glob(os.path.join(self.model_dir, "joiner*.onnx"))

                if not (encoder_files and decoder_files and joiner_files and os.path.exists(tokens_path)):
                    raise FileNotFoundError(
                        f"Zipformer Vietnamese model files missing in {self.model_dir} after download. "
                        f"Expected: encoder*.onnx, decoder*.onnx, joiner*.onnx, tokens.txt"
                    )

            self.encoder_path = encoder_files[0]
            self.decoder_path = decoder_files[0]
            self.joiner_path = joiner_files[0]
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

            # 下载并检查模型文件
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
```

- [ ] **Step 3: Add the zipformer_vi branch in model initialization**

Replace lines 76-97 (the `with CaptureOutput():` block) with:

```python
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
```

- [ ] **Step 4: Add the `_download_zipformer_vi_model` method**

Add this method to the `ASRProvider` class (after `__init__`, before `read_wave`):

```python
    def _download_zipformer_vi_model(self):
        """Download Zipformer Vietnamese model from HuggingFace."""
        from huggingface_hub import hf_hub_download
        import json as _json

        # Determine repo based on model_dir name
        dir_name = os.path.basename(self.model_dir)
        if "30m" in dir_name.lower():
            repo_id = "hynt/Zipformer-30M-RNNT-6000h"
        else:
            repo_id = "csukuangfj/sherpa-onnx-zipformer-vi-2025-04-20"

        logger.bind(tag=TAG).info(f"Downloading Zipformer Vietnamese model from {repo_id}...")

        # List of files to download
        files_to_download = [
            "tokens.txt",
            "bpe.model",
        ]

        # Download all .onnx files from the repo
        # Use hf_hub_download to get each file
        try:
            from huggingface_hub import list_repo_files
            repo_files = list_repo_files(repo_id)
            onnx_files = [f for f in repo_files if f.endswith(".onnx")]
            files_to_download.extend(onnx_files)
        except Exception as e:
            logger.bind(tag=TAG).warning(f"Could not list repo files, using known filenames: {e}")
            # Fallback: try common filenames
            if "30m" in dir_name.lower():
                files_to_download.extend([
                    "encoder-epoch-20-avg-10.int8.onnx",
                    "decoder-epoch-20-avg-10.int8.onnx",
                    "joiner-epoch-20-avg-10.int8.onnx",
                ])
            else:
                files_to_download.extend([
                    "encoder-epoch-12-avg-8.onnx",
                    "decoder-epoch-12-avg-8.onnx",
                    "joiner-epoch-12-avg-8.onnx",
                ])

        os.makedirs(self.model_dir, exist_ok=True)

        for filename in files_to_download:
            target_path = os.path.join(self.model_dir, filename)
            if os.path.exists(target_path):
                continue
            try:
                logger.bind(tag=TAG).info(f"Downloading {filename}...")
                hf_hub_download(
                    repo_id=repo_id,
                    filename=filename,
                    local_dir=self.model_dir,
                )
                logger.bind(tag=TAG).info(f"Downloaded {filename}")
            except Exception as e:
                logger.bind(tag=TAG).error(f"Failed to download {filename}: {e}")
                raise
```

- [ ] **Step 5: Update the default model_type comment**

On line 43, update the comment to include zipformer_vi:

```python
        self.model_type = config.get("model_type", "sense_voice")  # 支持 paraformer, zipformer_vi
```

- [ ] **Step 6: Verify the file has no syntax errors**

Run: `python3 -c "import ast; ast.parse(open('main/xiaozhi-server/core/providers/asr/sherpa_onnx_local.py').read()); print('Syntax OK')"`

Expected: `Syntax OK`

- [ ] **Step 7: Commit**

```bash
git add main/xiaozhi-server/core/providers/asr/sherpa_onnx_local.py
git commit -m "feat(asr): add zipformer_vi model type to sherpa_onnx_local provider

Supports Vietnamese-optimized Zipformer RNN-T models (30M and 68M) from
HuggingFace. Auto-downloads model files on first use. Uses Transducer
architecture (encoder/decoder/joiner) instead of single-file SenseVoice."
```

---

### Task 2: Add SherpaVietASR config to `.config.yaml`

**Covers:** Configuration — enables the new ASR provider in ViaoClaw's config system.

**Files:**
- Modify: `main/xiaozhi-server/data/.config.yaml` (lines 471-484, after existing SherpaASR block)

- [ ] **Step 1: Read the ASR config section**

Read `main/xiaozhi-server/data/.config.yaml` lines 454-490 to find the existing `SherpaASR` and `SherpaParaformerASR` blocks.

- [ ] **Step 2: Add SherpaVietASR config block**

After the `SherpaParaformerASR` block (around line 484), add:

```yaml
  SherpaVietASR:
    # Zipformer Vietnamese 68M (chính xác hơn, ~270MB)
    # Tải model từ HuggingFace: csukuangfj/sherpa-onnx-zipformer-vi-2025-04-20
    # Chạy offline trên CPU, tối ưu cho tiếng Việt
    type: sherpa_onnx_local
    model_dir: models/sherpa-onnx-zipformer-vi-2025-04-20
    output_dir: tmp/
    model_type: zipformer_vi
  SherpaViet30MASR:
    # Zipformer Vietnamese 30M (nhanh hơn, ~30MB, chính xác hơn SenseVoice cho tiếng Việt)
    # Tải model từ HuggingFace: hynt/Zipformer-30M-RNNT-6000h
    type: sherpa_onnx_local
    model_dir: models/zipformer-30m-rnnt-6000h
    output_dir: tmp/
    model_type: zipformer_vi
```

- [ ] **Step 3: Verify YAML syntax**

Run: `python3 -c "import yaml; yaml.safe_load(open('main/xiaozhi-server/data/.config.yaml')); print('YAML OK')"`

Expected: `YAML OK`

- [ ] **Step 4: Commit**

```bash
git add main/xiaozhi-server/data/.config.yaml
git commit -m "config: add SherpaVietASR and SherpaViet30MASR provider configs

Two Vietnamese-optimized Zipformer models:
- SherpaVietASR: 68M params, ~270MB, higher accuracy
- SherpaViet30MASR: 30M params, ~30MB, faster inference
Both run offline on CPU with auto-download from HuggingFace."
```

---

### Task 3: Test the integration end-to-end

**Covers:** Verification — ensures the new model type loads and transcribes correctly.

**Files:**
- Test: inline script (not committed)

- [ ] **Step 1: Test model download and initialization**

Run from the `main/xiaozhi-server/` directory:

```bash
cd /home/mlops/ViaoClaw/main/xiaozhi-server && python3 -c "
import sys, os
sys.path.insert(0, '.')

# Test 1: Import the module
from core.providers.asr.sherpa_onnx_local import ASRProvider
print('✓ Import OK')

# Test 2: Initialize with 30M model (smaller, faster download)
config = {
    'model_dir': 'models/zipformer-30m-rnnt-6000h',
    'output_dir': 'tmp/',
    'model_type': 'zipformer_vi',
}
os.makedirs('tmp', exist_ok=True)
provider = ASRProvider(config, delete_audio_file=True)
print('✓ Provider initialized (30M model)')

# Test 3: Check model attributes
assert hasattr(provider, 'encoder_path'), 'Missing encoder_path'
assert hasattr(provider, 'decoder_path'), 'Missing decoder_path'
assert hasattr(provider, 'joiner_path'), 'Missing joiner_path'
print(f'✓ Model files: encoder={os.path.basename(provider.encoder_path)}, '
      f'decoder={os.path.basename(provider.decoder_path)}, '
      f'joiner={os.path.basename(provider.joiner_path)}')
print('All tests passed!')
"
```

Expected output:
```
✓ Import OK
✓ Provider initialized (30M model)
✓ Model files: encoder=encoder-epoch-20-avg-10.int8.onnx, decoder=decoder-epoch-20-avg-10.int8.onnx, joiner=joiner-epoch-20-avg-10.int8.onnx
All tests passed!
```

- [ ] **Step 2: Test actual transcription with a WAV file**

First, create a test WAV file with Vietnamese speech (or use any existing test file):

```bash
cd /home/mlops/ViaoClaw/main/xiaozhi-server && python3 -c "
import sys, os, asyncio, wave, struct, math
sys.path.insert(0, '.')

# Create a simple test WAV (1 second of silence - just to test the pipeline)
test_wav = 'tmp/test_silence.wav'
with wave.open(test_wav, 'wb') as f:
    f.setnchannels(1)
    f.setsampwidth(2)
    f.setframerate(16000)
    # 1 second of silence
    f.writeframes(b'\x00\x00' * 16000)

from core.providers.asr.sherpa_onnx_local import ASRProvider

config = {
    'model_dir': 'models/zipformer-30m-rnnt-6000h',
    'output_dir': 'tmp/',
    'model_type': 'zipformer_vi',
}
provider = ASRProvider(config, delete_audio_file=False)

# Test speech_to_text with the WAV file
import asyncio

async def test():
    # Create mock artifacts
    artifacts = provider.AudioArtifacts(
        pcm_frames=[b''],
        pcm_bytes=b'',
        file_path=test_wav,
        temp_path=None,
    )
    text, path = await provider.speech_to_text([], 'test-session', 'opus', artifacts)
    print(f'✓ Transcription result: \"{text}\"')
    print(f'✓ File path: {path}')
    return text

result = asyncio.run(test())
print('Transcription pipeline works!')
"
```

Expected: The script should complete without errors. The text may be empty (silence) or contain hallucinated text — both are acceptable for this test.

- [ ] **Step 3: Test with the 68M model (optional, larger download)**

```bash
cd /home/mlops/ViaoClaw/main/xiaozhi-server && python3 -c "
import sys, os
sys.path.insert(0, '.')
from core.providers.asr.sherpa_onnx_local import ASRProvider

config = {
    'model_dir': 'models/sherpa-onnx-zipformer-vi-2025-04-20',
    'output_dir': 'tmp/',
    'model_type': 'zipformer_vi',
}
provider = ASRProvider(config, delete_audio_file=True)
print('✓ 68M model initialized successfully')
print(f'  Encoder: {os.path.basename(provider.encoder_path)}')
print(f'  Decoder: {os.path.basename(provider.decoder_path)}')
print(f'  Joiner: {os.path.basename(provider.joiner_path)}')
"
```

Expected: `✓ 68M model initialized successfully` (downloads ~270MB on first run).

- [ ] **Step 4: Verify existing model types still work**

```bash
cd /home/mlops/ViaoClaw/main/xiaozhi-server && python3 -c "
import sys
sys.path.insert(0, '.')
from core.providers.asr.sherpa_onnx_local import ASRProvider

# Test that sense_voice still works (if model exists)
import os
if os.path.exists('models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/model.int8.onnx'):
    config = {
        'model_dir': 'models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17',
        'output_dir': 'tmp/',
        'model_type': 'sense_voice',
    }
    provider = ASRProvider(config, delete_audio_file=True)
    print('✓ sense_voice model still works')
else:
    print('⊘ sense_voice model not downloaded, skipping test')
print('✓ No regression in existing model types')
"
```

Expected: No errors, existing model types unaffected.

- [ ] **Step 5: Clean up test files**

```bash
rm -f /home/mlops/ViaoClaw/main/xiaozhi-server/tmp/test_silence.wav
```

---

### Task 4: Switch active ASR to SherpaViet30MASR (optional)

**Covers:** Activation — switch from GroqASR to local Vietnamese ASR.

**Files:**
- Modify: `main/xiaozhi-server/data/.config.yaml` (line 324)

**Note:** This is optional. Only do this if you want to use the local model instead of Groq cloud API.

- [ ] **Step 1: Change selected ASR module**

In `data/.config.yaml`, change line 324 from:

```yaml
  ASR: GroqASR
```

to:

```yaml
  ASR: SherpaViet30MASR
```

- [ ] **Step 2: Verify config is valid**

Run: `python3 -c "import yaml; yaml.safe_load(open('main/xiaozhi-server/data/.config.yaml')); print('YAML OK')"`

Expected: `YAML OK`

- [ ] **Step 3: Commit (only if activating)**

```bash
git add main/xiaozhi-server/data/.config.yaml
git commit -m "config: switch default ASR to SherpaViet30MASR (offline Vietnamese)

Replaces GroqASR (cloud API) with local Zipformer 30M model for
offline Vietnamese speech recognition. No API key required."
```

---

## Summary

| Task | Description | Files Changed | Est. Time |
|------|-------------|---------------|-----------|
| 1 | Add `zipformer_vi` model type to provider | `sherpa_onnx_local.py` | 10 min |
| 2 | Add config blocks for new ASR providers | `.config.yaml` | 2 min |
| 3 | Test integration end-to-end | (test scripts) | 10 min |
| 4 | Switch active ASR (optional) | `.config.yaml` | 1 min |

**Total estimated time:** ~25 minutes (without model download time)

**Model download sizes:**
- 30M model: ~30MB (fast)
- 68M model: ~270MB (first run only)

**Rollback:** Revert the two git commits. Change `ASR:` back to `GroqASR` in config if Task 4 was executed.
