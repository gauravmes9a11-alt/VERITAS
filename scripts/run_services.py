"""Run all Workplace Compliance Desk services locally on Windows/Linux or in Cloud (Railway/Docker).
Usage:
    python scripts/run_services.py
"""
import os
import sys
import time
import socket
import subprocess
import signal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "logs"
INDEX_ROOT = ROOT / "data" / "index"
CORPUS_DIR = ROOT / "corpus"

PORT = int(os.environ.get("PORT", "8000"))
API_HOST = "0.0.0.0"

SERVICES = [
    {"name": "stub", "cmd": [sys.executable, "-m", "uvicorn", "stub.app:app", "--host", "127.0.0.1", "--port", "8080", "--log-level", "info"], "health": "http://127.0.0.1:8080/health"},
    {"name": "researcher", "cmd": [sys.executable, "-m", "uvicorn", "app.agents.researcher_service:app", "--host", "127.0.0.1", "--port", "8001", "--log-level", "info"], "health": "http://127.0.0.1:8001/health"},
    {"name": "advisor", "cmd": [sys.executable, "-m", "uvicorn", "app.agents.advisor_service:app", "--host", "127.0.0.1", "--port", "8002", "--log-level", "info"], "health": "http://127.0.0.1:8002/health"},
    {"name": "api", "cmd": [sys.executable, "-m", "uvicorn", "app.api:app", "--host", API_HOST, "--port", str(PORT), "--log-level", "info"], "health": f"http://127.0.0.1:{PORT}/health"},
    {"name": "worker", "cmd": [sys.executable, "-m", "app.worker"], "health": "http://127.0.0.1:9101/metrics"},
]

def check_redis():
    redis_url = os.environ.get("REDIS_URL")
    if redis_url and not any(h in redis_url for h in ("127.0.0.1", "localhost")):
        try:
            import redis
            client = redis.Redis.from_url(redis_url, socket_timeout=3.0)
            if client.ping():
                print(f"[+] Remote Redis reachable at {redis_url[:30]}...")
                return True
        except Exception as e:
            print(f"[-] Could not connect to remote Redis ({redis_url[:30]}...): {e}")

    # Check local port 6379
    try:
        with socket.create_connection(("127.0.0.1", 6379), timeout=1.5):
            print("[+] Redis is running on 127.0.0.1:6379")
            return True
    except OSError:
        pass

    # Windows WSL fallback: bind to 0.0.0.0
    print("[*] Redis not responding on 127.0.0.1:6379. Attempting to start via WSL...")
    try:
        subprocess.run(["wsl", "-u", "root", "-e", "bash", "-c", "killall -9 redis-server 2>/dev/null; /usr/bin/redis-server --bind 0.0.0.0 --protected-mode no --daemonize yes"], capture_output=True, text=True, timeout=10)
        time.sleep(2)
        with socket.create_connection(("127.0.0.1", 6379), timeout=2.0):
            print("[+] Redis started successfully via WSL!")
            return True
    except Exception as e:
        print(f"[-] Could not start Redis via WSL: {e}")

    # Linux native fallback
    try:
        subprocess.run(["redis-server", "--daemonize", "yes", "--bind", "127.0.0.1", "--protected-mode", "no"], check=True, capture_output=True)
        time.sleep(1.5)
        with socket.create_connection(("127.0.0.1", 6379), timeout=2.0):
            print("[+] Local Linux Redis daemon started successfully!")
            return True
    except Exception:
        pass

    return False

def ensure_index(env):
    manifest = INDEX_ROOT / "main" / "manifest.json"
    if not manifest.exists():
        print("[*] Index not found at", INDEX_ROOT, "- building now...")
        subprocess.run([sys.executable, "-m", "app.corpus.indexer", "build"], cwd=str(ROOT), env=env, check=True)
        print("[+] Index built successfully!")
    else:
        print("[+] Existing index found at", INDEX_ROOT)

def ensure_db(env):
    try:
        init_script = ROOT / "scripts" / "init_db.py"
        if init_script.exists():
            subprocess.run([sys.executable, str(init_script)], cwd=str(ROOT), env=env, check=False)
    except Exception as e:
        print(f"[*] Note on init_db: {e}")

