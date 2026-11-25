# Day 14 Lab - Mission Answers

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns found

File: `01-localhost-vs-production/develop/app.py`

| # | Anti-pattern | Dòng | Hậu quả |
|---|---|---|---|
| 1 | Hardcode secret: `OPENAI_API_KEY = "sk-..."` và `DATABASE_URL` có cả user/password | 17–18 | Push lên GitHub là lộ key ngay (bot quét key public chỉ trong vài phút); muốn đổi key phải sửa code và deploy lại |
| 2 | **Log ra secret**: `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")` | 34 | Key nằm trong log của platform, ai đọc được log là lấy được key |
| 3 | Config cứng trong code: `DEBUG = True`, `MAX_TOKENS = 500` | 21–22 | Không đổi được hành vi giữa dev/staging/prod mà không sửa code (vi phạm 12-factor *Config*) |
| 4 | Dùng `print()` thay vì logging có cấu trúc, có level | 33–38 | Không lọc theo level được, không parse/search được trong log aggregator; log toàn bộ câu hỏi và câu trả lời của user |
| 5 | Không có health check (`/health`, `/ready`) | 42–43 | Platform không biết agent còn sống hay đã treo để restart; load balancer không biết khi nào route traffic vào |
| 6 | Bind `host="localhost"` | 51 | Trong container chỉ nhận kết nối từ chính container, bên ngoài không gọi vào được |
| 7 | Port cứng `8000`, không đọc `PORT` | 52 | Railway/Render inject `PORT` qua env; port cứng thì health check của platform fail |
| 8 | `reload=True` luôn bật | 53 | Tốn CPU theo dõi file, process con khó tắt, không dành cho production |
| 9 | Không xử lý shutdown (SIGTERM), không có `lifespan` | — | Khi platform redeploy/scale down, request đang chạy có thể bị cắt ngang |
| 10 | Nhận `question` qua query string, không validate | 31 | Câu hỏi nằm trong URL nên bị ghi vào access log và proxy log; không giới hạn độ dài |

### Exercise 1.2: Chạy basic version

```bash
cd 01-localhost-vs-production/develop
python app.py
```

```text
$ curl -i -X POST "http://localhost:8000/ask?question=Hello"
HTTP/1.1 200 OK
{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}

$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 422 Unprocessable Entity
{"detail":[{"type":"missing","loc":["query","question"],"msg":"Field required","input":null}]}

$ curl -i http://localhost:8000/health
HTTP/1.1 404 Not Found
```

Log server (secret bị in ra):

```text
INFO:     Uvicorn running on http://localhost:8000 (Press CTRL+C to quit)
Starting agent on localhost:8000...
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this
[DEBUG] Response: Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận.
```

**Quan sát:** Agent chạy được và trả lời đúng, nhưng chưa sẵn sàng cho production: key lộ trong log, không có `/health` (404), chỉ nghe trên `localhost:8000`, và gửi JSON thì bị 422 vì `question` lấy từ query string.

### Exercise 1.3: Comparison table

Chạy bản production (`cp .env.example .env && python app.py`):

```text
$ curl -i localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":0.5,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T07:19:48.488190+00:00"}

$ curl localhost:8000/ready
{"ready":true}

$ curl -i -X POST localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 200 OK
{"question":"Hello","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận.","model":"gpt-4o-mini"}

$ curl -i -X POST localhost:8000/ask -H "Content-Type: application/json" -d '{}'
HTTP/1.1 422 Unprocessable Entity
{"detail":"question field is required"}

$ kill -TERM <pid>
INFO:     Shutting down
INFO:     Waiting for application shutdown.
INFO:     Application shutdown complete.
INFO:     Finished server process [22586]
```

