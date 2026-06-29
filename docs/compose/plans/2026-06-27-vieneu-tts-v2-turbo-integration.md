# VieNeu-TTS v2 Turbo Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate VieNeu-TTS v2 Turbo (GGUF/ONNX) as a TTS provider into ViaoClaw, replacing EdgeTTS for offline Vietnamese TTS with higher quality and voice cloning support.

**Architecture:** Create a new TTS adapter `vieneu.py` following the existing adapter pattern (`TTSProviderBase`). The adapter uses the `vieneu` Python SDK (GGUF backend, CPU-only, no PyTorch). Configuration lives in `.config.yaml` under `TTS.VieNeuTTS`. Reference audio is pre-configured for consistent voice output. Docker container installs `vieneu` via pip in `docker-compose.yml` command.

**Tech Stack:** Python 3.10, `vieneu` SDK (pip), GGUF/llama-cpp-python backend, Xeon E5-2690 CPU

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `core/providers/tts/vieneu.py` | VieNeu-TTS v2 Turbo adapter |
| Modify | `docker-compose.yml:15` | Add `vieneu` to pip install in container command |
| Modify | `data/.config.yaml:888-894` | Add `VieNeuTTS` config block |
| Modify | `requirements.txt` | Add `vieneu` dependency |
| Create | `config/assets/vieneu_ref.wav` | Reference audio for voice cloning (3-5s) |

---

### Task 1: Install VieNeu SDK and verify CPU inference

**Covers:** Prerequisites

- [ ] **Step 1: Install vieneu SDK**

```bash
pip install vieneu
```

Expected: Installs `vieneu`, `llama-cpp-python`, and ONNX dependencies (~200MB total). No PyTorch required.

- [ ] **Step 2: Verify installation and test basic inference**

```python
python3 -c "
from vieneu import Vieneu
tts = Vieneu(mode='turbo')
print('VieNeu-TTS v2 Turbo loaded successfully')
print('Available voices:', tts.list_preset_voices())
# Quick test synthesis
audio = tts.infer('Xin chào, đây là bài test.')
tts.save(audio, '/tmp/vieneu_test.wav')
print('Test audio saved to /tmp/vieneu_test.wav')
"
```

Expected: Model loads, inference completes, WAV file created.

- [ ] **Step 3: Check file size and audio quality**

```bash
ls -lh /tmp/vieneu_test.wav
# Should be ~50-200KB for a short sentence
file /tmp/vieneu_test.wav
# Should show: RIFF (little-endian) data, WAVE audio
```

- [ ] **Step 4: Measure inference time on Xeon E5-2690**

```python
python3 -c "
from vieneu import Vieneu
from time import time

tts = Vieneu(mode='turbo')
text = 'Xin chào, tôi là trợ lý ảo. Hôm nay thời tiết rất đẹp, bạn có muốn đi dạo không?'

start = time()
audio = tts.infer(text)
elapsed = time() - start
print(f'Inference time: {elapsed:.2f}s')
print(f'Text length: {len(text)} chars')
print(f'Chars/sec: {len(text)/elapsed:.1f}')
"
```

Expected: Inference <2s for 50-char sentence on Xeon 8-core.

---

### Task 2: Create reference audio file for consistent voice

**Covers:** Voice consistency

- [ ] **Step 1: Generate reference audio using VieNeu SDK**

```python
python3 -c "
from vieneu import Vieneu
tts = Vieneu(mode='turbo')

# Generate a 3-5 second reference clip
# Use a natural Vietnamese sentence
ref_text = 'Xin chào, tôi là trợ lý ảo của bạn. Rất vui được gặp bạn.'
audio = tts.infer(ref_text)
tts.save(audio, 'config/assets/vieneu_ref.wav')
print('Reference audio created: config/assets/vieneu_ref.wav')
"
```

- [ ] **Step 2: Verify reference audio duration**

```python
python3 -c "
from pydub import AudioSegment
audio = AudioSegment.from_wav('config/assets/vieneu_ref.wav')
duration = len(audio) / 1000
print(f'Reference audio duration: {duration:.1f}s')
assert 3 <= duration <= 10, 'Reference should be 3-10 seconds'
"
```

Expected: Duration 3-10 seconds, WAV format.

---

### Task 3: Create the TTS adapter

**Covers:** Core adapter implementation

