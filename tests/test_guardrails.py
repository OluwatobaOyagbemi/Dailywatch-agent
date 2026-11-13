"""Guardrails: input validation, output safety and runtime protection (offline)."""
import json, sys, pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import guardrails as g  # noqa: E402
import watch_agent as w  # noqa: E402

REAL_YT_GET = w.yt_get  # other tests swap in a fake; keep the real one for the network tests

GOOD = {"videos_per_day": 5, "deliver": ["telegram"],
        "days": {"monday": {"theme": "Speaking", "creators": ["TED", "@philipp-humm"]}}}


# ── input: config ────────────────────────────────────────────────────────────

def test_real_configs_are_valid():
    import yaml
    for f in ("config.yaml", "config.example.yaml"):
        g.validate_config(yaml.safe_load(open(pathlib.Path(__file__).parents[1] / f)))


@pytest.mark.parametrize("bad, fragment", [
    ({"videos_per_day": 0}, "videos_per_day"),
    ({"videos_per_day": "5"}, "videos_per_day"),
    ({"match_theme": "false"}, "match_theme"),            # string, not boolean
    ({"deliver": ["telegram", "sms"]}, "deliver"),
    ({"days": {"funday": {"theme": "x"}}}, "funday"),
    ({"days": {"monday": {"theme": ""}}}, "theme"),
    ({"days": {"monday": {"theme": "x", "creators": ["has space"]}}}, "valid handle"),
    ({"days": {"monday": {"theme": "x", "creators": [f"c{i}xx" for i in range(11)]}}}, "max 10"),
    ({"days": {"monday": {"theme": "x", "creators": "TED"}}}, "must be a list"),
])
def test_bad_config_is_rejected_with_a_clear_reason(bad, fragment):
    cfg = json.loads(json.dumps(GOOD)); cfg.update(bad)
    with pytest.raises(g.ConfigError) as e:
        g.validate_config(cfg)
    assert any(fragment in p for p in e.value.problems)


def test_typos_are_warned_with_suggestion():
    cfg = dict(GOOD, videos_per_dya=3)
    assert any("did you mean `videos_per_day`" in x for x in g.validate_config(cfg))


def test_empty_config_is_rejected():
    with pytest.raises(g.ConfigError):
        g.validate_config(None)


def test_broken_yaml_gives_line_number(tmp_path, monkeypatch):
    p = tmp_path / "config.yaml"
    p.write_text("days:\n  monday:\n    theme: [unclosed\n")
    monkeypatch.setattr(w, "CONFIG_PATH", p)
    with pytest.raises(g.ConfigError) as e:
        w.load_config()
    assert "line" in str(e.value)


# ── input: environment & API data ────────────────────────────────────────────

def test_env_format_checks(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "not-a-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "abc")
    monkeypatch.setenv("EMAIL_TO", "a@b.com, evil@x.com")
    monkeypatch.setenv("SMTP_PORT", "five")
    probs = " ".join(g.validate_env(["telegram", "email"]))
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "EMAIL_TO", "SMTP_PORT"):
        assert k in probs


def test_unsafe_video_ids_are_dropped():
    sn = {"title": "t", "channelTitle": "c", "publishedAt": "2026-01-01T00:00:00Z"}
    assert w._video("dQw4w9WgXcQ", sn)["id"] == "dQw4w9WgXcQ"
    for bad in ("x/../../evil", "a b", 'x"onclick=', None, 123):
        assert w._video(bad, sn) is None


def test_malformed_api_items_are_skipped_not_fatal():
    data = {"items": [None, "junk", {"id": {}}, {"id": {"videoId": "ok_id"}, "snippet": {}},
                      {"id": {"videoId": "good1"},
                       "snippet": {"title": "T", "channelTitle": "C", "publishedAt": "2026-01-01T00:00:00Z"}}]}
    assert [v["id"] for v in w._search_results(data)] == ["good1"]


def test_titles_are_cleaned():
    dirty = "Real‮title​ with\nnewline\x00 " + "x" * 300
    out = g.clean_text(dirty, g.TITLE_MAX)
    assert "‮" not in out and "​" not in out and "\n" not in out and "\x00" not in out
    assert len(out) <= g.TITLE_MAX and out.endswith("…")


# ── output ───────────────────────────────────────────────────────────────────