| Feature | Develop | Production | Tại sao quan trọng? |
|---|---|---|---|
| Config | Hardcode trong code (`DEBUG = True`, key, DB URL) | `config.py` đọc mọi thứ từ env var, có default và `validate()` | Cùng một image/code chạy được ở dev, staging, prod chỉ bằng cách đổi env; thiếu `AGENT_API_KEY` khi `ENVIRONMENT=production` thì crash ngay lúc khởi động (fail fast) thay vì chạy với cấu hình sai |
| Secrets | `"sk-hardcoded..."` trong code, còn bị `print` ra log | `os.getenv("OPENAI_API_KEY")`, `.env` bị `.gitignore`, chỉ commit `.env.example`; log chỉ ghi độ dài câu hỏi | Secret không vào git history và log; đổi key (rotate) không cần sửa code |
| Host / Port | `localhost:8000` cứng | `HOST=0.0.0.0`, `PORT` từ env | `0.0.0.0` để nhận traffic từ ngoài container; platform cloud tự chọn và inject `PORT` |
| Health check | Không có (`/health` → 404) | `/health` (liveness) + `/ready` (readiness, 503 khi chưa sẵn sàng) | Liveness: platform biết process treo để restart. Readiness: load balancer chỉ gửi traffic khi instance đã khởi động xong, không gửi vào instance đang tắt |
| Logging | `print()`, không level, có cả secret | `logging` với format JSON, có level, không log secret | Log JSON parse được trong Loki/Datadog/CloudWatch, lọc theo `event`, level; không rò rỉ dữ liệu nhạy cảm |
| Shutdown | Tắt đột ngột, không có hook | `lifespan`: đặt `is_ready=False`, cho request đang chạy hoàn thành rồi mới thoát; SIGTERM → process thoát sạch (log ở trên) | Platform gửi SIGTERM mỗi lần redeploy/scale; graceful shutdown giúp không cắt ngang request của user, đóng kết nối DB/Redis gọn |
| Reload / Debug | `reload=True` luôn bật | `reload` chỉ khi `DEBUG=true` | Reload tốn tài nguyên và chỉ có ích khi đang code |
| Input | `question` qua query string, không kiểm tra | Body JSON, thiếu `question` → 422 có thông báo rõ | Không để dữ liệu user nằm trong URL/access log; trả lỗi rõ ràng cho client |
| CORS | Không cấu hình | `ALLOWED_ORIGINS` từ env | Chỉ frontend được phép mới gọi được từ trình duyệt |

**Nhận xét thêm khi chạy thử bản production** (bản "chuẩn" vẫn có lỗi nhỏ):

1. **Log JSON của app không hiện ra.** `config.py:45` gọi `logging.warning(...)` lúc import, trước khi `app.py:28` gọi `logging.basicConfig(...)`. `logging.warning` tự cấu hình root logger với level WARNING, nên `basicConfig` sau đó không có tác dụng và mọi `logger.info(json.dumps(...))` bị bỏ. Log thực tế chỉ có `WARNING:root:OPENAI_API_KEY not set — using mock LLM` và log của uvicorn. Sửa: gọi `basicConfig(..., force=True)` hoặc cấu hình logging trước khi import `config`.
2. **`.env` không được nạp.** `requirements.txt` có `python-dotenv` nhưng code không gọi `load_dotenv()`, nên giá trị trong `.env` chỉ có tác dụng nếu shell/platform tự export. Trên cloud không sao (platform inject env), nhưng chạy local thì `.env` bị bỏ qua.
3. **`handle_sigterm` không bao giờ được gọi.** `uvicorn.run()` cài signal handler riêng đè lên `signal.signal(SIGTERM, ...)` ở dòng 189. Shutdown vẫn graceful là nhờ uvicorn + `lifespan`, không phải nhờ hàm này.

### Checkpoint 1

