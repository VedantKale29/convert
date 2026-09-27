# Architecture

## Tiers
```
Browser (React, your app)
   │  same origin, your session cookie            ── integrations/react  (hook + component)
   ▼
Node BFF (your Express backend)                   ── integrations/node-bff
   │  X-API-Key (secret, server-side only)            auth · per-user ownership · rate limit · SSE pass-through
   ▼
Generation API (FastAPI)                          ── service/api.py
   │  async jobs, bounded queue, SSE progress         /v1 versioned · OpenAPI contract in contracts/openapi.json
   ▼
Pipeline (library)                                ── uigen/
   governance → 1 LLM call → validators → ≤N repairs → deterministic compiler → sandbox build
   → render + quality check → optional fidelity repair (kept only if better) → artifacts + trace
```
Rules that keep it safe and predictable:
- The browser never sees the service or its key. The BFF decides *who* may see *which* job.
- The LLM only produces UI IR (JSON). It never writes code, CSS or HTML. Code comes from a
  deterministic compiler; a build failure is always a compiler bug (fuzz-tested on random IRs).
- Every LLM string is emitted as an escaped JS expression; generated pages are served under a CSP sandbox.
- Every call is bounded (repairs, retries, timeouts, queue size, image size/pixels, requirement length).

## Scaling path (what changes, what does not)
Each moving part sits behind a small interface, so scaling swaps implementations, not the API.

| Concern | Today (one machine) | At scale | Seam |
|---|---|---|---|
| Job execution | `ThreadJobRunner` (threads, bounded queue) | SQS / Redis queue + separate worker processes/containers | `uigen/jobs.py` `submit()` |
| Job state + events | `InMemoryJobStore` | Redis (events) + Postgres (records) — needed once >1 API instance | `uigen/jobs.py` store methods |
| Artifacts (runs/) | local folder, retention purge | S3 (+ lifecycle rule for retention) | `UIGEN_RUNS_DIR`; upload after job |
| IR cache | local files, atomic writes | Redis or S3, same key (image + requirement + model + versions + config) | `pipeline._cache_key` |
| LLM | OpenAI Chat Completions, timeout + retries | approved endpoint (e.g. Bedrock) behind the same `LLMProvider` | `uigen/llm.py` |
| Traces | JSONL per instance | Langfuse / OpenTelemetry exporter (same span shape) | `uigen/tracing.py` |
| BFF ownership + rate limit | in-memory maps | Redis (shared by all Node instances) | `integrations/node-bff/server.js` |
| Frameworks | React compiler | Angular / Vue compilers on the same IR | `pipeline.SUPPORTED_FRAMEWORKS` |

Capacity today: each generation ≈ 1 LLM call (+ repairs) + ~0.1 s build + ~2 s render/OCR.
`UIGEN_WORKERS` sets parallel jobs per instance; beyond `UIGEN_MAX_PENDING` callers get 429 + Retry-After.
Scale out by running more API/worker instances once the store and queue are shared (rows 1-2 above).

## Versioned contracts (all recorded on every trace, all part of the cache key)
`ir_version` · `prompt_version` · `registry_version` · `design_system_version` · `compiler_version` ·
`GenerationConfig` fingerprint · API `v1` + `contracts/openapi.json` (a test fails if it drifts).

## Statuses
Pipeline: `success` · `invalid_ir` · `compiler_error` · `llm_error` · `rejected_input`.
API job: `queued` → `running` → `done` (read `result.status`) or `failed` (internal error, details only in server logs).
