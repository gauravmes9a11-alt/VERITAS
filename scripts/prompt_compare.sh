#!/usr/bin/env bash
# L2: same evaluation set, two prompt versions (v1 minimal vs v2 explicit exception-check method).
set -e; mkdir -p results
docker compose run --rm eval python -m eval.run --label prompt_v1 --prompt-version v1 --out results/eval_prompt_v1.json
docker compose run --rm eval python -m eval.run --label prompt_v2 --prompt-version v2 --out results/eval_prompt_v2.json
python -m eval.compare results/eval_prompt_v1.json results/eval_prompt_v2.json | tee results/prompt_compare.txt
