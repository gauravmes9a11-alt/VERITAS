#!/usr/bin/env bash
# Developer convenience: run the whole stack WITHOUT Docker (needs a local redis-server).
# The graded path is `docker compose up`; this exists for fast iteration and tests.
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd); cd "$ROOT"
export REDIS_URL=${REDIS_URL:-redis://localhost:6379/0} INDEX_ROOT=${INDEX_ROOT:-/tmp/desk-index} CORPUS_DIR=$ROOT/corpus
export LLM_BASE_URL=${LLM_BASE_URL:-http://localhost:8080/v1} LLM_MODEL=${LLM_MODEL:-stub-model}
export STUB_DELAY_MS=${STUB_DELAY_MS:-100} STUB_JITTER_MS=${STUB_JITTER_MS:-20}
export RESEARCHER_URL=http://localhost:8001 ADVISOR_URL=http://localhost:8002 PYTHONPATH=$ROOT
LOGS=${LOGS:-/tmp/logs}; mkdir -p "$LOGS" "$INDEX_ROOT"
redis-cli ping >/dev/null 2>&1 || redis-server --port 6379 --save "" --daemonize yes >/dev/null
python -m app.corpus.indexer build >/dev/null 2>&1
for spec in "stub.app:app:8080:stub" "app.agents.researcher_service:app:8001:researcher" "app.agents.advisor_service:app:8002:advisor" "app.api:app:8000:api"; do
  IFS=: read -r mod obj port name <<<"$spec"
  setsid nohup uvicorn $mod:$obj --port $port >"$LOGS/$name.log" 2>&1 < /dev/null &
done
for i in $(seq 1 ${WORKERS:-1}); do
  if [ "$i" = 1 ]; then P=9101; else P=$((9100+i)); fi
  setsid nohup python -c "
import logging,sys
from prometheus_client import start_http_server
from rq import Queue, SimpleWorker
from app import logging_setup, coordinator
from app.state import rq_conn
logging_setup.setup('worker'); start_http_server($P)
SimpleWorker([Queue('runs', connection=rq_conn())], connection=rq_conn()).work()" >"$LOGS/worker$i.log" 2>&1 < /dev/null &
done
sleep 4; echo "stack up: api :8000 researcher :8001 advisor :8002 stub :8080"
