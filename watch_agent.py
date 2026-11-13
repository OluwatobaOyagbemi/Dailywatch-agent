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
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml

import guardrails as g

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.yaml"
SENT_PATH = ROOT / "sent.json"
LAST_PATH = ROOT / "last_sent.txt"
PAGE_DATA_PATH = ROOT / "docs" / "today.json"
YT = "https://www.googleapis.com/youtube/v3"
WEEKDAYS = g.WEEKDAYS
QUOTA = g.Quota()
RETRY_STATUS = {429, 500, 502, 503, 504}


class YouTubeError(Exception):
    pass


def warn(msg):
    """All warnings go through here so secrets are always redacted."""
    print(g.redact(f"  ! {msg}"), file=sys.stderr)


# ── helpers ──────────────────────────────────────────────────────────────────

def load_config(validate=True):
    """Load config.yaml. Raises ConfigError with every problem listed, in plain words."""
    try:
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f)
    except FileNotFoundError:
        raise g.ConfigError([f"{CONFIG_PATH.name} not found — copy config.example.yaml to config.yaml"])
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
        raise g.ConfigError([f"config.yaml isn't valid YAML{where}: check indentation and quotes"])
    if validate:
        for w in g.validate_config(cfg):
            warn(w)
    if isinstance(cfg, dict) and isinstance(cfg.get("days"), dict):
        cfg["days"] = {str(k).lower(): v for k, v in cfg["days"].items()}
    return cfg


def load_sent():
    """Sent history. A corrupt file is set aside (never silently overwritten) and the run continues."""
    if not SENT_PATH.exists():
        return set()
    try:
        data = json.loads(SENT_PATH.read_text() or "[]")
        if not isinstance(data, list):
            raise ValueError("not a list")
        return {x for x in data if isinstance(x, str)}
    except ValueError as e:
        backup = SENT_PATH.with_name(SENT_PATH.name + ".corrupt")
        SENT_PATH.replace(backup)
        warn(f"{SENT_PATH.name} was unreadable ({e}); moved it to {backup.name} and started fresh")
        return set()


def save_sent(sent):
    g.atomic_write(SENT_PATH, json.dumps(sorted(sent), indent=2) + "\n")


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


def _reason(r):
    try:
        return r.json()["error"]["errors"][0]["reason"]
    except (ValueError, KeyError, IndexError, TypeError):
        return ""


def yt_get(endpoint, api_key, **params):
    """One YouTube API call with a unit cap, retries on transient errors, and no secrets in errors."""
    try:
        QUOTA.spend(endpoint)
    except g.QuotaExceeded as e:
        raise YouTubeError(str(e))
    params["key"] = api_key
    for attempt in range(3):
        try:
            r = requests.get(f"{YT}/{endpoint}", params=params, timeout=20)
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt == 2:
                raise YouTubeError(g.redact(f"network error: {e}"))
            time.sleep(2 ** attempt)
            continue
        if r.status_code in RETRY_STATUS and attempt < 2:
            time.sleep(2 ** attempt)
            continue
        reason = _reason(r)
        if r.status_code == 403 and reason in ("quotaExceeded", "dailyLimitExceeded"):
            QUOTA.exhausted = True
            raise YouTubeError("YouTube's daily quota is used up — try again tomorrow")
        if not r.ok:
            raise YouTubeError(f"YouTube API error {r.status_code} {reason}".strip())
        try:
            data = r.json()
        except ValueError:
            raise YouTubeError("YouTube returned a response that isn't JSON")
        if not isinstance(data, dict):
            raise YouTubeError("YouTube returned an unexpected response")
        return data


def _video(vid, sn):
    """Build a video dict from API data, or None if anything about it looks wrong."""
    try:
        if not g.safe_video_id(vid):
            return None
        return {
            "id": vid,
            "title": g.clean_text(html.unescape(sn["title"]), g.TITLE_MAX) or "(untitled)",
            "channel": g.clean_text(html.unescape(sn["channelTitle"]), g.CHANNEL_MAX),
            "published": datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00")),
        }
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def _items(data):
    items = data.get("items") or []
    return [it for it in items if isinstance(it, dict)]


