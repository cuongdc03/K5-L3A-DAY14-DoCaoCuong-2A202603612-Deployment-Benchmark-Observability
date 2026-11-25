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

## Part 3: Cloud Deployment

_(Làm sau Part 6, theo CHECKPOINTS CP6.)_

---

## Part 4: API Security

### Exercise 4.1: API Key authentication

File: `04-api-gateway/develop/app.py`

- **API key được check ở đâu:** dependency `verify_api_key` (dòng 39–54). Header được đọc bằng `APIKeyHeader(name="X-API-Key", auto_error=False)` (dòng 36), rồi gắn vào endpoint bằng `Depends(verify_api_key)` (dòng 70). Chỉ `/ask` cần key; `/` và `/health` để public (platform cần gọi `/health` mà không có key).
- **Sai key thì sao:** thiếu header → **401** `Missing API key`; có header nhưng sai giá trị → **403** `Invalid API key.` Request bị chặn trước khi chạy vào thân hàm, nên LLM không bị gọi.
- **Rotate key thế nào:** key đọc từ env `AGENT_API_KEY` (dòng 35), không nằm trong code. Rotate = đặt giá trị mới trên platform rồi restart/redeploy, báo client dùng key mới. Muốn không downtime thì cho phép **2 key cùng lúc** trong thời gian chuyển (vd `AGENT_API_KEYS=new,old`), client chuyển xong thì bỏ key cũ.
- **Điểm yếu thấy được trong code:** default `"demo-key-change-in-production"` nếu quên đặt env thì app vẫn chạy với key ai cũng đoán được (nên fail fast); dòng 91 `print(f"API Key: {API_KEY}")` in key ra log; so sánh `!=` không phải constant-time (nên dùng `hmac.compare_digest`).

Chạy: `AGENT_API_KEY=secret-key-123 python app.py`

```text
$ curl -i http://localhost:8000/ask -X POST -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 401 Unauthorized
content-type: application/json

{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

$ curl -i http://localhost:8000/ask -X POST -H "X-API-Key: wrong-key" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 403 Forbidden
content-type: application/json

{"detail":"Invalid API key."}

$ curl -i http://localhost:8000/ask -X POST -H "X-API-Key: secret-key-123" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 200 OK
content-type: application/json

{"question":"Hello","answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}
```

### Exercise 4.2: JWT authentication

File: `04-api-gateway/production/auth.py`

**JWT flow:**

1. Client gửi `POST /auth/token` với `{"username","password"}`. `authenticate_user` (dòng 70–75) so với `DEMO_USERS`; sai → 401 `Invalid credentials`.
2. `create_token` (dòng 35–43) tạo payload `{sub, role, iat, exp = now + 60 phút}` và ký **HS256** bằng `JWT_SECRET` (đọc từ env, dòng 22).
3. Client gửi token ở header `Authorization: Bearer <token>`.
4. `verify_token` (dòng 46–67) kiểm chữ ký và hạn bằng `jwt.decode`: không có header → 401 (+ `WWW-Authenticate: Bearer`); hết hạn → 401 `Token expired`; chữ ký sai/token rác → 403 `Invalid token`. Hợp lệ thì trả `{username, role}` cho endpoint, **không cần tra DB** (stateless): bất kỳ instance nào có cùng `JWT_SECRET` đều verify được.

Payload chỉ được **ký, không mã hoá**: ai có token cũng đọc được nội dung (decode base64), nên không được để thông tin nhạy cảm trong token. Payload token của mình:

```text
{'sub': 'student', 'role': 'user', 'iat': 1795593103, 'exp': 1795596703}   # exp - iat = 3600 s
```

Test (token đã che):

