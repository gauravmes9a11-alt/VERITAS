# Data contracts (D1)
Machine-readable: `contracts/*.schema.json` (generated from the Pydantic models by `scripts/export_contracts.py`; also served at `/v1/contracts/{name}`). Enforced at the ingestion boundary before any agent starts.

## `POST /v1/advisories` (ask_request)
| Field | Type | Rule |
|---|---|---|
| client_id | string | `^[a-z0-9_-]{3,64}$`, required |
| question | string | 10-2000 chars, required; scanned for PII (email, phone, 12-digit id), injection, toxicity |
| establishment.employee_count | int | 1..1,000,000 |
| establishment.establishment_type | enum | factory, shop_establishment, it_services, mine, plantation, other |
| establishment.has_internal_committee | bool | |
| employment.employment_type | enum | permanent, fixed_term, probationary, contract_labour |
| employment.start_date / end_date | date | 1950-01-01..2100-01-01 |
| employment.contract_months | int | 1..600 |
| employment.years_of_service | float | 0..60 |
| employment.days_worked_last_12_months | int | 0..366 |
| employment.separation_reason | enum | ongoing, contract_expiry, resignation, retirement, superannuation, death, disablement, termination |
| as_of_date | date | 2020-01-01..2100-01-01; defaults to today |
| notes | string | ≤ 500 chars; same scans as question |
| options.no_cache / options.prompt_version | bool / `^v\d{1,2}$` | eval/load-test overrides (`ALLOW_OPTIONS_OVERRIDE`) |

Unknown fields are rejected. **Cross-field rules:** `end_date ≥ start_date`; `as_of_date ≥ start_date`; `fixed_term` requires `contract_months` or both dates; `contract_expiry` only valid for `fixed_term`; `contract_months` must agree (±1) with the date span; `years_of_service` cannot exceed the span from `start_date`.
**Rejection:** `422 {error: validation_failed, quarantine_id, errors:[{field,error,type}], run_started:false}`; the record is stored in the Redis `quarantine` list (`GET /v1/quarantine`). Guardrail rejection: `400 guardrail_blocked`, also quarantined. Rate limit: `429` with `Retry-After`.

## Response (run_result)
`run_id, status(queued|running|completed|failed), route, route_basis, summary, assertions[{text, citations[{chunk_id,label,status,text}]}], recommendation, escalation, missing_fields, prompt_version, index_version, degraded, cached, verification, cost{...by_step}, disclaimer`.

## `POST /v1/feedback`
`run_id` (existing), `rating` in {satisfactory, unsatisfactory}, `reason` 3-500 chars (PII-scanned). `201 {stored:true}`.

## Internal contracts
Agent hand-off (`ResearchIn`, `AdviseIn`) in `app/agents/*_service.py`; trace event shape in `docs/architecture.md`; index manifest in `app/corpus/indexer.py`.
