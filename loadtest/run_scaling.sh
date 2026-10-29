#!/usr/bin/env bash
# A7: measure throughput and p95 at 1 and 3 workers, then a user sweep to find where the SLO fails.
# Prereq: .env uses the stub provider (default). Run from the repo root.
set -e; mkdir -p results
USERS_SWEEP=${USERS_SWEEP:-"2 5 10 20 40"}
for W in 1 3; do
  docker compose up -d --scale worker=$W --wait
  sleep 5
  for U in $USERS_SWEEP; do
    locust -f loadtest/locustfile.py --host http://localhost:8000 --headless -u $U -r 5 -t ${DURATION:-180s} --csv results/lt_w${W}_u${U} --only-summary >/dev/null 2>&1 || true
    echo "== workers=$W users=$U"; python loadtest/report.py results/lt_w${W}_u${U}_stats.csv
  done
done
