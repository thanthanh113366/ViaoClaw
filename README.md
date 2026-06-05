# ViaoClaw — Xiaozhi ESP32 Server

Backend voice AI cho **quản gia nhà thông minh ViaoClaw**, fork từ [xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server).

Repo GitHub của dự án gồm **hai thư mục chính**:

```
.
├── main/          # Mã nguồn: server, API quản trị, web/mobile
└── docs/          # Tài liệu triển khai, tích hợp, FAQ
```

---

## Cấu trúc `main/`

| Thư mục | Công nghệ | Vai trò |
|---------|-----------|---------|
| **`xiaozhi-server/`** | Python | Core AI: WebSocket, ASR, LLM, TTS, **ViaoClaw AgentRuntime**, plugins |
| `manager-api/` | Java (Spring Boot) | REST API quản trị, config DB |
| `manager-web/` | Vue.js | Dashboard web |
| `manager-mobile/` | uni-app + Vue 3 | App / mini-program quản trị |

Phần lớn tùy biến ViaoClaw nằm trong **`main/xiaozhi-server/`**.

---

## ViaoClaw — tính năng chính

| Module | Mô tả |
|--------|--------|
| **AgentRuntime** | `xiaoclaw.agent` — session voice + Telegram, ChatEngine, tool dispatch |
| **Home Assistant** | `hass_set_state`, `hass_get_state` — điều khiển / truy vấn thiết bị |
| **Cron** | Nhắc giờ, lịch lặp, chạy lệnh theo schedule |
| **Exec** | Sandbox shell trong workspace cấu hình |
| **Telegram** | Bot nhận tin + agent; plugin `telegram_send` |
| **Memory** | Stub `memory_read` / `memory_write` (plugin) |
| **Function call** | `Intent: function_call` — LLM gọi tool qua `UnifiedToolHandler` |

Config runtime: **`main/xiaozhi-server/data/.config.yaml`** (ưu tiên hơn `config.yaml`).

---

## Chạy nhanh — chỉ Server (Docker)

Phù hợp Raspberry Pi / dev local với **host network** (WS port `8000`).

```bash
cd main/xiaozhi-server

# Chuẩn bị (lần đầu)
mkdir -p data models/snakers4_silero-vad
cp config.yaml data/.config.yaml   # rồi sửa API key, HA, Telegram, …

# Chạy
docker compose up -d

# Kiểm tra
curl http://127.0.0.1:8000/
# WebSocket: ws://127.0.0.1:8000/xiaozhi/v1/
```

`docker-compose.yml` mount `./data`, `./core`, `./plugins_func` — sửa code plugin/core trên host rồi **restart container** để áp dụng.

Biến môi trường thường dùng:

```bash
export OPENAI_API_KEY=...    # nếu LLM/ASR dùng ${OPENAI_API_KEY} trong yaml
export TZ=Asia/Ho_Chi_Minh
```

---

## Cấu hình quan trọng

File: `main/xiaozhi-server/data/.config.yaml`

| Khối | Ý nghĩa |
|------|---------|
| `selected_module.ASR` | ASR (vd. `GroqASR`, `OpenaiASR`) |
| `selected_module.LLM` | LLM (vd. `OpenAILLM`, `ChatGLMLLM`, `GroqLLM`) |
| `selected_module.TTS` | TTS |
| `Intent.function_call.functions` | Danh sách plugin tool cho LLM |
| `plugins.home_assistant` | URL, API key, danh sách entity |
| `xiaoclaw` | Agent, Telegram, session TTL |
| `cron` / `exec` | Scheduler và sandbox lệnh |

**Không commit** file `.config.yaml` có API key thật — dùng `.gitignore` hoặc secret riêng.

---

## Client kết nối

| Client | Ghi chú |
|--------|---------|
| **ESP32** | Firmware [xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) — trỏ WS tới IP server |
| **py-xiaozhi** | Client Python (repo riêng) — cùng giao thức WebSocket |

Luồng voice: `hello` → `listen` → opus frames → ASR → LLM (+ function_call) → TTS.

---

## Tài liệu (`docs/`)

| Tài liệu | Nội dung |
|----------|----------|
| [Deployment.md](docs/Deployment.md) | Triển khai Docker (server / full stack) |
| [Deployment_all.md](docs/Deployment_all.md) | Server + API + Web |
| [homeassistant-integration.md](docs/homeassistant-integration.md) | Tích hợp Home Assistant |
| [FAQ.md](docs/FAQ.md) | Câu hỏi thường gặp |
| [docker-build.md](docs/docker-build.md) | Build image ARM64 |

Kiến trúc chi tiết: [main/README.md](main/README.md) (tiếng Trung, bản upstream).

---

## Benchmark (repo workspace)

Bộ đo chất lượng **B1–B7** (latency, ASR WER, function-call, cron, MQTT, exec, parity) nằm ở repo workspace `benchmark/` — không nằm trong folder này khi push GitHub. Xem hướng dẫn chạy trong repo gốc nếu có.

---

## Phát triển

```bash
cd main/xiaozhi-server

# Plugin mới
# → plugins_func/functions/<tên>.py
# → đăng ký trong data/.config.yaml → Intent.function_call.functions

docker restart xiaozhi-esp32-server
```

Benchmark nội bộ server (cron/exec/telegram): `main/xiaozhi-server/benchmarks/`.

---

## License

Dự án gốc: MIT — xem [LICENSE](LICENSE) (nếu có trong repo).  
ViaoClaw: fork/tùy biến trên codebase xiaozhi-esp32-server.
