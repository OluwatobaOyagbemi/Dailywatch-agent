#!/usr/bin/env python3
"""
Daily Watch Agent
-----------------
Every day: look up today's theme in config.yaml, find fresh on-theme videos
from the creators you listed, skip anything already sent, keep the total
inside your daily time budget, and send the picks to Telegram and/or email.

Env vars:
    YOUTUBE_API_KEY                         required
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID    for Telegram delivery
    SMTP_USER, SMTP_PASS (+ optional EMAIL_TO, SMTP_HOST, SMTP_PORT)  for email

Usage:
    python watch_agent.py --check            # validate config, handles and secrets
    python watch_agent.py --preview          # the whole week's picks, send nothing
    python watch_agent.py --dry-run          # today's picks, send nothing
    python watch_agent.py --day friday       # pretend it's another weekday
    python watch_agent.py                    # send for real
"""

import argparse
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.yaml"
SENT_PATH = ROOT / "sent.json"
LAST_PATH = ROOT / "last_sent.txt"
YT = "https://www.googleapis.com/youtube/v3"
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


# ── helpers ──────────────────────────────────────────────────────────────────

def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def load_sent():
    if SENT_PATH.exists():
        return set(json.loads(SENT_PATH.read_text() or "[]"))
    return set()


def save_sent(sent):
    SENT_PATH.write_text(json.dumps(sorted(sent), indent=2))


def iso_duration_to_minutes(iso):
    """PT1H5M30S -> 65.5"""
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return d * 1440 + h * 60 + mi + s / 60


def fmt_minutes(m):
    """12.4 -> '12 min', 95 -> '1h 35m'."""
    m = int(round(m or 0))
    if m < 60:
        return f"{max(m, 1)} min"
    return f"{m // 60}h {m % 60:02d}m"


