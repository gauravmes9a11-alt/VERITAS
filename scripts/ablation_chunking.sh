#!/usr/bin/env bash
# L3 "one deliberate change, four metrics before and after".
# Change: section-aware chunking (provisos separate, linked to their rule)  ->  naive fixed-size chunking.
# Hypothesis: naive chunking breaks the section-4.1 case and lowers gold recall/groundedness.
set -e; mkdir -p results
docker compose up -d --wait
docker compose run --rm eval python -m eval.run --label section_aware --out results/eval_section_aware.json
docker compose run --rm indexer python -m app.corpus.indexer build --chunking naive --index-name naive
INDEX_NAME=naive docker compose up -d --force-recreate researcher advisor --wait
docker compose run --rm -e INDEX_NAME=naive eval python -m eval.run --label naive --out results/eval_naive.json
INDEX_NAME=main docker compose up -d --force-recreate researcher advisor --wait   # restore
python -m eval.compare results/eval_section_aware.json results/eval_naive.json | tee results/ablation_chunking.txt
