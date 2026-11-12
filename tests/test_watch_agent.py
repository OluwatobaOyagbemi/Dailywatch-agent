"""Offline tests: a fake YouTube API stands in for the real one, so no key or network is needed."""
import sys, pathlib
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import watch_agent as w  # noqa: E402
import pytest  # noqa: E402

NOW = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def fake_yt(themed=("A", "B"), empty=(), short=(), lengths=None, no_embed=()):
    """Build a fake yt_get: channels in `themed` return on-theme videos; those in `empty` return none."""
    def _get(endpoint, api_key, **p):
        if endpoint == "channels":
            h = p["forHandle"]
            if h == "missing":
                return {"items": []}
            return {"items": [{"id": "C_" + h, "contentDetails": {"relatedPlaylists": {"uploads": "U_" + h}}}]}
        if endpoint == "search":
            ch = p["channelId"][2:]
            if ch in empty:
                return {"items": []}
            return {"items": [{"id": {"videoId": f"{ch}_{i}"},
                               "snippet": {"title": f"{ch} &quot;talk&quot; {i}", "channelTitle": ch, "publishedAt": NOW}}
                              for i in range(4)]}
        if endpoint == "playlistItems":
            ch = p["playlistId"][2:]
            return {"items": [{"snippet": {"resourceId": {"videoId": f"{ch}_latest"},
                                           "title": "unrelated upload", "channelTitle": ch, "publishedAt": NOW}}]}
        if endpoint == "videos":
            lengths_ = lengths or {}
            return {"items": [{"id": i,
                               "contentDetails": {"duration": "PT0M30S" if i in short else lengths_.get(i, "PT12M")},
                               "status": {"embeddable": i not in no_embed}}
                              for i in p["id"].split(",")]}
        raise AssertionError(endpoint)
    return _get


def cfg(**over):
    base = {"videos_per_day": 5, "max_video_age_days": 60, "min_duration_minutes": 4,
            "creators_only": True, "match_theme": True, "fallback_to_latest": False}
    base.update(over)
    return base


@pytest.fixture(autouse=True)
def _clear_cache():
    w._channel_cache.clear()


DAY = {"theme": "Leadership", "keywords": "leadership", "creators": ["A", "B"], "search": ["leadership tips"]}


def test_round_robin_across_creators():
    w.yt_get = fake_yt()
    vids = w.pick_videos(DAY, cfg(), "k", set())
    assert [v["channel"] for v in vids] == ["A", "B", "A", "B", "A"]


def test_never_repeats_sent_videos():
    w.yt_get = fake_yt()
    vids = w.pick_videos(DAY, cfg(), "k", {"A_0", "A_1", "B_0"})
    assert {v["id"] for v in vids}.isdisjoint({"A_0", "A_1", "B_0"})


def test_off_theme_creator_is_skipped_by_default():
    w.yt_get = fake_yt(empty=("B",))
    vids = w.pick_videos(DAY, cfg(), "k", set())
    assert all(v["channel"] == "A" for v in vids)
    assert not any(v["id"].endswith("_latest") for v in vids)


def test_fallback_to_latest_when_enabled():
    w.yt_get = fake_yt(empty=("B",))
    vids = w.pick_videos(DAY, cfg(fallback_to_latest=True), "k", set())
    assert any(v["id"] == "B_latest" for v in vids)


def test_shorts_are_dropped():
    w.yt_get = fake_yt(short=("A_0", "A_1"))
    vids = w.pick_videos(DAY, cfg(), "k", set())
    assert "A_0" not in {v["id"] for v in vids} and "A_1" not in {v["id"] for v in vids}


def test_missing_channel_does_not_crash():
    w.yt_get = fake_yt()
    day = dict(DAY, creators=["missing", "A"])
    vids = w.pick_videos(day, cfg(), "k", set())
    assert vids and all(v["channel"] == "A" for v in vids)


def test_html_entities_are_decoded():
    w.yt_get = fake_yt()
    vids = w.pick_videos(DAY, cfg(), "k", set())
    assert '"talk"' in vids[0]["title"] and "&quot;" not in vids[0]["title"]


def test_creators_only_blocks_general_search():
    calls = []
    inner = fake_yt(empty=("A", "B"))
    def spy(endpoint, api_key, **p):
        calls.append((endpoint, p.get("channelId")))
        return inner(endpoint, api_key, **p)
    w.yt_get = spy
    vids = w.pick_videos(DAY, cfg(), "k", set())
    assert vids == []
    assert all(ch is not None for e, ch in calls if e == "search"), "general search must not run"


def test_telegram_message_escapes_titles_and_channels():
    msg = w.build_message("Leadership", "monday",
                          [{"id": "x1", "title": "a*b_c[d] <b>", "channel": "Chan_nel & co", "minutes": 12}])
    assert "<b>Monday — Leadership</b>" in msg
    assert '<a href="https://youtu.be/x1">a*b_c[d] &lt;b&gt;</a> · 12 min' in msg
    assert "<i>Chan_nel &amp; co</i>" in msg
    assert "1 video · 12 min total" in msg


def test_plain_text_keeps_underscores_in_links():
    txt = w.build_plain("Money", "thursday", [{"id": "1uVjU0B6_YM", "title": "t", "channel": "c"}])
    assert "https://youtu.be/1uVjU0B6_YM" in txt


