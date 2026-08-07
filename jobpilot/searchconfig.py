"""Read and write config.yaml from the app.

The file ships with explanatory comments, which PyYAML can't round-trip. Once a
user edits their search from the interface the comments go, so the writer adds a
short header back rather than leaving a bare dump.
"""

from __future__ import annotations

from typing import Any

import yaml

from .config import ROOT, load_config

CONFIG_PATH = ROOT / "config.yaml"

HEADER = """# disqo jobs configuration.
# Written by the Settings page — edit here or there, whichever you prefer.
#
# Matching note: Adzuna and Jooble search the full posting, while Greenhouse and
# Lever match your query against the job TITLE only. Short phrases that really
# appear in titles work best.
"""

DEFAULTS: dict[str, Any] = {
    "searches": [],
    "ats": {"greenhouse": [], "lever": []},
    "results_per_search": 25,
    "fit_threshold": 55,
    "max_apply_per_day": 15,
    "exclude_title_keywords": [],
    "exclude_company": [],
}


def current() -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    cfg.update(load_config() or {})
    ats = dict(DEFAULTS["ats"])
    ats.update(cfg.get("ats") or {})
    cfg["ats"] = ats
    return cfg


def save(cfg: dict[str, Any]) -> None:
    CONFIG_PATH.write_text(
        HEADER + "\n" + yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    load_config.cache_clear()


def update(**changes: Any) -> dict[str, Any]:
    cfg = current()
    cfg.update({k: v for k, v in changes.items() if v is not None})
    save(cfg)
    return cfg
