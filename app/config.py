"""All configuration comes from the environment (D4). No defaults for provider
endpoints, credentials or model identifiers live in code."""
import os
from dataclasses import dataclass


def _b(name, default):
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _f(name, default):
    return float(os.environ.get(name, default) or default)


def _i(name, default):
    return int(float(os.environ.get(name, default) or default))


@dataclass
class Settings:
    llm_base_url: str
    llm_model: str
    llm_api_key: str
    llm_json_mode: bool
    llm_timeout_s: float
    llm_max_tokens: int
    price_in: float
    price_out: float
    embed_backend: str
    embed_model: str
    embed_dim: int
    chunking: str
    index_name: str
    index_root: str
    corpus_dir: str
    require_official: bool
    prompt_version: str
    max_steps: int
    max_tool_calls: int
    max_rounds: int
    run_deadline_s: float
    tool_timeout_s: float
    agent_http_timeout_s: float
    retry_max: int
    retry_base_s: float
    retry_cap_s: float
    breaker_threshold: int
    breaker_window_s: float
    breaker_cooldown_s: float
    cache_enabled: bool
    cache_ttl_s: int
    rate_per_min: int
    rate_daily: int
    allow_options_override: bool
    injection_block: float
    toxicity_block: float
    live_eval_rate: float
    redis_url: str
    researcher_url: str
    advisor_url: str


def load() -> Settings:
    e = os.environ.get
    return Settings(
        llm_base_url=e("LLM_BASE_URL", ""),
        llm_model=e("LLM_MODEL", ""),
        llm_api_key=e("LLM_API_KEY", ""),
        llm_json_mode=_b("LLM_JSON_MODE", False),
        llm_timeout_s=_f("LLM_TIMEOUT_S", 30),
        llm_max_tokens=_i("LLM_MAX_TOKENS", 900),
        price_in=_f("LLM_PRICE_IN_PER_1K", 0),
        price_out=_f("LLM_PRICE_OUT_PER_1K", 0),
        embed_backend=e("EMBED_BACKEND", "hash"),
        embed_model=e("EMBED_MODEL", ""),
        embed_dim=_i("EMBED_DIM", 384),
        chunking=e("CHUNKING", "section_aware"),
        index_name=e("INDEX_NAME", "main"),
        index_root=e("INDEX_ROOT", "/data/index"),
        corpus_dir=e("CORPUS_DIR", "/app/corpus"),
        require_official=_b("REQUIRE_OFFICIAL_CORPUS", False),
        prompt_version=e("PROMPT_VERSION", "v2"),
        max_steps=_i("MAX_STEPS", 8),
        max_tool_calls=_i("MAX_TOOL_CALLS", 10),
        max_rounds=_i("MAX_ROUNDS", 2),
        run_deadline_s=_f("RUN_DEADLINE_S", 90),
        tool_timeout_s=_f("TOOL_TIMEOUT_S", 10),
        agent_http_timeout_s=_f("AGENT_HTTP_TIMEOUT_S", 60),
        retry_max=_i("RETRY_MAX", 3),
        retry_base_s=_f("RETRY_BASE_S", 0.5),
        retry_cap_s=_f("RETRY_CAP_S", 8),
        breaker_threshold=_i("BREAKER_FAILURE_THRESHOLD", 4),
        breaker_window_s=_f("BREAKER_WINDOW_S", 30),
        breaker_cooldown_s=_f("BREAKER_COOLDOWN_S", 20),
        cache_enabled=_b("CACHE_ENABLED", True),
        cache_ttl_s=_i("CACHE_TTL_S", 3600),
        rate_per_min=_i("RATE_LIMIT_PER_MIN", 20),
        rate_daily=_i("RATE_LIMIT_DAILY_RUNS", 500),
        allow_options_override=_b("ALLOW_OPTIONS_OVERRIDE", True),
        injection_block=_f("INJECTION_BLOCK_SCORE", 3),
        toxicity_block=_f("TOXICITY_BLOCK_SCORE", 2),
        live_eval_rate=_f("LIVE_EVAL_SAMPLE_RATE", 0.2),
        redis_url=e("REDIS_URL", "redis://localhost:6379/0"),
        researcher_url=e("RESEARCHER_URL", "http://researcher:8001"),
        advisor_url=e("ADVISOR_URL", "http://advisor:8002"),
    )


settings = load()
