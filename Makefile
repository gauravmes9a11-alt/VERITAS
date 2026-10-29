.PHONY: up up3 down test eval demo index reembed load contracts dashboard secrets
up: ; docker compose up --build
up3: ; docker compose up --build --scale worker=3
down: ; docker compose down
test: ; python -m pytest -q tests
index: ; docker compose run --rm indexer
reembed: ; docker compose run --rm indexer python -m app.corpus.indexer reembed
eval: ; docker compose run --rm eval
demo: ; scripts/demo.sh
load: ; loadtest/run_scaling.sh
contracts: ; PYTHONPATH=. python scripts/export_contracts.py
dashboard: ; python observability/gen_dashboard.py
secrets: ; scripts/check_no_secrets.sh
