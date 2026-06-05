# ViaoClaw Benchmark Suite

Bộ benchmark trong `benchmark/` — đo latency, ASR, function-call, cron, MQTT wake, exec policy và parity Telegram/Voice. Import module từ `main/xiaozhi-server/` nhưng **không sửa code production** trong quá trình chạy benchmark.

## Vị trí trong repo

```
xiaozhi-esp32-server/
├── benchmark/              ← bạn đang ở đây
│   ├── runner.py
│   ├── data/
│   └── results/            # git-ignored
└── main/
    └── xiaozhi-server/     # SERVER_ROOT — config & core server
        └── data/.config.yaml
```

Chạy mọi lệnh từ **root repo** `xiaozhi-esp32-server/` (thư mục cha của `benchmark/`).

## Cài đặt

```bash
cd xiaozhi-esp32-server

python3 -m venv .venv-benchmark
source .venv-benchmark/bin/activate
python -m pip install -r benchmark/requirements.txt
```

## Tổng quan benchmark

| ID | Tên | Mock | Live | Phụ thuộc chính |
|----|-----|------|------|-----------------|
| B1 | Voice latency (E2E) | WS feed audio | WS feed audio | Server chạy, WAV fixtures |
| B2 | ASR WER | `mock_transcript` | API transcribe | `LLM_API_KEY` / `BENCHMARK_ASR_*` |
| B3 | Function-call accuracy | Adapter replay | LLM thật | Docker hoặc `LLM_API_KEY` |
| B4 | Cron timing | `CronService` | `CronService` | Không |
| B5 | Offline MQTT wake | Simulated | MQTT broker | `MQTT_USERNAME` / `PASSWORD` |
| B6 | Exec guard | `ExecRunner` | `ExecRunner` | Không |
| B7 | Telegram ↔ Voice parity | Adapter replay | LLM thật | `LLM_API_KEY` |

**Phân tầng:**

- **B1 / B2** — đổi **ASR** (server config cho B1; env `BENCHMARK_ASR_*` cho B2 live).
- **B3 / B7** — đổi **LLM** (`selected_module.LLM` trong `data/.config.yaml` + env).
- **B4 / B5 / B6** — không phụ thuộc ASR/LLM.

## Chạy nhanh

```bash
source .venv-benchmark/bin/activate

# Mock (CI) — B2–B7 không cần API key; B1 vẫn cần server
python -m benchmark.runner --mode mock

# Một vài benchmark
python -m benchmark.runner --mode mock --only B2 B4 B6 B7
python -m benchmark.runner --mode mock --only B1   # cần server WS

# Live — toàn bộ
python -m benchmark.runner --mode live

# Live — từng nhóm
python -m benchmark.runner --mode live --only B1 B2
python -m benchmark.runner --mode live --only B3
python -m benchmark.runner --mode live --only B7
```

Report JSON: `benchmark/results/run_<timestamp>.json` (git-ignored).

---

## Preflight (live)

```bash
python -m benchmark.tools.check_live_prereqs
python benchmark/tools/check_asr_fixtures.py
```

| Dịch vụ | Mặc định | Ghi chú |
|---------|----------|---------|
| xiaozhi-server | `127.0.0.1:8000` | Docker `NetworkMode=host` |
| WebSocket | `ws://127.0.0.1:8000/xiaozhi/v1/` | B1, B3 docker |
| MQTT | `127.0.0.1:1883` | B5 live — cần auth |
| ASR fixtures | `benchmark/data/fixtures/asr/*.wav` | 30 file cho B1/B2 |

### Biến môi trường chung

```bash
export VIAOCLAW_WS_URL=ws://127.0.0.1:8000/xiaozhi/v1/
export VIAOCLAW_SERVER_URL=http://127.0.0.1:8000/

export LLM_API_KEY=...              # B2 live, B3 host, B7 live
export MQTT_HOST=127.0.0.1
export MQTT_PORT=1883
export MQTT_USERNAME=...
export MQTT_PASSWORD=...

export BENCHMARK_CONFIG_PATH=...    # tuỳ chọn — yaml runtime (mặc định: data/.config.yaml)
export BENCHMARK_LLM_MODEL=...      # ghi đè model LLM (B3 host, B7)
export BENCHMARK_LLM_BASE_URL=...   # ghi đè endpoint LLM
```