def test_blocked_words_and_shorts_never_sent():
    from test_watch_agent import fake_yt, cfg, DAY
    w._channel_cache.clear()
    inner = fake_yt()
    def titled(endpoint, api_key, **p):
        out = inner(endpoint, api_key, **p)
        if endpoint == "search":
            out["items"][0]["snippet"]["title"] = "Crazy PRANK video"
            out["items"][1]["snippet"]["title"] = "tips #Shorts"
        return out
    w.yt_get = titled
    vids = w.pick_videos(DAY, cfg(blocked_words=["prank"]), "k", set())
    assert vids and not any("prank" in v["title"].lower() or "#shorts" in v["title"].lower() for v in vids)


def test_email_subject_cannot_inject_headers():
    assert "\r" not in g.header_safe("Hi\r\nBcc: victim@x.com") and "\n" not in g.header_safe("a\nb")


def test_telegram_message_fits_limit():
    vids = [{"id": f"id{i}", "title": "x" * 150, "channel": "c" * 80, "minutes": 10} for i in range(40)]
    text, kept = g.fit_telegram(lambda vs: w.build_message("T", "monday", vs), vids)
    assert len(text) <= g.TELEGRAM_LIMIT and 0 < len(kept) < 40


# ── code / runtime ───────────────────────────────────────────────────────────

def test_secrets_are_redacted(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "AIzaSECRETKEY123")
    msg = ("GET https://x/v3/search?q=a&key=AIzaSECRETKEY123 failed; "
           "url: /bot123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw/sendMessage")
    out = g.redact(msg)
    assert "AIzaSECRETKEY123" not in out and "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw" not in out


def test_quota_cap_stops_spending():
    q = g.Quota(limit=150)
    q.spend("search"); q.spend("channels")
    with pytest.raises(g.QuotaExceeded):
        q.spend("search")


class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body, self.ok = status, body, 200 <= status < 300
        self.text = json.dumps(body)
    def json(self):
        return self._body


def test_yt_get_retries_transient_errors(monkeypatch):
    calls = []
    def get(url, params, timeout):
        calls.append(1)
        return FakeResp(503, {}) if len(calls) < 3 else FakeResp(200, {"items": []})
    monkeypatch.setattr(w.requests, "get", get)
    monkeypatch.setattr(w.time, "sleep", lambda s: None)
    monkeypatch.setattr(w, "QUOTA", g.Quota())
    assert REAL_YT_GET("videos", "k", id="x") == {"items": []} and len(calls) == 3


def test_yt_get_quota_exceeded_fails_fast(monkeypatch):
    calls = []
    def get(url, params, timeout):
        calls.append(1)
        return FakeResp(403, {"error": {"errors": [{"reason": "quotaExceeded"}]}})
    monkeypatch.setattr(w.requests, "get", get)
    monkeypatch.setattr(w, "QUOTA", g.Quota())
    for _ in range(3):
        with pytest.raises(w.YouTubeError, match="quota"):
            REAL_YT_GET("search", "k", q="x")
    assert len(calls) == 1, "after quotaExceeded no more requests should be made"


def test_yt_get_network_error_is_redacted(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "AIzaSECRETKEY123")
    def get(url, params, timeout):
        raise w.requests.ConnectionError("Max retries exceeded with url: /search?key=AIzaSECRETKEY123")
    monkeypatch.setattr(w.requests, "get", get)
    monkeypatch.setattr(w.time, "sleep", lambda s: None)
    monkeypatch.setattr(w, "QUOTA", g.Quota())
    with pytest.raises(w.YouTubeError) as e:
        REAL_YT_GET("videos", "AIzaSECRETKEY123", id="x")
    assert "AIzaSECRETKEY123" not in str(e.value)


def test_corrupt_sent_history_is_set_aside(tmp_path, monkeypatch):
    p = tmp_path / "sent.json"
    p.write_text("{not json")
    monkeypatch.setattr(w, "SENT_PATH", p)
    assert w.load_sent() == set()
    assert (tmp_path / "sent.json.corrupt").read_text() == "{not json"


def test_atomic_write(tmp_path):
    p = tmp_path / "f.txt"
    g.atomic_write(p, "hello")
    assert p.read_text() == "hello" and [x.name for x in tmp_path.iterdir()] == ["f.txt"]