def yt_get(endpoint, api_key, **params):
    params["key"] = api_key
    r = requests.get(f"{YT}/{endpoint}", params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def _video(vid, sn):
    return {
        "id": vid,
        "title": html.unescape(sn["title"]),
        "channel": html.unescape(sn["channelTitle"]),
        "published": datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00")),
    }


# ── YouTube lookups ──────────────────────────────────────────────────────────

_channel_cache = {}


def resolve_channel(handle, api_key):
    """Resolve @handle -> (channel_id, uploads_playlist_id). Cost: 1 unit, cached per run."""
    handle = handle.lstrip("@")
    if handle in _channel_cache:
        return _channel_cache[handle]
    data = yt_get("channels", api_key, part="contentDetails", forHandle=handle)
    items = data.get("items", [])
    if not items:
        print(f"  ! channel @{handle} not found", file=sys.stderr)
        result = (None, None)
    else:
        it = items[0]
        result = (it["id"], it["contentDetails"]["relatedPlaylists"]["uploads"])
    _channel_cache[handle] = result
    return result


def recent_from_channel(handle, api_key, cutoff, limit=10):
    """Latest uploads from one channel (cost: ~2 units)."""
    try:
        _, playlist = resolve_channel(handle, api_key)
        if not playlist:
            return []
        data = yt_get("playlistItems", api_key, part="snippet",
                      playlistId=playlist, maxResults=limit)
    except requests.HTTPError as e:
        print(f"  ! could not read @{handle}: {e.response.status_code} — skipping", file=sys.stderr)
        return []
    out = []
    for it in data.get("items", []):
        sn = it["snippet"]
        v = _video(sn["resourceId"]["videoId"], sn)
        if v["published"] >= cutoff:
            out.append(v)
    return out


def search_videos(query, api_key, cutoff, limit=5):
    """Keyword search across YouTube (cost: 100 units — used only as fallback)."""
    data = yt_get("search", api_key, part="snippet", q=query, type="video",
                  order="relevance", maxResults=limit,
                  publishedAfter=cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
                  videoDuration="medium")
    return [_video(it["id"]["videoId"], it["snippet"]) for it in data.get("items", [])]


def theme_from_channel(handle, query, api_key, cutoff, limit=8):
    """Videos from one creator that match the day's theme, newest first (cost: ~101 units).
    cutoff=None searches the creator's whole back catalogue."""
    try:
        channel_id, _ = resolve_channel(handle, api_key)
        if not channel_id:
            return []
        params = dict(part="snippet", channelId=channel_id, q=query,
                      type="video", order="date", maxResults=limit)
        if cutoff is not None:
            params["publishedAfter"] = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
        data = yt_get("search", api_key, **params)
    except requests.HTTPError as e:
        print(f"  ! theme search failed for @{handle}: {e.response.status_code} — skipping", file=sys.stderr)
        return []
    return [_video(it["id"]["videoId"], it["snippet"]) for it in data.get("items", [])]


def add_details(videos, api_key, min_minutes):
    """Attach duration + embeddable flag, and drop Shorts / short clips (cost: 1 unit per 50)."""
    if not videos:
        return videos
    details = {}
    for i in range(0, len(videos), 50):
        ids = ",".join(v["id"] for v in videos[i:i + 50])
        try:
            data = yt_get("videos", api_key, part="contentDetails,status", id=ids)
        except requests.HTTPError as e:
            print(f"  ! could not read video details: {e.response.status_code}", file=sys.stderr)
            continue
        for it in data.get("items", []):
            details[it["id"]] = it
    out = []
    for v in videos:
        d = details.get(v["id"])
        if d is None:
            if min_minutes > 0:
                continue  # unknown length — can't prove it isn't a Short
            out.append(v)
            continue
        v["minutes"] = iso_duration_to_minutes(d["contentDetails"].get("duration"))
        v["embeddable"] = d.get("status", {}).get("embeddable", True)
        if v["minutes"] >= min_minutes:
            out.append(v)
    return out


# ── core ─────────────────────────────────────────────────────────────────────

def select_within_budget(candidates, n, budget):
    """Take up to n videos in order, keeping total minutes <= budget (0 = no budget)."""
    out, total = [], 0
    for v in candidates:
        if len(out) >= n:
            break
        m = v.get("minutes", 0)
        if budget and total + m > budget:
            continue
        out.append(v)
        total += m
    if not out and candidates and budget:
        # nothing fits: send the single shortest one rather than nothing
        out = [min(candidates, key=lambda v: v.get("minutes", 0))]
    return out


def pick_videos(day_cfg, cfg, api_key, sent):
    n = cfg.get("videos_per_day", 5)
    budget = cfg.get("daily_minutes_budget", 0) or 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.get("max_video_age_days", 60))
    min_dur = cfg.get("min_duration_minutes", 0)

    seen = set()
    match_theme = cfg.get("match_theme", True)
    keywords = day_cfg.get("keywords") or day_cfg["theme"]

    def fresh(videos):
        out = []
        for v in videos:
            if v["id"] in sent or v["id"] in seen:
                continue
            seen.add(v["id"])
            out.append(v)
        return out

    # 1) one list per creator: recent on-theme videos; if none, that creator's older
    #    on-theme videos; if still none (and allowed), their latest uploads
    per_creator = []
    for handle in day_cfg.get("creators") or []:
        handle = str(handle).lstrip("@")
        vids = []
        if match_theme:
            print(f"  @{handle}: searching for '{keywords}'")
            vids = add_details(fresh(theme_from_channel(handle, keywords, api_key, cutoff)),
                               api_key, min_dur)
            if not vids and cfg.get("older_on_theme", True):
                print(f"  @{handle}: nothing recent on-theme — searching their older videos")
                vids = add_details(fresh(theme_from_channel(handle, keywords, api_key, None, limit=25)),
                                   api_key, min_dur)
                for v in vids:
                    v["older"] = True
        if not vids:
            if match_theme and not cfg.get("fallback_to_latest", False):
                print(f"  @{handle}: no theme match — skipping (fallback_to_latest is off)")
                continue
            print(f"  @{handle}: using latest uploads")
            vids = add_details(fresh(recent_from_channel(handle, api_key, cutoff)), api_key, min_dur)
        vids.sort(key=lambda v: v["published"], reverse=True)
        if vids:
            per_creator.append(vids)

    # 2) round-robin so every creator gets a turn
    candidates = []
    while any(per_creator):
        for lst in per_creator:
            if lst:
                candidates.append(lst.pop(0))
    pool = select_within_budget(candidates, n, budget)

    # 3) general search only if allowed and still short
    search_terms = [] if cfg.get("creators_only", False) else (day_cfg.get("search") or [])
    for q in search_terms:
        if len(pool) >= n:
            break
        print(f"  searching '{q}'")
        try:
            extra = search_videos(q, api_key, cutoff)
        except requests.HTTPError as e:
            print(f"  ! search failed: {e.response.status_code} — skipping", file=sys.stderr)
            continue
        candidates.extend(add_details(fresh(extra), api_key, min_dur))
        pool = select_within_budget(candidates, n, budget)

    return pool