- **Vì sao hardcode secret nguy hiểm:** secret nằm vĩnh viễn trong git history (xoá file vẫn còn trong commit cũ), ai có quyền đọc repo, image hoặc log đều lấy được; muốn đổi key phải sửa code và deploy lại.
- **Env var:** mọi thứ khác nhau giữa các môi trường (secret, URL, port, flag) đọc qua `os.getenv` và gom vào một chỗ (`config.py`); local dùng `.env` (không commit), cloud đặt trên dashboard của platform.
- **Health check:** endpoint rẻ, không phụ thuộc logic nghiệp vụ, để platform/load balancer hỏi định kỳ "còn sống không" (`/health`) và "nhận traffic được chưa" (`/ready`).
- **Graceful shutdown:** khi nhận SIGTERM thì ngừng nhận request mới, cho request đang chạy hoàn thành, đóng kết nối, rồi thoát với exit code 0.

---

## Part 2: Docker

### Exercise 2.1: Dockerfile questions

File: `02-docker/develop/Dockerfile`

1. **Base image:** `python:3.11` (dòng 8): bản Python đầy đủ trên Debian, có sẵn gcc, git, header để build… nên rất nặng (image cuối 1,67 GB).
2. **Working directory:** `/app` (dòng 11). Mọi lệnh `COPY`, `RUN`, `CMD` sau đó chạy tương đối với `/app` (kiểm tra bằng `docker exec ... pwd` → `/app`).
3. **Vì sao `COPY requirements.txt` trước `COPY app.py`:** Docker cache từng layer; một layer chỉ build lại khi input của nó hoặc layer trước nó thay đổi. Copy `requirements.txt` rồi `pip install` trước thì khi chỉ sửa code, layer `pip install` (bước tốn thời gian nhất) vẫn lấy từ cache, chỉ layer `COPY app.py` build lại. Nếu `COPY . .` trước thì sửa 1 dòng code cũng phải cài lại toàn bộ dependency.
4. **CMD vs ENTRYPOINT:** `ENTRYPOINT` là chương trình cố định của container; `CMD` là lệnh/tham số mặc định, bị thay thế khi truyền lệnh sau `docker run <image> ...`. Khi có cả hai, `CMD` thành tham số mặc định cho `ENTRYPOINT`. Ở đây chỉ có `CMD ["python", "app.py"]` nên `docker run my-agent:develop sh` sẽ chạy `sh` thay cho app. Dùng dạng exec (JSON array) để process là PID 1 và nhận được SIGTERM trực tiếp.

### Exercise 2.2: Build và run

```bash
# chạy từ gốc repo
docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .
docker run -d --name k5-p2-develop -p 8000:8000 my-agent:develop
```

```text
$ docker ps
NAMES           IMAGE              STATUS                  PORTS
k5-p2-develop   my-agent:develop   Up Less than a second   0.0.0.0:8000->8000/tcp, [::]:8000->8000/tcp

$ curl -i localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":0.5,"container":true}

$ curl -i -X POST "http://localhost:8000/ask?question=What%20is%20Docker%3F"
HTTP/1.1 200 OK
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}

$ docker exec k5-p2-develop sh -c 'whoami; pwd; ls; python --version'
root
/app
__pycache__  app.py  requirements.txt  utils
Python 3.11.18

$ docker logs k5-p2-develop
INFO:     Started server process [1]
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     192.168.65.1:55392 - "POST /ask?question=What%20is%20Docker%3F HTTP/1.1" 200 OK
```

Ghi chú: app develop của Part 2 nhận `question` qua query string nên dùng `?question=...` (gửi JSON như CODE_LAB sẽ bị 422). Container chạy với user `root` và uvicorn là PID 1.

### Exercise 2.3: Multi-stage build

File: `02-docker/production/Dockerfile`

