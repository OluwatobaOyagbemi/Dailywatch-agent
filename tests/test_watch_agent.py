"""Offline tests: a fake YouTube API stands in for the real one, so no key or network is needed."""
import sys, pathlib
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import watch_agent as w  # noqa: E402

NOW = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def fake_yt(themed=("A", "B"), empty=(), short=()):
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
            return {"items": [{"id": i, "contentDetails": {"duration": "PT0M30S" if i in short else "PT12M"}}
                              for i in p["id"].split(",")]}
        raise AssertionError(endpoint)
    return _get


def cfg(**over):
    base = {"videos_per_day": 5, "max_video_age_days": 60, "min_duration_minutes": 4,
            "creators_only": True, "match_theme": True, "fallback_to_latest": False}
    base.update(over)
    return base


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


def test_message_formatting_strips_markdown_specials():
    msg = w.build_message("Leadership", "monday",
                          [{"id": "x1", "title": "a*b_c[d]", "channel": "Chan_nel"}])
    assert "*Monday — Leadership*" in msg
    assert "[abc(d)](https://youtu.be/x1)" in msg


def test_email_html_escapes_titles():
    html_out = w.build_html("Leadership", "monday",
                            [{"id": "x1", "title": "<script>alert(1)</script>", "channel": "C"}])
    assert "<script>" not in html_out and "&lt;script&gt;" in html_out
