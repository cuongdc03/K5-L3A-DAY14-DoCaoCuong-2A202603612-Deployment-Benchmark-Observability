# Benchmark & Observability Report — Day 14

**Họ tên:** Đỗ Cao Cường — 2A202603612
**Ngày chạy:** 2026-11-25
**Môi trường:** MacBook Apple M4 (10 core, 24 GB RAM), macOS 27.0.1, Python 3.11 (venv); Docker Desktop 6 CPU / 7.7 GB RAM. Agent và công cụ load test chạy **trên cùng một máy** (localhost).

Report gốc: `reports/*.json` (copy từ `06-lab-complete/benchmark-observability/develop/reports/`).

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < 500 ms | Mock LLM mất ~150–250 ms/call (120 ms + jitter 0–60 ms + retrieval ~20 ms), đo được p50 ≈ 190 ms. 500 ms cho ~2,5× dư địa để chịu tải và jitter mạng, nhưng vẫn là mức người dùng thấy "nhanh" với một câu trả lời ngắn. Đặt theo p95 (không theo avg) để 95% người dùng được đảm bảo. |
| Error rate `/ask` | < 2 % | Mock có `MOCK_ERROR_RATE=0.01` (1% lỗi upstream giống OpenAI/Anthropic 503). SLO 2% = error budget gấp đôi mức lỗi nền, đủ để không báo động vì nhiễu nhưng bắt được khi lỗi tăng thật (vd upstream sập một phần). |
| Accuracy trên golden dataset | ≥ 80 % (gate CI mặc định), mục tiêu 100 % sau khi sửa KB | 80% là ngưỡng `--min-accuracy` mặc định của quality gate: chặn bản tệ hẳn (v1 = 4,3%). Kèm điều kiện **không regression** so với baseline, nên dù accuracy vẫn ≥ 80% mà làm hỏng câu đang đúng thì vẫn bị chặn. Bản cuối được kiểm với `--min-accuracy 1.0`. |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.1 Concurrency vs latency (agent v2)

Lệnh: `python load_test.py --url http://localhost:8002 --requests 300 --concurrency <c> --out reports/load_v2_c<c>.json`

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 4.08 | 190.6 | 221.8 | 1696.5 | 0.00% | ✅ PASS |
| 10 | 38.39 | 186.4 | 217.8 | 1681.2 | 0.67% | ✅ PASS |
| 50 | 105.95 | 205.3 | 338.0 | 1693.7 | 1.67% | ✅ PASS |
| 100 | 53.14 | 391.6 | 3645.6 | 3963.6 | 0.00% | ❌ FAIL (p95) |

**Nhận xét:** RPS **gần tuyến tính ở mức thấp rồi bão hoà**:

- c=1 → c=10: RPS ×9,4 (4,08 → 38,39), p50 gần như không đổi (~190 ms). Mỗi request chủ yếu là **chờ** (`asyncio.sleep` mô phỏng LLM), không tốn CPU, nên một event loop phục vụ song song được nhiều request: theo Little's law RPS ≈ concurrency / latency ≈ 10 / 0,2 s ≈ 50 (đo được 38 vì có ~3% request chậm 1,7 s kéo dài thời gian chạy).
- c=10 → c=50: RPS chỉ ×2,8 (38 → 106) thay vì ×5, p95 bắt đầu tăng (218 → 338 ms): bắt đầu có hàng đợi.
- c=100: RPS **giảm** còn 53 và p95 vọt lên 3,6 s → vi phạm SLO. Agent chỉ có **1 process uvicorn / 1 event loop**, phải xử lý middleware, Prometheus metrics, ghi log JSON cho từng request; cùng lúc `load_test.py` (cũng là Python) chạy trên **cùng máy** tranh CPU. Khi vượt điểm bão hoà, request xếp hàng chờ event loop nên latency tăng mạnh mà throughput không tăng nữa. Mình chạy lại c=100 hai lần (không lưu vào report nộp) được 140,8 và 83,9 RPS với p95 ≈ 1,5–1,6 s: kết quả dao động lớn đúng kiểu hệ thống đã quá tải, và `netstat` không thấy tràn listen queue, nên nút thắt là CPU/event loop chứ không phải kết nối TCP.
- Kết luận: 1 instance chịu được khoảng **50 request đồng thời** trong SLO; nhiều hơn phải scale ngang (nhiều worker/instance sau load balancer, xem phần 5).