```text
$ curl -i -X POST localhost:8000/token -d '{"username":"admin","password":"secret"}'     # endpoint/tài khoản trong CODE_LAB
HTTP/1.1 404 Not Found

$ curl -i -X POST localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username":"student","password":"wrong"}'
HTTP/1.1 401 Unauthorized
{"detail":"Invalid credentials"}

$ curl -i -X POST localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username":"student","password":"demo123"}'
HTTP/1.1 200 OK
x-content-type-options: nosniff
x-frame-options: DENY
x-xss-protection: 1; mode=block
referrer-policy: strict-origin-when-cross-origin

{"access_token":"eyJ***MASKED***","token_type":"bearer","expires_in_minutes":60,"hint":"Include in header: Authorization: Bearer eyJ***"}

$ curl -i -X POST localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Explain JWT"}'
HTTP/1.1 401 Unauthorized
www-authenticate: Bearer
{"detail":"Authentication required. Include: Authorization: Bearer <token>"}

$ curl -i -X POST localhost:8000/ask -H "Authorization: Bearer abc.def.ghi" -H "Content-Type: application/json" -d '{"question":"Explain JWT"}'
HTTP/1.1 403 Forbidden
{"detail":"Invalid token."}

$ curl -i -X POST localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question":""}'
HTTP/1.1 422 Unprocessable Entity      # AskRequest: min_length=1, max_length=1000
```

### Exercise 4.3: Rate limiting

File: `04-api-gateway/production/rate_limiter.py`

- **Algorithm:** **sliding window log** (docstring ghi "Sliding Window Counter" nhưng code lưu từng timestamp). Mỗi user có một `deque` timestamp; mỗi request bỏ các timestamp cũ hơn 60 s (dòng 39–40), nếu còn ≥ `max_requests` thì 429, không thì thêm `now` vào. Không bị hiện tượng "dồn 2× request ở ranh giới phút" như fixed window, đổi lại tốn bộ nhớ O(số request trong window) mỗi user.
- **Limit:** user thường **10 request/60 s** (`rate_limiter_user`, dòng 86); admin **100 request/60 s** (`rate_limiter_admin`, dòng 87). Tính theo `username` trong JWT, không theo IP.
- **Bypass cho admin:** code không bypass hẳn mà chọn limiter theo role ở `app.py:141` (`rate_limiter_admin if role == "admin" else rate_limiter_user`), tức admin có quota cao hơn. Đã test: `teacher` (role admin) gửi 20 request liên tục → **20 × 200**. Muốn bypass hoàn toàn thì bỏ qua `limiter.check()` khi `role == "admin"`; nhưng nên giữ một limit cao thay vì bỏ hẳn, để token admin bị lộ không thành công cụ đốt tiền.
- **Hạn chế:** state nằm trong RAM của process. Đã test: `student` đang bị 429, restart app → request tiếp theo trả **200** (bộ đếm mất). Chạy nhiều instance/worker thì mỗi process đếm riêng, limit thực tế nhân lên theo số process. Vì vậy Part 6 phải lưu state trong Redis.

Vòng lặp 20 request bằng token `student` (token đã che):

```bash
for i in $(seq 1 20); do
  printf "Request %2d: " $i
  curl -s -i -X POST localhost:8000/ask -H "Authorization: Bearer $TOKEN" \
       -H "Content-Type: application/json" -d "{\"question\": \"Test $i\"}" | head -1
done
```

```text
Request  1: HTTP/1.1 200 OK
Request  2: HTTP/1.1 200 OK
Request  3: HTTP/1.1 200 OK
Request  4: HTTP/1.1 200 OK
Request  5: HTTP/1.1 200 OK
Request  6: HTTP/1.1 200 OK
Request  7: HTTP/1.1 200 OK
Request  8: HTTP/1.1 200 OK
Request  9: HTTP/1.1 200 OK
Request 10: HTTP/1.1 200 OK
Request 11: HTTP/1.1 429 Too Many Requests
Request 12: HTTP/1.1 429 Too Many Requests
...
Request 20: HTTP/1.1 429 Too Many Requests
```

Response đầy đủ của request 10 (200) và 11 (429 đầu tiên):

```text
HTTP/1.1 200 OK
{"question":"Test 10","answer":"Đây là câu trả lời từ AI agent (mock). ...","usage":{"requests_remaining":0,"budget_remaining_usd":0.000188}}

HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
x-ratelimit-reset: 1795593165
retry-after: 59
content-type: application/json

{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":59}}
```

Ghi chú: field `budget_remaining_usd` trong response `/ask` (`app.py:160`) thực ra trả `usage.total_cost_usd`, tức **số đã tiêu**, không phải số còn lại. `/me/usage` mới trả đúng: `"cost_usd":0.000188,"budget_remaining_usd":0.999812`.

### Exercise 4.4: Cost guard