- **Stage 1 (`builder`, dòng 16–29):** từ `python:3.11-slim`, cài `gcc`, `libpq-dev` (công cụ để compile package có phần C), rồi `pip install --user` toàn bộ requirements vào `/root/.local`. Stage này chỉ dùng để build, không được deploy.
- **Stage 2 (`runtime`, dòng 36–72):** bắt đầu lại từ `python:3.11-slim` sạch, tạo user non-root `appuser`, chỉ `COPY --from=builder /root/.local` (site-packages đã cài sẵn, 41,2 MB) cùng `main.py` và `utils/`. Thêm `USER appuser`, `HEALTHCHECK`, `CMD uvicorn ... --workers 2`.
- **Vì sao nhỏ hơn:**
  1. Base `slim` thay cho bản full (`python:3.11` có cả bộ build toolchain, man page, header…).
  2. `gcc`, `libpq-dev`, apt cache và pip chỉ nằm trong stage `builder`, bị bỏ lại; image cuối chỉ chứa các layer của stage cuối.
  3. `--no-cache-dir` nên không lưu file tải về của pip.

  Ngoài nhỏ hơn còn an toàn hơn: không có compiler trong image (ít công cụ cho attacker), chạy bằng `appuser` thay vì `root`.

```bash
docker build -f 02-docker/production/Dockerfile -t my-agent:advanced .
```

```text
$ docker images my-agent
IMAGE               ID             DISK USAGE   CONTENT SIZE
my-agent:advanced   7563657ac886        265MB         57.5MB
my-agent:develop    c3d1a93c795d       1.67GB          415MB

$ docker image inspect my-agent:develop my-agent:advanced --format '{{.RepoTags}} size={{.Size}} user={{.Config.User}} layers={{len .RootFS.Layers}}'
[my-agent:develop] size=1672161630 user= layers=13
[my-agent:advanced] size=265497332 user=appuser layers=11
```

**So sánh kích thước (đo trên máy mình, Docker 29.9.3):**

- Develop: **1,67 GB** (1 672 161 630 bytes)
- Production: **265 MB** (265 497 332 bytes)
- Chênh lệch: **giảm 84,1 %** (`(1672161630 − 265497332) / 1672161630`), nhỏ hơn khoảng 6,3 lần. Tính theo dung lượng nén (`CONTENT SIZE`, phần phải kéo về khi deploy): 415 MB → 57,5 MB, giảm 86,1 %.

`docker history my-agent:advanced` cho thấy phần thêm vào base chỉ khoảng 41 MB (layer `COPY /root/.local`); phần còn lại là `python:3.11-slim`.

### Exercise 2.4: Docker Compose stack

Chạy (từ `02-docker/production/`, sau `touch .env.local`):

```bash
docker compose -p k5-day14-docker up -d --build
```

**Architecture diagram:**

```text
                         máy host
                            │  :80 (và :443)
                            ▼
┌──────────────────── network "internal" (bridge) ────────────────────┐
│                                                                     │
│   ┌──────────────────────────┐                                      │
│   │ nginx (nginx:alpine)     │  reverse proxy, rate limit 10 r/s/IP │
│   │ cổng duy nhất mở ra host │  (burst 20), security headers        │
│   └────────────┬─────────────┘                                      │
│                │ proxy_pass http://agent:8000  (upstream agent_backend)
│                ▼                                                    │
│   ┌──────────────────────────┐                                      │
│   │ agent (build Dockerfile, │  uvicorn 2 workers, user appuser,    │
│   │ target: runtime) :8000   │  healthcheck GET /health             │
│   └──────┬──────────────┬────┘                                      │
│          │ REDIS_URL     │ QDRANT_URL                               │
│          ▼               ▼                                          │
│   ┌──────────────┐ ┌──────────────────┐                             │
│   │ redis :6379  │ │ qdrant :6333     │                             │
│   │ redis:7-alpine│ │ qdrant/qdrant    │                             │
│   │ vol redis_data│ │ vol qdrant_data  │                             │
│   └──────────────┘ └──────────────────┘                             │
└─────────────────────────────────────────────────────────────────────┘
```

**Services được start và cách chúng giao tiếp:**

