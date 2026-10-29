# Incident log

Entries below are real failures hit while the system was being built and tested (scripts/run_local.sh stack: real Redis, FAISS, RQ, uvicorn; no Docker). **Add your own entries from `docker compose` runs** (build failures, healthcheck timing, provider errors) before the demo; the brief asks for genuine entries from your own development.

## INC-001: Worker crashed on first dequeue; every run stuck in `queued`
* **Detected:** first end-to-end submit polled for 90 s with status `queued`; worker log showed "unhandled exception, quitting" in `dequeue_job_and_maintain_ttl`.
* **Cause:** the shared Redis client was created with `decode_responses=True` (convenient for JSON state), but RQ stores binary payloads and needs a bytes connection.
* **Impact:** total outage of run execution (API still accepted requests: submission and execution are decoupled, so the queue silently grew).
* **Fix:** separate `state.rq_conn()` (no `decode_responses`) used by API enqueue and the worker.
* **Prevention:** runbook failure mode 2 covers this symptom; worker healthcheck checks the metrics endpoint; add a queue-depth alert in production.

## INC-002: Section 53 silently missing from the index
* **Detected:** inspecting chunk ids after the first index build showed the Gratuity Act chunks but no `coss_2020:s53:*`; section titles were also split mid-word ("yment of gratuity").
* **Cause:** two parser bugs: a non-greedy heading regex let the body start inside the title, and a "numbering gap ≤ 40" guard (meant to reject footnotes) rejected section 53 following section 1.
* **Impact:** the governing provision for the central case would never have been retrievable; no error was raised.
* **Fix:** heading regex requires a terminating period/dash after the title; removed the gap guard and kept monotonic numbering only. Added `test_sections_split` and `test_proviso_chunks`.
* **Prevention:** the indexer logs chunks per document; check them after swapping in official PDFs (real PDFs will need parser tuning for tables of contents, footers and "Provided that" formatting).

## INC-003: Router asked for more detail on questions that already contained it
* **Detected:** batch evaluation (23 scenarios) reported 6 wrong routes: gratuity questions with "6 years of service" returned `request-more-establishment-detail`.
* **Cause:** the required-facts rule demanded `employment_type` even when duration or a death/disablement reason was given.
* **Fix:** gratuity needs a duration (years, months, or dates) or a death/disablement separation; employment type is optional. Routing accuracy on the set went 74 % → 100 %.
* **Prevention:** the evaluation set runs in CI-style; route accuracy is a tracked metric.

## INC-004: Verification loop exhausted without the fix (design validation, not an outage)
* **Observed:** with the stub provider the researcher expanded the wrong section (the stub ignored hints), so verification failed three times on a missing explanation; the run correctly escalated instead of returning an unverified answer.
* **Outcome:** confirmed the bounded-loop and escalate-don't-guess behaviour. The stub now follows `get_provisions(...)` hints, as a real model is prompted to.