# ── YouTube lookups ──────────────────────────────────────────────────────────

_channel_cache = {}


def resolve_channel(handle, api_key):
    """Resolve @handle -> (channel_id, uploads_playlist_id). Cost: 1 unit, cached per run."""
    handle = handle.lstrip("@")
    if handle in _channel_cache:
        return _channel_cache[handle]
    data = yt_get("channels", api_key, part="contentDetails", forHandle=handle)
    try:
        it = _items(data)[0]
        result = (it["id"], it["contentDetails"]["relatedPlaylists"]["uploads"])
    except (IndexError, KeyError, TypeError):
        warn(f"channel @{handle} not found")
        result = (None, None)
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
    except YouTubeError as e:
        warn(f"could not read @{handle}: {e} — skipping")
        return []
    out = []
    for it in _items(data):
        sn = it.get("snippet") or {}
        v = _video((sn.get("resourceId") or {}).get("videoId"), sn)
        if v and v["published"] >= cutoff:
            out.append(v)
    return out


def search_videos(query, api_key, cutoff, limit=5):
    """Keyword search across YouTube (cost: 100 units — used only as fallback)."""
    data = yt_get("search", api_key, part="snippet", q=query, type="video",
                  order="relevance", maxResults=limit,
                  publishedAfter=cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
                  videoDuration="medium")
    return _search_results(data)


def _search_results(data):
    out = []
    for it in _items(data):
        v = _video((it.get("id") or {}).get("videoId"), it.get("snippet") or {})
        if v:
            out.append(v)
    return out


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
    except YouTubeError as e:
        warn(f"theme search failed for @{handle}: {e} — skipping")
        return []
    return _search_results(data)


def add_details(videos, api_key, min_minutes):
    """Attach duration + embeddable flag, and drop Shorts / short clips (cost: 1 unit per 50)."""
    if not videos:
        return videos
    details = {}
    for i in range(0, len(videos), 50):
        ids = ",".join(v["id"] for v in videos[i:i + 50])
        try:
            data = yt_get("videos", api_key, part="contentDetails,status", id=ids)
        except YouTubeError as e:
            warn(f"could not read video details: {e}")
            continue
        for it in _items(data):
            if isinstance(it.get("id"), str):
                details[it["id"]] = it
    out = []
    for v in videos:
        d = details.get(v["id"])
        if d is None:
            if min_minutes > 0:
                continue  # unknown length — can't prove it isn't a Short
            out.append(v)
            continue
        v["minutes"] = iso_duration_to_minutes((d.get("contentDetails") or {}).get("duration"))
        v["embeddable"] = (d.get("status") or {}).get("embeddable", True) is not False
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

    blocked = cfg.get("blocked_words") or []

    def fresh(videos):
        out = []
        for v in videos:
            if v["id"] in sent or v["id"] in seen:
                continue
            if g.is_blocked(v["title"], blocked):
                print(f"  blocked: {v['title']}")
                seen.add(v["id"])
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
        except YouTubeError as e:
            warn(f"search failed: {e} — skipping")
            continue
        candidates.extend(add_details(fresh(extra), api_key, min_dur))
        pool = select_within_budget(candidates, n, budget)

    return pool


# ── messages ─────────────────────────────────────────────────────────────────

