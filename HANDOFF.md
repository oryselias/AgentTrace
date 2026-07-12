# LLM Reliability Control Plane — Project Handoff

## Purpose

Build one interview-grade flagship project for broad, high-paying AI Engineer roles:

> A production-oriented multi-model gateway paired with an evaluation control plane that prevents degraded prompt and model configurations from being released.

This project fills a gap in Omkar Chalak's existing portfolio. Existing Samsung work already demonstrates multi-agent orchestration, MCP, RAG, semantic search, and LLM-based test generation. This project should instead demonstrate the infrastructure needed to operate non-deterministic models reliably: routing, validation, fallbacks, caching, observability, cost controls, and regression evaluation.

Target effort: **1–2 weeks of evening work**.

## Product Story

An application sends every LLM request through one OpenAI-compatible gateway. The gateway selects a configured provider/model, removes sensitive data, enforces limits, validates outputs, caches safe requests, and records traces.

Before a prompt or routing configuration is promoted, the control plane runs a versioned evaluation dataset. A release is blocked when quality drops beyond an explicit threshold, cost exceeds its budget, or latency violates its service target.

A narrow **customer-escalation assistant** is the demonstration workload:

1. Classify an incoming escalation.
2. Retrieve a matching policy excerpt from a small fixture dataset.
3. Produce a structured resolution proposal with citations.
4. Return the proposal for human review.

The assistant is only a workload used to prove the infrastructure. Do not expand it into a full CRM, Jira, Slack, or Confluence integration platform.

## Primary Resume Signal

The repository must clearly demonstrate:

- Production LLM infrastructure rather than another chatbot.
- Deterministic controls around non-deterministic model output.
- Measured trade-offs between quality, latency, and cost.
- Repeatable evaluations and release gating.
- Backend and distributed-systems fundamentals.

## Scope

### 1. OpenAI-Compatible Gateway

Expose a minimal endpoint:

- `POST /v1/chat/completions`
- Support non-streaming responses first.
- Accept a logical model alias such as `support-default`, not arbitrary provider credentials.
- Return a stable response envelope and request ID.

Gateway pipeline:

1. Authenticate tenant API key.
2. Validate request size and schema.
3. Redact configured PII patterns before provider calls.
4. Check tenant rate and cost limits.
5. Resolve logical alias to primary and fallback model.
6. Check exact cache for eligible deterministic requests.
7. Call provider with timeout and bounded retry.
8. Validate structured output when a response schema is configured.
9. Invoke fallback only for configured failure classes.
10. Persist trace metadata and return the response.

### 2. Routing and Reliability

Support two provider adapters:

- One real OpenAI-compatible provider selected through environment configuration.
- One deterministic fake provider for local development and tests.

Required behavior:

- Per-model timeout.
- At most one retry for transient failures.
- Circuit-breaker state after repeated provider failures.
- Primary-to-fallback routing.
- Idempotency key support so retried client requests are not billed twice.
- Exact request cache keyed by normalized request, model configuration version, and tenant.

Do **not** build semantic caching in the first version. Exact caching is easier to reason about and avoids unsafe reuse across superficially similar prompts.

### 3. Policy and Safety Layer

- Tenant-scoped API keys stored as hashes.
- PII redaction for email, phone, and configurable account-like identifiers.
- Per-tenant requests-per-minute and daily cost budget.
- Maximum prompt and output-token limits.
- Structured-output validation with Pydantic.
- Audit record for denied, redacted, retried, cached, and fallback requests.
- Never log provider credentials or unredacted prompt contents.

This is not a complete security product. Document that regex-based PII detection has known limits.

### 4. Observability

Record per request:

- Request and trace IDs.
- Tenant.
- Logical alias and resolved provider/model.
- Prompt/configuration version.
- Cache hit.
- Retry and fallback events.
- Input/output token counts.
- Estimated cost.
- Provider latency and end-to-end latency.
- Validation outcome and error category.

Expose:

- `GET /health`
- `GET /metrics` in Prometheus text format.
- A simple trace-list API for the local demonstration.

Use structured JSON logs. A full observability stack is optional; generated metrics and a concise demo report are sufficient.

### 5. Evaluation Control Plane

Evaluation cases are versioned JSONL fixtures containing:

- Input messages.
- Expected structured fields.
- Required citation IDs or keywords.
- Optional forbidden content.
- Tags such as `classification`, `grounding`, and `safety`.

The evaluator runs a candidate prompt/model configuration against the dataset and calculates:

- Deterministic schema-validity rate.
- Exact or normalized classification accuracy.
- Citation/keyword coverage.
- Refusal or forbidden-content checks.
- p50 and p95 latency.
- Mean estimated cost per case.

An optional LLM judge may provide an additional rubric score, but it must not be the only release criterion.

### 6. Release Gate

Compare a candidate run against a stored baseline.

Fail promotion when any hard condition is met:

- Schema-validity rate is below 100%.
- Classification accuracy falls by more than 2 percentage points.
- Required citation coverage falls below 95%.
- Mean cost rises by more than 20% without an explicit override.
- p95 latency rises by more than 25% without an explicit override.

The command exits non-zero on failure so it can run in CI.

Example:

```powershell
python -m control_plane eval --candidate configs/support-v2.yaml
```

### 7. Demo Workload

Provide 20–30 synthetic escalation cases and a small policy corpus. No real customer or employer data.

The demo should show:

1. A valid request routed to the primary model.
2. A repeated request served from cache.
3. PII redaction visible in audit metadata.
4. A provider timeout triggering fallback.
5. Invalid structured output rejected or repaired once.
6. A bad prompt version rejected by the evaluation gate.
7. A better candidate passing the gate.

## Architecture

Keep the repository as one Python service plus one evaluation CLI:

