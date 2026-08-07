"""Track AI token usage so the cost of running the app is visible.

Cost is an *estimate*: rates change and vary by model, so they're stored where
the user can correct them rather than hard-coded as fact. Tokens are counted
exactly; dollars are labelled as an approximation everywhere they're shown.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Optional

from .config import DATA_DIR

USAGE_PATH = DATA_DIR / "usage.json"

# Dollars per million tokens. Defaults are a reasonable mid-tier estimate; the
# Your data page lets the user set their provider's actual numbers.
DEFAULT_INPUT_RATE = 3.00
DEFAULT_OUTPUT_RATE = 15.00


def _load() -> dict:
    if USAGE_PATH.exists():
        try:
            return json.loads(USAGE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return {"input_tokens": 0, "output_tokens": 0, "calls": 0, "by_day": {},
            "input_rate": DEFAULT_INPUT_RATE, "output_rate": DEFAULT_OUTPUT_RATE}


def _save(data: dict) -> None:
    try:
        USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        USAGE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass  # never let bookkeeping break a real request


def record(input_tokens: int, output_tokens: int) -> None:
    data = _load()
    data["input_tokens"] += int(input_tokens or 0)
    data["output_tokens"] += int(output_tokens or 0)
    data["calls"] += 1
    today = date.today().isoformat()
    day = data.setdefault("by_day", {}).setdefault(today, {"input": 0, "output": 0, "calls": 0})
    day["input"] += int(input_tokens or 0)
    day["output"] += int(output_tokens or 0)
    day["calls"] += 1
    _save(data)


def set_rates(input_rate: float, output_rate: float) -> None:
    data = _load()
    data["input_rate"] = max(0.0, float(input_rate))
    data["output_rate"] = max(0.0, float(output_rate))
    _save(data)


def _cost(input_tokens: int, output_tokens: int, data: dict) -> float:
    in_rate = float(data.get("input_rate", DEFAULT_INPUT_RATE))
    out_rate = float(data.get("output_rate", DEFAULT_OUTPUT_RATE))
    return (input_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate


def summary() -> dict:
    data = _load()
    today = date.today().isoformat()
    day = data.get("by_day", {}).get(today, {"input": 0, "output": 0, "calls": 0})
    return {
        "calls": data.get("calls", 0),
        "input_tokens": data.get("input_tokens", 0),
        "output_tokens": data.get("output_tokens", 0),
        "total_cost": _cost(data.get("input_tokens", 0), data.get("output_tokens", 0), data),
        "today_calls": day.get("calls", 0),
        "today_cost": _cost(day.get("input", 0), day.get("output", 0), data),
        "input_rate": data.get("input_rate", DEFAULT_INPUT_RATE),
        "output_rate": data.get("output_rate", DEFAULT_OUTPUT_RATE),
    }


def estimate_per_job() -> Optional[float]:
    """Average cost of an AI action so far, for 'about Nc per job' hints."""
    data = _load()
    calls = data.get("calls", 0)
    if calls < 3:
        return None
    total = _cost(data.get("input_tokens", 0), data.get("output_tokens", 0), data)
    return total / calls