def video_url(v, cfg=None, index=None):
    """Watch page (when live) > bare player (distraction_free) > normal YouTube link."""
    page = (cfg or {}).get("_page")
    if page:
        return f"{page}#{index}" if index else page
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
    lines.append(summary_line(videos))
    if (cfg or {}).get("_page"):
        lines.append(f'▶️ <a href="{e(cfg["_page"])}"><b>Start today\'s session</b></a>')
    lines.append("")
    for i, v in enumerate(videos, 1):
        length = f" · {video_meta(v)}" if video_meta(v) else ""
        lines.append(f'{i}. <a href="{e(video_url(v, cfg, i))}">{e(v["title"])}</a>{e(length)}')
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
    lines.append(summary_line(videos))
    if (cfg or {}).get("_page"):
        lines.append(f"▶ Start today's session: {cfg['_page']}")
    lines.append("")
    for i, v in enumerate(videos, 1):
        length = f" ({video_meta(v)})" if video_meta(v) else ""
        lines.append(f"{i}. {v['title']}{length}")
        lines.append(f"   {v['channel']} — {video_url(v, cfg, i)}")
    prompt = day_prompt(day_cfg, cfg)
    if prompt:
        lines += ["", f"💭 {prompt}"]
    return "\n".join(lines)


def build_html(theme, weekday, videos, cfg=None, day_cfg=None):
    """Email (HTML) version."""
    e = html.escape
    rows = "".join(
        f'<li style="margin:0 0 14px"><a href="{e(video_url(v, cfg, i))}" '
        f'style="font-size:16px;color:#1a5fb4;text-decoration:none">{e(v["title"])}</a>'
        f'<br><span style="color:#666;font-size:13px">{e(v["channel"])}'
        f'{" · " + e(video_meta(v)) if video_meta(v) else ""}</span></li>'
        for i, v in enumerate(videos, 1)
    ) or f'<li>{EMPTY}</li>'
    sub = summary_line(videos) if videos else "Your picks for today."
    page = (cfg or {}).get("_page")
    cta = (f'<p style="margin:0 0 22px"><a href="{e(page)}" style="display:inline-block;background:#4f46e5;'
           f'color:#fff;text-decoration:none;font-weight:600;font-size:15px;padding:11px 20px;'
           f'border-radius:999px">▶ Start today\'s session</a></p>') if page and videos else ""
    prompt = day_prompt(day_cfg, cfg)
    prompt_html = (f'<p style="margin:20px 0 0;padding:12px 14px;background:#f4f6fa;border-radius:8px">'
                   f'💭 {e(prompt)}</p>') if prompt and videos else ""
    return (f'<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;max-width:560px;'
            f'margin:0 auto;padding:24px;color:#222">'
            f'<h2 style="margin:0 0 4px">🎯 {e(weekday.title())} — {e(theme)}</h2>'
            f'<p style="margin:0 0 20px;color:#666">{e(sub)}</p>{cta}'
            f'<ol style="padding-left:20px">{rows}</ol>{prompt_html}'
            f'<p style="margin-top:28px;font-size:12px;color:#999">Sent by Daily Watch Agent</p></div>')


# ── watch page (GitHub Pages) ────────────────────────────────────────────────

def page_base(cfg):
    """Where the watch page lives: WATCH_PAGE_URL / watch_page_url, else derived from the GitHub repo."""
    if not cfg.get("watch_page", True):
        return None
    url = os.environ.get("WATCH_PAGE_URL") or cfg.get("watch_page_url")
    if not url:
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        if repo.count("/") != 1:
            return None
        owner, name = repo.split("/")
        url = f"https://{owner.lower()}.github.io/{name}/"
    url = url.strip()
    if not url.endswith("/"):
        url += "/"
    return url if g.safe_page_url(url) else None


def page_is_live(url):
    """Only link to the page if it's actually published (Pages may not be switched on yet)."""
    try:
        r = requests.get(url, timeout=10)
        return r.ok and "Daily Watch" in r.text
    except requests.RequestException:
        return False


def session_link(base, day, videos):
    """The link carries the date + IDs so it works before the new today.json has deployed."""
    return f"{base}?d={day}&v={','.join(v['id'] for v in videos)}"


