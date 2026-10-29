# Runbook (D8): the two most likely failures

## 1. LLM provider unavailable or rate-limited
**Symptoms.** Runs complete as `legal-counsel-review` with `degraded=true` and basis "Degraded mode…"; p95 latency spikes then drops (fail-fast); Grafana "Circuit breaker open" = 1; retry rate high; no `answer-with-citation` outputs.
**Confirm.**
```bash
docker compose exec redis redis-cli get cb:llm_provider:open_until      # epoch in the future => open
docker compose logs researcher advisor | grep -E 'circuit opened|retrying|provider'   # JSON logs, filter by correlation_id = run id
curl -s localhost:9090/api/v1/query --data-urlencode 'query=increase(desk_circuit_breaker_trips_total[10m])'
curl -s $LLM_BASE_URL/models -H "Authorization: Bearer $LLM_API_KEY"    # is the provider itself reachable? (real provider)
```
**Recover.** (1) Check provider status page / quota / spending limit; fix the key or limit. (2) Nothing needs restarting: after `BREAKER_COOLDOWN_S` one probe call is allowed, and success closes the breaker. Force-close: `redis-cli del cb:llm_provider:fail cb:llm_provider:open_until`. (3) If the outage is long, runs keep escalating safely; re-submit affected requests afterwards (no answer was ever returned unverified). (4) If it is 429s from a free tier, lower load or raise `RATE_LIMIT_PER_MIN`'s opposite (reduce it), and keep `CACHE_ENABLED=true`. (5) Switch provider by editing `LLM_BASE_URL/LLM_MODEL/LLM_API_KEY` in `.env` and `docker compose up -d researcher advisor`.

## 2. Queue backlog / workers not consuming
**Symptoms.** `status` stays `queued`; p95 end-to-end climbs while API latency is fine; "queue depth" panel rising; `rq:queue:runs` length growing.
**Confirm.**
```bash
docker compose exec redis redis-cli llen rq:queue:runs
docker compose ps worker                                   # healthy? restarting?
docker compose logs --tail=50 worker                       # worker tracebacks (e.g. Redis connection errors)
curl -s localhost:9090/api/v1/query --data-urlencode 'query=up{job="worker"}'
docker compose exec redis redis-cli smembers rq:workers    # registered workers
```
**Recover.** (1) Worker crashed or wedged: `docker compose restart worker`. (2) Genuine overload: `docker compose up -d --scale worker=3` (see `docs/report.md` for where scaling stops helping). (3) Jobs stuck `running` after a crash: they hit the RQ job timeout; re-submit them. (4) If one client is flooding, check `desk_rate_limited_total` and lower `RATE_LIMIT_PER_MIN`/`RATE_LIMIT_DAILY_RUNS`. (5) Purge poisoned backlog only if necessary: `redis-cli del rq:queue:runs`.
