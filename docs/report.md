# Report: Workplace Compliance Advisory Desk

> Sections marked **[MEASURE]** need numbers from your own run (commands given). Everything else describes what is built.

## 1. Problem and scope
HR compliance questions (gratuity, maternity benefit, workplace harassment) whose answer depends on which law governs, whether the provision is in force, and on provisos that the question never mentions. The service returns a cited advisory or escalates; it never gives a legal opinion. Out of scope: model training, cloud deployment, real employee data (requests containing emails/phones/ID numbers are rejected). Prioritisation: first a working end-to-end path (submit → validate → queue → research → advise/verify → route), the 4.1 case, then escalation and safety, then observability, evaluation and load testing.

## 2. Agent design
Topology, tools, verification, loop limits and failure behaviour: `docs/architecture.md`. Summary: coordinator (LangGraph) → Researcher agent (tool loop; search / get_clause / get_provisions / get_request_field; ≤ 8 steps, ≤ 10 tool calls, 90 s deadline) → Advisor agent (compose + verify) → optional second research round driven by verification gaps (≤ 2) → route. Distinct processes, prompts, tool sets and failure domains.

## 3. Architecture diagram with deployment boundaries
See the diagram and the boundary table in `docs/architecture.md` (api | worker | researcher | advisor | redis | indexer | stub-llm | observability stack).

## 4. Design decisions and what was rejected
Async 202+poll (rejected: synchronous request, callbacks). RQ (rejected: Celery, too heavy for one queue). FAISS with ID map (rejected: Chroma; per-id removal and zero extra service). Section-aware chunking with proviso-context embeddings and `has_provisos` flags (rejected: fixed windows; kept as ablation). Rule-based domain/entity classification and code-owned routing (rejected: LLM routing; cost, auditability and injection surface). Verifier includes a *completeness* check (rejected: relying on the model to remember exceptions). Redis-shared circuit breaker (rejected: per-process breakers that disagree). Hash embedder as offline fallback beside sentence-transformers. Whole-run + retrieval + embedding caches with TTL and index/prompt in the key.

## 5. Evaluation (L2, L3)
Set: `eval/scenarios.jsonl`, 23 scenarios with expected route and supporting clause ids (includes the 4.1 case, a repealed-Act trap, a pre-commencement as-of case, escalations, a detail request and an injection). Run: `docker compose run --rm eval`. Metric definitions (`app/scoring.py`): groundedness, faithfulness, context relevance, hallucination rate (proxy metrics from token/embedding overlap, not an LLM judge; stated so you can defend them), plus route accuracy, gold-clause recall (retrieved/cited), repealed-citation violations.
* Prompt comparison v1 vs v2 (`scripts/prompt_compare.sh`): **[MEASURE]** which metric, by how much.
* Deliberate change (`scripts/ablation_chunking.sh`, section-aware → naive chunking): **[MEASURE]** four metrics before/after. Expected direction: naive chunking loses gold recall on proviso scenarios (S01, S03, S04, S09, S10, S15) because provisos are no longer separate, linked chunks.
* Development-time sanity (stub provider, not a result): 23/23 routes correct; 4.1 correct; stub content metrics are meaningless because the stub's answers are canned.

## 6. Performance and cost
SLO (declared in advance): `docs/slo.md`. **[MEASURE]** `loadtest/run_scaling.sh` → RPS, p50, p95 at 1 and 3 workers, concurrency at which the SLO fails, responsible component (name it from Grafana CPU and per-step latency). Cache effect: `python scripts/cache_benchmark.py` **[MEASURE]**. Cost per 100 runs and largest driver: `scripts/cost_report.py` with `LLM_PRICE_*` set **[MEASURE]**. Development observation on the stub: researcher prompts dominate tokens (~3.3k of ~4.2k prompt tokens per run) because observations accumulate each step; the advisor is small.

## 7. What did not work / limitations
Heuristic injection/toxicity scanners miss novel phrasing (structural controls compensate). Parser tuned on fixtures; official PDFs need tuning for tables of contents and layouts. Faithfulness/groundedness are lexical proxies. Gratuity applicability thresholds and transitional provisions are only partly modelled (service straddling 21 Nov 2025 escalates rather than reasons). Incidents: `docs/incident_log.md`.