**Cost guard có sẵn (`04-api-gateway/production/cost_guard.py`):**

- Ước lượng chi phí từ số token: `input/1000 × $0.00015 + output/1000 × $0.0006` (giá GPT-4o-mini).
- Hai tầng: mỗi user **$1/ngày** (vượt → **402** Payment Required) và toàn hệ thống **$10/ngày** (vượt → **503** cho mọi người). Cảnh báo trong log khi user dùng tới 80 %.
- `check_budget` gọi **trước** khi gọi LLM, `record_usage` gọi **sau** khi có kết quả (số token thật).
- Reset theo ngày bằng cách so `record.day` với ngày hiện tại.
- Hạn chế: lưu trong dict RAM nên mất khi restart, không chia sẻ giữa instance; chỉ chặn khi **đã** vượt (`>=` budget), nên request cuối có thể làm vượt một chút.

**Rate limit vs cost guard, vì sao cần cả hai:** rate limit chặn **tốc độ** (số request/phút), bảo vệ server khỏi spam/DoS. Cost guard chặn **tổng tiền** theo ngày/tháng. Một user gửi đúng 10 request/phút nhưng mỗi request là prompt dài 1000 ký tự, chạy suốt 24 h, vẫn không bao giờ bị rate limit nhưng có thể đốt hết budget. Ngược lại, câu hỏi rẻ nhưng gửi dồn dập thì cost guard chưa chặn kịp mà server đã quá tải.

**Implementation `check_budget` của mình ($10/tháng, lưu Redis, reset đầu tháng):**

```python
import os
from datetime import datetime, timezone
import redis

r = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
MONTHLY_BUDGET_USD = float(os.getenv("MONTHLY_BUDGET_USD", "10"))
KEY_TTL_SECONDS = 32 * 24 * 3600

def _budget_key(user_id: str) -> str:
    month = datetime.now(timezone.utc).strftime("%Y-%m")   # tháng mới -> key mới -> tự reset
    return f"budget:{user_id}:{month}"

def check_budget(user_id: str, estimated_cost: float) -> bool:
    """True nếu còn budget (và đã trừ estimated_cost), False nếu vượt."""
    if estimated_cost < 0:
        raise ValueError("estimated_cost must be >= 0")
    key = _budget_key(user_id)
    new_total = r.incrbyfloat(key, estimated_cost)      # atomic trên Redis
    r.expire(key, KEY_TTL_SECONDS, nx=True)             # chỉ đặt TTL lần đầu
    if new_total > MONTHLY_BUDGET_USD:
        r.incrbyfloat(key, -estimated_cost)             # hoàn lại phần vừa giữ chỗ
        return False
    return True
```

Các quyết định thiết kế:

- **Key theo tháng** `budget:<user>:<YYYY-MM>` (giờ UTC): sang tháng mới tự dùng key mới, không cần cron reset. TTL 32 ngày để key cũ tự bị xoá.
- **Cộng trước, kiểm sau** bằng `INCRBYFLOAT` (atomic) thay vì `GET` rồi mới `INCRBYFLOAT` như lời giải mẫu. Với `GET` → so sánh → `INCR`, nhiều instance có thể cùng đọc `9.9` và cùng cho qua, làm vượt budget. Ở đây mỗi request "giữ chỗ" ngay; nếu vượt thì hoàn lại và trả `False`.
- State nằm trong Redis nên **dùng chung cho mọi instance**, còn sau khi restart.
- Trong app: `check_budget(...)` trả `False` → trả **402**.

Test với Redis thật (`docker run -p 6390:6379 redis:7-alpine`):

```text
1) 3 lần x $3: [True, True, True] spent = 9.0
2) thêm $2 (tổng 11 > 10): False spent vẫn = 9.0
3) thêm $1 (tổng đúng 10): True spent = 10.0
4) user khác độc lập: True bob spent = 5.0
   key = budget:alice:2026-11 TTL = 2764800 s
5) 200 request song song x $0.1: 100 ok, 100 bị chặn, spent = 10.0
```

Test 5 (50 thread, 200 request) cho đúng 100 request được qua và tổng dừng ở đúng $10.0: không bị vượt budget khi nhiều request đồng thời.

---

## Part 5: Scaling & Reliability

### Exercise 5.1: Health checks

File: `05-scaling-reliability/develop/app.py`

