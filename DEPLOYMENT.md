# Deployment Information

## Public URL

https://day14-agent-xe55.onrender.com

## Platform

**Render** (Blueprint `06-lab-complete/render.yaml`), region Oregon, gói Free.

| Resource | Loại | Ghi chú |
|---|---|---|
| `day14-agent` | Web Service, runtime Docker | Build từ `06-lab-complete/Dockerfile` (multi-stage), health check `/health` |
| `day14-redis` | Key Value (Valkey 8, tương thích Redis) | Chỉ nhận kết nối nội bộ (`ipAllowList: []`), agent nối qua `REDIS_URL` |

Ghi chú: gói Free của Render cho web service "ngủ" sau ~15 phút không có request; request đầu tiên sau đó mất 30–60 s để khởi động lại. Gọi `/health` một lần trước khi test. Key Value gói Free không lưu dữ liệu xuống đĩa, nên bộ đếm rate limit / chi phí về 0 nếu Key Value restart.

Railway (nền tảng dự định ban đầu) không dùng được: cả hai tài khoản Railway đều bị chặn khi tạo service với thông báo *"We limited this workspace after detecting unusual activity. Upgrade to a paid plan to lift this restriction automatically."* `railway.toml` vẫn được giữ trong `06-lab-complete/`.

## Test Commands

Đặt biến (key lấy trên Render: `day14-agent` → Environment → `AGENT_API_KEY`; nhập ẩn, không in ra màn hình):

```bash
BASE=https://day14-agent-xe55.onrender.com
read -s AGENT_API_KEY      # zsh: read -s "AGENT_API_KEY?Key: "
```

### Health / readiness

```bash
curl "$BASE/health"
# {"status":"ok","version":"1.0.0","environment":"production","uptime_seconds":169.9}

curl "$BASE/ready"
# {"status":"ready"}     (503 nếu không kết nối được Redis)
```

### API test (authentication)

```bash
# Không có key → 401
curl -X POST "$BASE/ask" -H "Content-Type: application/json" -d '{"question": "Hello"}'
# {"detail":"Invalid or missing API key"}

# Có key → 200
curl -X POST "$BASE/ask" \
  -H "X-API-Key: $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "test", "question": "What is deployment?"}'
# {"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server để người khác dùng được.","usage":{"input_tokens":5,"output_tokens":20,"cost_usd":1.275e-05,"requests_remaining":8}}
```

### 4 lệnh tự kiểm (CP6) — chỉ in mã HTTP

```bash
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

Kết quả thực tế:

```text
200
200
401
200
```

### Rate limit trên cloud (`RATE_LIMIT_PER_MINUTE=10`)

```text
200 200 200 200 200 200 200 200 429 429 429
HTTP/2 429
retry-after: 53
{"detail":{"error":"Rate limit exceeded","limit_per_minute":10,"retry_after_seconds":53}}
```

### Script chấm với URL public

```bash
cd 06-lab-complete
python check_production_ready.py --public-url https://day14-agent-xe55.onrender.com
```

```text
     ✅ Public URL: GET https://day14-agent-xe55.onrender.com/health → 200
  Result: 12/12 tiêu chí đạt · 44/44 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
```

## Environment Variables Set

Đặt trên Render qua `render.yaml` (không có giá trị secret nào nằm trong code hay git):

| Biến | Giá trị / nguồn |
|---|---|
| `AGENT_API_KEY` | `generateValue: true` — Render tự sinh 256-bit ngẫu nhiên |
| `REDIS_URL` | `fromService` → `connectionString` của Key Value `day14-redis` (mạng nội bộ) |
| `ENVIRONMENT` | `production` (tắt `/docs`; thiếu `AGENT_API_KEY` thì app không khởi động) |
| `RATE_LIMIT_PER_MINUTE` | `10` |
| `DAILY_BUDGET_USD` | `1.0` |
| `MONTHLY_BUDGET_USD` | `10.0` |
| `PORT` | Render tự cấp; `CMD` trong Dockerfile lắng nghe `${PORT:-8000}` |

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)
