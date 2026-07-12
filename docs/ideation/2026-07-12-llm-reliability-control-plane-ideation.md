---
date: 2026-07-12
topic: llm-reliability-control-plane
focus: HANDOFF.md development structure + Composer 2.5 / Grok dual-lane build
mode: repo-grounded
---

# Ideation: LLM Reliability Control Plane

## Grounding Context

Greenfield docs-only repo (`HANDOFF.md`, ponytail `AGENTS.md`). Target: Python 3.12 FastAPI OpenAI-compatible gateway + eval release gate; Postgres + Redis; fake + real providers; exact cache; 1–2 week portfolio flagship. Samsung work already covers multi-agent/MCP/RAG — this project must show LLM *infrastructure*. Prior art (LiteLLM, Portkey, Helicone, Promptfoo, Langfuse) owns pieces; wedge is versioned config → blocking deterministic eval → redacted traces.

## Ranked Ideas

### 1. Contract freeze + dual-lane Composer / Grok build
**Description:** Freeze `schemas.py`, Protocols, and `RequestContext` before features. Composer 2.5 owns schemas, fake provider, fixtures, Docker/pytest stubs; Grok owns gateway orchestration, circuit breaker, eval gate.
**Warrant:** `direct:` HANDOFF interfaces-first + user dual-lane request
**Rationale:** Parallel speed requires contracts; wrong-model thrash kills a short build
**Downsides:** Upfront interface tax
**Confidence:** 90%
**Complexity:** Medium
**Status:** Explored (selected as build spine)

### 2. Gate-first vertical slice
**Description:** Eval fixtures + hard thresholds + failing candidate via fake provider early; grow gateway under that gate.
**Warrant:** `direct:` resume wedge is config→eval-block→trace
**Rationale:** Avoids mini-LiteLLM with a late thin gate
**Downsides:** Deviates from HANDOFF M1→M3 order slightly
**Confidence:** 85%
**Complexity:** Medium
**Status:** Unexplored

### 3. Fake-provider-as-spec / forever-free CI spine
**Description:** Fake defines contract; real adapter conforms; all metrics without paid key.
**Warrant:** `direct:` HANDOFF unpaid tests + fake provider required
**Rationale:** Unlocks injection, baselines, demo
**Downsides:** Live-model demo deferred
**Confidence:** 92%
**Complexity:** Low–Medium
**Status:** Unexplored

### 4. Failure-class taxonomy + SCRAM-ordered injection
**Description:** Lock error categories; trip → safe typed error + audit before happy-path polish.
**Warrant:** `direct:` HANDOFF error rules
**Rationale:** Shared kernel for retry/fallback/circuit breaker
**Downsides:** Easy to over-enumerate
**Confidence:** 88%
**Complexity:** Medium
**Status:** Unexplored

### 5. Safe exact-cache contract
**Description:** Eligibility matrix + golden key vectors before Redis wiring.
**Warrant:** `direct:` exact cache key recipe; no semantic cache
**Rationale:** Cache is a correctness hazard
**Downsides:** Strict eligibility can thin hit-rate demos
**Confidence:** 86%
**Complexity:** Medium
**Status:** Unexplored

### 6. Config-version identity + YAML/semver promotion
**Description:** Every request carries config version; same YAML for gateway load and eval gate.
**Warrant:** `direct:` traces + gate CLI in HANDOFF
**Rationale:** Join key for control-plane story
**Downsides:** Version ceremony
**Confidence:** 89%
**Complexity:** Low–Medium
**Status:** Unexplored

### 7. One evidence surface
**Description:** Eval JSONL = demo corpus; gate JSON = CI + demo + resume numbers.
**Warrant:** `direct:` measurable evidence only; dual corpora risk drift
**Rationale:** Prevents M4 number drift
**Downsides:** Fixtures must cover all demo beats
**Confidence:** 84%
**Complexity:** Low
**Status:** Unexplored

## Rejection Summary

| # | Idea | Reason Rejected |
|---|------|-----------------|
| 1 | ATC strip board | Process ceremony; absorbed into contracts |
| 2 | Solo one-file kernel | Fights dual-lane parallel build |
| 3 | 100-agent swarm | Variant of #1; unrealistic |
| 4 | No-Postgres first | Plan-time stack decision |
| 5 | Ban gateway.py / non-goal linter | Overlaps Protocols; low leverage |
| 6 | Adversarial-only lane | Folds into #1 + #2 |
| 7 | SprintIQ as flagship | App AI / PM SaaS; duplicates Samsung signal |