```python
@app.get("/health")            # dòng 104–144
def health():
    # LIVENESS: process còn sống và trả lời được là 200.
    # Không check Redis/DB ở đây: dependency chết mà /health fail thì platform
    # restart container vô ích (restart không sửa được Redis), còn gây restart dây chuyền.
    ...
    return {"status": overall_status, "uptime_seconds": uptime, "version": "1.0.0",
            "environment": ..., "timestamp": ..., "checks": {"memory": {...}}}

@app.get("/ready")             # dòng 147–168
def ready():
    # READINESS: chỉ 200 khi đã khởi động xong và chưa bắt đầu shutdown.
    if not _is_ready:
        raise HTTPException(status_code=503, detail="Agent not ready. Check back in a few seconds.")
    return {"ready": True, "in_flight_requests": _in_flight_requests}
```

Bản `production/app.py` (dòng 208–215) thêm điều kiện dependency cho readiness: `/ready` gọi `_redis.ping()`, lỗi → **503** `Redis not available`.

**Liveness vs readiness:**

| | `/health` (liveness) | `/ready` (readiness) |
|---|---|---|
| Câu hỏi | Process còn sống không? | Có nên gửi traffic vào không? |
| Fail thì platform làm gì | **Restart** container | **Ngừng route traffic** vào instance đó, không restart |
| Nên check gì | Rẻ, chỉ trong process (không gọi dependency) | Đã load xong, dependency (Redis/DB) kết nối được, chưa shutdown |
| Khi Redis chết | Vẫn **200** | **503** |

Test:

```text
$ curl -i localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":0.8,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T07:55:31.315091+00:00","checks":{"memory":{"status":"ok","used_percent":80.7}}}

$ curl -i localhost:8000/ready
HTTP/1.1 200 OK
{"ready":true,"in_flight_requests":1}
```

(`in_flight_requests` = 1 vì middleware đếm cả chính request `/ready`.)

### Exercise 5.2: Graceful shutdown

Cơ chế trong `develop/app.py`:

1. **Ngừng nhận request mới:** uvicorn nhận SIGTERM thì đóng socket đang lắng nghe; đồng thời lifespan đặt `_is_ready = False` để `/ready` trả 503 (load balancer bỏ instance khỏi pool).
2. **Hoàn thành request đang chạy:** uvicorn chờ các request dở dang xong (tối đa `timeout_graceful_shutdown=30` s); middleware `track_requests` đếm `_in_flight_requests`, lifespan chờ bộ đếm về 0.
3. **Đóng kết nối:** chỗ dọn dẹp là phần sau `yield` trong `lifespan` (đóng Redis/DB pool).
4. **Thoát:** process kết thúc với exit code 0.

Signal handler mình sẽ dùng (không gọi `sys.exit()` trong handler, mà để uvicorn chạy luồng shutdown):

```python
def shutdown_handler(signum, frame):
    logger.info(json.dumps({"event": "signal", "signum": signum}))
    global _is_ready
    _is_ready = False            # /ready -> 503 ngay, LB ngừng gửi request mới
    # uvicorn tự: đóng listener -> chờ in-flight -> chạy lifespan shutdown (đóng kết nối) -> exit 0
```

Test: gửi 2 request `/ask` (mock mất ~3 s) song song, gửi SIGTERM sau 0,5 s:

```bash
python app.py & PID=$!
curl -s -X POST "http://localhost:8000/ask?question=Long%20task" &
curl -s -X POST "http://localhost:8000/ask?question=Long%20task" &
sleep 0.5; kill -TERM $PID
```

```text
SIGTERM sent at +0.5s
new request: connection refused          # request mới sau SIGTERM không vào được
req1: HTTP 200 sau 6.079419s
req2: HTTP 200 sau 6.079632s
process exit code: 0
```

Log server:

```text
INFO:     Shutting down
INFO:     127.0.0.1:61413 - "POST /ask?question=Long%20task HTTP/1.1" 200 OK
INFO:     127.0.0.1:61414 - "POST /ask?question=Long%20task HTTP/1.1" 200 OK
INFO:     Waiting for application shutdown.
2026-11-25 14:55:37,563 INFO 🔄 Graceful shutdown initiated...
2026-11-25 14:55:37,563 INFO ✅ Shutdown complete
INFO:     Application shutdown complete.
INFO:     Finished server process [37894]
2026-11-25 14:55:37,563 INFO Received signal 15 — uvicorn will handle graceful shutdown
```

