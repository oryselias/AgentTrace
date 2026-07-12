# Measured report

Generated from checked-in scripts on this machine. Re-run to refresh:

```powershell
python scripts/demo.py --write-report docs/measured-results.json
python scripts/bench.py --n 200 --workers 8 --write-report docs/measured-results.json
python -m control_plane eval --candidate configs/support-v1.yaml
python -m control_plane eval --candidate configs/support-bad.yaml
```

Raw JSON: [measured-results.json](measured-results.json). Demo stdout capture: [demo-transcript.txt](demo-transcript.txt).

**Scope:** all numbers use the deterministic `FakeProvider`. They measure gateway/control-plane overhead and gate behavior, not paid-model latency.

## Evaluation gate (24 synthetic cases)

| Config | Schema | Classification | Citation | Mean cost | p95 lat (provider) | Gate |
|--------|--------|----------------|----------|-----------|--------------------|------|
| `support-v1` (baseline) | 1.00 | 1.00 | 1.00 | $0.0000318 | 1.0 ms | **pass** (exit 0) |
| `support-bad` | 1.00 | 0.17 | 0.00 | $0.000500 | 50.0 ms | **fail** (exit 1) |

`support-bad` failures recorded:

- classification_accuracy dropped 0.8333 (> 0.02)
- citation_coverage 0.0000 < 0.95
- mean_cost rose ~1470% (> 20%)
- p95_latency_ms rose ~4900% (> 25%)

## Gateway load bench (`scripts/bench.py`)

| Metric | Value |
|--------|-------|
| Requests | 200 |
| Workers | 8 |
| Throughput | **11,908 req/s** |
| p50 end-to-end | **0.038 ms** |
| p95 end-to-end | **0.107 ms** |
| Mean end-to-end | 0.051 ms |

## Exact cache

| Metric | Value |
|--------|-------|
| Miss p50 | 0.059 ms |
| Hit p50 | 0.041 ms |
| p50 speedup | **1.46×** |

(Fake provider is already ~1 ms; cache wins are small absolute, but hits skip the provider call.)

## Injected failure / cost controls

| Check | Result |
|-------|--------|
| Fallback success rate (`unavailable` on primary, N=40) | **100%** |
| Idempotent replay double-bill | **prevented** (budget charged once) |
| Demo: timeout -> fallback | ok |
| Demo: invalid-response -> fallback | ok |
| Demo: PII absent from trace JSON | ok |