# ── messages ─────────────────────────────────────────────────────────────────

def video_url(v, cfg=None):
    """Distraction-free mode links to the bare player: no feed, no comments, no autoplay rabbit hole."""
    if (cfg or {}).get("distraction_free") and v.get("embeddable", True):
        return f"https://www.youtube-nocookie.com/embed/{v['id']}?rel=0"
    return f"https://youtu.be/{v['id']}"


def summary_line(videos):
    total = sum(v.get("minutes", 0) for v in videos)
    count = f"{len(videos)} video{'s' if len(videos) != 1 else ''}"
    return f"{count} · {fmt_minutes(total)} total" if total else count


def day_prompt(day_cfg, cfg):
    return (day_cfg or {}).get("prompt") or (cfg or {}).get("prompt") or ""


EMPTY = "Nothing new from your creators today. Add a creator or two in config.yaml."


def video_meta(v):
    """'12 min', plus the upload month for older picks: '12 min · Mar 2024'."""
    parts = []
    if v.get("minutes"):
        parts.append(fmt_minutes(v["minutes"]))
    if v.get("older") and v.get("published"):
        parts.append(v["published"].strftime("%b %Y"))
    return " · ".join(parts)


def build_message(theme, weekday, videos, cfg=None, day_cfg=None):
    """Telegram version (HTML parse mode — safe for any title)."""
    e = html.escape
    lines = [f"🎯 <b>{e(weekday.title())} — {e(theme)}</b>"]
    if not videos:
        return "\n".join(lines + ["", EMPTY])
    lines += [summary_line(videos), ""]
    for i, v in enumerate(videos, 1):
        length = f" · {video_meta(v)}" if video_meta(v) else ""
        lines.append(f'{i}. <a href="{e(video_url(v, cfg))}">{e(v["title"])}</a>{e(length)}')
        lines.append(f"   <i>{e(v['channel'])}</i>")
    prompt = day_prompt(day_cfg, cfg)
    if prompt:
        lines += ["", f"💭 {e(prompt)}"]
    return "\n".join(lines)


def build_plain(theme, weekday, videos, cfg=None, day_cfg=None):
    """Plain-text version (email fallback, terminal preview)."""
    lines = [f"🎯 {weekday.title()} — {theme}"]
    if not videos:
        return "\n".join(lines + ["", EMPTY])
    lines += [summary_line(videos), ""]
    for i, v in enumerate(videos, 1):
        length = f" ({video_meta(v)})" if video_meta(v) else ""
        lines.append(f"{i}. {v['title']}{length}")
        lines.append(f"   {v['channel']} — {video_url(v, cfg)}")
    prompt = day_prompt(day_cfg, cfg)
    if prompt:
        lines += ["", f"💭 {prompt}"]
    return "\n".join(lines)


