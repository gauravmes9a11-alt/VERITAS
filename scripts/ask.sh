#!/usr/bin/env bash
# usage: scripts/ask.sh '<json body>'   -> submits, polls, prints result
API=${API:-http://localhost:8000}
R=$(curl -s -X POST $API/v1/advisories -H 'Content-Type: application/json' -d "$1")
echo "$R" | python3 -c "import sys,json;d=json.load(sys.stdin);print('submit:',json.dumps(d)[:300])"
ID=$(echo "$R" | python3 -c "import sys,json;print(json.load(sys.stdin).get('run_id',''))")
[ -z "$ID" ] && exit 0
for i in $(seq 1 90); do
  S=$(curl -s $API/v1/advisories/$ID)
  ST=$(echo "$S" | python3 -c "import sys,json;print(json.load(sys.stdin)['status'])")
  if [ "$ST" = completed ] || [ "$ST" = failed ]; then echo "$S" | python3 -m json.tool | head -${LINES_OUT:-60}; echo "RUN_ID=$ID"; exit 0; fi
  sleep 1
done
echo "timeout polling"
