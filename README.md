# Workplace Compliance Advisory Desk (VERITAS)

An enterprise-grade, deterministic multi-agent AI system designed to resolve Indian labor law and human resources compliance inquiries (specifically covering **Payment of Gratuity**, **Maternity Benefit**, and **Workplace Sexual Harassment / POSH**).

The system operates under a strict **deterministic zero-hallucination mandate**:
- Every statutory assertion **must cite an in-force legal provision** retrieved directly from the corpus.
- The system **never issues legal opinions** or binding legal determinations.
- If a statutory rule is modified by provisos, **the verifier strictly rejects any advisory citing the general rule without its accompanying provisos and exceptions**.
- If statutory facts are missing, or provisions conflict, the system **decisively escalates to human legal counsel**.

---

## 🎯 The Benchmark Hard Case: 18-Month Fixed-Term Contract Gratuity

Consider the canonical test case from the specification:

> *"An employee engaged after 21 November 2025 on a written fixed-term contract of 18 months has completed the contract. Is gratuity payable?"*

### Why Standard LLM/RAG Systems Fail
1. **The General Rule Trap:** Section 53(1) of the *Code on Social Security, 2020 (CoSS 2020)* states that gratuity is payable upon termination after continuous service for not less than **5 years**. Naive retrieval systems fetch this general rule and produce an incorrect answer: *"No, gratuity is not payable because 18 months is less than 5 years."*
2. **The Hidden Statutory Proviso:** The second proviso to Section 53(1) establishes an explicit exception:
   > *"Provided further that the completion of continuous service of five years shall not be necessary in the case of an employee employed on fixed term employment, where the employment ends on the expiry of the fixed term..."*
3. **The Multi-Agent Solution:**
   - **Section-Aware Chunking:** Statutory hierarchies preserve the link between the main rule and its qualifying provisos (`has_provisos=True`).
   - **Proviso Expansion Tool:** The Researcher Agent uses the `get_provisions` tool to expand all provisos of any cited section.
   - **The 5-Stage Complete Verifier:** The Verifier audits the draft advisory and rejects any citation to a general rule whose provisos were not retrieved. It injects an executable hint (`call get_provisions('coss_2020:s53')`), forcing a second research round until the fixed-term exception is evaluated.

---

## 👥 Default Demo Credentials (Dual-Portal Web UI)