### 2.2 Tail latency

Khởi động lại v2 với từng giá trị `MOCK_SLOW_RATE`, mỗi lần `--requests 300 --concurrency 20` (`reports/load_v2_slow_<rate>.json`):

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 191.5 | 219.6 | 232.2 |
| 0.03 (mặc định) | 240.6 | 227.0 | 1695.7 |
| 0.1 | 299.9 | 1682.2 | 1714.1 |

(RPS: 101,32 / 62,28 / 54,44; error rate: 0,67% / 0,67% / 1,00%. p50 gần như không đổi: 192,5 / 192,7 / 188,3 ms.)

**Nhận xét:**

- **Percentile cao nhạy nhất**, và nhảy theo kiểu bậc thang chứ không tăng dần: request chậm thêm ~1500 ms (`MOCK_SLOW_MS`), nên khi tỉ lệ request chậm vượt qua 1% thì **p99** nhảy từ 232 lên 1696 ms (0 → 0,03), khi vượt 5% thì **p95** nhảy từ 227 lên 1682 ms (0,03 → 0,1). p50 không đổi.
- **avg** chỉ tăng từ 191 → 241 → 300 ms: nhìn avg tưởng hệ thống chỉ chậm hơn ~50%, trong khi thực tế cứ 10 người thì 1 người chờ ~1,7 s (gấp 9 lần bình thường). Avg là trung bình cộng, vài request rất chậm bị "pha loãng" bởi số đông request nhanh.
- Vì vậy SLO đặt theo p95/p99: nó mô tả trải nghiệm của nhóm người dùng tệ nhất, và chỉ ra ngay khi tail latency xuất hiện. Đặt SLO theo avg (vd avg < 500 ms) thì cả 3 cấu hình trên đều "đạt", dù cấu hình 0,1 có 10% request chờ gần 2 giây.
- Tail latency còn làm giảm throughput (101 → 54 RPS ở cùng concurrency 20) vì worker bị giữ lâu bởi request chậm.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

Lệnh:

```bash
python eval/run_eval.py --url http://localhost:8001 --out reports/eval_v1.json
python eval/run_eval.py --url http://localhost:8002 --out reports/eval_v2.json --baseline reports/eval_v1.json
# sau khi sửa KNOWLEDGE_BASE và khởi động lại v2:
python eval/run_eval.py --url http://localhost:8002 --min-accuracy 1.0 --baseline reports/eval_v2.json --out reports/eval_v2_fixed.json
```

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3% (1/23) | 91.3% (21/23) | 100.0% (23/23) |
| Avg tokens / câu | 57.9 | 109.1 | 118.5 |
| Tổng cost (USD) | 0.000415 | 0.000762 | 0.000832 |
| Cost / câu đúng (USD) | 0.00041475 | 0.00003627 | 0.00003618 |
| Quality gate | FAIL (accuracy < 80%) | PASS (≥ 80%, 0 regression so với v1) | PASS (≥ 100%, 0 regression so với v2) |

**Những câu v2 trả lời sai ban đầu và cách bạn sửa:**

| ID | Keyword bị thiếu | Bạn đã sửa gì |
|---|---|---|
| `security-02` "JWT khác gì API key trong xác thực?" | `token`, `hết hạn`, `chữ ký` | Retrieval không tìm được tài liệu nào (câu trả lời là câu chung chung). Thêm entry `KNOWLEDGE_BASE` kích hoạt bởi `jwt` / `json web token`: JWT là token tự chứa thông tin user, có thời điểm hết hạn (`exp`), được bảo vệ bằng chữ ký số nên server chỉ cần verify chữ ký; API key là chuỗi cố định, không tự hết hạn. |
| `scaling-03` "Kubernetes HPA scale dựa trên những chỉ số nào?" | `cpu`, `memory`, `custom metrics` | Tương tự, không có tài liệu về Kubernetes. Thêm entry kích hoạt bởi `hpa` / `horizontal pod autoscaler` / `autoscaling` / `kubernetes`: HPA scale số pod theo CPU và memory so với resource requests, hoặc custom metrics (request/giây, độ dài hàng đợi) qua Prometheus Adapter. |