**Kết luận:** cả 2 request đang xử lý đều trả 200, request mới bị từ chối, process thoát với exit code 0, nên shutdown là graceful.

**Quan sát thêm:**

1. **2 request "song song" mất 6 s chứ không phải 3 s.** `ask_agent` là `async def` nhưng gọi `ask()` đồng bộ (`time.sleep(3)`), làm **block event loop**: request thứ hai phải chờ request thứ nhất. Sửa: khai báo endpoint là `def` (FastAPI chạy trong threadpool) hoặc `await asyncio.to_thread(ask, question)`. Với LLM thật nên dùng client async.
2. **`handle_sigterm` chạy cuối cùng**, sau `Finished server process`: `uvicorn.run()` thay signal handler bằng handler của nó và chỉ gọi lại handler cũ sau khi shutdown xong. Vì vậy logic quan trọng (đặt `_is_ready=False`, đóng kết nối) phải nằm trong `lifespan`, không nên đặt trong `signal.signal(...)`.
3. **Vòng chờ in-flight trong lifespan không bao giờ chạy** (log không có dòng `Waiting for ... in-flight requests`): uvicorn đã chờ request xong **trước khi** gọi lifespan shutdown. Vòng lặp đó lại dùng `time.sleep` trong hàm async, nếu chạy sẽ block event loop.
4. Trong thực tế listener đóng ngay khi nhận SIGTERM nên client nhận "connection refused" thay vì 503 từ `/ready`. Ở môi trường có load balancer, nên để `/ready` trả 503 một lúc **trước khi** đóng listener (pre-stop delay) để LB kịp gỡ instance.

### Exercise 5.3: Stateless design

Anti-pattern: lưu `conversation_history = {}` trong RAM của process. Khi chạy 3 instance sau load balancer, mỗi instance có RAM riêng: turn 1 lưu ở instance A, turn 2 vào instance B thì B không thấy history. Restart/redeploy cũng xoá sạch state. Thêm nữa, uvicorn `--workers 2` cũng là 2 process riêng, nên ngay cả 1 container cũng đã bị.

`production/app.py` đã làm stateless:

- Session lưu trong Redis với key `session:<session_id>`, giá trị JSON, **TTL 3600 s** (`save_session`, dòng 59–65 dùng `SETEX`), nên session bỏ dở tự hết hạn, không tràn bộ nhớ.
- `append_to_history` (dòng 76–90): load → append → giữ tối đa 20 message (10 turn) → save.
- Client giữ `session_id` và gửi lại mỗi request; bất kỳ instance nào cũng load được từ Redis. Response trả `served_by` để thấy instance nào đã phục vụ.
- Redis chạy với `maxmemory 128mb` + `allkeys-lru`, nên khi đầy Redis tự xoá key ít dùng thay vì lỗi.

Điểm yếu còn lại trong code mẫu (mình sẽ tránh ở Part 6):

1. **Fallback âm thầm sang dict trong RAM** khi không kết nối được Redis lúc khởi động (dòng 42–45): app vẫn chạy nhưng không còn stateless và không ai biết. Nên để `/ready` trả 503 thay vì fallback.
2. **Race condition read-modify-write** trong `append_to_history`: 2 request cùng session vào 2 instance cùng lúc thì `GET` → sửa → `SET` có thể ghi đè mất message của nhau. Nên dùng cấu trúc atomic của Redis: `RPUSH history:<sid> msg` + `LTRIM history:<sid> -20 -1` + `EXPIRE`.
3. Trường `turn` lệch 1 (tính sau khi đã append câu hỏi hiện tại rồi còn `+1`), và dừng ở 11 khi history đã đầy 20 message (xem test ở 5.4).

### Exercise 5.4: Load balancing

```bash
cd 05-scaling-reliability/production
touch .env.local
docker compose -p k5-day14-scaling up -d --build --scale agent=3
```

