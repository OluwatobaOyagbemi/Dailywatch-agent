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

def channel_uploads_playlist(handle, api_key):
    """Resolve @handle -> uploads playlist id (cost: 1 unit)."""
    data = yt_get("channels", api_key, part="contentDetails", forHandle=handle)
    items = data.get("items", [])
    if not items:
        print(f"  ! channel @{handle} not found", file=sys.stderr)
        return None
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


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

    pool = []
    seen = set()

    def add(videos):
        for v in videos:
            if v["id"] in sent or v["id"] in seen:
                continue
            seen.add(v["id"])
            pool.append(v)

    # 1) creators first — cheap and on-brand
    for handle in day_cfg.get("creators", []):
        print(f"  fetching @{handle}")
        add(recent_from_channel(handle, api_key, cutoff))

    pool = filter_by_duration(pool, api_key, min_dur)

    # 2) fallback searches only if allowed and we still need more
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
        extra = filter_by_duration([v for v in extra if v["id"] not in seen and v["id"] not in sent],
                                   api_key, min_dur)
        add(extra)

    # newest first, then cap
    pool.sort(key=lambda v: v["published"], reverse=True)
    return pool[:n]


def build_message(theme, weekday, videos):
    lines = [f"🎯 *{weekday.title()} — {theme}*", ""]
    if not videos:
        lines.append("No new videos today. Try adding more creators or search terms to config.yaml.")
        return "\n".join(lines)
    for i, v in enumerate(videos, 1):
        title = v["title"].replace("*", "").replace("_", "").replace("[", "(").replace("]", ")")
        lines.append(f"{i}. [{title}](https://youtu.be/{v['id']})")
        lines.append(f"   _{v['channel']}_")
    return "\n".join(lines)


def send_telegram(text, token, chat_id):
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat_id, "text": text,
                            "parse_mode": "Markdown", "disable_web_page_preview": False},
                      timeout=20)
    r.raise_for_status()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", help="weekday name, e.g. monday (default: today)")
    ap.add_argument("--dry-run", action="store_true", help="print, don't send or record")
    args = ap.parse_args()

    cfg = load_config()
    weekday = (args.day or datetime.now().strftime("%A")).lower()
    day_cfg = cfg["days"].get(weekday)
    if not day_cfg:
        sys.exit(f"No entry for '{weekday}' in config.yaml")

    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        sys.exit("YOUTUBE_API_KEY is not set")

    sent = load_sent()
    print(f"{weekday.title()}: {day_cfg['theme']}")
    videos = pick_videos(day_cfg, cfg, api_key, sent)
    msg = build_message(day_cfg["theme"], weekday, videos)

    if args.dry_run:
        print("\n" + msg)
        return

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        sys.exit("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")

    send_telegram(msg, token, chat_id)
    sent.update(v["id"] for v in videos)
    save_sent(sent)
    print(f"Sent {len(videos)} video(s).")


if __name__ == "__main__":
    main()