**Files:**
- Create: `core/providers/tts/vieneu.py`

- [ ] **Step 1: Create the adapter file**

```python
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
        self.emotion = config.get("emotion", "natural")
        self.ref_audio = config.get("ref_audio", "config/assets/vieneu_ref.wav")
        self.voice = config.get("voice", None)

        # Lazy-init: load model on first call to avoid startup delay
        self._tts = None

    def _get_tts(self):
        if self._tts is None:
            from vieneu import Vieneu
            logger.bind(tag=TAG).info(f"Loading VieNeu-TTS mode={self.mode}")
            self._tts = Vieneu(mode=self.mode, emotion=self.emotion)
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

            # Build inference kwargs
            kwargs = {"text": text}

            # Use preset voice or reference audio for cloning
            if self.voice:
                kwargs["voice"] = self.voice
            elif self.ref_audio and os.path.exists(self.ref_audio):
                kwargs["ref_audio"] = self.ref_audio

            audio = tts.infer(**kwargs)

            if output_file:
                os.makedirs(os.path.dirname(output_file), exist_ok=True)
                tts.save(audio, output_file)
            else:
                # Return raw bytes - save to temp then read
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
```

- [ ] **Step 2: Verify adapter follows base class interface**

Check that `text_to_speak(self, text, output_file)` signature matches `TTSProviderBase`:
- `text`: string to synthesize
- `output_file`: path to write audio, or `None` to return bytes
- Returns: bytes (when output_file is None) or writes to file

- [ ] **Step 3: Test adapter instantiation**

```python
cd main/xiaozhi-server
python3 -c "
import sys
sys.path.insert(0, '.')
from core.providers.tts.vieneu import TTSProvider

config = {
    'output_dir': 'tmp/',
    'mode': 'turbo',
    'emotion': 'natural',
    'ref_audio': 'config/assets/vieneu_ref.wav',
}
tts = TTSProvider(config, delete_audio_file=True)
print('Adapter instantiated successfully')
"
```

Expected: No import errors, adapter created.

---

### Task 4: Add configuration to .config.yaml

**Covers:** Configuration

**Files:**
- Modify: `main/xiaozhi-server/data/.config.yaml:888-894`

- [ ] **Step 1: Add VieNeuTTS config block**

Insert after the existing `EdgeTTS` block (after line 894):

```yaml
  VieNeuTTS:
    # VieNeu-TTS v2 Turbo - chạy offline trên CPU, không cần GPU
    # GitHub: https://github.com/pnnbao97/VieNeu-TTS
    type: vieneu
    mode: turbo
    emotion: natural
    # Đường dẫn file audio mẫu 3-5s để clone giọng
    ref_audio: config/assets/vieneu_ref.wav
    # Hoặc dùng voice preset có sẵn (bỏ qua ref_audio nếu set voice)
    # voice: "Bình"
    output_dir: tmp/
```

- [ ] **Step 2: Update selected_module to use VieNeuTTS**

Change line 331 from:
```yaml
  TTS: EdgeTTS
```
to:
```yaml
  TTS: VieNeuTTS
```

- [ ] **Step 3: Verify config is valid YAML**

```bash
cd main/xiaozhi-server
python3 -c "
import ruamel.yaml
yaml = ruamel.yaml.YAML()
with open('data/.config.yaml') as f:
    config = yaml.load(f)
tts_config = config['TTS']['VieNeuTTS']
print('VieNeuTTS config:', tts_config)
assert tts_config['type'] == 'vieneu'
assert tts_config['mode'] == 'turbo'
print('Config valid!')
"
```

---

### Task 5: Add vienteu to requirements.txt

**Covers:** Dependencies

**Files:**
- Modify: `main/xiaozhi-server/requirements.txt`

- [ ] **Step 1: Add vieneu dependency**

Add to `requirements.txt` (after line 16, near `edge_tts`):

```
vieneu>=1.0.0
```

- [ ] **Step 2: Verify no conflicting dependencies**

```bash
pip install vieneu --dry-run 2>&1 | head -20
```

Check that it doesn't conflict with existing `torch==2.2.2` or other pinned packages.

---

### Task 5b: Add vieneu to docker-compose.yml

**Covers:** Docker deployment

**Files:**
- Modify: `main/xiaozhi-server/docker-compose.yml:15`

- [ ] **Step 1: Add vieneu to the pip install command**

