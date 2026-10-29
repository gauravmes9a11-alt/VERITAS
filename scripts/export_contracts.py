"""Write JSON Schemas for every external interface to contracts/ (D1)."""
import json
from pathlib import Path
from app.contracts import AskRequest, FeedbackIn, RunResult
out = Path("contracts"); out.mkdir(exist_ok=True)
for name, m in [("ask_request", AskRequest), ("feedback", FeedbackIn), ("run_result", RunResult)]:
    (out / f"{name}.schema.json").write_text(json.dumps(m.model_json_schema(), indent=2))
    print("wrote", name)
