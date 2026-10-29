"""Generates observability/grafana/dashboards/desk.json. Edit here, re-run, commit the JSON."""
import json
P = []; y = 0; n = 0
def panel(title, exprs, x, w=8, h=7, unit="short", kind="timeseries"):
    global n
    n += 1
    P.append({"id": n, "title": title, "type": kind, "datasource": {"type": "prometheus", "uid": "prom"}, "gridPos": {"x": x, "y": y, "w": w, "h": h},
              "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
              "targets": [{"expr": e, "legendFormat": l, "refId": chr(65 + i)} for i, (e, l) in enumerate(exprs)]})
def row(*specs):
    global y
    x = 0
    for s in specs:
        opts = s[2] if len(s) > 2 else {}
        panel(*s[:2], x=x, **opts)
        x += opts.get("w", 8)
    y += 7
q = lambda p, m, extra="": f"histogram_quantile({p}, sum by (le{extra}) (rate({m}_bucket[2m])))"
row(("Run latency end-to-end p50 / p95 (s)", [(q(.5, "desk_run_duration_seconds"), "p50"), (q(.95, "desk_run_duration_seconds"), "p95")], {"unit": "s"}),
    ("Per-step latency p95 (s)", [(q(.95, "desk_step_duration_seconds", ", agent, step"), "{{agent}}/{{step}}")], {"unit": "s"}),
    ("Queue wait p95 (s) / depth", [(q(.95, "desk_queue_wait_seconds"), "wait p95"), ("max(desk_queue_depth)", "depth")], {"unit": "s"}))
row(("Throughput (runs/s) and API req/s", [("sum(rate(desk_runs_total[1m]))", "runs/s"), ("sum(rate(desk_http_request_duration_seconds_count{service=\"api\"}[1m]))", "api req/s")], {"unit": "reqps"}),
    ("Error rate (failed runs, API 5xx)", [("sum(rate(desk_runs_total{status=\"failed\"}[2m])) / clamp_min(sum(rate(desk_runs_total[2m])),1e-9)", "failed runs"),
     ("sum(rate(desk_http_request_duration_seconds_count{status=~\"5..\"}[2m])) / clamp_min(sum(rate(desk_http_request_duration_seconds_count[2m])),1e-9)", "http 5xx")], {"unit": "percentunit"}),
    ("Availability (up by job)", [("avg by (job) (up)", "{{job}}")], {"unit": "percentunit"}))
row(("Task success rate (completed, not degraded)", [("sum(rate(desk_runs_total{status=\"completed\",degraded=\"false\"}[5m])) / clamp_min(sum(rate(desk_runs_total[5m])),1e-9)", "success")], {"unit": "percentunit"}),
    ("Steps and tool calls per run (avg)", [("rate(desk_steps_per_run_sum[5m]) / clamp_min(rate(desk_steps_per_run_count[5m]),1e-9)", "steps"),
     ("rate(desk_tool_calls_per_run_sum[5m]) / clamp_min(rate(desk_tool_calls_per_run_count[5m]),1e-9)", "tool calls")]),
    ("Retry rate (retries / downstream calls)", [("sum by (target) (rate(desk_retries_total[2m])) / clamp_min(sum by (target) (rate(desk_downstream_calls_total[2m])),1e-9)", "{{target}}")], {"unit": "percentunit"}))
row(("Routes", [("sum by (route) (rate(desk_runs_total[5m]))", "{{route}}")], {}),
    ("Circuit breaker open (1 = open) / trips", [("max by (target) (desk_circuit_breaker_open)", "open {{target}}"), ("sum by (target) (increase(desk_circuit_breaker_trips_total[5m]))", "trips {{target}}")]),
    ("Verification outcomes", [("sum by (outcome) (rate(desk_verification_total[5m]))", "{{outcome}}")]))
row(("Container CPU (cores)", [("sum by (name) (rate(container_cpu_usage_seconds_total{name!=\"\"}[1m]))", "{{name}}")]),
    ("Container memory (bytes)", [("sum by (name) (container_memory_working_set_bytes{name!=\"\"})", "{{name}}")], {"unit": "bytes"}),
    ("Container uptime (s)", [("time() - container_start_time_seconds{name!=\"\"}", "{{name}}")], {"unit": "s"}))
row(("LLM cost USD per step (rate/min)", [("sum by (step) (increase(desk_llm_cost_usd_total[5m]))", "{{step}}")], {"unit": "currencyUSD"}),
    ("LLM tokens per step", [("sum by (step, kind) (increase(desk_llm_tokens_total[5m]))", "{{step}} {{kind}}")]),
    ("LLM call latency p95 (s)", [(q(.95, "desk_llm_call_duration_seconds", ", step"), "{{step}}")], {"unit": "s"}))
row(("Cache hit ratio by layer", [("sum by (layer) (rate(desk_cache_total{result=\"hit\"}[5m])) / clamp_min(sum by (layer) (rate(desk_cache_total[5m])),1e-9)", "{{layer}}")], {"unit": "percentunit"}),
    ("Rate-limited / validation rejects / guardrail blocks", [("sum by (reason) (increase(desk_rate_limited_total[5m]))", "rate {{reason}}"), ("sum by (reason) (increase(desk_validation_rejects_total[5m]))", "reject {{reason}}"),
     ("sum by (kind, stage) (increase(desk_guardrail_blocks_total[5m]))", "guard {{kind}}/{{stage}}")]),
    ("Live quality score (sampled runs, rolling 20)", [("avg by (metric) (desk_live_quality_score)", "{{metric}}")], {"unit": "percentunit"}))
row(("Index freshness (age s)", [("time() - max(desk_index_built_timestamp_seconds)", "age")], {"unit": "s", "w": 6}),
    ("Index chunks per doc", [("max by (doc) (desk_index_chunks)", "{{doc}}")], {"w": 6}),
    ("Indexing throughput (chunks/s) / duration (s)", [("max(desk_pipeline_throughput_chunks_per_second)", "chunks/s"), ("max(desk_pipeline_duration_seconds)", "duration s")], {"w": 6}),
    ("Feedback", [("sum by (rating) (desk_feedback_total)", "{{rating}}")], {"w": 6}))
d = {"uid": "desk", "title": "Compliance Desk", "schemaVersion": 39, "version": 1, "refresh": "5s", "time": {"from": "now-30m", "to": "now"}, "panels": P}
open("observability/grafana/dashboards/desk.json", "w").write(json.dumps(d, indent=1))
print(len(P), "panels")
