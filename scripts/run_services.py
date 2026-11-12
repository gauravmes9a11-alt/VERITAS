
"""Run all Workplace Compliance Desk services locally on Windows/Linux without Docker."""

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

# Redis configuration
REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_URL = os.environ.get("REDIS_URL", f"redis://{REDIS_HOST}:{REDIS_PORT}/0")

API_PORT = int(os.environ.get("PORT", "8000"))
API_HOST = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"

SERVICES = [
    {
        "name": "stub",
        "cmd": [sys.executable, "-m", "uvicorn", "stub.app:app",
                "--host", "127.0.0.1", "--port", "8080", "--log-level", "info"],
        "health": "http://127.0.0.1:8080/health"
    },
    {
        "name": "researcher",
        "cmd": [sys.executable, "-m", "uvicorn", "app.agents.researcher_service:app",
                "--host", "127.0.0.1", "--port", "8001", "--log-level", "info"],
        "health": "http://127.0.0.1:8001/health"
    },
    {
        "name": "advisor",
        "cmd": [sys.executable, "-m", "uvicorn", "app.agents.advisor_service:app",
                "--host", "127.0.0.1", "--port", "8002", "--log-level", "info"],
        "health": "http://127.0.0.1:8002/health"
    },
    {
        "name": "api",
        "cmd": [sys.executable, "-m", "uvicorn", "app.api:app",
                "--host", API_HOST, "--port", str(API_PORT), "--log-level", "info"],
        "health": f"http://127.0.0.1:{API_PORT}/health"
    },
    {
        "name": "worker",
        "cmd": [sys.executable, "-m", "app.worker"],
        "health": "http://127.0.0.1:9101/metrics"
    },
]


def check_redis():
    hosts_to_try = [REDIS_HOST]
    if "127.0.0.1" not in hosts_to_try:
        hosts_to_try.append("127.0.0.1")
    if "172.22.175.18" not in hosts_to_try:
        hosts_to_try.append("172.22.175.18")

    for h in hosts_to_try:
        try:
            with socket.create_connection((h, REDIS_PORT), timeout=2):
                print(f"[+] Redis is running at {h}:{REDIS_PORT}")
                return True
        except OSError:
            pass

    if sys.platform != "win32":
        try:
            subprocess.run(["redis-server", "--daemonize", "yes", "--bind", "127.0.0.1", "--protected-mode", "no"], capture_output=True)
            time.sleep(1)
            with socket.create_connection(("127.0.0.1", REDIS_PORT), timeout=2):
                print("[+] Redis started automatically in background!")
                return True
        except Exception as e:
            print(f"[-] Could not auto-start Redis: {e}")

    print(f"[-] Redis connection failed across hosts: {hosts_to_try}")
    return False


def ensure_index():
    manifest = INDEX_ROOT / "main" / "manifest.json"

    if not manifest.exists():
        print("[*] Index not found at", INDEX_ROOT, "- building now...")

        env = os.environ.copy()
        env["INDEX_ROOT"] = str(INDEX_ROOT)
        env["CORPUS_DIR"] = str(CORPUS_DIR)
        env["PYTHONPATH"] = str(ROOT)

        subprocess.run(
            [sys.executable, "-m", "app.corpus.indexer", "build"],
            cwd=str(ROOT),
            env=env,
            check=True
        )

        print("[+] Index built successfully!")

    else:
        print("[+] Existing index found at", INDEX_ROOT)


def check_url(url, timeout=2.0):
    try:
        import urllib.request
        response = urllib.request.urlopen(url, timeout=timeout)
        return response.status == 200
    except Exception:
        return False


def free_ports(ports=(8000, 8001, 8002, 8080)):
    for port in ports:
        try:
            with socket.create_connection(
                ("127.0.0.1", port), timeout=0.4
            ):
                print(f"[*] Port {port} is occupied.")

                if sys.platform == "win32":
                    result = subprocess.run(
                        ["netstat", "-ano"],
                        capture_output=True,
                        text=True
                    )

                    for line in result.stdout.splitlines():
                        if f":{port}" in line and "LISTENING" in line:
                            pid = line.strip().split()[-1]

                            subprocess.run(
                                ["taskkill", "/F", "/PID", pid],
                                capture_output=True
                            )

                            print(f"[+] Released port {port} (PID {pid})")

        except OSError:
            pass