def page_data(day, weekday, day_cfg, cfg, videos):
    hour = os.environ.get("SEND_HOUR", "")
    return {
        "date": day,
        "weekday": weekday,
        "theme": day_cfg["theme"],
        "prompt": day_prompt(day_cfg, cfg),
        "send_time": f"{int(hour):02d}:00" if hour.isdigit() and int(hour) < 24 else "",
        "week": [{"day": d, "theme": ((cfg.get("days") or {}).get(d) or {}).get("theme", ""),
                  "rest": d not in (cfg.get("days") or {})} for d in WEEKDAYS],
        "videos": [{
            "id": v["id"],
            "title": v["title"],
            "channel": v["channel"],
            "minutes": round(v.get("minutes", 0), 2),
            "embeddable": v.get("embeddable", True),
            "older": bool(v.get("older")),
            "published": v["published"].date().isoformat() if v.get("published") else "",
        } for v in videos],
    }


def write_page_data(data):
    PAGE_DATA_PATH.parent.mkdir(exist_ok=True)
    g.atomic_write(PAGE_DATA_PATH, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


# ── delivery ─────────────────────────────────────────────────────────────────

def send_telegram(text, token, chat_id):
    """Send one message; retries once on rate limits / server errors / network blips."""
    payload = {"chat_id": chat_id.strip(), "text": text[:g.TELEGRAM_LIMIT], "parse_mode": "HTML",
               "disable_web_page_preview": True}
    for attempt in range(2):
        try:
            r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=20)
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt == 1:
                raise RuntimeError(g.redact(f"network error: {e}"))
            time.sleep(3)
            continue
        if r.ok:
            return
        if r.status_code in RETRY_STATUS and attempt == 0:
            try:
                wait = int(r.json().get("parameters", {}).get("retry_after", 3))
            except (ValueError, AttributeError):
                wait = 3
            time.sleep(min(wait, 30))
            continue
        raise RuntimeError(g.redact(f"Telegram said {r.status_code}: {r.text[:200]}"))


