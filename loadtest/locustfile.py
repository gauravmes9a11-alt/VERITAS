"""D6: closed-loop load test. Each virtual user submits a run then polls until it completes;
the recorded time is the whole run (submission to final result), not the submit call.
Run against the stub provider:  locust -f loadtest/locustfile.py --host http://localhost:8000 --headless -u 10 -t 3m --csv results/lt"""
import itertools
import random
import time

from locust import HttpUser, between, events, task

QUESTIONS = [
    {"question": "An employee engaged after 21 November 2025 on a written fixed-term contract of 18 months has completed the contract. Is gratuity payable?"},
    {"question": "An employee with 6 years of continuous service is retiring. Is gratuity payable?"},
    {"question": "A woman employee has worked 95 days in the last 12 months before her expected delivery. Is she entitled to maternity benefit?", "employment": {"days_worked_last_12_months": 95}},
    {"question": "Does our company with 45 employees need to constitute an Internal Committee under the POSH Act?", "establishment": {"employee_count": 45}},
    {"question": "How is the amount of gratuity calculated for an employee with 8 years of service?"},
]
ids = itertools.count(1)


class DeskUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        self.cid = f"lt-{next(ids):05d}"          # one client id per virtual user: rate limit is per client

    @task
    def full_run(self):
        body = dict(random.choice(QUESTIONS), client_id=self.cid, options={"no_cache": True})  # bypass whole-run cache: measure the system
        t0 = time.time()
        with self.client.post("/v1/advisories", json=body, name="submit", catch_response=True) as r:
            if r.status_code != 202:
                r.failure(f"submit {r.status_code}")
                return
            rid = r.json()["run_id"]
        status = "timeout"
        while time.time() - t0 < 180:
            time.sleep(0.5)
            with self.client.get(f"/v1/advisories/{rid}", name="poll", catch_response=True) as p:
                s = p.json()
                if s["status"] in ("completed", "failed"):
                    status = s["status"] if not s.get("degraded") else "degraded"
                    break
        total_ms = (time.time() - t0) * 1000
        events.request.fire(request_type="RUN", name="end_to_end_run", response_time=total_ms, response_length=0,
                            exception=None if status == "completed" else Exception(status), context={})
