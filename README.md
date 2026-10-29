# Workplace Compliance Advisory Desk 

A multi-agent service that answers HR compliance questions (gratuity, maternity benefit, workplace harassment) with **cited advisories grounded in provisions in force**, and escalates to legal counsel whenever it cannot verify an answer. It never gives a legal opinion.

The hard case from the brief (18-month fixed-term contract, gratuity) is handled by an agent that finds the five-year rule, discovers that a proviso modifies it, reads the proviso, and a verifier that refuses any answer citing a rule without its provisos.

## Quick start (clean clone)

```bash
git clone <repo> && cd <repo>
docker compose up --build            # first build downloads the embedding stack; allow several minutes
# open http://localhost:8000   (web client)       http://localhost:3000 (Grafana, admin / change-me)
#      http://localhost:16686  (Jaeger traces)    http://localhost:9090 (Prometheus)
```
No `.env` is needed: defaults come from `.env.example` (stub LLM provider, hash embeddings). To go faster on the first build: `EMBEDDINGS=hash docker compose up --build` (skips torch).
To use real embeddings/provider, `cp .env.example .env` and edit (see the comments in the file). Set a provider spending limit first.

Scale the task runners: `docker compose up -d --scale worker=3`.

> **Corpus.** The repo ships *synthetic paraphrased fixtures* in `corpus/samples/` so it starts without downloads. They are **not** the official text. Download the official PDFs into `corpus/data/` (see `corpus/README.md`), run `docker compose run --rm indexer`, then set `REQUIRE_OFFICIAL_CORPUS=true`. `/health` of researcher/advisor reports `corpus_is_synthetic`. Re-check `eval/scenarios.jsonl` gold clause ids against the official section numbering.

## Try it

```bash
curl -s -X POST localhost:8000/v1/advisories -H 'Content-Type: application/json' -d '{
  "client_id":"me-001",
  "question":"An employee engaged after 21 November 2025 on a written fixed-term contract of 18 months has completed the contract. Is gratuity payable?"}'
# -> 202 {"run_id": "...", "status_url": "/v1/advisories/<id>"}
curl -s localhost:8000/v1/advisories/<id>          # poll until status=completed
curl -s localhost:8000/v1/advisories/<id>/trace    # every tool call, query, passage, prompt version, latency, cost, verification
```
Or `scripts/ask.sh '<json>'` (submits and polls). Full demo walk-through: `scripts/demo.sh` (needs the stack up).

## API

| Endpoint | Purpose |
|---|---|
| `POST /v1/advisories` | Validate (schema/range/category/cross-field), guardrail-scan, rate-limit, enqueue. `202` with run id. `422` quarantined, `400` guardrail, `429` rate limited |
| `GET /v1/advisories/{id}` | Status and result (route, basis, citations, escalation, missing fields, prompt version) |
| `GET /v1/advisories/{id}/trace` | Full run reconstruction |
| `POST /v1/feedback`, `GET /v1/feedback/summary` | Feedback with reason, persisted and aggregated |
| `GET /v1/quality` | Sampled live scores over time |
| `GET /v1/quarantine` | Rejected requests with field-level errors |
| `GET /v1/contracts/{name}` | JSON Schemas (`ask_request`, `feedback`, `run_result`); also in `contracts/` |
| `GET /health`, `GET /metrics` | on every service |
| `http://localhost:8000/docs` | OpenAPI |

Routes returned: `answer-with-citation`, `request-more-establishment-detail`, `legal-counsel-review`.

## Services (docker compose)

`api` (FastAPI) · `worker` (RQ task runner / LangGraph coordinator, scalable) · `researcher` (Agent 1) · `advisor` (Agent 2) · `redis` (queue, state, caches, rate limits) · `indexer` (one-shot corpus pipeline) · `stub-llm` · `jaeger` · `prometheus` · `cadvisor` · `grafana` · `eval` (profile `tools`). Boundaries are justified in `docs/architecture.md`.

## Tests, evaluation, load

```bash
pip install -r requirements.txt -r requirements-dev.txt && make test        # unit tests, no services needed
docker compose run --rm eval                                              # 23-scenario batch evaluation -> results/eval_baseline.json
scripts/prompt_compare.sh                                                 # L2: prompt v1 vs v2 on the same set
scripts/ablation_chunking.sh                                              # L3: one deliberate change, four metrics before/after
loadtest/run_scaling.sh                                                   # D6/A7: Locust vs the declared SLO, 1 vs 3 workers
python scripts/cache_benchmark.py                                         # D7 measured effect
docker compose exec api python scripts/cost_report.py                     # L5 cost per 100 runs, largest driver
make secrets                                                              # D4 check
```
Docs: `docs/architecture.md`, `docs/data_contract.md`, `docs/slo.md`, `docs/runbook.md`, `docs/incident_log.md`, `docs/report.md`.

## Repository map
`app/` service code · `app/agents/` the two agents · `app/prompts/` versioned prompts · `app/corpus/` chunker, embedder, FAISS store, indexer · `eval/` scenarios and runner · `stub/` stub provider · `loadtest/` Locust + SLO · `observability/` Prometheus, Grafana (generated dashboard) · `contracts/` JSON Schemas · `scripts/` ops and demo · `tests/`.

## Honest status
Verified in development without Docker (real Redis, FAISS, LangGraph, RQ, uvicorn processes via `scripts/run_local.sh`): 22 unit tests pass; the 23-scenario routing set passes against the stub; demonstration items 2-8 and 10 behave as described. 
**Not yet run:** `docker compose up` itself, Grafana/Jaeger/cAdvisor rendering, sentence-transformers embeddings, a real LLM provider, the real corpus, and the Locust/ablation/prompt-comparison numbers. Those need your machine; the commands above produce them. Do a full clean-clone `docker compose up` rehearsal before the demo (a failure to start scores zero).