```text
NAME                       STATUS                    PORTS
k5-day14-scaling-agent-1   Up 24 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-2   Up 24 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-3   Up 24 seconds (healthy)   8000/tcp
k5-day14-scaling-nginx-1   Up 24 seconds             0.0.0.0:8080->80/tcp, [::]:8080->80/tcp
k5-day14-scaling-redis-1   Up 34 seconds (healthy)   6379/tcp
```

**Cách Nginx chia tải:** service `agent` scale 3 replica, DNS nội bộ của Docker trả 3 IP cho tên `agent`; `upstream agent_cluster { server agent:8000; }` nhận cả 3 và chia **round-robin**. Header `X-Served-By: $upstream_addr` cho thấy request đi vào container nào (vd `X-Served-By: 172.20.0.5:8000`).

10 request liên tiếp vào cùng một session (cột 1 là `turn`, cột 2 là `served_by`):

```text
3 instance-54d338
4 instance-f95d60
5 instance-6bc0c9
6 instance-54d338
7 instance-f95d60
8 instance-6bc0c9
9 instance-54d338
10 instance-f95d60
11 instance-6bc0c9
11 instance-54d338
```

Phân bố: `instance-54d338` 4, `instance-6bc0c9` 3, `instance-f95d60` 3, đúng vòng tròn A → B → C. Ánh xạ container: agent-1 = `6bc0c9`, agent-2 = `f95d60`, agent-3 = `54d338`.

**Khi một instance chết:** `docker stop k5-day14-scaling-agent-2` (gửi SIGTERM, log agent-2: `Shutting down` → `Instance instance-f95d60 shutting down` → `Finished server process [1]`), rồi gửi tiếp 10 request cùng session:

```text
200 11 instance-6bc0c9
200 11 instance-6bc0c9
200 11 instance-54d338
200 11 instance-6bc0c9
200 11 instance-54d338
200 11 instance-6bc0c9
200 11 instance-54d338
200 11 instance-6bc0c9
200 11 instance-54d338
200 11 instance-6bc0c9
```

Log Nginx:

```text
[error] 30#30: *28 upstream timed out (110: Operation timed out) while connecting to upstream, ... upstream: "http://172.20.0.4:8000/chat"
```

- **10/10 request trả 200**, không còn `instance-f95d60`. Request đầu tiên sau khi agent-2 chết bị Nginx kết nối thất bại tới `172.20.0.4`, nhưng nhờ `proxy_next_upstream error timeout http_503` + `proxy_next_upstream_tries 3`, Nginx **tự thử lại sang instance khác** nên client vẫn nhận 200. Sau đó Nginx đánh dấu upstream đó lỗi (mặc định `max_fails=1 fail_timeout=10s`) và chỉ chia giữa 2 instance còn lại.
- Session vẫn còn: `count = 20`, gồm `Request 11` … `Request 20`. Request 1–10 không mất vì instance chết mà do giới hạn 20 message (10 turn) của `append_to_history`; `turn` dừng ở 11 cũng vì vậy.
- `docker start` lại agent-2: container có **instance id mới** (`instance-8a1a95`), vì state trong RAM đã mất. Nhưng không ảnh hưởng gì vì session nằm trong Redis.
- Hạn chế: Nginx open-source chỉ phát hiện instance chết **khi có request thất bại** (passive health check), không chủ động gọi `/ready`. Upstream tĩnh `server agent:8000` chỉ resolve DNS lúc Nginx khởi động, nên scale thêm agent sau đó Nginx không biết (phải reload Nginx).

`docker compose -p k5-day14-scaling logs agent`, số request `POST /chat` theo container (gồm cả test 5.5 và phần thử failover):

```text
  12 agent-1
   4 agent-2
  10 agent-3
```

Ghi chú máy mình: ngoài stack Docker, trên máy còn một Nginx (Homebrew) nghe `*:8080` trên IPv4. `localhost` resolve `::1` trước nên request vào đúng stack Docker (header `X-Served-By`), còn `127.0.0.1:8080` lại vào Nginx Homebrew (404). Vì vậy mọi lệnh test dùng `localhost:8080`.

### Exercise 5.5: Test stateless

