"""Run all Workplace Compliance Desk services locally on Windows/Linux without Docker.
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

SERVICES = [
    {"name": "stub", "cmd": [sys.executable, "-m", "uvicorn", "stub.app:app", "--host", "127.0.0.1", "--port", "8080", "--log-level", "info"], "health": "http://127.0.0.1:8080/health"},
    {"name": "researcher", "cmd": [sys.executable, "-m", "uvicorn", "app.agents.researcher_service:app", "--host", "127.0.0.1", "--port", "8001", "--log-level", "info"], "health": "http://127.0.0.1:8001/health"},
    {"name": "advisor", "cmd": [sys.executable, "-m", "uvicorn", "app.agents.advisor_service:app", "--host", "127.0.0.1", "--port", "8002", "--log-level", "info"], "health": "http://127.0.0.1:8002/health"},
    {"name": "api", "cmd": [sys.executable, "-m", "uvicorn", "app.api:app", "--host", "127.0.0.1", "--port", "8000", "--log-level", "info"], "health": "http://127.0.0.1:8000/health"},
    {"name": "worker", "cmd": [sys.executable, "-m", "app.worker"], "health": "http://127.0.0.1:9101/metrics"},
]

def check_redis():
    try:
        with socket.create_connection(("127.0.0.1", 6379), timeout=1.5):
            print("[+] Redis is running on 127.0.0.1:6379")
            return True
    except OSError:
        pass

    print("[*] Redis not responding on 127.0.0.1:6379. Attempting to start via WSL...")
    try:
        subprocess.run(["wsl", "-u", "root", "-e", "bash", "-c", "systemctl stop redis-server 2>/dev/null; killall -9 redis-server 2>/dev/null; redis-server --bind 0.0.0.0 --protected-mode no --daemonize yes"], capture_output=True, text=True, timeout=10)
        time.sleep(2)
        with socket.create_connection(("127.0.0.1", 6379), timeout=2.0):
            print("[+] Redis started successfully via WSL!")
            return True
    except Exception as e:
        print(f"[-] Could not start Redis automatically: {e}")
        return False

def ensure_index():
    manifest = INDEX_ROOT / "main" / "manifest.json"
    if not manifest.exists():
        print("[*] Index not found at", INDEX_ROOT, "- building now...")
        env = os.environ.copy()
        env["INDEX_ROOT"] = str(INDEX_ROOT)
        env["CORPUS_DIR"] = str(CORPUS_DIR)
        env["PYTHONPATH"] = str(ROOT)
        subprocess.run([sys.executable, "-m", "app.corpus.indexer", "build"], cwd=str(ROOT), env=env, check=True)
        print("[+] Index built successfully!")
    else:
        print("[+] Existing index found at", INDEX_ROOT)

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
        print("[-] ERROR: Redis server must be running on 127.0.0.1:6379. Please start Redis and try again.")
        sys.exit(1)

    ensure_index()

    # Environment
    env = os.environ.copy()
    env["INDEX_ROOT"] = str(INDEX_ROOT)
    env["CORPUS_DIR"] = str(CORPUS_DIR)
    env["PYTHONPATH"] = str(ROOT)
    env["REDIS_URL"] = "redis://127.0.0.1:6379/0"
    env["LLM_BASE_URL"] = "http://127.0.0.1:8080/v1"
    env["LLM_MODEL"] = "stub-model"
    env["STUB_DELAY_MS"] = os.environ.get("STUB_DELAY_MS", "150")
    env["STUB_JITTER_MS"] = os.environ.get("STUB_JITTER_MS", "30")
    env["RESEARCHER_URL"] = "http://127.0.0.1:8001"
    env["ADVISOR_URL"] = "http://127.0.0.1:8002"
    env["PROMPT_VERSION"] = "v2"
    env["CACHE_ENABLED"] = "true"

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
    print("   Web UI:         http://localhost:8000")
    print("   API Docs:       http://localhost:8000/docs")
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