def build_html(theme, weekday, videos, cfg=None, day_cfg=None):
    """Email (HTML) version."""
    e = html.escape
    rows = "".join(
        f'<li style="margin:0 0 14px"><a href="{e(video_url(v, cfg))}" '
        f'style="font-size:16px;color:#1a5fb4;text-decoration:none">{e(v["title"])}</a>'
        f'<br><span style="color:#666;font-size:13px">{e(v["channel"])}'
        f'{" · " + e(video_meta(v)) if video_meta(v) else ""}</span></li>'
        for v in videos
    ) or f'<li>{EMPTY}</li>'
    sub = summary_line(videos) if videos else "Your picks for today."
    prompt = day_prompt(day_cfg, cfg)
    prompt_html = (f'<p style="margin:20px 0 0;padding:12px 14px;background:#f4f6fa;border-radius:8px">'
                   f'💭 {e(prompt)}</p>') if prompt and videos else ""
    return (f'<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;max-width:560px;'
            f'margin:0 auto;padding:24px;color:#222">'
            f'<h2 style="margin:0 0 4px">🎯 {e(weekday.title())} — {e(theme)}</h2>'
            f'<p style="margin:0 0 20px;color:#666">{e(sub)}</p>'
            f'<ol style="padding-left:20px">{rows}</ol>{prompt_html}'
            f'<p style="margin-top:28px;font-size:12px;color:#999">Sent by Daily Watch Agent</p></div>')


# ── delivery ─────────────────────────────────────────────────────────────────

def send_telegram(text, token, chat_id):
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                            "disable_web_page_preview": True},
                      timeout=20)
    if not r.ok:
        raise RuntimeError(f"Telegram said {r.status_code}: {r.text[:200]}")


