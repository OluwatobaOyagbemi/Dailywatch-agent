"""
Gamification for Daily Watch Agent: XP, levels, streaks and badges.

Everything here is a pure function over a plain dict (saved as progress.json),
so it's easy to test and there's no database or server.

    ✅ watched a video        +10 XP
    💪 did the day's practice  +20 XP
    💯 perfect day bonus       +15 XP  (every video watched or skipped, ≥1 watched, practice done)
"""

import json
from datetime import date, timedelta

XP_WATCH = 10
XP_TASK = 20
XP_PERFECT = 15

LEVELS = [
    (0, "Recovering Scroller"),
    (100, "Intentional Viewer"),
    (250, "Learner"),
    (500, "Student"),
    (900, "Practitioner"),
    (1400, "Builder"),
    (2000, "Achiever"),
    (3000, "Mentor"),
    (4500, "Master"),
    (6500, "Legend"),
]

BADGES = {
    "first_watch": "🎬 First Watch",
    "first_task": "💪 First Rep",
    "perfect_day": "💯 Perfect Day",
    "streak_3": "🔥 3-Day Streak",
    "streak_7": "🗓️ Full Week",
    "streak_30": "🏆 30-Day Streak",
    "perfect_week": "⭐ Perfect Week",
    "videos_25": "📚 25 Videos",
    "videos_100": "🧠 100 Videos",
    "tasks_10": "🛠️ 10 Practice Reps",
}
THEME_BADGE_AT = 10  # watched videos in one theme → "🎓 <theme> Regular"


# ── state ────────────────────────────────────────────────────────────────────

def new_state():
    return {"xp": 0, "last_update_id": 0, "days": {}, "badges": {}}


def load(path):
    try:
        state = json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return new_state()
    base = new_state()
    base.update(state)
    return base


def save(path, state):
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def record_day(state, day, weekday, theme, videos, task=None, message_id=None):
    """Remember what was sent on `day` (YYYY-MM-DD) so later button taps can be scored."""
    state["days"][day] = {
        "weekday": weekday,
        "theme": theme,
        "videos": [v["id"] for v in videos],
        "titles": [v.get("title", "") for v in videos],
        "watched": [],
        "skipped": [],
        "task": task or None,
        "task_done": False,
        "perfect": False,
        "xp": 0,
        "message_id": message_id,
    }


# ── scoring ──────────────────────────────────────────────────────────────────

def level(xp):
    """-> (level number, name, xp needed for next level or None)"""
    n = 0
    for i, (threshold, _) in enumerate(LEVELS):
        if xp >= threshold:
            n = i
    nxt = LEVELS[n + 1][0] if n + 1 < len(LEVELS) else None
    return n + 1, LEVELS[n][1], nxt


def _gain(state, d, amount):
    state["xp"] += amount
    d["xp"] = d.get("xp", 0) + amount


def _active(d):
    return bool(d["watched"]) or d["task_done"]


def _days_desc(state, today):
    return sorted(((k, d) for k, d in state["days"].items() if k <= today), reverse=True)


def _run(state, today, test):
    """Consecutive sent days (newest first) passing `test`. Today doesn't break a run while it's in progress."""
    count = 0
    for i, (k, d) in enumerate(_days_desc(state, today)):
        if test(d):
            count += 1
        elif i == 0 and k == today:
            continue
        else:
            break
    return count


def streak(state, today):
    """Days in a row you watched something or practised. Rest days don't count against you."""
    return _run(state, today, _active)


def total_watched(state):
    return sum(len(d["watched"]) for d in state["days"].values())


def total_tasks(state):
    return sum(1 for d in state["days"].values() if d["task_done"])


def apply(state, action, day, idx=0, today=None):
    """Apply one button tap. action: 'w' watched, 's' skip, 't' task done. Returns event lines."""
    d = state["days"].get(day)
    if not d:
        return []
    today = today or date.today().isoformat()
    before = level(state["xp"])[0]
    events = []

    if action in ("w", "s"):
        if not 0 <= idx < len(d["videos"]):
            return []
        vid, title = d["videos"][idx], (d["titles"][idx] if idx < len(d["titles"]) else "")
        if action == "w":
            if vid in d["watched"]:
                return []
            if vid in d["skipped"]:
                d["skipped"].remove(vid)
            d["watched"].append(vid)
            _gain(state, d, XP_WATCH)
            events.append(f"✅ +{XP_WATCH} XP — {title}" if title else f"✅ +{XP_WATCH} XP")
        else:
            if vid in d["watched"] or vid in d["skipped"]:
                return []
            d["skipped"].append(vid)
            events.append(f"⏭ Skipped — {title}" if title else "⏭ Skipped")
    elif action == "t":
        if not d["task"] or d["task_done"]:
            return []
        d["task_done"] = True
        _gain(state, d, XP_TASK)
        events.append(f"💪 +{XP_TASK} XP — practice done")
    else:
        return []

    done = set(d["watched"]) | set(d["skipped"])
    if (not d["perfect"] and d["watched"] and done >= set(d["videos"])
            and (not d["task"] or d["task_done"])):
        d["perfect"] = True
        _gain(state, d, XP_PERFECT)
        events.append(f"💯 Perfect day! +{XP_PERFECT} XP")

    after, name, _ = level(state["xp"])
    if after > before:
        events.append(f"⬆️ Level up! Lv {after} — {name}")
    events += award_badges(state, today)
    return events


