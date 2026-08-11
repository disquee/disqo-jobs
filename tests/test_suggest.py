"""parse_reply has to survive whatever a chat UI pastes back."""

import pytest

from jobpilot.suggest import parse_reply, _MAX_TITLES


def test_parses_clean_reply():
    out = parse_reply({
        "titles": [{"title": "Documentation Lead", "why": "You led a docs team."}],
        "pivots": [{"direction": "Developer relations", "why": "Public writing.",
                    "entry_title": "developer advocate"}],
    })
    assert out["titles"][0]["title"] == "Documentation Lead"
    assert out["pivots"][0]["entry_title"] == "developer advocate"


def test_parses_fenced_text_paste():
    raw = 'Sure! Here you go:\n```json\n{"titles": [{"title": "UX Writer"}], "pivots": []}\n```'
    out = parse_reply(raw)
    assert out["titles"] == [{"title": "UX Writer", "why": ""}]
    assert out["pivots"] == []


def test_drops_blanks_and_caps_counts():
    out = parse_reply({
        "titles": [{"title": ""}] + [{"title": f"t{i}"} for i in range(30)],
        "pivots": [{"direction": "", "entry_title": ""}],
    })
    assert len(out["titles"]) == _MAX_TITLES
    assert out["titles"][0]["title"] == "t0"
    assert out["pivots"] == []


def test_bare_string_titles_are_accepted():
    out = parse_reply({"titles": ["content designer"], "pivots": None})
    assert out["titles"] == [{"title": "content designer", "why": ""}]


def test_pivot_missing_entry_title_falls_back_to_direction():
    out = parse_reply({"pivots": [{"direction": "Support engineering", "why": "w"}]})
    assert out["pivots"][0]["entry_title"] == "Support engineering"


def test_non_object_reply_raises():
    with pytest.raises(ValueError):
        parse_reply("[1, 2, 3]")