Từ khoá kích hoạt được chọn sao cho **không xuất hiện trong câu hỏi nào khác** của golden dataset (đã `grep` kiểm tra), nên không cướp retrieval của câu đang đúng → eval cuối: 2 câu được cải thiện, **0 regression**. Không sửa `golden_dataset.jsonl`. Bản sửa cũng được chép sang `production/utils/mock_llm.py` cho stack Docker.

**Nhận xét về trade-off chất lượng / chi phí / latency giữa v1 và v2:**

- **Chất lượng:** v2 (RAG: retrieval → đưa tài liệu vào prompt) tăng accuracy từ 4,3% lên 91,3% (100% sau khi bổ sung KB). v1 chỉ biết vài câu trả lời chung chung.
- **Chi phí:** prompt của v2 dài hơn vì chứa context, nên **token/câu gần gấp đôi** (57,9 → 109,1) và tổng cost tăng 84% (0,000415 → 0,000762 USD). Nhưng **cost / câu đúng giảm ~11,4 lần** (0,000415 → 0,0000363 USD): trả tiền cho câu trả lời sai là lãng phí hoàn toàn, nên "cost / câu đúng" mới là chỉ số đúng để so hai phiên bản, không phải "cost / câu".
- **Latency:** v2 thêm bước retrieval (~20 ms) và prompt dài hơn (+0,2 ms/token), nên p50 tăng nhẹ (eval: 157 → 190 ms). p95 trong eval_v2 (1682 ms) cao do 3/23 câu rơi vào request chậm ngẫu nhiên của mock (`MOCK_SLOW_RATE`), không phải do v2; ở lần eval sau p95 chỉ còn 220 ms. Eval 23 câu không đủ mẫu để kết luận về latency — latency phải đo bằng load test.
- **Bổ sung KB** tăng nhẹ token (109 → 118,5/câu) vì 2 câu trước đây trả lời chung chung nay có context, nhưng cost/câu đúng gần như không đổi.
- Kết luận: v2 đáng triển khai. Đổi ~2× token lấy accuracy ×21 và rẻ hơn ~11× trên mỗi câu trả lời đúng.

**Quality gate trong CI (Exercise 7.3 / bài tập 4):** chạy eval v1 với baseline v2:

```text
$ python eval/run_eval.py --url http://localhost:8001 --baseline reports/eval_v2.json
  Accuracy                     91.3%        4.3%    -87.0 điểm %
  ⚠️  Câu bị regression : 20 ['docker-01', 'docker-02', 'probe-01', ...]
  Quality gate (accuracy ≥ 80%, không regression): ❌ FAIL
exit code = 1
```

`run_eval.py` trả **exit code 1** khi accuracy < `--min-accuracy` hoặc có câu regression so với `--baseline`, và **0** khi đạt. CI coi mọi bước có exit code khác 0 là thất bại, nên chỉ cần chạy lệnh eval như một bước của job và đặt job đó là *required status check* trong branch protection của `main`: PR làm giảm chất lượng sẽ có check đỏ và **không merge được**. Bước GitHub Actions:

```yaml
# .github/workflows/eval-gate.yml
name: eval-quality-gate
on: pull_request
jobs:
  eval:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: 06-lab-complete/benchmark-observability/develop
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -r requirements.txt
      - name: Start agent (PR version)
        run: |
          AGENT_VERSION=v2 PORT=8002 python app.py &
          for i in $(seq 30); do curl -sf localhost:8002/health && break; sleep 1; done
      - name: Quality gate — fail PR if accuracy < 100% or any regression vs baseline on main
        run: |
          python eval/run_eval.py --url http://localhost:8002 \
            --min-accuracy 1.0 --baseline reports/eval_v2_fixed.json --out eval_pr.json
      - uses: actions/upload-artifact@v4
        if: always()
        with: { name: eval-report, path: 06-lab-complete/benchmark-observability/develop/eval_pr.json }
```

(Baseline `eval_v2_fixed.json` là report của bản đang chạy trên `main`; khi một PR được merge thì cập nhật baseline bằng report mới. Có thể đặt `MOCK_ERROR_RATE=0` trong CI để lỗi upstream ngẫu nhiên không làm gate fail oan, vì `run_eval.py` đã retry 1 lần.)