def award_badges(state, today):
    earned = []

    def give(key, label):
        if key not in state["badges"]:
            state["badges"][key] = today
            earned.append(f"🏅 New badge: {label}")

    watched, tasks, s = total_watched(state), total_tasks(state), streak(state, today)
    checks = [
        ("first_watch", watched >= 1),
        ("first_task", tasks >= 1),
        ("perfect_day", any(d["perfect"] for d in state["days"].values())),
        ("streak_3", s >= 3),
        ("streak_7", s >= 7),
        ("streak_30", s >= 30),
        ("perfect_week", _run(state, today, lambda d: d["perfect"]) >= 7),
        ("videos_25", watched >= 25),
        ("videos_100", watched >= 100),
        ("tasks_10", tasks >= 10),
    ]
    for key, ok in checks:
        if ok:
            give(key, BADGES[key])

    per_theme = {}
    for d in state["days"].values():
        per_theme[d["theme"]] = per_theme.get(d["theme"], 0) + len(d["watched"])
    for theme, n in per_theme.items():
        if n >= THEME_BADGE_AT:
            give(f"theme:{theme}", f"🎓 {theme} Regular")
    return earned


def badge_label(key):
    return BADGES.get(key) or (f"🎓 {key[6:]} Regular" if key.startswith("theme:") else key)


# ── display ──────────────────────────────────────────────────────────────────

def status_line(state, today):
    n, name, nxt = level(state["xp"])
    s = streak(state, today)
    lead = f"🔥 {s}-day streak" if s else "🌱 Tap ✅ after each video to start a streak"
    to_next = f" ({nxt - state['xp']} to Lv {n + 1})" if nxt else ""
    return f"{lead} · Lv {n} {name} · {state['xp']} XP{to_next}"


def weekly_recap(state, today):
    start = (date.fromisoformat(today) - timedelta(days=7)).isoformat()
    week = [d for k, d in state["days"].items() if start <= k < today]
    if not week:
        return ""
    vids = sum(len(d["videos"]) for d in week)
    watched = sum(len(d["watched"]) for d in week)
    tasks = [d for d in week if d["task"]]
    done = sum(1 for d in tasks if d["task_done"])
    xp = sum(d.get("xp", 0) for d in week)
    perfect = sum(1 for d in week if d["perfect"])
    line = f"📊 Last 7 days: {watched}/{vids} videos"
    if tasks:
        line += f" · {done}/{len(tasks)} practice"
    line += f" · {perfect} perfect day{'s' if perfect != 1 else ''} · +{xp} XP"
    return line


def stats_text(state, today):
    n, name, nxt = level(state["xp"])
    lines = [
        f"🎮 Lv {n} — {name}",
        f"{state['xp']} XP" + (f" · {nxt - state['xp']} to next level" if nxt else " · max level"),
        f"🔥 Streak: {streak(state, today)} day(s)",
        f"✅ Videos watched: {total_watched(state)}",
        f"💪 Practice done: {total_tasks(state)}",
    ]
    recap = weekly_recap(state, today)
    if recap:
        lines.append(recap)
    if state["badges"]:
        lines += ["", "Badges:"] + [f"  {badge_label(k)}" for k in sorted(state["badges"], key=state["badges"].get)]
    return "\n".join(lines)


def keyboard(day, n_videos, has_task, watched_idx=(), skipped_idx=(), task_done=False):
    """Telegram inline keyboard: a ✅ row, a ⏭ row, and a practice button."""
    rows = []
    if n_videos:
        rows.append([{"text": ("☑️" if i in watched_idx else "✅") + f" {i + 1}",
                      "callback_data": f"w|{day}|{i}"} for i in range(n_videos)])
        rows.append([{"text": ("➖" if i in skipped_idx else "⏭") + f" {i + 1}",
                      "callback_data": f"s|{day}|{i}"} for i in range(n_videos)])
    if has_task:
        rows.append([{"text": "🏅 Practice done" if task_done else "💪 I did the practice",
                      "callback_data": f"t|{day}|0"}])
    return {"inline_keyboard": rows}


def keyboard_for_day(state, day):
    d = state["days"][day]
    watched = {i for i, v in enumerate(d["videos"]) if v in d["watched"]}
    skipped = {i for i, v in enumerate(d["videos"]) if v in d["skipped"]}
    return keyboard(day, len(d["videos"]), bool(d["task"]), watched, skipped, d["task_done"])