```text
$ python test_stateless.py
============================================================
Stateless Scaling Demo
============================================================

Session ID: 2a110be8-ef48-4f4c-9717-46f90eaf6ac1

Request 1: [instance-6bc0c9]
  Q: What is Docker?
  A: Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!...

Request 2: [instance-54d338]
  Q: Why do we need containers?
  A: Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ O...

Request 3: [instance-f95d60]
  Q: What is Kubernetes?
  A: Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận....

Request 4: [instance-6bc0c9]
  Q: How does load balancing work?
  A: Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé....

Request 5: [instance-54d338]
  Q: What is Redis used for?
  A: Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận....

------------------------------------------------------------
Total requests: 5
Instances used: {'instance-6bc0c9', 'instance-f95d60', 'instance-54d338'}
✅ All requests served despite different instances!

--- Conversation History ---
Total messages: 10
  [user]: What is Docker?...
  [assistant]: Container là cách đóng gói app để chạy ở mọi nơi. Build once...
  [user]: Why do we need containers?...
  [assistant]: Đây là câu trả lời từ AI agent (mock). Trong production, đây...
  [user]: What is Kubernetes?...
  [assistant]: Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã đư...
  [user]: How does load balancing work?...
  [assistant]: Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đ...
  [user]: What is Redis used for?...
  [assistant]: Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã đư...

✅ Session history preserved across all instances via Redis!
```

**Giải thích:** 5 câu trong cùng một `session_id` được Nginx chia vòng tròn qua **cả 3 instance** (6bc0c9 → 54d338 → f95d60 → 6bc0c9 → 54d338). Không instance nào giữ cả cuộc hội thoại trong RAM, vậy mà history đọc lại có **đủ 10 message (5 user + 5 assistant) đúng thứ tự**. Lý do: mỗi instance đọc và ghi session vào cùng một Redis, nên instance nào phục vụ cũng như nhau. Nếu lưu trong dict RAM, history mỗi lần đọc chỉ có phần do instance đó xử lý (khoảng 1–2 turn). Kết hợp với thử nghiệm tắt agent-2 ở 5.4: một instance chết hoặc restart không làm mất session.

Dọn stack: `docker compose -p k5-day14-scaling down`.

---

## AI log

| # | Hỏi AI gì | Nhận được gì | Tự sửa / tự kiểm tra thế nào |
|---|---|---|---|
| 1 | (Claude Code) Đọc các README, tóm tắt yêu cầu bài lab | Tóm tắt các checkpoint, rubric, cấu trúc nộp bài | Đối chiếu lại với `RUBRIC.md`, `SUBMISSION.md`, `CHECKPOINTS.md` |
| 2 | (Claude Code) Làm CP1: chạy bản develop và production của Part 1, soạn câu trả lời | Chạy hai app, lệnh curl, bản nháp phần Part 1 (anti-pattern, bảng so sánh, 3 lỗi phát hiện trong bản production) | Output curl và log ở trên là output thật từ máy mình; tự đọc lại `develop/app.py`, `production/app.py`, `config.py` để kiểm số dòng và giải thích |
| 3 | (Claude Code) Làm CP2: build 2 image, chạy container, bật stack Compose, soạn câu trả lời Part 2 | Lệnh build/run/test, số đo kích thước image, sơ đồ kiến trúc, giải thích multi-stage | Số MB lấy từ `docker images` / `docker image inspect` chạy trên máy mình; tự tính lại % chênh lệch; đọc lại `Dockerfile`, `docker-compose.yml`, `nginx.conf` để đối chiếu sơ đồ |
| 4 | (Claude Code) Làm CP3: chạy Part 4 develop/production, vòng lặp rate limit, viết `check_budget` dùng Redis | Output `curl -i` 401/403/200/429, giải thích JWT flow và sliding window, code cost guard Redis + script test | Output curl là từ máy mình (đã che token); tự chạy test cost guard với Redis thật (kể cả 200 request song song); tự kiểm lại vì sao dùng `INCRBYFLOAT` trước rồi hoàn lại thay vì `GET` rồi `INCR` |
| 5 | (Claude Code) Làm CP4: test health/ready, SIGTERM khi đang có request, stack 3 agent + Nginx, `test_stateless.py`, tắt 1 instance | Lệnh test, output, giải thích liveness/readiness, graceful shutdown, round-robin và failover; các quan sát (event loop bị block, thứ tự signal handler, race trong `append_to_history`) | Output là từ máy mình; tự đối chiếu `served_by` với log từng container để ánh xạ instance id ↔ container; tự kiểm lại vì sao 2 request mất 6 s |
