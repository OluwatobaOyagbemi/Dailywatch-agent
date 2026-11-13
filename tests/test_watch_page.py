"""Watch page: link building, page data and the fallback when Pages isn't live (offline)."""
import json, sys, pathlib
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import guardrails as g  # noqa: E402
import watch_agent as w  # noqa: E402

VIDS = [{"id": "abcDEF12345", "title": "One", "channel": "C1", "minutes": 12.0, "embeddable": True,
         "published": datetime(2024, 3, 1, tzinfo=timezone.utc), "older": True},
        {"id": "xyz_-987654", "title": "Two", "channel": "C2", "minutes": 8.5, "embeddable": False,
         "published": datetime(2026, 11, 1, tzinfo=timezone.utc)}]


def test_page_url_derived_from_github_repo(monkeypatch):
    monkeypatch.delenv("WATCH_PAGE_URL", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "SomeOwner/Dailywatch-agent")
    assert w.page_base({}) == "https://someowner.github.io/Dailywatch-agent/"


def test_page_url_override_and_off_switch(monkeypatch):
    monkeypatch.setenv("WATCH_PAGE_URL", "https://watch.example.com")
    assert w.page_base({}) == "https://watch.example.com/"
    assert w.page_base({"watch_page": False}) is None


def test_unsafe_page_url_is_refused(monkeypatch):
    for bad in ("http://insecure.example.com/", "https://x.com/\"onmouseover=", "javascript:alert(1)"):
        monkeypatch.setenv("WATCH_PAGE_URL", bad)
        assert w.page_base({}) is None


def test_no_page_outside_github_without_config(monkeypatch):
    monkeypatch.delenv("WATCH_PAGE_URL", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert w.page_base({}) is None


def test_session_link_carries_date_and_ids():
    link = w.session_link("https://o.github.io/r/", "2026-11-13", VIDS)
    assert link == "https://o.github.io/r/?d=2026-11-13&v=abcDEF12345,xyz_-987654"


def test_messages_link_to_page_when_live():
    cfg = {"_page": "https://o.github.io/r/?d=2026-11-13&v=a,b", "distraction_free": True}
    msg = w.build_message("T", "monday", VIDS, cfg)
    assert "Start today's session" in msg
    assert 'href="https://o.github.io/r/?d=2026-11-13&amp;v=a,b#2"' in msg
    assert "youtube" not in msg
    assert "Start today's session" in w.build_html("T", "monday", VIDS, cfg)
    assert "#1" in w.build_plain("T", "monday", VIDS, cfg)


def test_messages_fall_back_to_youtube_without_page():
    msg = w.build_message("T", "monday", VIDS, {"distraction_free": True})
    assert "Start today's session" not in msg and "youtube-nocookie.com/embed/abcDEF12345" in msg


def test_page_is_live_requires_our_page(monkeypatch):
    class R:
        def __init__(self, ok, text): self.ok, self.text = ok, text
    monkeypatch.setattr(w.requests, "get", lambda url, timeout: R(True, "<title>Daily Watch</title>"))
    assert w.page_is_live("https://o.github.io/r/")
    monkeypatch.setattr(w.requests, "get", lambda url, timeout: R(True, "Site not found · GitHub Pages"))
    assert not w.page_is_live("https://o.github.io/r/")
    def boom(url, timeout): raise w.requests.ConnectionError("down")
    monkeypatch.setattr(w.requests, "get", boom)
    assert not w.page_is_live("https://o.github.io/r/")


def test_page_data_shape(monkeypatch, tmp_path):
    monkeypatch.setenv("SEND_HOUR", "12")
    data = w.page_data("2026-11-13", "friday", {"theme": "Fatherhood", "prompt": "One action?"}, {}, VIDS)
    assert data["send_time"] == "12:00" and data["prompt"] == "One action?"
    assert [d["day"] for d in data["week"]] == w.WEEKDAYS and all(d["rest"] for d in data["week"])
    full = w.page_data("2026-11-13", "friday", {"theme": "F"}, {"days": {"friday": {"theme": "F"}}}, VIDS)
    assert full["week"][4] == {"day": "friday", "theme": "F", "rest": False}
    assert data["videos"][0] == {"id": "abcDEF12345", "title": "One", "channel": "C1", "minutes": 12.0,
                                 "embeddable": True, "older": True, "published": "2024-03-01"}
    assert data["videos"][1]["embeddable"] is False
    monkeypatch.setattr(w, "PAGE_DATA_PATH", tmp_path / "docs" / "today.json")
    w.write_page_data(data)
    assert json.loads((tmp_path / "docs" / "today.json").read_text())["theme"] == "Fatherhood"


def test_watch_page_settings_validate():
    base = {"days": {"monday": {"theme": "x", "creators": ["TED"]}}}
    g.validate_config(dict(base, watch_page=True, watch_page_url="https://me.github.io/Dailywatch-agent"))
    import pytest
    with pytest.raises(g.ConfigError):
        g.validate_config(dict(base, watch_page_url="http://me.github.io/"))