---

## 4. Observability stack (production)

Stack chạy bằng `docker compose -p k5-day14-observability up -d --build` trong `production/`. Trước khi chạy được phải sửa 1 lỗi có sẵn trong `production/Dockerfile`: user `agent` có HOME `/app` (`useradd -d /app`) nên Python tìm package `--user` ở `/app/.local`, trong khi package được copy vào `/home/agent/.local` → container crash `ModuleNotFoundError: No module named 'uvicorn'`. Sửa bằng `ENV PYTHONUSERBASE=/home/agent/.local`. Unit test alert: `promtool test rules /p/alert_rules_test.yml` → `SUCCESS`.

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals) — [`screenshots/grafana-dashboard.png`](screenshots/grafana-dashboard.png): đang chạy A+B, Traffic 15,4 req/s, Error rate 19,83% (đỏ), p95 1,89 s (đỏ), 3 instance; biểu đồ latency thấy p95/p99 vọt lên từ ~16:16.
- [x] Một trace chậm trong Jaeger — [`screenshots/jaeger-slow-trace.png`](screenshots/jaeger-slow-trace.png): `72c81c9…`, 1,8 s, span `chat mock-llm-small` 1,7 s.
- [x] Một trace lỗi trong Jaeger — [`screenshots/jaeger-error-trace.png`](screenshots/jaeger-error-trace.png): `0792ecc…`, 123 ms, HTTP 503; span `agent.run` và `chat mock-llm-small` có status error và event `exception` (`utils.mock_llm.LLMError: Upstream LLM error: 503 Service Unavailable (mock)`, lấy qua Jaeger API).
- [x] Trang Alerts của Prometheus — [`screenshots/prometheus-alert-firing.png`](screenshots/prometheus-alert-firing.png): nhóm `agent-slo` **FIRING (2)**: `AgentHighErrorRate` + `AgentHighLatencyP95` cùng firing.

### 4.1b Trả lời Exercise 7.4 (stack mặc định: 2 instance agent, Locust 30 user / ramp 5, đo sau 2 phút qua Prometheus API)

1. **p95 và avg đang là bao nhiêu? Khoảng cách nói lên điều gì?** p95 = **294,8 ms**, avg = **237,5 ms** (p50 = 182,2 ms, p99 = 1834 ms). Avg cao hơn p50 ~55 ms dù 95% request đều < 295 ms: vài request rất chậm (~1,7 s, 3% `MOCK_SLOW_RATE`) kéo avg lên. Khoảng cách avg ↔ p95 nhỏ nhưng p99 gấp ~8 lần avg → có **tail latency**: đa số người dùng thấy nhanh, nhưng ~1/100 người chờ gần 2 giây. Avg không cho thấy nhóm này, nên SLO đặt theo p95/p99. (Trong ảnh `grafana-dashboard.png` chụp lúc chạy A+B, khoảng cách còn rõ hơn: p95 1,89 s.)
2. **Panel "Requests/s theo instance": Nginx có chia đều cho 2 instance không?** Có: `172.20.0.4:8000` **9,36 req/s** và `172.20.0.6:8000` **9,35 req/s** (`sum by (instance) (rate(http_requests_total{route="/ask"}[1m]))`). Nginx round-robin nên chia gần như tuyệt đối đều. Khi scale lên 3 instance, `X-Served-By` cũng lần lượt 3/3/3.
3. **Vì sao input tokens/s lớn hơn output tokens/s?** Input **1484,7 tokens/s** so với output **800,2 tokens/s** (~1,9×). Agent v2 là RAG: prompt gửi cho LLM = system prompt + **tài liệu retrieve được (context)** + câu hỏi, còn output chỉ là câu trả lời ngắn. Ví dụ trace `72c81c9…`: `input_tokens=81`, `output_tokens=46`. Vì input rẻ hơn output 4× (0,00015 vs 0,0006 USD/1K) nên cost vẫn do cả hai đóng góp; context càng dài thì input token (và cost, latency +0,2 ms/token) càng tăng.
4. **`curl http://localhost:8080/metrics` trả về gì? Vì sao?** Trả **HTTP 404** (trang lỗi của Nginx). `nginx.conf` có `location = /metrics { return 404; }`: `/metrics` chỉ dành cho Prometheus, scrape **trực tiếp** từng agent trong mạng Docker nội bộ (`agent:8000/metrics`, vẫn trả `http_requests_total{...}` bình thường). Không public ra ngoài vì metric lộ thông tin hệ thống (route, lưu lượng, version, chi phí) và ai cũng gọi được thì tốn tài nguyên. Thêm nữa, qua load balancer mỗi lần gọi trúng một instance ngẫu nhiên nên số liệu cũng vô nghĩa — Prometheus phải scrape từng instance riêng.