def main():
    LOGS.mkdir(parents=True, exist_ok=True)
    INDEX_ROOT.mkdir(parents=True, exist_ok=True)

    free_ports()

    if not check_redis():
        print("[-] Cannot start VERITAS without Redis.")
        sys.exit(1)

    ensure_index()

    env = os.environ.copy()

    env["INDEX_ROOT"] = str(INDEX_ROOT)
    env["CORPUS_DIR"] = str(CORPUS_DIR)
    env["PYTHONPATH"] = str(ROOT)

    # Redis configuration
    env["REDIS_HOST"] = REDIS_HOST
    env["REDIS_URL"] = REDIS_URL

    # AI service configuration
    env["LLM_BASE_URL"] = "http://127.0.0.1:8080/v1"
    env["LLM_MODEL"] = "stub-model"
    env["STUB_DELAY_MS"] = os.environ.get("STUB_DELAY_MS", "150")
    env["STUB_JITTER_MS"] = os.environ.get("STUB_JITTER_MS", "30")

    env["RESEARCHER_URL"] = "http://127.0.0.1:8001"
    env["ADVISOR_URL"] = "http://127.0.0.1:8002"

    env["PROMPT_VERSION"] = "v2"
    env["CACHE_ENABLED"] = "true"

    env.setdefault("DATABASE_URL", f"sqlite:///{ROOT / 'veritas.db'}")
    env.setdefault("PYTHONIOENCODING", "utf-8")

    processes = []
    log_files = []

    print("\nStarting VERITAS services...\n")

    for service in SERVICES:
        name = service["name"]
        log_path = LOGS / f"{name}.log"

        log_file = open(log_path, "w", encoding="utf-8")
        log_files.append(log_file)

        process = subprocess.Popen(
            service["cmd"],
            cwd=str(ROOT),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT
        )

        processes.append(
            (name, process, service.get("health"))
        )

        print(
            f"  -> Started {name} "
            f"(PID: {process.pid}) -> logs/{name}.log"
        )

    print("\nWaiting for services to become healthy...")

    start_time = time.time()
    all_ready = False

    while time.time() - start_time < 30:
        ready = []

        for name, process, health_url in processes:

            if process.poll() is not None:
                print(
                    f"[-] Service {name} exited with "
                    f"code {process.returncode}!"
                )
                print(f"[!] Check logs/{name}.log")

                for _, running_process, _ in processes:
                    if running_process.poll() is None:
                        running_process.terminate()

                for log_file in log_files:
                    log_file.close()

                sys.exit(1)

            if health_url:
                ready.append(check_url(health_url))
            else:
                ready.append(True)

        if all(ready):
            all_ready = True
            break

        time.sleep(1)

    if all_ready:
        print("\n[+] All services are UP and healthy!\n")
    else:
        print("\n[-] Warning: Some services did not become healthy.")
        print("[!] Check the individual files inside logs/")

    print("======================================================")
    print(" VERITAS - Workplace Compliance Desk")
    print("======================================================")
    print(" Web UI:         http://localhost:8000")
    print(" API Docs:       http://localhost:8000/docs")
    print(" Researcher:     http://localhost:8001/health")
    print(" Advisor:        http://localhost:8002/health")
    print(" Mock LLM:       http://localhost:8080/health")
    print(" Worker Metrics: http://localhost:9101/metrics")
    print("======================================================")
    print("Press Ctrl+C to stop all services.\n")

    def shutdown(signum=None, frame=None):
        print("\nStopping VERITAS services...")

        for name, process, _ in processes:
            if process.poll() is None:
                print(f"  -> Stopping {name}...")
                process.terminate()

        for log_file in log_files:
            try:
                log_file.close()
            except Exception:
                pass

        print("[+] All services stopped.")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        while True:
            for name, process, _ in processes:
                if process.poll() is not None:
                    print(
                        f"[-] Service {name} stopped "
                        f"with code {process.returncode}!"
                    )
                    shutdown()

            time.sleep(2)

    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()