- 4 service: `nginx`, `agent`, `redis`, `qdrant`, cùng một bridge network `internal`.
- Các container gọi nhau bằng **tên service** (DNS nội bộ của Docker): `agent` resolve `redis` → `172.20.0.3`, `qdrant` → `172.20.0.2`; Nginx gọi `agent:8000`.
- Chỉ **Nginx** publish cổng ra host (`80`, `443`). `agent`, `redis`, `qdrant` không mở cổng ra ngoài: gọi thẳng `localhost:8000` từ host thì không kết nối được.
- Thứ tự khởi động theo `depends_on`: `redis` và `qdrant` phải **healthy** (`condition: service_healthy`) thì `agent` mới start; `nginx` start sau `agent`.
- Dữ liệu Redis/Qdrant nằm trong named volume nên còn sau khi container bị xoá (trừ khi `down -v`).
- Ghi chú: `main.py` của bài này chưa thực sự dùng Redis/Qdrant (chỉ được truyền `REDIS_URL`, `QDRANT_URL`); stack minh hoạ cách nối dây giữa các service.

```text
$ docker compose -p k5-day14-docker ps
SERVICE   IMAGE                   STATUS                   PORTS
agent     k5-day14-docker-agent   Up 11 seconds (healthy)  8000/tcp
nginx     nginx:alpine            Up 11 seconds            0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
qdrant    qdrant/qdrant:v1.9.0    Up 21 seconds (healthy)  6333-6334/tcp
redis     redis:7-alpine          Up 21 seconds (healthy)  6379/tcp
```

**Test qua Nginx:**

```text
$ curl -i http://localhost/health
HTTP/1.1 200 OK
Server: nginx
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
X-XSS-Protection: 1; mode=block
{"status":"ok","uptime_seconds":10.0,"version":"2.0.0","timestamp":"2026-11-25T07:27:38.191079"}

$ curl -i http://localhost/ask -X POST -H "Content-Type: application/json" -d '{"question": "Explain microservices"}'
HTTP/1.1 200 OK
Server: nginx
{"answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}

$ curl -s -m 2 -o /dev/null -w '%{http_code}\n' localhost:8000/health   # agent không mở cổng ra host
000

$ seq 40 | xargs -P 40 -I{} curl -s -o /dev/null -w '%{http_code}\n' http://localhost/ | sort | uniq -c
  21 200
  19 429
```

Header `Server: nginx` và các security header chứng tỏ request đi qua Nginx. Với 40 request cùng lúc, Nginx cho qua 21 (1 + `burst=20`) và trả 429 cho 19 request còn lại, đúng cấu hình `limit_req rate=10r/s burst=20 nodelay`.

**Debug container:** `docker compose -p k5-day14-docker logs agent` để xem log, `docker compose -p k5-day14-docker exec agent sh` để vào shell (`whoami` → `appuser`). Dọn stack: `docker compose -p k5-day14-docker down`.

---

## AI log

| # | Hỏi AI gì | Nhận được gì | Tự sửa / tự kiểm tra thế nào |
|---|---|---|---|
| 1 | (Claude Code) Đọc các README, tóm tắt yêu cầu bài lab | Tóm tắt các checkpoint, rubric, cấu trúc nộp bài | Đối chiếu lại với `RUBRIC.md`, `SUBMISSION.md`, `CHECKPOINTS.md` |
| 2 | (Claude Code) Làm CP1: chạy bản develop và production của Part 1, soạn câu trả lời | Chạy hai app, lệnh curl, bản nháp phần Part 1 (anti-pattern, bảng so sánh, 3 lỗi phát hiện trong bản production) | Output curl và log ở trên là output thật từ máy mình; tự đọc lại `develop/app.py`, `production/app.py`, `config.py` để kiểm số dòng và giải thích |
| 3 | (Claude Code) Làm CP2: build 2 image, chạy container, bật stack Compose, soạn câu trả lời Part 2 | Lệnh build/run/test, số đo kích thước image, sơ đồ kiến trúc, giải thích multi-stage | Số MB lấy từ `docker images` / `docker image inspect` chạy trên máy mình; tự tính lại % chênh lệch; đọc lại `Dockerfile`, `docker-compose.yml`, `nginx.conf` để đối chiếu sơ đồ |
