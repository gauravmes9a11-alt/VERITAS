"""L2: prompts are versioned artifacts on disk: app/prompts/<agent>_<version>.txt.
The version used is returned with every response and recorded in the trace."""
import hashlib
from pathlib import Path

D = Path(__file__).parent / "prompts"


def load(agent: str, version: str):
    p = D / f"{agent}_{version}.txt"
    if not p.exists():
        raise FileNotFoundError(f"prompt {agent}_{version} not found")
    text = p.read_text()
    return text, hashlib.sha256(text.encode()).hexdigest()[:10]


def available():
    return sorted({p.stem.split("_")[-1] for p in D.glob("*.txt")})