def send_email(subject, html_body, plain_body):
    """SMTP email. Works with Gmail (app password), Outlook, or any SMTP host."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    host = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
    port = os.environ.get("SMTP_PORT") or "587"
    if not port.isdigit():
        raise RuntimeError("SMTP_PORT must be a number such as 587")
    port = int(port)
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASS"]
    to = (os.environ.get("EMAIL_TO") or user).strip()
    user = user.strip()
    for addr in (user, to):
        if not g.EMAIL_RE.match(addr):
            raise RuntimeError("SMTP_USER / EMAIL_TO must each be a single email address")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = g.header_safe(subject)
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
            warn(f"unknown delivery channel '{ch}' (use telegram or email)")
            continue
        if missing:
            warn(f"{ch}: {', '.join(missing)} not set — skipping")
            continue
        try:
            if ch == "telegram":
                text, kept = g.fit_telegram(lambda vs: build_message(theme, weekday, vs, cfg, day_cfg), videos)
                if len(kept) < len(videos):
                    warn(f"telegram: message too long, sent {len(kept)} of {len(videos)} videos")
                send_telegram(text, os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"])
            else:
                subject = f"{weekday.title()} — {theme}"
                if videos:
                    subject += f" ({summary_line(videos)})"
                send_email(subject, build_html(theme, weekday, videos, cfg, day_cfg),
                           build_plain(theme, weekday, videos, cfg, day_cfg))
            delivered.append(ch)
        except Exception as ex:  # keep going: email can still land if Telegram is down
            warn(f"{ch} failed: {ex}")
    return delivered


# ── setup check ──────────────────────────────────────────────────────────────

def check(cfg, api_key):
    """Validate config, creator handles and secrets. Returns a process exit code."""
    problems, notes = [], []
    try:
        for w in g.validate_config(cfg):
            notes.append(f"warning: {w}")
        print("✓ config.yaml is valid")
    except g.ConfigError as e:
        problems += e.problems
        notes += [f"warning: {w}" for w in e.warnings]
        cfg = cfg if isinstance(cfg, dict) else {}
    days = cfg.get("days") if isinstance(cfg.get("days"), dict) else {}
    days = {str(k).lower(): v for k, v in days.items()}
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
    problems += g.validate_env(delivery_channels(cfg))

    handles = sorted({h.lstrip("@") for d in days.values() if isinstance(d, dict)
                      for h in (d.get("creators") or []) if isinstance(h, str) and g.HANDLE_RE.match(h)})
    if not api_key:
        problems.append("YOUTUBE_API_KEY is not set (can't check creator handles)")
    else:
        for h in handles:
            try:
                cid, _ = resolve_channel(h, api_key)
            except YouTubeError as e:
                problems.append(f"{e} — is the key valid and YouTube Data API v3 enabled?")
                break
            if cid:
                print(f"✓ @{h}")
            else:
                problems.append(f"@{h} not found — copy the part after @ from youtube.com/@handle")

    per_creator = 102 if cfg.get("match_theme", True) else 3
    busiest = max((len(d.get("creators") or []) for d in days.values() if isinstance(d, dict)), default=0)
    notes.append(f"busiest day uses ~{busiest * per_creator} of 10,000 free YouTube units")

    for n in notes:
        print(g.redact(f"· {n}"))
    if problems:
        print("\nFix these:")
        for p in problems:
            print(g.redact(f"  ✗ {p}"))
        return 1
    print("\nAll good. Try: python watch_agent.py --dry-run")
    return 0


# ── entry point ──────────────────────────────────────────────────────────────

def main():
    """Entry point: turns every failure into a short, secret-free message and a non-zero exit."""
    try:
        run()
    except g.ConfigError as e:
        sys.exit(f"{e}\n\nRun `python watch_agent.py --check` for a full setup check.")
    except KeyboardInterrupt:
        sys.exit("Stopped.")
    except Exception as e:  # last line of defence: never dump secrets in a traceback message
        if os.environ.get("DEBUG"):
            raise
        sys.exit(g.redact(f"Unexpected error ({type(e).__name__}): {e}\nSet DEBUG=1 for the full traceback."))
    finally:
        if QUOTA.used:
            print(f"YouTube API units used this run: {QUOTA.used}")


def run():
    ap = argparse.ArgumentParser(description="Send today's themed YouTube picks.")
    ap.add_argument("--day", help="weekday name, e.g. monday (default: today)")
    ap.add_argument("--dry-run", action="store_true", help="print today's picks, send nothing")
    ap.add_argument("--preview", action="store_true", help="print the whole week's picks, send nothing")
    ap.add_argument("--check", action="store_true", help="validate config, creator handles and secrets")
    ap.add_argument("--force", action="store_true", help="send even if already sent today")
    args = ap.parse_args()

    api_key = (os.environ.get("YOUTUBE_API_KEY") or "").strip() or None

    if args.check:
        sys.exit(check(load_config(validate=False), api_key))
    cfg = load_config()
    QUOTA.limit = cfg.get("max_api_units", g.DEFAULT_MAX_API_UNITS)
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

    base = page_base(cfg) if videos else None
    if base and page_is_live(base):
        cfg["_page"] = session_link(base, today, videos)
    elif base:
        print(f"  watch page not live at {base} — linking straight to YouTube (see README: Watch page)")

    delivered = deliver(cfg, day_cfg, weekday, videos)
    if not delivered:
        sys.exit("Nothing was delivered — run `python watch_agent.py --check` to see what's missing.")
    if base:
        write_page_data(page_data(today, weekday, day_cfg, cfg, videos))
    sent.update(v["id"] for v in videos)
    save_sent(sent)
    g.atomic_write(LAST_PATH, today + "\n")
    print(f"Sent {len(videos)} video(s) via {', '.join(delivered)}.")


if __name__ == "__main__":
    main()
