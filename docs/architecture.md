# Architecture

```
 browser/curl ──► api (FastAPI) ──► redis ◄── worker x N (RQ + LangGraph coordinator)
                  │ validate/quarantine           │  HTTP, per-target breaker + retry
                  │ guardrails, rate limit        ├──► researcher (Agent 1: tools + bounded loop) ──► FAISS index (ro volume)
                  │ cache lookup                  └──► advisor   (Agent 2: compose + verify)       ──► chunk manifest (ro volume)
                  ▼                                        │ both call ──► LLM provider (stub or real) via shared Redis breaker
              /metrics ◄── prometheus ◄── every service      indexer (one-shot) ─► index_data volume
              traces ──► jaeger     dashboards ◄ grafana ◄ prometheus + cadvisor
```

## Agent topology (A1, A2)
* **Coordinator** (worker): deterministic LangGraph state machine `prep → research → advise → finalize`, with edges `advise → research` (verification gaps, ≤ `MAX_ROUNDS`) and `→ degrade → finalize`. `prep` classifies the domain, extracts entities, and routes without any model call when the answer is obvious (out of scope, termination dispute, harassment complaint, service straddling 21 Nov 2025, missing facts). That is the cost-control point: escalations and detail requests spend zero LLM calls.
* **Researcher** (own container/process/prompt/tool set): LangGraph loop in which the LLM *chooses* the next tool: `search_corpus` (semantic|lexical), `get_clause` (by identifier), `get_provisions` (all provisos/explanations of a section), `get_request_field`. Arguments are schema-validated; each call has a timeout. Bounded by `MAX_STEPS`, `MAX_TOOL_CALLS`, and the run deadline. Output: a **bundle of passages**, nothing else. It never composes an answer.
* **Advisor** (own container/process/prompt, no retrieval tools): composes assertions from the bundle only, then runs the verifier (A3). Verdicts: `verified`, `failed(gaps)`, `cannot_answer`, `blocked_output`.
* **Hand-off contract**: `ResearchIn → {passages, queries, trace, tokens}` → `AdviseIn(passages) → {verdict, advisory, gaps, verification}`. Gaps carry executable hints (e.g. `call get_provisions('coss_2020:s53')`) which become the next researcher round's `hints`.

### What happens if an agent fails mid-run
| Failure | Behaviour |
|---|---|
| Provider down / 429 / timeout | per-call timeout → retry with exponential backoff + jitter (≤ `RETRY_MAX`) → shared Redis circuit breaker opens after `BREAKER_FAILURE_THRESHOLD` failures; further calls fail fast |
| Researcher or advisor `llm_unavailable`, container down, or timeout after retries | coordinator trips the per-target breaker and goes to **degrade** |
| Degrade | researcher `/degraded_search` (no LLM: lexical retrieval + provisos of top hits). If that is also down, empty evidence. Route is always `legal-counsel-review`, `degraded=true`, provisions marked *unverified*. **Never** answer-with-citation |
| Advisor invalid JSON | one retry, then verification failure → another research round or escalation |
| Verification fails after `MAX_ROUNDS` | escalate with the unverified provisions attached |
| Deadline exceeded | degrade/escalate with the reason |
| Worker crash mid-run | RQ job fails; status stays `running` until the job timeout; run-level `except` marks `failed` + escalation when the process survives. Partial trace is preserved (events are appended as they occur) |

Invariant: only a run that passed verification can return `answer-with-citation`.

## Verification (A3, F5)
1 every assertion cites; 2 every citation resolves to retrieved text; 3 cited text is in force on the as-of date (`in_force_from/until` per document, so a 2024 question correctly uses the 1972 Act); 4 cited text supports the assertion (token support ≥ 0.45, and every number in the assertion must appear in the cited text); 5 **completeness**: every proviso/explanation attached to a cited sub-section was retrieved. Check 5 is what stops the "five years" answer without the fixed-term proviso.