---

## B1 — Voice latency

Replay WAV qua WebSocket (opus): `hello` → `listen` → frames → `stop` → đo tới TTS đầu tiên.

**Cần:** server chạy, `websockets`, `opuslib-next`.

```bash
python -m benchmark.runner --mode live --only B1

# Một lần thử
python -m benchmark.tools.feed_voice_latency --wav benchmark/data/fixtures/asr/001.wav
```

Env tuỳ chọn: `BENCHMARK_DEVICE_ID`, `BENCHMARK_DEVICE_MAC`, `BENCHMARK_B1_TIMEOUT`, `VIAOCLAW_WS_TOKEN`.

ASR/LLM/TTS lấy từ `main/xiaozhi-server/data/.config.yaml` (container Docker).

---

## B2 — ASR WER

So sánh transcript với reference trong `benchmark/data/asr_testset.json`.

**Mock:** dùng `mock_transcript` — không gọi API.

**Live:** gọi API transcribe (mặc định OpenAI `gpt-4o-transcribe`; cấu hình qua env).

```bash
# OpenAI
export LLM_API_KEY=sk-...
python -m benchmark.runner --mode live --only B2

# Groq Whisper (khớp GroqASR trong server config)
export BENCHMARK_ASR_API_KEY=gsk_...
export BENCHMARK_ASR_BASE_URL=https://api.groq.com/openai/v1/audio/transcriptions
export BENCHMARK_ASR_MODEL=whisper-large-v3-turbo
export BENCHMARK_ASR_LANGUAGE=vi
# Groq: không dùng prompt EN; benchmark tự dùng prompt VI ngắn khi base_url chứa groq.com
# Tắt prompt: export BENCHMARK_ASR_PROMPT=

python -m benchmark.runner --mode live --only B2
python benchmark/tools/try_asr_sample.py --id asr_005
```

Override từng sample trong JSON: `asr_base_url`, `model_name`, `prompt`.

---

## B3 — Function-call accuracy

Fixture: `benchmark/data/funcall_scenarios.json` (25 scenario).

**Ngưỡng PASS:** `function_accuracy ≥ 90%`, `args_exact_accuracy ≥ 80%`.

### Docker-live (khuyến nghị trên Pi)

LLM chạy trong container — **không cần** `LLM_API_KEY` trên host.

1. Container `xiaozhi-esp32-server` đang chạy.
2. Sửa `selected_module.LLM` trong `data/.config.yaml` → restart container.
3. Tool call parse từ docker logs: `执行工具: …，参数: …`

```bash
export BENCHMARK_B3_TARGET=docker
export BENCHMARK_DOCKER_CONTAINER=xiaozhi-esp32-server
export VIAOCLAW_WS_URL=ws://127.0.0.1:8000/xiaozhi/v1/
export BENCHMARK_LIVE_MAX_SCENARIOS=25   # mặc định 5

python -m benchmark.tools.try_ws_funcall --scenario fc_001
python -m benchmark.runner --mode live --only B3
```

Env tuỳ chọn: `BENCHMARK_B3_TIMEOUT` (default 90s), `BENCHMARK_WS_CONN_READY_TIMEOUT` (30s), `BENCHMARK_WS_INIT_WAIT_SECONDS` (5s).

### Host-live

```bash
export LLM_API_KEY=...
python -m benchmark.runner --mode live --only B3
# hoặc: export BENCHMARK_B3_TARGET=host
```

Cần import `core.*` trên host (`benchmark/requirements.txt`).

---

## B4 — Cron timing

```bash
python -m benchmark.runner --mode mock --only B4
python -m benchmark.runner --mode live --only B4
```

Không cần server hay API key.

---

## B5 — Offline MQTT wake

