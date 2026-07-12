# LLM Reliability Control Plane

OpenAI-compatible multi-model gateway + evaluation release gate. Built to show production controls around non-deterministic LLM calls: routing, PII redaction, rate/cost limits, exact caching, idempotency, circuit breaking, fallbacks, traces/metrics, and a CI gate that blocks degraded prompt/model configs.

Demo workload: synthetic customer-escalation assistant (classify → cite policy → structured proposal). The assistant is only the fixture; the product is the control plane.

## Quick start (Docker Compose, no paid API key)

```powershell
docker compose up --build
```

Health check:

```powershell
curl http://localhost:8080/health
```

Chat completion (demo tenant from `configs/tenants.yaml`):

```powershell
curl http://localhost:8080/v1/chat/completions `
  -H "Authorization: Bearer sk-demo-tenant-a" `
  -H "Content-Type: application/json" `
  -d "{\"model\":\"support-default\",\"messages\":[{\"role\":\"user\",\"content\":\"billing dispute on last invoice\"}]}"
```

Traces / metrics:

```powershell
curl http://localhost:8080/v1/traces?limit=5
curl http://localhost:8080/metrics
```

## Local (no Docker)

```powershell
python -m pip install -e .
python -m uvicorn control_plane.api:app --port 8080
```

## One-line checks

```powershell
python -m tests.check_m0_contracts
python -m tests.check_m1a_stub
python -m tests.check_m1b_reliability
python -m tests.check_m2a_policies
python -m tests.check_m2b_observability
python -m tests.check_m3_eval_gate
python -m tests.check_m4_portfolio
```

## Reproducible demo + measurements

```powershell
python scripts/demo.py --write-report docs/measured-results.json
python scripts/bench.py --write-report docs/measured-results.json
python -m control_plane eval --candidate configs/support-v1.yaml
python -m control_plane eval --candidate configs/support-bad.yaml
```

`scripts/demo.py` walks the HANDOFF scenarios: primary route, cache hit, PII redaction, timeout→fallback, invalid-response→fallback, bad candidate fails gate, good candidate passes, idempotent replay.

Measured numbers live in [docs/measured-report.md](docs/measured-report.md) (from [docs/measured-results.json](docs/measured-results.json)). Demo stdout: [docs/demo-transcript.txt](docs/demo-transcript.txt).

## Architecture

See [docs/architecture.md](docs/architecture.md).

Short version: FastAPI gateway runs auth → PII → limits → exact cache → primary/retry/circuit → fallback → metadata traces. Eval CLI uses the same route YAML against versioned JSONL cases and exits non-zero on regression.

v1 uses in-process cache/limits and memory traces (SQLite store available). Compose does **not** ship unused Postgres/Redis.

## Eval release gate

| Candidate | Expected |
|-----------|----------|
| `configs/support-v1.yaml` | exit 0 |
| `configs/support-bad.yaml` | exit 1 |

Hard thresholds: schema 100%, classification drop ≤2pp, citation ≥95%, cost +≤20%, p95 +≤25%.

## Trade-offs and limitations

- **Exact cache only** — safe and explainable; no semantic reuse across similar prompts.
- **Regex PII** — email/phone/`ACCT-*`; will miss novel formats. Do not treat as a DLP product.
- **Fake provider is CI authority** — portfolio demo needs no paid key. Optional real path: set route `provider: openai`, `OPENAI_API_KEY`, and optional `OPENAI_BASE_URL` (default `https://api.openai.com/v1`).
- **Single process** — Redis/Postgres are documented upgrade paths once multi-worker persistence is required.
- **No streaming** in v1.
- **No LLM-as-judge as sole gate** — deterministic metrics decide promotion.

## Resume bullets (measured only)

From [docs/measured-report.md](docs/measured-report.md) — re-run bench/eval before changing:

- Built an OpenAI-compatible LLM reliability gateway with tenant rate/cost controls, PII redaction, exact caching, idempotent retries, circuit breaking, and model fallback; sustained **~11.9k req/s** with **0.11 ms p95** end-to-end gateway overhead on the checked-in fake-provider load bench (200 requests, 8 workers).
- Developed a versioned evaluation and CI release gate across **24** synthetic enterprise escalation cases, blocking a degraded candidate (classification 1.00->0.17, citation 0.00, cost +1470%) while the baseline config passes.
- Instrumented end-to-end traces and Prometheus metrics for tokens, estimated spend, cache hits, retry/fallback, and schema/error categories without persisting unredacted prompts.
