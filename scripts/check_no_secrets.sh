#!/usr/bin/env bash
# D4 gate: no credentials, provider endpoints or model identifiers in tracked code.
cd "$(dirname "$0")/.."
BAD=$(grep -rIEn --exclude-dir=.git --exclude-dir=results --exclude=.env.example --exclude=check_no_secrets.sh --exclude=report.md \
  -e 'sk-[A-Za-z0-9]{20,}' -e 'api[_-]?key *= *["'"'"'][A-Za-z0-9]{16,}' -e 'https?://(api\.openai|api\.anthropic|generativelanguage|api\.groq|openrouter)' \
  -e '(gpt-[0-9]|claude-[0-9a-z-]+|gemini-[0-9]|llama-?[0-9]|mistral-)' -e 'all-MiniLM' . )
if [ -n "$BAD" ]; then echo "FOUND:"; echo "$BAD"; exit 1; fi; echo "clean: no secrets, endpoints or model ids"