The web client at [http://localhost:8000](http://localhost:8000) provides a dual-portal single-page application:

| Portal / Role | Email | Password | Access / Scope |
| :--- | :--- | :--- | :--- |
| **Employee Portal** | `employee@demo.veritas` | `Demo@Employee1` | Submit inquiries, track advisory status, view grounded citations |
| **HR / Admin Portal** | `hr@demo.veritas` | `Demo@HRAdmin1` | Review employee inquiries, workforce audits, and system metrics |

> 💡 *Note: The local database is pre-seeded with these demo accounts. You can also re-seed or initialize the database anytime by running `python scripts/init_db.py`.*

---

## 🏛️ System Architecture & Topology

```
[ Browser / Web UI / cURL ]
           │
           ▼
     ┌───────────┐         ┌────────────────────────┐
     │ api:8000  │────────►│ redis:6379             │
     │ (FastAPI) │         │ - RQ job queue ('runs')│
     └───────────┘         │ - Shared circuit state │
           ▲               │ - Full-run result cache│
           │ poll 202      │ - Rate limit windows   │
           │               └────────────────────────┘
           │                           ▲
           │                           │ de-queue
           │               ┌────────────────────────┐
           │               │ worker (RQ Runner)     │
           │               │ LangGraph Coordinator  │
           │               └────────────────────────┘
           │                    │             │
           │         HTTP call  │             │ HTTP call
           │                    ▼             ▼
           │     ┌─────────────────┐       ┌─────────────────┐
           │     │ researcher:8001 │       │ advisor:8002    │
           │     │ (Agent 1)       │       │ (Agent 2)       │
           │     │ - Tool loop     │       │ - Assertion syn.│
           │     │ - FAISS index   │       │ - Verifier (A3) │
           │     └─────────────────┘       └─────────────────┘
           │                    ▲             ▲
           │                    │             │ HTTP call
           │                    └──────┬──────┘
           │                           │
           │                           ▼
           │                ┌───────────────────────┐
           │                │ stub-llm / LLM:8080   │
           │                └───────────────────────┘
           ▼
[ Observability Stack ]
- Prometheus (9090): Latency histograms, breaker status, token metrics
- OpenTelemetry / Jaeger (16686): Distributed trace spans across services
- Grafana (3000): End-to-end system health & cost dashboard
```

### Microservice Roles & Ports

| Service | Port / URL | Technology | Responsibility |
| :--- | :--- | :--- | :--- |
| **`api`** | `http://localhost:8000` | FastAPI, Uvicorn | Web UI, ingress validation, guardrails, rate limiting, enqueuing runs, auth & portal APIs |
| **`worker`** | `http://localhost:9101/metrics` | RQ, LangGraph | Pulls runs from Redis, coordinates state machine, orchestrates Agent 1 & Agent 2 |
| **`researcher`** | `http://localhost:8001/health` | FastAPI, FAISS, BM25 | **Agent 1**: Autonomous ReAct agent for statutory corpus retrieval and proviso expansion |
| **`advisor`** | `http://localhost:8002/health` | FastAPI, Pydantic | **Agent 2**: Drafts cited assertions solely from retrieved passages and executes 5-stage Verifier |
| **`stub`** | `http://localhost:8080/health` | FastAPI, Uvicorn | Deterministic OpenAI-compatible chat completion provider (zero token cost) |
| **`redis`** | `127.0.0.1:6379` | Redis | Task queue (`runs`), distributed circuit breakers, retrieval cache, rate limits |

---

## 🚀 Running the Project

### Option 1: Native Windows / Linux (No Docker Required)

#### Windows
Run from the repository root:
```powershell
.\run_local.ps1
```
This automatically:
1. Verifies/starts Redis (via WSL if not already running on `127.0.0.1:6379`).
2. Validates the statutory FAISS index at `data/index/main/manifest.json` (builds it if absent).
3. Concurrently launches all 5 microservices (`stub`, `researcher`, `advisor`, `api`, `worker`) and health-checks them.

#### Linux / macOS
```bash
./scripts/run_local.sh
```

---

### Option 2: Docker Compose (Full Observability Stack)

To run the entire system including Grafana, Prometheus, and Jaeger:

```bash
docker compose up --build
```

Scale background task runners:
```bash
docker compose up -d --scale worker=3
```

- **Web Client UI:** [http://localhost:8000](http://localhost:8000)
- **API Docs (Swagger):** [http://localhost:8000/docs](http://localhost:8000/docs)
- **Grafana Dashboards:** [http://localhost:3000](http://localhost:3000) (`admin` / `change-me`)
- **Jaeger Distributed Tracing:** [http://localhost:16686](http://localhost:16686)
- **Prometheus Metrics:** [http://localhost:9090](http://localhost:9090)

---

## 📡 REST API Reference

Full OpenAPI documentation is available interactively at `http://localhost:8000/docs`.

### Compliance Advisories

| Endpoint | Method | Status | Description |
| :--- | :---: | :---: | :--- |
| `/v1/advisories` | `POST` | `202 Accepted` | Submits an advisory query. Validates payload, checks rate limits, and enqueues run. Returns `run_id` and `status_url`. |
| `/v1/advisories/{id}` | `GET` | `200 OK` | Retrieves run status, route, assertions, statutory citations, and verification status. |
| `/v1/advisories/{id}/trace` | `GET` | `200 OK` | Delivers the full audit trace (tool calls, queries, passages, and verifier decisions). |
| `/v1/feedback` | `POST` | `201 Created` | Submits human reviewer ratings (`satisfactory` / `unsatisfactory`) and reasons. |
| `/v1/feedback/summary` | `GET` | `200 OK` | Aggregated user feedback metrics and failure reasons. |
| `/v1/quality` | `GET` | `200 OK` | Live rolling evaluation metrics (groundedness, faithfulness, hallucination rate). |
| `/v1/quarantine` | `GET` | `200 OK` | Lists quarantined requests rejected by PII, injection, or schema validators. |
| `/v1/contracts/{name}` | `GET` | `200 OK` | Serves JSON Schemas for `ask_request`, `run_result`, or `feedback`. |

### Authentication & Dual-Portal

| Endpoint | Method | Description |
| :--- | :---: | :--- |
| `/auth/login` | `POST` | Authenticates user credentials (bcrypt) and returns a Bearer JWT. |
| `/auth/register` | `POST` | Self-service registration for employees. |
| `/auth/me` | `GET` | Returns authenticated user profile and assigned role (`EMPLOYEE` or `HR_ADMIN`). |
| `/employee/profile` | `GET` | Retrieves the employee's workforce profile and employment details. |
| `/employee/inquiries` | `GET` / `POST` | Lists previous inquiries or submits a new workplace compliance inquiry. |
| `/hr/inquiries` | `GET` | Lists all employee inquiries across the organization for HR review. |
| `/hr/inquiries/{id}/review` | `POST` | Records an HR compliance decision/review on an employee inquiry. |
| `/hr/workforce` | `GET` / `POST` | Lists workforce directory or registers a new employee account. |
| `/hr/metrics` | `GET` | Aggregated metrics for compliance inquiries, approvals, and escalations. |

---

## 🧪 Testing, Verification & Evaluation

### Unit & Resilience Tests
```bash
pytest tests/test_resilience.py
pytest tests/test_auth.py tests/test_hr_portal.py tests/test_employee_portal.py
```

### 23-Scenario Evaluation Suite
```bash
python eval/run.py
```
Evaluates all canonical benchmark scenarios against ground-truth legal provisions and routing requirements.

### Prompt Comparison & Ablation
```bash
./scripts/prompt_compare.sh       # Compares Prompt v1 vs Prompt v2 across the evaluation suite
./scripts/ablation_chunking.sh    # Measures impact of section-aware chunking vs naive chunking
python scripts/cache_benchmark.py # Benchmarks L1/L2/L3 cache hit latency (<20ms)
```

---

## 📂 Repository Structure

```
├── alembic/                # Database schema migrations
├── app/
│   ├── agents/             # Autonomous agent microservices (researcher & advisor)
│   ├── corpus/             # Section-aware chunker, embedder, indexer, and FAISS store
│   ├── prompts/            # Versioned agent prompts (v1 / v2)
│   ├── routers/            # FastAPI routers (auth, employee, hr_admin)
│   ├── api.py              # Main API application & ingress gate
│   ├── auth.py             # JWT authentication & bcrypt password hashing
│   ├── coordinator.py      # LangGraph state machine & multi-agent workflow
│   ├── guardrails.py       # PII detection & prompt injection scanning
│   ├── models.py           # SQLAlchemy database models
│   ├── resilience.py       # Distributed Redis circuit breakers & retries
│   ├── verify.py           # 5-stage advisory completeness verifier
│   └── worker.py           # RQ background task runner
├── contracts/              # Strict JSON Schemas for API payloads and results
├── corpus/                 # Statutory legal texts, citations, and sample fixtures
├── docker/                 # Container Dockerfiles for app and stub services
├── docs/                   # Architectural specs, data contracts, and SLO definitions
│   └── PROJECT_DOCUMENTATION.md  # Comprehensive technical architecture guide
├── eval/                   # Scenario evaluation suite and gold test fixtures
├── loadtest/               # Locust load test definitions and SLO verification
├── observability/          # Prometheus & Grafana dashboard configurations
├── scripts/                # Local orchestrators, benchmarking, and database seed scripts
├── stub/                   # Local deterministic OpenAI-compatible stub LLM provider
├── tests/                  # Unit and integration test suites
├── web/                    # Single-Page Application (Employee & HR Portals)
├── docker-compose.yml      # Multi-container service composition
├── requirements.txt        # Production dependencies
└── run_local.ps1           # One-click Windows local orchestrator
```

---

## ⚖️ License & Statutory Disclaimer

*VERITAS is an automated workplace compliance advisory system grounded strictly in statutory provisions in force. It does not provide legal opinions, legal determinations, or formal legal advice. All complex determinations and escalations are referred to qualified legal counsel.*