### 4.2 Thí nghiệm "phá hệ thống"

Locust chạy nền 30 user suốt bài; 3 instance agent. Mỗi lần đổi `.env` rồi `docker compose up -d --scale agent=3 agent`; thời gian đo bằng script hỏi `GET /api/v1/alerts` của Prometheus mỗi 5 s, tính từ lúc container mới đã start (sai số ±5 s).

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` (error rate 20,5%) | pending sau 25 s → **firing sau 85 s** (`for: 1m`) | **Error rate** (stat đỏ) và "Requests/s theo status code" (đường 503 tăng) |
| B | `MOCK_SLOW_RATE=0.3` | `AgentHighLatencyP95` (p95 1,92 s) | pending sau 25 s → **firing sau 145 s** (`for: 2m`) | **Latency p50/p95/p99**: p95 1920 ms, p99 1984 ms, avg chỉ 659 ms, p50 vẫn 224 ms |
| C | `LLM_MODEL=mock-llm-large` | `LLMCostBurnRateHigh` (lên $16,5/giờ ngay lúc firing) | pending sau 20 s → **firing sau 140 s** (`for: 2m`) | **Cost burn rate (USD/giờ)**: $1,66 → $37,25/giờ; cost/request ~$0,0006; LLM p95 494 ms, p50 `/ask` 414 ms |
| D | `docker pause` agent-1 | `AgentInstanceDown` | pending sau 10 s → **firing sau 40 s** (`for: 30s`) | **Instances up** (3 → 2). Panel Error rate vẫn xanh (0,71%) dù Nginx ghi **28 lỗi 504** (`upstream timed out`) |
| E | `AGENT_VERSION=v1` | **Không có** (theo dõi 3 phút) | — | Bảng **version** (v2 → v1), **Retrieval hit ratio** (1,0 → không còn dữ liệu), **Tokens/s** (2293 → ~1050) |

Ghi chú quan sát:

- **Ngưỡng cost alert đang đặt sai.** Ngay ở cấu hình mặc định với 30 user, cost là ~$2,5/giờ > ngưỡng $2/giờ nên `LLMCostBurnRateHigh` firing cả khi không có sự cố (bắt đầu từ lúc benchmark 100 user và vẫn giữ ở 30 user). Thí nghiệm C đo được là vì trước đó B đã làm traffic giảm (request chậm → user Locust gửi ít hơn) nên cost tụt xuống $1,66/giờ và alert về `inactive`. Ngưỡng nên đặt theo traffic dự kiến (vd 2–3× cost bình thường) hoặc theo **cost / request**, không theo con số tuyệt đối.
- **B làm hết cost alert:** request chậm → throughput giảm → cost/giờ giảm. Một sự cố latency có thể "trông như" tiết kiệm tiền.
- **D:** lỗi 504 xảy ra ở Nginx (chờ quá `proxy_read_timeout`), request chưa bao giờ được agent xử lý xong nên metric của agent không đếm. Cần giám sát cả tầng load balancer (log/metric Nginx) hoặc synthetic check từ ngoài. Dùng `pause` thay `stop` vì container bị stop biến mất khỏi Docker DNS → Prometheus coi target không tồn tại chứ không phải `up == 0`, nên không có alert.
- **E:** v1 trông "khoẻ hơn" trên mọi metric hạ tầng: p95 295 → 199 ms, cost $2,54 → $1,18/giờ, error rate 1,24% → 0,48%. Không alert nào firing; alert cost còn tự tắt.

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

Lỗi chất lượng không làm hạ tầng đỏ, nên phải đo chất lượng trực tiếp:

1. **Chặn trước khi deploy:** chạy `run_eval.py` với `--baseline` trong CI (phần 3) — v1 có 20 câu regression, exit code 1 → không merge/deploy được.
2. **Eval liên tục trên production:** chạy golden dataset định kỳ (vd mỗi 15 phút) vào endpoint thật, xuất metric `agent_eval_accuracy` và alert khi < 90% (synthetic quality check).
3. **Metric proxy cho chất lượng:** alert khi `agent_retrieval_total{result="hit"}` / tổng giảm mạnh hoặc mất hẳn series (`absent()`), khi token/request giảm đột ngột, hoặc khi `agent_info{version}` đổi ngoài lịch release.
4. **Tín hiệu từ người dùng:** tỉ lệ thumbs-down / hỏi lại cùng câu, và LLM-as-judge chấm một mẫu nhỏ câu trả lời thật.

### 4.3 Debug bằng trace

Chọn 1 trace chậm nhất bạn tìm được (Jaeger, lọc `POST /ask` ≥ 1,5 s trong 30 phút, lấy trace dài nhất):

- `trace_id`: `72c81c9cc5ab683447e8b17d1dfb6c76`
- Tổng thời gian: 1781,3 ms (`POST /ask`, HTTP 200, instance `39bb7a5657a7`)
- Span chiếm nhiều thời gian nhất: `chat mock-llm-small` (1687,8 ms, 94,7%). Các span khác: `agent.run` 1709,4 ms (span cha), `agent.retrieve_context` 21,3 ms (1,2%). Attributes: `gen_ai.request.model=mock-llm-small`, `gen_ai.usage.input_tokens=81`, `gen_ai.usage.output_tokens=46` → chậm nằm ở LLM upstream (request "slow" ngẫu nhiên của mock), không phải code của agent hay retrieval.
- Dòng log tương ứng (grep theo `trace_id`):

```json
{"ts": "2026-11-25T09:19:03.672423+00:00", "level": "INFO", "service": "observable-agent", "instance": "39bb7a5657a7", "event": "llm_call", "trace_id": "72c81c9cc5ab683447e8b17d1dfb6c76", "span_id": "da9dc7658620f67e", "request_id": "18f1d524a7b2", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 81, "output_tokens": 46, "cost_usd": 4e-05, "llm_ms": 1685.5}
{"ts": "2026-11-25T09:19:03.673614+00:00", "level": "INFO", "service": "observable-agent", "instance": "39bb7a5657a7", "event": "http_request", "trace_id": "72c81c9cc5ab683447e8b17d1dfb6c76", "span_id": "da9dc7658620f67e", "request_id": "18f1d524a7b2", "method": "POST", "route": "/ask", "status": 200, "ms": 1780.3}
```

Trace lỗi (`0792eccf7f5b4682b59ea12115dfb9ed`, 122,9 ms, HTTP 503): span `chat mock-llm-small` 95,1 ms có `error.type=upstream_unavailable` và event `exception`; log cùng `trace_id`:

```json
{"ts": "2026-11-25T09:20:06.550077+00:00", "level": "INFO", "service": "observable-agent", "instance": "a9c3cf39af8c", "event": "llm_error", "trace_id": "0792eccf7f5b4682b59ea12115dfb9ed", "span_id": "3e4f1bda42b4f63f", "request_id": "1cd0179183ff", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
{"ts": "2026-11-25T09:20:06.550736+00:00", "level": "INFO", "service": "observable-agent", "instance": "a9c3cf39af8c", "event": "http_request", "trace_id": "0792eccf7f5b4682b59ea12115dfb9ed", "span_id": "3e4f1bda42b4f63f", "request_id": "1cd0179183ff", "method": "POST", "route": "/ask", "status": 503, "ms": 121.9}
```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

Lệnh (theo CP8): `docker compose -p k5-day14-observability run --rm locust -f /mnt/locust/locustfile.py --host http://nginx --headless -u 100 -r 10 -t 2m --csv /mnt/results/scale_<n>`. Số liệu dòng `POST /ask` trong `benchmark-results/scale_<n>_stats.csv`:

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 60.40 | 190 | 230 | 1700 | 74 / 7207 (1,03%, đều là 503) |
| 3 | 59.52 | 190 | 230 | 1700 | 77 / 7102 (1,08%, đều là 503) |

(Dòng Aggregated, gồm cả `GET /health`: 66,17 và 65,84 req/s.)

**Giải thích kết quả:** Scale lên 3 instance **không cải thiện** RPS hay p95 (giống hệt nhau), vì 1 instance chưa hề bão hoà:

- **Agent không phải nút thắt:** CPU tối đa mỗi agent ~0,128 core (giới hạn 0,5 core), in-flight tối đa 22 request. Nginx chia đều cho 3 instance (kiểm tra `X-Served-By`: 3/3/3), nhưng mỗi instance chỉ phải làm ít việc hơn chứ không làm nhanh hơn.
- **Latency do LLM:** p95 của riêng LLM call ≈ 198 ms (Prometheus `llm_request_duration_seconds`), gần bằng p95 của `/ask` (230 ms). p99 = 1700 ms là các request "slow" 3% của mock. Đây là thời gian **chờ upstream** (`await asyncio.sleep`), thêm instance không làm LLM nhanh hơn.
- **Throughput do Locust:** mô hình closed-loop, mỗi user nghỉ 0,5–2 s giữa các lần gọi → 100 user ≈ 100 / (1,25 s + 0,24 s) ≈ 67 req/s, đúng với 66 req/s đo được. Muốn thấy lợi ích của scale phải tăng tải tới khi 1 instance bão hoà (như c=100 ở phần 2.1).
- **Failures ~1%** ở cả hai lần đều là 503 từ lỗi upstream giả lập `MOCK_ERROR_RATE=0.01`, không do thiếu tài nguyên.

Kết luận: scale ngang giúp tăng **sức chứa** (chịu được nhiều request đồng thời hơn, chịu được mất 1 instance như thí nghiệm D), nhưng không giảm latency khi nút thắt là LLM upstream. Để giảm latency phải xử lý ở LLM: model nhanh hơn, cache câu trả lời, streaming, timeout + retry/hedging cho request chậm.

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. **Average nói dối, percentile thì không.** Với 10% request chậm, avg chỉ tăng 191 → 300 ms trong khi p95 nhảy 220 → 1682 ms. SLO và alert latency phải đặt theo p95/p99; và benchmark phải nói rõ điều kiện tải (c=50 đạt SLO, c=100 thì vỡ trên cùng một máy).
  2. **Metric hạ tầng không thấy lỗi chất lượng và không thấy lỗi ngoài tầm nhìn của service.** Thí nghiệm E (đổi sang v1, accuracy 4,3%) làm dashboard *đẹp hơn*; thí nghiệm D có 504 ở Nginx mà error rate của agent vẫn xanh. Cần eval/quality gate (chặn bằng exit code trong CI) và giám sát nhiều tầng (LB, synthetic check).
  3. **Ba trụ cột phải nối được với nhau và alert cũng là code.** Dashboard báo p95 tăng → Jaeger chỉ ra span `chat mock-llm-small` chiếm 94,7% → grep `trace_id` trong log JSON ra đúng request, model, token, cost. Alert cần ngưỡng hợp lý (ngưỡng cost $2/giờ firing ngay cả khi bình thường → alert fatigue), có `for` để tránh nhiễu, và có unit test (`promtool test rules`).
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?
  - **Chất lượng:** metric `agent_eval_accuracy` từ eval định kỳ + alert < 90%; alert retrieval hit ratio giảm hoặc series biến mất (`absent()`); theo dõi tỉ lệ feedback xấu.
  - **Cost:** alert theo **cost / request** và theo % ngân sách ngày (thay cho ngưỡng tuyệt đối $2/giờ), kèm alert khi model đổi ngoài lịch release (`agent_info`).
  - **SLO burn rate** nhiều cửa sổ (vd 1h và 6h) thay vì ngưỡng tức thời, để vừa bắt sự cố nhanh vừa không báo động vì spike ngắn.
  - **Tầng ngoài agent:** metric/log Nginx (tỉ lệ 502/504, upstream response time), synthetic check từ ngoài Internet vào `/health` và `/ask`.
  - **Saturation & dependency:** in-flight / CPU / memory gần giới hạn container; latency và lỗi của Redis; tỉ lệ 429 từ LLM provider và số lần retry.
  - **Tail-based sampling** cho tracing: giữ 100% trace lỗi và trace chậm, chỉ lấy mẫu ~10% trace bình thường để giảm chi phí.
