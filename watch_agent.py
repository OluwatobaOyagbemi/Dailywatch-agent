#!/usr/bin/env python3
"""
Daily Watch Agent
-----------------
Every morning: look up today's theme in config.yaml, find fresh videos from
the listed creators (and fallback searches), skip anything already sent,
and push the top picks to Telegram.

Env vars required:
    YOUTUBE_API_KEY     from Google Cloud Console (YouTube Data API v3)
    TELEGRAM_BOT_TOKEN  from @BotFather
    TELEGRAM_CHAT_ID    your chat id (see README)

Usage:
    python watch_agent.py              # run for today
    python watch_agent.py --day friday # run for a specific weekday
    python watch_agent.py --dry-run    # print instead of sending
"""

import argparse
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


# ── helpers ──────────────────────────────────────────────────────────────────

def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def load_sent():
    if SENT_PATH.exists():
        return set(json.loads(SENT_PATH.read_text()))
    return set()


def save_sent(sent):
    SENT_PATH.write_text(json.dumps(sorted(sent), indent=2))


def iso_duration_to_minutes(iso):
    """PT1H5M30S -> 65.5"""
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 60 + mi + s / 60


def yt_get(endpoint, api_key, **params):
    params["key"] = api_key
    r = requests.get(f"{YT}/{endpoint}", params=params, timeout=20)
    r.raise_for_status()
    return r.json()


# ── YouTube lookups ──────────────────────────────────────────────────────────

def resolve_channel(handle, api_key):
    """Resolve @handle -> (channel_id, uploads_playlist_id). Cost: 1 unit."""
    data = yt_get("channels", api_key, part="contentDetails", forHandle=handle)
    items = data.get("items", [])
    if not items:
        print(f"  ! channel @{handle} not found", file=sys.stderr)
        return None, None
    it = items[0]
    return it["id"], it["contentDetails"]["relatedPlaylists"]["uploads"]


def channel_uploads_playlist(handle, api_key):
    return resolve_channel(handle, api_key)[1]


def recent_from_channel(handle, api_key, cutoff, limit=10):
    """Latest uploads from one channel (cost: ~2 units)."""
    try:
        playlist = channel_uploads_playlist(handle, api_key)
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
        published = datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00"))
        if published < cutoff:
            continue
        out.append({
            "id": sn["resourceId"]["videoId"],
            "title": sn["title"],
            "channel": sn["channelTitle"],
            "published": published,
        })
    return out


def search_videos(query, api_key, cutoff, limit=5):
    """Keyword search (cost: 100 units — used only as fallback)."""
    data = yt_get("search", api_key, part="snippet", q=query, type="video",
                  order="relevance", maxResults=limit,
                  publishedAfter=cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
                  videoDuration="medium")
    out = []
    for it in data.get("items", []):
        sn = it["snippet"]
        out.append({
            "id": it["id"]["videoId"],
            "title": sn["title"],
            "channel": sn["channelTitle"],
            "published": datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00")),
        })
    return out


def theme_from_channel(handle, query, api_key, cutoff, limit=8):
    """Videos from one creator that match the day's theme (cost: ~101 units)."""
    try:
        channel_id, _ = resolve_channel(handle, api_key)
        if not channel_id:
            return []
        data = yt_get("search", api_key, part="snippet", channelId=channel_id, q=query,
                      type="video", order="date", maxResults=limit,
                      publishedAfter=cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"))
    except requests.HTTPError as e:
        print(f"  ! theme search failed for @{handle}: {e.response.status_code} — skipping", file=sys.stderr)
        return []
    out = []
    for it in data.get("items", []):
        sn = it["snippet"]
        out.append({
            "id": it["id"]["videoId"],
            "title": sn["title"],
            "channel": sn["channelTitle"],
            "published": datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00")),
        })
    return out


def filter_by_duration(videos, api_key, min_minutes):
    """Drop Shorts / very short clips (cost: 1 unit per 50 videos)."""
    if not videos or min_minutes <= 0:
        return videos
    ids = ",".join(v["id"] for v in videos)
    data = yt_get("videos", api_key, part="contentDetails", id=ids)
    durations = {it["id"]: iso_duration_to_minutes(it["contentDetails"]["duration"])
                 for it in data.get("items", [])}
    return [v for v in videos if durations.get(v["id"], 0) >= min_minutes]


# ── core ─────────────────────────────────────────────────────────────────────

def pick_videos(day_cfg, cfg, api_key, sent):
    n = cfg.get("videos_per_day", 5)
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

    # 1) one list per creator: theme-matched videos, else their latest uploads
    per_creator = []
    for handle in day_cfg.get("creators", []):
        vids = []
        if match_theme:
            print(f"  @{handle}: searching for '{keywords}'")
            vids = fresh(theme_from_channel(handle, keywords, api_key, cutoff))
        if not vids:
            print(f"  @{handle}: no theme match, using latest uploads")
            vids = fresh(recent_from_channel(handle, api_key, cutoff))
        vids = filter_by_duration(vids, api_key, min_dur)
        vids.sort(key=lambda v: v["published"], reverse=True)
        if vids:
            per_creator.append(vids)

    # 2) round-robin so every creator gets a turn
    pool = []
    while len(pool) < n and any(per_creator):
        for lst in per_creator:
            if lst and len(pool) < n:
                pool.append(lst.pop(0))

    # 3) general search only if allowed and still short
    search_terms = [] if cfg.get("creators_only", False) else day_cfg.get("search", [])
    for q in search_terms:
        if len(pool) >= n:
            break
        print(f"  searching '{q}'")
        try:
            extra = search_videos(q, api_key, cutoff)
        except requests.HTTPError as e:
            print(f"  ! search failed: {e.response.status_code} — skipping", file=sys.stderr)
            continue
        pool.extend(filter_by_duration(fresh(extra), api_key, min_dur))

    return pool[:n]


def build_message(theme, weekday, videos):
    """Telegram (Markdown) version."""
    lines = [f"🎯 *{weekday.title()} — {theme}*", ""]
    if not videos:
        lines.append("No new videos today. Try adding more creators to config.yaml.")
        return "\n".join(lines)
    for i, v in enumerate(videos, 1):
        title = v["title"].replace("*", "").replace("_", "").replace("[", "(").replace("]", ")")
        lines.append(f"{i}. [{title}](https://youtu.be/{v['id']})")
        lines.append(f"   _{v['channel']}_")
    return "\n".join(lines)


def build_html(theme, weekday, videos):
    """Email (HTML) version."""
    import html
    rows = "".join(
        f'<li style="margin:0 0 14px"><a href="https://youtu.be/{v["id"]}" '
        f'style="font-size:16px;color:#1a5fb4;text-decoration:none">{html.escape(v["title"])}</a>'
        f'<br><span style="color:#666;font-size:13px">{html.escape(v["channel"])}</span></li>'
        for v in videos
    ) or '<li>No new videos today. Try adding more creators to config.yaml.</li>'
    return (f'<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;max-width:560px;'
            f'margin:0 auto;padding:24px;color:#222">'
            f'<h2 style="margin:0 0 4px">🎯 {weekday.title()} — {html.escape(theme)}</h2>'
            f'<p style="margin:0 0 20px;color:#666">Your picks for today.</p>'
            f'<ol style="padding-left:20px">{rows}</ol>'
            f'<p style="margin-top:28px;font-size:12px;color:#999">Sent by Daily Watch Agent</p></div>')


# ── delivery ─────────────────────────────────────────────────────────────────

def send_telegram(text, token, chat_id):
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat_id, "text": text,
                            "parse_mode": "Markdown", "disable_web_page_preview": False},
                      timeout=20)
    r.raise_for_status()