## Retrieval (L1)
Section-aware chunking: section → sub-section → main / proviso / explanation chunks with ids like `coss_2020:s53:1:proviso2`. Provisos are embedded with the rule they qualify (so they are found by what they modify), and the main chunk records `has_provisos`/`proviso_ids` so an agent reading a rule knows an exception exists and can fetch it by identifier. Repealed Acts are indexed deliberately and tagged with validity dates and `superseded_by`; results carry status as of the request date. Embeddings: `EMBED_BACKEND=st` (sentence-transformers, local, free) or `hash` (offline). FAISS `IndexIDMap2` allows removing/replacing single chunks: the indexer is incremental and idempotent (content-hash diff), and `reembed [--doc X]` re-vectorises stored chunks in place after an embedding-model change, without re-parsing or rebuilding. `naive` chunking exists as the ablation baseline.
Strategies the agent can switch between: semantic, lexical (BM25), by-identifier, by-section expansion.

## Deployment boundaries (D3)
| Boundary | Why |
|---|---|
| `api` vs `worker` | **Scaling + failure isolation.** API is cheap, latency-sensitive, many connections; workers are long-running and scaled by queue depth. A stuck worker cannot stop submission or status reads |
| `researcher` vs `advisor` | **Independent release + failure isolation + resource profile.** Researcher owns the index and embedding model (memory-heavy); advisor is light. Prompts/tools can be released separately; either can be down and the coordinator degrades |
| `redis` | One shared state/queue/cache/rate-limit/breaker store; the breaker must be shared so every process sees the provider's health |
| `indexer` | **Release + resource.** One-shot, bursty CPU, writes the index; services mount it read-only. Re-index without touching services |
| `stub-llm` | Isolates provider variance for load tests; the fault-injection point for demos |
| `jaeger`/`prometheus`/`grafana`/`cadvisor` | Observability stack independent of the app's lifecycle |
Rejected: a single process with two prompts (not two agents, no failure isolation); Celery (heavier than needed for one queue); Chroma (FAISS gives cheap per-id removal and is enough at this corpus size); callbacks instead of polling (SSRF and delivery-retry cost for no benefit).

## Async execution (A4)
Runs take tens of seconds (several model calls). `POST` returns `202` + run id immediately; the caller polls `GET /v1/advisories/{id}`. A Redis-backed RQ queue sits between submission and execution. Polling is stateless for the client, retryable, and needs no inbound path to the caller.

## Observability (D5, A6)
Structured JSON logs with `correlation_id` = run id across api → worker → agents (`X-Correlation-ID` header). OpenTelemetry spans (FastAPI, httpx, `run`, `agent.*`) to Jaeger. Prometheus scrapes every service; workers are discovered by DNS so `--scale` is picked up. Grafana dashboard `desk.json` (generated by `observability/gen_dashboard.py`): p50/p95 latency, per-step latency, queue, throughput, error rate, availability, task success, steps/tool calls per run, retry rate, breaker state, per-container CPU/memory/uptime (cAdvisor), cost and tokens per step, cache hit ratio, rate-limit/validation/guardrail counters, live quality, index freshness. `GET /v1/advisories/{id}/trace` reconstructs a run.

## Guardrails (L4)
Ingress: every free-text field is scanned for injection patterns/score and toxicity; blocked requests are quarantined and no run starts. Surviving text is NFKC-normalised, stripped of control markers and fenced as `<untrusted_user_text>` in prompts. Tools: schema-validated arguments (identifier regexes, enums, bounds) so injected text cannot widen what a tool can do. Output: determinations the desk may not make are filtered in the advisor and again at the final gate; routing is decided by code, not by the model. Limits: pattern scanners are a layer, not a guarantee; the structural controls (untrusted fencing, validated tools, verification, code-owned routing) carry the weight.

## Caching and rate limiting (D7)
Embedding LRU (in process), retrieval cache (Redis, keyed by query/strategy/domain/index version), whole-run cache (keyed by normalised request + prompt version + model, TTL 1 h, never for degraded runs). Whole-run caching saves the most (a hit skips every model call) and is safe because the key includes the prompt and index inputs and the TTL bounds staleness against corpus updates. Rate limit: sliding window per client per minute plus a daily run budget (a looping client is stopped by 429 before it can spend the budget). Measured effect: `scripts/cache_benchmark.py`.
