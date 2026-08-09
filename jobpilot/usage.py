"""Track AI token usage so the cost of running the app is visible.

Cost is an *estimate*. Default rates follow the model actually answering —
the tracker once hard-coded mid-tier prices while the default model was
Opus-class, which understated a run's cost by nearly half. Rates the user
sets on the Your data page always win. Tokens are counted exactly; dollars
are labelled as an approximation everywhere they're shown.
"""

from __future__ import annotations

import json
import os
from datetime import date
from typing import Optional

from .config import DATA_DIR

USAGE_PATH = DATA_DIR / "usage.json"

#: Dollars per million tokens by model family (Anthropic list prices). Matched
#: as substrings of the model id, first hit wins, so dated ids land on their
#: family. Prices drift — the Your data page lets the user correct them.
MODEL_RATES: tuple[tuple[str, tuple[float, float]], ...] = (
    ("fable", (10.0, 50.0)),
    ("mythos", (10.0, 50.0)),
    ("opus", (5.0, 25.0)),
    ("sonnet", (3.0, 15.0)),
    ("haiku", (1.0, 5.0)),
)

#: The rates the tracker hard-coded before they followed the model. A stored
#: pair equal to these was written automatically, not chosen by anyone.
_LEGACY_RATES = (3.0, 15.0)


def default_rates() -> tuple[float, float]:
    """Per-million rates for whatever answers AI calls right now.

    Claude Code and local models have no per-token bill, so their estimate is
    honestly zero rather than a made-up number.
    """
    from .settings import load_settings

    if load_settings().llm_mode in ("cli", "local", "manual"):
        return 0.0, 0.0

    from .config import DEFAULT_MODEL

    model = (os.getenv("JOBPILOT_MODEL") or DEFAULT_MODEL).lower()
    for family, rates in MODEL_RATES:
        if family in model:
            return rates
    return 5.0, 25.0  # unknown model: assume the default (Opus-class) pricing


def _load() -> dict:
    if USAGE_PATH.exists():
        try:
            return json.loads(USAGE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return {"input_tokens": 0, "output_tokens": 0, "calls": 0, "by_day": {}}


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
    # From here on the stored numbers are the user's, never to be second-guessed.
    data["rates_set_by_user"] = True
    _save(data)


def _rates(data: dict) -> tuple[float, float]:
    """The rates to bill the estimate at: the user's own, or the model's.

    Old usage.json files carry auto-written legacy rates without the flag;
    those weren't a choice, so they defer to the model too. A stored pair that
    *differs* from the legacy defaults was typed into the rates form back when
    that was the only way rates were kept, and still wins.
    """
    if data.get("rates_set_by_user"):
        return float(data.get("input_rate", 0.0)), float(data.get("output_rate", 0.0))
    stored_in, stored_out = data.get("input_rate"), data.get("output_rate")
    if stored_in is not None and stored_out is not None:
        stored = (float(stored_in), float(stored_out))
        if stored != _LEGACY_RATES:
            return stored
    return default_rates()


def _cost(input_tokens: int, output_tokens: int, data: dict) -> float:
    in_rate, out_rate = _rates(data)
    return (input_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate


def summary() -> dict:
    data = _load()
    today = date.today().isoformat()
    day = data.get("by_day", {}).get(today, {"input": 0, "output": 0, "calls": 0})
    in_rate, out_rate = _rates(data)
    return {
        "calls": data.get("calls", 0),
        "input_tokens": data.get("input_tokens", 0),
        "output_tokens": data.get("output_tokens", 0),
        "total_cost": _cost(data.get("input_tokens", 0), data.get("output_tokens", 0), data),
        "today_calls": day.get("calls", 0),
        "today_cost": _cost(day.get("input", 0), day.get("output", 0), data),
        "input_rate": in_rate,
        "output_rate": out_rate,
        "rates_are_custom": bool(data.get("rates_set_by_user")),
    }


def estimate_per_job() -> Optional[float]:
    """Average cost of an AI action so far, for 'about Nc per job' hints."""
    data = _load()
    calls = data.get("calls", 0)
    if calls < 3:
        return None
    total = _cost(data.get("input_tokens", 0), data.get("output_tokens", 0), data)
    return total / calls