def send_email(subject, html_body, plain_body):
    """SMTP email. Works with Gmail (app password), Outlook, or any SMTP host."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    host = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
    port = int(os.environ.get("SMTP_PORT") or "587")
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASS"]
    to = os.environ.get("EMAIL_TO") or user
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls()
        s.login(user, password)
        s.sendmail(user, [to], msg.as_string())


def delivery_channels(cfg):
    channels = cfg.get("deliver", ["telegram"])
    return [channels] if isinstance(channels, str) else list(channels)


def missing_secrets(channel):
    need = {"telegram": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"],
            "email": ["SMTP_USER", "SMTP_PASS"]}.get(channel)
    if need is None:
        return None
    return [k for k in need if not os.environ.get(k)]


def deliver(cfg, day_cfg, weekday, videos):
    """Send via every channel in `deliver:`. One failing channel doesn't stop the others."""
    theme = day_cfg["theme"]
    delivered = []
    for ch in delivery_channels(cfg):
        missing = missing_secrets(ch)
        if missing is None:
            print(f"  ! unknown delivery channel '{ch}' (use telegram or email)", file=sys.stderr)
            continue
        if missing:
            print(f"  ! {ch}: {', '.join(missing)} not set — skipping", file=sys.stderr)
            continue
        try:
            if ch == "telegram":
                send_telegram(build_message(theme, weekday, videos, cfg, day_cfg),
                              os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"])
            else:
                subject = f"{weekday.title()} — {theme}"
                if videos:
                    subject += f" ({summary_line(videos)})"
                send_email(subject, build_html(theme, weekday, videos, cfg, day_cfg),
                           build_plain(theme, weekday, videos, cfg, day_cfg))
            delivered.append(ch)
        except Exception as ex:  # keep going: email can still land if Telegram is down
            print(f"  ! {ch} failed: {ex}", file=sys.stderr)
    return delivered


# ── setup check ──────────────────────────────────────────────────────────────

def check(cfg, api_key):
    """Validate config, creator handles and secrets. Returns a process exit code."""
    problems, notes = [], []
    days = cfg.get("days") or {}
    for d in days:
        if d not in WEEKDAYS:
            problems.append(f"'{d}' under days: is not a weekday name (use monday … sunday)")
        elif not (days[d] or {}).get("theme"):
            problems.append(f"{d}: missing `theme:`")
    rest = [d for d in WEEKDAYS if d not in days]
    if rest:
        notes.append(f"rest days (nothing sent): {', '.join(rest)}")

    for ch in delivery_channels(cfg):
        missing = missing_secrets(ch)
        if missing is None:
            problems.append(f"deliver: unknown channel '{ch}' (use telegram or email)")
        elif missing:
            problems.append(f"{ch}: set {', '.join(missing)}")
        else:
            print(f"✓ {ch} secrets present")

    handles = sorted({str(h).lstrip("@") for d in days.values() for h in ((d or {}).get("creators") or [])})
    if not api_key:
        problems.append("YOUTUBE_API_KEY is not set (can't check creator handles)")
    else:
        for h in handles:
            try:
                cid, _ = resolve_channel(h, api_key)
            except requests.HTTPError as e:
                problems.append(f"YouTube API error {e.response.status_code} — is the key valid and the API enabled?")
                break
            if cid:
                print(f"✓ @{h}")
            else:
                problems.append(f"@{h} not found — copy the part after @ from youtube.com/@handle")

    per_creator = 102 if cfg.get("match_theme", True) else 3
    busiest = max((len((d or {}).get("creators") or []) for d in days.values()), default=0)
    notes.append(f"busiest day uses ~{busiest * per_creator} of 10,000 free YouTube units")

    for n in notes:
        print(f"· {n}")
    if problems:
        print("\nFix these:")
        for p in problems:
            print(f"  ✗ {p}")
        return 1
    print("\nAll good. Try: python watch_agent.py --dry-run")
    return 0


# ── entry point ──────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Send today's themed YouTube picks.")
    ap.add_argument("--day", help="weekday name, e.g. monday (default: today)")
    ap.add_argument("--dry-run", action="store_true", help="print today's picks, send nothing")
    ap.add_argument("--preview", action="store_true", help="print the whole week's picks, send nothing")
    ap.add_argument("--check", action="store_true", help="validate config, creator handles and secrets")
    ap.add_argument("--force", action="store_true", help="send even if already sent today")
    args = ap.parse_args()

    cfg = load_config()
    api_key = os.environ.get("YOUTUBE_API_KEY")

    if args.check:
        sys.exit(check(cfg, api_key))
    if not api_key:
        sys.exit("YOUTUBE_API_KEY is not set (run with --check for a full setup check)")
    sent = load_sent()
    days = cfg.get("days") or {}

    if args.preview:
        for weekday in WEEKDAYS:
            day_cfg = days.get(weekday)
            print(f"\n{'=' * 60}")
            if not day_cfg:
                print(f"{weekday.title()}: rest day")
                continue
            print(f"{weekday.title()}: {day_cfg['theme']}")
            videos = pick_videos(day_cfg, cfg, api_key, sent)
            print(build_plain(day_cfg["theme"], weekday, videos, cfg, day_cfg))
        return

    today = datetime.now().strftime("%Y-%m-%d")
    if not args.dry_run and not args.force and LAST_PATH.exists() and LAST_PATH.read_text().strip() == today:
        print(f"Already sent today ({today}); skipping. Use --force to resend.")
        return

    weekday = (args.day or datetime.now().strftime("%A")).lower()
    if weekday not in WEEKDAYS:
        sys.exit(f"'{weekday}' is not a weekday name")
    day_cfg = days.get(weekday)
    if not day_cfg:
        print(f"{weekday.title()} is a rest day (no entry in config.yaml) — nothing to send.")
        return

    print(f"{weekday.title()}: {day_cfg['theme']}")
    videos = pick_videos(day_cfg, cfg, api_key, sent)

    if args.dry_run:
        print("\n" + build_plain(day_cfg["theme"], weekday, videos, cfg, day_cfg))
        return

    delivered = deliver(cfg, day_cfg, weekday, videos)
    if not delivered:
        sys.exit("Nothing was delivered — run `python watch_agent.py --check` to see what's missing.")
    sent.update(v["id"] for v in videos)
    save_sent(sent)
    LAST_PATH.write_text(today + "\n")
    print(f"Sent {len(videos)} video(s) via {', '.join(delivered)}.")


if __name__ == "__main__":
    main()