```text
Client / demo workload
        |
        v
FastAPI gateway
  auth -> policy -> route -> cache -> provider -> validate
        |                         |
        v                         v
 PostgreSQL traces          Provider adapters
 Redis limits/cache         real + deterministic fake
        |
        v
Evaluation CLI -> dataset runner -> metrics -> baseline comparison -> exit code
```

Recommended stack:

- Python 3.12
- FastAPI and Pydantic
- PostgreSQL
- Redis
- SQLAlchemy or the project's smallest reasonable existing database layer
- `httpx` for provider calls
- Prometheus client
- Docker Compose
- Pytest

Do not add Celery, Kafka, Kubernetes, React, LangChain, or LangGraph unless an implemented requirement genuinely needs them. The standard request path and evaluation runner do not.

## Suggested Repository Boundaries

```text
src/control_plane/
  api.py                 # HTTP boundary only
  settings.py
  schemas.py
  gateway.py             # request orchestration
  policies.py            # auth, limits, PII, budgets
  routing.py
  cache.py
  providers/
    base.py
    fake.py
    openai_compatible.py
  traces.py
  evals/
    dataset.py
    metrics.py
    runner.py
    gate.py
configs/
evals/
tests/
docker-compose.yml
README.md
```

Keep source files focused and below 500 lines.

## Error Handling Rules

- Map provider errors into stable internal categories: timeout, rate-limited, unavailable, invalid-response, and permanent-client-error.
- Retry only transient categories.
- Never retry authentication or invalid-request errors.
- Fall back only when the configured alternative can satisfy the same response contract.
- If structured validation still fails after one repair attempt, return a typed gateway error and record the failure.
- Database or telemetry failure must not silently convert a failed model request into success.
- If cost cannot be calculated, mark it unknown rather than inventing a value.

## Testing

Minimum runnable checks:

- Unit tests for PII redaction, cache keys, budget accounting, error classification, and release-gate thresholds.
- Contract tests shared by both provider adapters.
- Integration test with FastAPI, PostgreSQL, Redis, and fake provider.
- Failure tests for timeout, retry, fallback, malformed structured output, and idempotent replay.
- Evaluation test proving a deliberately degraded candidate exits non-zero.
- Small concurrency/load check reporting throughput and p95 latency with the fake provider.

Tests must run without paid API access.

## Measurable Evidence

Only claim metrics produced by checked-in scripts. Capture:

- Evaluation pass rate.
- Classification and citation accuracy.
- p50/p95 gateway overhead.
- Cache-hit latency improvement.
- Successful fallback rate in injected-failure tests.
- Duplicate-cost prevention under idempotent replay.
- Cost difference between baseline and candidate configurations.

Do not invent production scale or business-impact percentages.

## Non-Goals

- Full enterprise workflow builder.
- Autonomous multi-agent system.
- Fine-tuning or training a foundation model.
- Vector database or advanced RAG platform.
- Billing and payment collection.
- Kubernetes deployment.
- Polished React dashboard.
- Supporting every commercial LLM provider.
- Production-grade secrets management.

## Delivery Milestones

### Milestone 1 — Reliable request path

- FastAPI endpoint and stable schemas.
- Fake and real provider adapters.
- Routing, timeout, retry, fallback, and structured validation.
- Core unit and contract tests.

### Milestone 2 — Tenant controls and observability

- API-key authentication.
- PII redaction, rate limits, budgets, idempotency, and exact cache.
- PostgreSQL traces, Prometheus metrics, and failure injection.

### Milestone 3 — Evaluation and release gate

- Synthetic escalation dataset and policy fixtures.
- Deterministic metrics.
- Baseline comparison and non-zero CI gate.
- Passing and deliberately failing configurations.

### Milestone 4 — Portfolio proof

- Docker Compose quick start.
- Architecture diagram.
- Reproducible demo script.
- Benchmark/evaluation report with actual measured results.
- Short demo video or GIF.
- Final resume bullets based only on measured evidence.

## Acceptance Criteria

The project is ready for a resume when:

- A new user can run it locally with Docker Compose and no paid model key.
- The OpenAI-compatible endpoint works with the deterministic provider.
- Timeout, fallback, cache, PII, rate-limit, budget, and idempotency paths are demonstrated.
- Every gateway request produces trace metadata without storing unredacted prompts.
- The evaluation runner produces a machine-readable report.
- A degraded candidate fails the release gate and a valid candidate passes.
- Tests and the demo command run from documented one-line commands.
- README contains architecture, trade-offs, limitations, and measured results.

## Candidate Resume Bullets

Replace bracketed values only after measurement:

- Built an OpenAI-compatible LLM reliability gateway with tenant rate/cost controls, PII redaction, exact caching, idempotent retries, circuit breaking, and model fallback; sustained **[measured throughput]** with **[measured p95 overhead]** using reproducible load tests.
- Developed a versioned evaluation and CI release gate across **[N]** synthetic enterprise cases, blocking prompt/model configurations that regressed structured-output validity, grounded citation coverage, latency, or cost.
- Instrumented end-to-end model traces and Prometheus metrics for token usage, estimated spend, cache performance, retry/fallback behavior, and schema-validation failures without persisting unredacted prompts.

## Instructions for the Next Cursor Session

Open this folder directly in Cursor:

```text
C:\Users\omkar\OneDrive\Desktop\vaults\llm-reliability-control-plane
```

Use this opening prompt:

> Read HANDOFF.md. First challenge the scope and identify contradictions or missing decisions. Do not write code yet. Propose a detailed implementation plan for Milestone 1, including interfaces, test cases, file changes, and verification commands. Keep the project minimal and preserve the non-goals.

Do not begin by generating the entire repository. Implement and verify one milestone at a time.
