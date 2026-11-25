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

### 4.1 Screenshot

- [ ] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals)
- [ ] Một trace chậm trong Jaeger (thấy span nào chiếm thời gian)
- [ ] Một trace lỗi trong Jaeger (có event `exception`)
- [ ] Trang Alerts của Prometheus với ít nhất 1 alert đang **firing**

### 4.2 Thí nghiệm "phá hệ thống"

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | | | |
| B | `MOCK_SLOW_RATE=0.3` | | | |
| C | `LLM_MODEL=mock-llm-large` | | | |
| D | `docker pause` 1 instance | | | |
| E | `AGENT_VERSION=v1` | | | |

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

...

### 4.3 Debug bằng trace

Chọn 1 trace chậm nhất bạn tìm được:

- `trace_id`: ...
- Tổng thời gian: ... ms
- Span chiếm nhiều thời gian nhất: ... (... ms, ...%)
- Dòng log tương ứng (grep theo `trace_id`):

```json

```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | | | | | |
| 3 | | | | | |

**Giải thích kết quả:** Scale có cải thiện p95 không? Nút thắt nằm ở đâu?

...

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. ...
  2. ...
  3. ...
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?

...