def send_email(subject, html_body, plain_body):
    """SMTP email. Works with Gmail (app password), Outlook, or any SMTP host."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASS"]
    to = os.environ.get("EMAIL_TO", user)
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


def deliver(cfg, theme, weekday, videos):
    """Send via every channel listed in config `deliver:` (default: telegram)."""
    channels = cfg.get("deliver", ["telegram"])
    if isinstance(channels, str):
        channels = [channels]
    text = build_message(theme, weekday, videos)
    delivered = []
    for ch in channels:
        if ch == "telegram":
            token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
            if not token or not chat_id:
                print("  ! telegram: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — skipping", file=sys.stderr)
                continue
            send_telegram(text, token, chat_id)
            delivered.append("telegram")
        elif ch == "email":
            if not os.environ.get("SMTP_USER") or not os.environ.get("SMTP_PASS"):
                print("  ! email: SMTP_USER / SMTP_PASS not set — skipping", file=sys.stderr)
                continue
            plain = text.replace("*", "").replace("_", "")
            send_email(f"{weekday.title()} — {theme}", build_html(theme, weekday, videos), plain)
            delivered.append("email")
        else:
            print(f"  ! unknown delivery channel '{ch}'", file=sys.stderr)
    return delivered


# ── entry point ──────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Send today's themed YouTube picks.")
    ap.add_argument("--day", help="weekday name, e.g. monday (default: today)")
    ap.add_argument("--dry-run", action="store_true", help="print today's picks, send nothing")
    ap.add_argument("--preview", action="store_true", help="print the whole week's picks, send nothing")
    ap.add_argument("--force", action="store_true", help="send even if already sent today")
    args = ap.parse_args()

    cfg = load_config()
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        sys.exit("YOUTUBE_API_KEY is not set")
    sent = load_sent()

    if args.preview:
        for weekday, day_cfg in cfg["days"].items():
            print(f"\n{'='*60}\n{weekday.title()}: {day_cfg['theme']}")
            videos = pick_videos(day_cfg, cfg, api_key, sent)
            print(build_message(day_cfg["theme"], weekday, videos).replace("*", "").replace("_", ""))
        return

    today = datetime.now().strftime("%Y-%m-%d")
    if not args.dry_run and not args.force and LAST_PATH.exists() and LAST_PATH.read_text().strip() == today:
        print(f"Already sent today ({today}); skipping. Use --force to resend.")
        return

    weekday = (args.day or datetime.now().strftime("%A")).lower()
    day_cfg = cfg["days"].get(weekday)
    if not day_cfg:
        sys.exit(f"No entry for '{weekday}' in config.yaml")

    print(f"{weekday.title()}: {day_cfg['theme']}")
    videos = pick_videos(day_cfg, cfg, api_key, sent)

    if args.dry_run:
        print("\n" + build_message(day_cfg["theme"], weekday, videos))
        return

    delivered = deliver(cfg, day_cfg["theme"], weekday, videos)
    if not delivered:
        sys.exit("Nothing was delivered — check your secrets / `deliver:` setting.")
    sent.update(v["id"] for v in videos)
    save_sent(sent)
    LAST_PATH.write_text(today + "\n")
    print(f"Sent {len(videos)} video(s) via {', '.join(delivered)}.")


if __name__ == "__main__":
    main()