def check_url(url, timeout=2.0):
    try:
        import urllib.request
        req = urllib.request.urlopen(url, timeout=timeout)
        return req.status == 200
    except Exception:
        return False

def main():
    LOGS.mkdir(parents=True, exist_ok=True)
    INDEX_ROOT.mkdir(parents=True, exist_ok=True)

    if not check_redis():
        print("[-] ERROR: Redis server must be running on 127.0.0.1:6379 or specified in REDIS_URL. Please start Redis and try again.")
        sys.exit(1)

    # Environment
    env = os.environ.copy()
    env["INDEX_ROOT"] = str(INDEX_ROOT)
    env["CORPUS_DIR"] = str(CORPUS_DIR)
    env["PYTHONPATH"] = str(ROOT)
    env["REDIS_URL"] = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
    env["LLM_BASE_URL"] = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:8080/v1")
    env["LLM_MODEL"] = os.environ.get("LLM_MODEL", "stub-model")
    env["STUB_DELAY_MS"] = os.environ.get("STUB_DELAY_MS", "150")
    env["STUB_JITTER_MS"] = os.environ.get("STUB_JITTER_MS", "30")
    env["RESEARCHER_URL"] = os.environ.get("RESEARCHER_URL", "http://127.0.0.1:8001")
    env["ADVISOR_URL"] = os.environ.get("ADVISOR_URL", "http://127.0.0.1:8002")
    env["PROMPT_VERSION"] = os.environ.get("PROMPT_VERSION", "v2")
    env["CACHE_ENABLED"] = os.environ.get("CACHE_ENABLED", "true")
    env.setdefault("DATABASE_URL", f"sqlite:///{ROOT / 'veritas.db'}")
    env.setdefault("PYTHONIOENCODING", "utf-8")

    ensure_index(env)
    ensure_db(env)

    procs = []
    log_files = []

    print("\nStarting services...")
    for s in SERVICES:
        name = s["name"]
        log_path = LOGS / f"{name}.log"
        f = open(log_path, "w", encoding="utf-8")
        log_files.append(f)
        p = subprocess.Popen(s["cmd"], cwd=str(ROOT), env=env, stdout=f, stderr=subprocess.STDOUT)
        procs.append((name, p, s.get("health")))
        print(f"  -> Started {name} (PID: {p.pid}) -> logging to logs/{name}.log")

    print("\nWaiting for services to become healthy...")
    t0 = time.time()
    all_ready = False
    while time.time() - t0 < 30:
        ready = []
        for name, p, health_url in procs:
            if p.poll() is not None:
                print(f"[-] Service {name} exited prematurely with code {p.returncode}! Check logs/{name}.log")
                for _, pr, _ in procs:
                    if pr.poll() is None:
                        pr.terminate()
                sys.exit(1)
            if health_url:
                ready.append(check_url(health_url))
            else:
                ready.append(True)
        if all(ready):
            all_ready = True
            break
        time.sleep(1)

    if not all_ready:
        print("[-] Warning: Timed out waiting for all health endpoints to report OK.")
    else:
        print("[+] All services are UP and healthy!\n")

    print("==================================================================")
    print(" Workplace Compliance Advisory Desk is RUNNING:")
    print(f"   Web UI:         http://localhost:{PORT}")
    print(f"   API Docs:       http://localhost:{PORT}/docs")
    print("   Researcher Svc: http://localhost:8001/health")
    print("   Advisor Svc:    http://localhost:8002/health")
    print("   Stub LLM:       http://localhost:8080/health")
    print("   Worker Metrics: http://localhost:9101/metrics")
    print("==================================================================")
    print("Press Ctrl+C to stop all services.\n")

    def shutdown(signum=None, frame=None):
        print("\nStopping services...")
        for name, p, _ in procs:
            if p.poll() is None:
                print(f"  -> Terminating {name} (PID: {p.pid})...")
                p.terminate()
        for f in log_files:
            try:
                f.close()
            except Exception:
                pass
        print("[+] All services stopped.")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        while True:
            for name, p, _ in procs:
                if p.poll() is not None:
                    print(f"[-] Service {name} died with code {p.returncode}! Check logs/{name}.log")
                    shutdown()
            time.sleep(2)
    except KeyboardInterrupt:
        shutdown()

if __name__ == "__main__":
    main()