def test_email_html_escapes_titles():
    html_out = w.build_html("Leadership", "monday",
                            [{"id": "x1", "title": "<script>alert(1)</script>", "channel": "C"}])
    assert "<script>" not in html_out and "&lt;script&gt;" in html_out


def test_daily_minutes_budget_caps_total_watch_time():
    w.yt_get = fake_yt(lengths={"A_0": "PT40M", "B_0": "PT15M", "A_1": "PT20M", "B_1": "PT10M"})
    vids = w.pick_videos(DAY, cfg(daily_minutes_budget=30), "k", set())
    assert sum(v["minutes"] for v in vids) <= 30
    assert [v["id"] for v in vids] == ["B_0", "B_1"]


def test_budget_still_sends_one_video_if_nothing_fits():
    w.yt_get = fake_yt()  # everything is 12 min
    vids = w.pick_videos(DAY, cfg(daily_minutes_budget=5), "k", set())
    assert len(vids) == 1


def test_distraction_free_links_use_bare_player_unless_not_embeddable():
    w.yt_get = fake_yt(no_embed=("B_0",))
    c = cfg(distraction_free=True)
    vids = w.pick_videos(DAY, c, "k", set())
    urls = {v["id"]: w.video_url(v, c) for v in vids}
    assert urls["A_0"] == "https://www.youtube-nocookie.com/embed/A_0?rel=0"
    assert urls["B_0"] == "https://youtu.be/B_0"


def test_prompt_is_appended():
    vids = [{"id": "x", "title": "t", "channel": "c"}]
    msg = w.build_message("T", "monday", vids, {"prompt": "Write one takeaway."}, {})
    assert msg.endswith("💭 Write one takeaway.")
    msg = w.build_message("T", "monday", vids, {"prompt": "global"}, {"prompt": "day"})
    assert msg.endswith("💭 day")


def test_one_failing_channel_does_not_block_the_other(monkeypatch):
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "SMTP_USER", "SMTP_PASS"):
        monkeypatch.setenv(k, "x")
    def boom(*a):
        raise RuntimeError("telegram down")
    sent = []
    monkeypatch.setattr(w, "send_telegram", boom)
    monkeypatch.setattr(w, "send_email", lambda *a: sent.append(a))
    out = w.deliver({"deliver": ["telegram", "email"]}, {"theme": "T"}, "monday",
                    [{"id": "x", "title": "t", "channel": "c"}])
    assert out == ["email"] and sent


def test_check_reports_bad_handles_and_missing_secrets(monkeypatch, capsys):
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(k, raising=False)
    w.yt_get = fake_yt()
    code = w.check({"deliver": ["telegram"], "days": {"monday": {"theme": "T", "creators": ["A", "missing"]},
                                                       "funday": {"theme": "x"}}}, "k")
    out = capsys.readouterr().out
    assert code == 1
    assert "@missing not found" in out and "TELEGRAM_BOT_TOKEN" in out and "'funday'" in out
    assert "rest days" in out


def test_durations_parse():
    assert w.iso_duration_to_minutes("PT1H5M30S") == 65.5
    assert w.iso_duration_to_minutes("P0D") == 0
    assert w.fmt_minutes(95) == "1h 35m" and w.fmt_minutes(12.4) == "12 min"


def only_old_on_theme(old_for=("B",)):
    """Creators in `old_for` have nothing recent on-theme, but older on-theme videos exist."""
    inner = fake_yt()
    def _get(endpoint, api_key, **p):
        if endpoint == "search" and p["channelId"][2:] in old_for:
            if "publishedAfter" in p:
                return {"items": []}
            ch = p["channelId"][2:]
            return {"items": [{"id": {"videoId": f"{ch}_old{i}"},
                               "snippet": {"title": f"{ch} old talk {i}", "channelTitle": ch,
                                           "publishedAt": f"202{3 - i}-03-01T00:00:00Z"}}
                              for i in range(2)]}
        return inner(endpoint, api_key, **p)
    return _get


def test_creator_with_nothing_recent_gets_older_on_theme_video():
    w.yt_get = only_old_on_theme()
    vids = w.pick_videos(DAY, cfg(), "k", set())
    assert [v["id"] for v in vids] == ["A_0", "B_old0", "A_1", "B_old1", "A_2"]
    assert not any(v["id"].endswith("_latest") for v in vids), "fallback must stay on-theme"


def test_older_on_theme_skips_already_sent():
    w.yt_get = only_old_on_theme()
    vids = w.pick_videos(DAY, cfg(), "k", {"B_old0"})
    assert "B_old0" not in {v["id"] for v in vids} and "B_old1" in {v["id"] for v in vids}


def test_older_pick_shows_upload_month():
    w.yt_get = only_old_on_theme(old_for=("A", "B"))
    vids = w.pick_videos(DAY, cfg(), "k", set())
    msg = w.build_message("Leadership", "monday", vids)
    assert "· 12 min · Mar 2023" in msg


def test_older_on_theme_can_be_turned_off():
    w.yt_get = only_old_on_theme()
    vids = w.pick_videos(DAY, cfg(older_on_theme=False), "k", set())
    assert all(v["channel"] == "A" for v in vids)
