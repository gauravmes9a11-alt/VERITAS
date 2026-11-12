#!/bin/bash
set -e

echo "=== Starting Redis server in background ==="
redis-server --daemonize yes --bind 127.0.0.1 --protected-mode no

echo "=== Checking / initializing SQLite database ==="
python scripts/init_db.py || true

echo "=== Launching VERITAS Services on port ${PORT:-8000} ==="
exec python scripts/run_services.py