Current line 15:
```yaml
    command: sh -c "pip3 install -q apscheduler==3.10.4 paho-mqtt==2.1.0 'aiogram>=3,<4' 'aiohttp-socks>=0.8.4' && exec python app.py"
```

Change to:
```yaml
    command: sh -c "pip3 install -q apscheduler==3.10.4 paho-mqtt==2.1.0 'aiogram>=3,<4' 'aiohttp-socks>=0.8.4' vieneu && exec python app.py"
```

Note: `vieneu` is appended to the existing pip install list. No version pin to allow latest stable.

- [ ] **Step 2: Verify docker-compose syntax**

```bash
cd main/xiaozhi-server
docker-compose config --quiet
```

Expected: No errors.

- [ ] **Step 3: Test container startup with new dependency**

```bash
docker-compose up -d xiaozhi-esp32-server
docker-compose logs -f xiaozhi-esp32-server
```

Expected: Container starts, pip installs vieneu, server boots with "初始化组件: tts成功 VieNeuTTS".

---

### Task 6: End-to-end integration test

**Covers:** Verification

- [ ] **Step 1: Start the ViaoClaw server**

```bash
cd main/xiaozhi-server
python3 app.py
```

Expected: Server starts, logs show "初始化组件: tts成功 VieNeuTTS"

- [ ] **Step 2: Send a test TTS request via WebSocket**

Use the OTA interface or a test client to send a text message and verify audio response.

- [ ] **Step 3: Check logs for TTS generation**

```bash
grep -i "vieneu\|tts" tmp/server.log | tail -20
```

Expected: Logs show VieNeu-TTS loading and inference calls.

- [ ] **Step 4: Verify audio output format**

Check that generated audio files in `tmp/` are valid WAV/PCM and match the expected sample rate (24000 Hz for Opus encoding).

---

### Task 7: Performance validation

**Covers:** Performance

- [ ] **Step 1: Measure end-to-end TTS latency**

```python
import time
import sys
sys.path.insert(0, 'main/xiaozhi-server')

from core.providers.tts.vieneu import TTSProvider

config = {
    'output_dir': 'tmp/',
    'mode': 'turbo',
    'emotion': 'natural',
    'ref_audio': 'config/assets/vieneu_ref.wav',
}
tts = TTSProvider(config, delete_audio_file=False)

# Test various text lengths
test_cases = [
    "Xin chào",
    "Xin chào, hôm nay thời tiết thế nào?",
    "Đèn phòng ngủ đã được bật thành công. Bạn có muốn tắt đèn không?",
]

for text in test_cases:
    start = time.time()
    import asyncio
    result = asyncio.run(tts.text_to_speak(text, f"/tmp/test_{len(text)}.wav"))
    elapsed = time.time() - start
    print(f"Text ({len(text)} chars): {elapsed:.2f}s")

# Check memory usage
import psutil
process = psutil.Process()
print(f"Memory usage: {process.memory_info().rss / 1024 / 1024:.1f} MB")
```

- [ ] **Step 2: Compare with EdgeTTS latency (optional)**

Run same test with EdgeTTS config for comparison.

- [ ] **Step 3: Verify memory footprint**

```bash
ps aux | grep "python3 app.py" | awk '{print $6/1024 " MB RSS"}'
```

Expected: VieNeu-TTS adds ~300-500MB RSS vs EdgeTTS.

---

### Task 8: Fallback and cleanup

**Covers:** Reliability

- [ ] **Step 1: Verify EdgeTTS config is preserved**

Ensure `TTS.EdgeTTS` block remains in `.config.yaml` so users can switch back by changing `selected_module.TTS` to `EdgeTTS`.

- [ ] **Step 2: Test model caching**

Restart server and verify that the second load is faster (GGUF model cached in `~/.cache/huggingface/`).

- [ ] **Step 3: Commit all changes**

```bash
git add core/providers/tts/vieneu.py
git add data/.config.yaml
git add requirements.txt
git add config/assets/vieneu_ref.wav
git commit -m "feat: integrate VieNeu-TTS v2 Turbo as TTS provider

- Add vieneu adapter (core/providers/tts/vieneu.py)
- Add VieNeuTTS config block in .config.yaml
- Add vieneu to requirements.txt
- Add reference audio for voice consistency
- Default TTS switched from EdgeTTS to VieNeuTTS"
```
