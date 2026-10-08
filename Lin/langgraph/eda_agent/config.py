from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_PMMS_DIR = Path(r"C:\AI Agent\EDA Project\tools\pmms")
LEGACY_ENV_FILE = Path(r"C:\AI Agent\EDA Project\.env")


def _clean(value: str | None) -> str:
    """Remove whitespace and common quote characters from an env value."""
    return (value or "").strip().strip('"').strip("'")


def _path_from_env(name: str, default: Path) -> Path:
    raw_value = _clean(os.getenv(name))
    path = Path(raw_value) if raw_value else default
    return path.expanduser().resolve()


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str
    primary_model: str
    evaluator_model: str
    max_review_rounds: int
    max_tool_calls: int
    approval_score: float
    pmms_exe: Path
    pmms_config: Path

    @classmethod
    def load(cls, require_models: bool = True) -> "Settings":
        # Keep compatibility with the original EDA Agent installation while
        # allowing this repository to override values with its own local file.
        load_dotenv(LEGACY_ENV_FILE)
        load_dotenv(PROJECT_DIR / ".env", override=True)

        api_key = _clean(os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY"))
        base_url = _clean(os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL"))
        legacy_model = _clean(os.getenv("LLM_MODEL_ID"))
        primary_model = _clean(os.getenv("PRIMARY_MODEL_ID")) or legacy_model
        evaluator_model = _clean(os.getenv("EVALUATOR_MODEL_ID")) or legacy_model

        if require_models:
            required = {
                "LLM_API_KEY": api_key,
                "LLM_BASE_URL": base_url,
                "PRIMARY_MODEL_ID (or LLM_MODEL_ID)": primary_model,
                "EVALUATOR_MODEL_ID (or LLM_MODEL_ID)": evaluator_model,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise RuntimeError("Missing model configuration: " + ", ".join(missing))

        return cls(
            api_key=api_key,
            base_url=base_url,
            primary_model=primary_model,
            evaluator_model=evaluator_model,
            max_review_rounds=max(1, int(os.getenv("MAX_REVIEW_ROUNDS", "3"))),
            max_tool_calls=max(1, int(os.getenv("MAX_TOOL_CALLS", "8"))),
            approval_score=float(os.getenv("APPROVAL_SCORE", "8.0")),
            pmms_exe=_path_from_env("PMMS_EXE", DEFAULT_PMMS_DIR / "pmms.exe"),
            pmms_config=_path_from_env(
                "PMMS_CONFIG", DEFAULT_PMMS_DIR / "config.txt"
            ),
        )
