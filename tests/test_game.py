"""Gamification: XP, streaks, badges, and the Telegram tap sync (offline)."""
import sys, pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import game as g  # noqa: E402
import watch_agent as w  # noqa: E402

VIDS = [{"id": "v1", "title": "One"}, {"id": "v2", "title": "Two"}]


def day_state(days=("2026-11-10",), task="Do it"):
    st = g.new_state()
    for d in days:
        g.record_day(st, d, "monday", "Speaking", VIDS, task)
    return st


def test_watching_earns_xp_once():
    st = day_state()
    assert g.apply(st, "w", "2026-11-10", 0, "2026-11-10")[0].startswith("✅ +10 XP — One")
    assert g.apply(st, "w", "2026-11-10", 0, "2026-11-10") == []
    assert st["xp"] == 10


def test_perfect_day_bonus_needs_all_videos_and_task():
    st = day_state()
    g.apply(st, "w", "2026-11-10", 0)
    ev = g.apply(st, "s", "2026-11-10", 1)
    assert not any("Perfect" in e for e in ev)          # task still open
    ev = g.apply(st, "t", "2026-11-10", 0)
    assert any("Perfect day" in e for e in ev)
    assert st["xp"] == 10 + 20 + 15
    assert "perfect_day" in st["badges"] and "first_task" in st["badges"]


def test_skip_only_is_not_a_perfect_day():
    st = day_state(task=None)
    g.apply(st, "s", "2026-11-10", 0)
    g.apply(st, "s", "2026-11-10", 1)
    assert st["days"]["2026-11-10"]["perfect"] is False and st["xp"] == 0


def test_streak_counts_consecutive_sent_days_and_today_is_pending():
    days = ("2026-11-08", "2026-11-09", "2026-11-10", "2026-11-11")
    st = day_state(days)
    for d in days[1:3]:
        g.apply(st, "w", d, 0, d)
    # 11th (today) untouched -> still in progress, 10th + 9th count, 8th untouched ends it
    assert g.streak(st, "2026-11-11") == 2
    g.apply(st, "w", "2026-11-11", 0, "2026-11-11")
    assert g.streak(st, "2026-11-11") == 3
    assert "streak_3" in st["badges"]


def test_missed_day_breaks_streak():
    st = day_state(("2026-11-09", "2026-11-10", "2026-11-11"))
    g.apply(st, "w", "2026-11-09", 0)
    g.apply(st, "w", "2026-11-11", 0)
    assert g.streak(st, "2026-11-11") == 1


def test_level_up_event():
    st = day_state()
    st["xp"] = 95
    ev = g.apply(st, "w", "2026-11-10", 0)
    assert "⬆️ Level up! Lv 2 — Intentional Viewer" in ev


def test_levels():
    assert g.level(0)[:2] == (1, "Recovering Scroller")
    assert g.level(250)[:2] == (3, "Learner")
    assert g.level(99999)[2] is None


def test_keyboard_shows_state_and_fits_telegram_limits():
    st = day_state()
    g.apply(st, "w", "2026-11-10", 0)
    g.apply(st, "s", "2026-11-10", 1)
    kb = g.keyboard_for_day(st, "2026-11-10")["inline_keyboard"]
    assert [b["text"] for b in kb[0]] == ["☑️ 1", "✅ 2"]
    assert [b["text"] for b in kb[1]] == ["⏭ 1", "➖ 2"]
    assert kb[2][0]["callback_data"] == "t|2026-11-10|0"
    assert all(len(b["callback_data"].encode()) <= 64 for row in kb for b in row)


def test_weekly_recap():
    st = day_state(("2026-11-09", "2026-11-10"))
    g.apply(st, "w", "2026-11-09", 0)
    g.apply(st, "t", "2026-11-10", 0)
    r = g.weekly_recap(st, "2026-11-15")
    assert "1/4 videos" in r and "1/2 practice" in r and "+30 XP" in r


def test_load_missing_file_gives_fresh_state(tmp_path):
    assert g.load(tmp_path / "nope.json")["xp"] == 0


def test_sync_scores_taps_ignores_strangers_and_answers_stats(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    st = day_state(task=None)
    st["days"]["2026-11-10"]["message_id"] = 7
    calls = []

    def fake(token, method, payload):
        calls.append((method, payload))
        if method == "getUpdates":
            me, stranger = {"chat": {"id": 42}}, {"chat": {"id": 99}}
            return [
                {"update_id": 5, "callback_query": {"id": "a", "data": "w|2026-11-10|0", "message": me}},
                {"update_id": 6, "callback_query": {"id": "b", "data": "w|2026-11-10|1", "message": stranger}},
                {"update_id": 7, "callback_query": {"id": "c", "data": "w|2026-11-10|0", "message": me}},
                {"update_id": 8, "message": {"chat": {"id": 42}, "text": "/stats"}},
            ]
        return {}
    monkeypatch.setattr(w, "tg_call", fake)

    assert w.sync_telegram(st, "2026-11-10") is True
    assert st["xp"] == 10 and st["last_update_id"] == 8
    methods = [m for m, _ in calls]
    assert methods.count("editMessageReplyMarkup") == 1
    texts = [p["text"] for m, p in calls if m == "sendMessage"]
    assert any("+10 XP" in t for t in texts) and any("🎮 Lv" in t for t in texts)
    assert calls[0][1]["offset"] == 1


def test_message_shows_status_task_and_hint():
    vids = [{"id": "x", "title": "t", "channel": "c"}]
    msg = w.build_message("T", "monday", vids, {}, {"task": "Record a voice note"}, ["🔥 3-day streak"])
    assert "🔥 3-day streak" in msg and "<b>Practice:</b> Record a voice note" in msg and "Tap ✅" in msg