```bash
export MQTT_USERNAME=...
export MQTT_PASSWORD=...

python -m benchmark.runner --mode live --only B5
python -m benchmark.runner --mode mock --only B5   # simulated
```

---

## B6 — Exec guard

```bash
python -m benchmark.runner --mode mock --only B6
python -m benchmark.runner --mode live --only B6
```

Fixture: `benchmark/data/exec_policy_testset.json`.

---

## B7 — Telegram ↔ Voice parity

5 case cố định: `fc_001`, `fc_006`, `fc_011`, `fc_016`, `fc_021`.

**Ngưỡng PASS:** parity 5/5.

**Live:** spawn `AgentRuntime` trên **host** — cần `LLM_API_KEY` + deps (`aiohttp`, …). **Không** dùng docker mode như B3.

```bash
export LLM_API_KEY=...

python -m benchmark.runner --mode mock --only B7
python -m benchmark.runner --mode live --only B7
```

Đọc `selected_module.LLM` từ `data/.config.yaml`. Override: `BENCHMARK_LLM_MODEL`, `BENCHMARK_LLM_BASE_URL`.

---

## Đổi LLM cho B3 / B7

1. Trong `main/xiaozhi-server/data/.config.yaml`:

```yaml
selected_module:
  LLM: GroqLLM    # hoặc OpenAILLM, ChatGLMLLM, …

LLM:
  GroqLLM:
    type: openai
    base_url: https://api.groq.com/openai/v1
    model_name: llama-3.1-8b-instant
    api_key: gsk_...
```

2. Restart Docker (cho B3 docker):

```bash
docker restart xiaozhi-esp32-server
```

3. Chạy:

```bash
# B3 docker — key trong yaml container
export BENCHMARK_B3_TARGET=docker
export VIAOCLAW_WS_URL=ws://127.0.0.1:8000/xiaozhi/v1/
python -m benchmark.runner --mode live --only B3

# B7 live — key trên host
export LLM_API_KEY=gsk_...
python -m benchmark.runner --mode live --only B7
```

---

## Biến môi trường đầy đủ

| Biến | Benchmark | Mô tả |
|------|-----------|-------|
| `VIAOCLAW_WS_URL` | B1, B3 docker | WebSocket server |
| `LLM_API_KEY` | B2, B3 host, B7 | API key LLM/ASR |
| `BENCHMARK_ASR_API_KEY` | B2 | Key ASR (fallback `LLM_API_KEY`) |
| `BENCHMARK_ASR_BASE_URL` | B2 | Endpoint transcribe |
| `BENCHMARK_ASR_MODEL` | B2 | Model ASR |
| `BENCHMARK_ASR_LANGUAGE` | B2 | Ngôn ngữ (default `vi`) |
| `BENCHMARK_ASR_PROMPT` | B2 | Prompt; `""` = tắt |
| `BENCHMARK_B3_TARGET` | B3 | `docker` hoặc `host` |
| `BENCHMARK_LIVE_MAX_SCENARIOS` | B3 | Cap số scenario (default 5) |
| `BENCHMARK_B3_TIMEOUT` | B3 | Timeout mỗi scenario (default 90) |
| `BENCHMARK_LLM_MODEL` | B3 host, B7 | Ghi đè model |
| `BENCHMARK_LLM_BASE_URL` | B3 host, B7 | Ghi đè endpoint |
| `MQTT_USERNAME` / `PASSWORD` | B5 | Auth mosquitto |

---

## Mở rộng fixture

- ASR: `benchmark/data/asr_testset.json` + WAV trong `benchmark/data/fixtures/asr/`
- Function-call: `benchmark/data/funcall_scenarios.json` (`expected_function`, `expected_args`, `mock_response`)
- Exec guard: `benchmark/data/exec_policy_testset.json` (`safe` / `dangerous`)

Mỗi module benchmark export `run(config) -> BenchmarkResult`.

---

## Kiểm tra / lint

```bash
python -m compileall benchmark
python -m ruff check benchmark
python -m benchmark.runner --mode mock
```
