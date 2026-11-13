"""
Guardrails for Daily Watch Agent.

INPUT   validate_config()       config.yaml types, ranges, handles, typos
        validate_env()          secrets / SMTP settings look sane before we use them
        safe_video_id()         API data can only produce youtube.com links
        clean_text()            strip control / zero-width / bidi-override chars, cap length
OUTPUT  is_blocked()            drop titles with words you never want (#shorts, prank, …)
        header_safe()           no CR/LF in email headers (header injection)
        fit_telegram()          stay under Telegram's 4096-char message limit
CODE    redact()                never print API keys, bot tokens or passwords
        Quota                   hard cap on YouTube API units per run
        atomic_write()          state files are never left half-written
"""

import os
import re
import tempfile
import unicodedata
from pathlib import Path

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
CHANNELS = {"telegram", "email"}

HANDLE_RE = re.compile(r"^@?[A-Za-z0-9._-]{3,30}$")
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")   # real IDs are 11 chars of this set
EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
CHAT_ID_RE = re.compile(r"^(-?\d{1,20}|@[A-Za-z0-9_]{5,32})$")

TITLE_MAX = 150
CHANNEL_MAX = 80
TELEGRAM_LIMIT = 4096
ALWAYS_BLOCKED = ["#shorts"]

MAX_CREATORS_PER_DAY = 10
MAX_SEARCH_PER_DAY = 5
DEFAULT_MAX_API_UNITS = 5000   # half the free daily 10,000, leaves room for a manual rerun


class ConfigError(Exception):
    def __init__(self, problems, warnings=()):
        self.problems, self.warnings = list(problems), list(warnings)
        text = "config.yaml has problems:\n" + "\n".join(f"  ✗ {p}" for p in self.problems)
        if self.warnings:
            text += "\nAlso:\n" + "\n".join(f"  · {w}" for w in self.warnings)
        super().__init__(text)


# ── input: config ────────────────────────────────────────────────────────────

def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v):
    return (isinstance(v, (int, float))) and not isinstance(v, bool)


# key: (check, description)
TOP_LEVEL = {
    "videos_per_day":       (lambda v: _is_int(v) and 1 <= v <= 10, "a whole number from 1 to 10"),
    "max_video_age_days":   (lambda v: _is_int(v) and 1 <= v <= 3650, "a whole number from 1 to 3650"),
    "min_duration_minutes": (lambda v: _is_num(v) and 0 <= v <= 600, "a number from 0 to 600"),
    "daily_minutes_budget": (lambda v: _is_num(v) and 0 <= v <= 1440, "a number from 0 to 1440 (0 = no cap)"),
    "max_api_units":        (lambda v: _is_int(v) and 100 <= v <= 10000, "a whole number from 100 to 10000"),
    "creators_only":        (lambda v: isinstance(v, bool), "true or false"),
    "match_theme":          (lambda v: isinstance(v, bool), "true or false"),
    "fallback_to_latest":   (lambda v: isinstance(v, bool), "true or false"),
    "older_on_theme":       (lambda v: isinstance(v, bool), "true or false"),
    "distraction_free":     (lambda v: isinstance(v, bool), "true or false"),
    "prompt":               (lambda v: isinstance(v, str) and len(v) <= 300, "text up to 300 characters"),
    "blocked_words":        (lambda v: isinstance(v, list) and all(isinstance(w, str) and 0 < len(w) <= 50 for w in v),
                             "a list of words, e.g. [prank, reaction]"),
    "deliver":              (lambda v: (isinstance(v, str) and v in CHANNELS) or
                             (isinstance(v, list) and bool(v) and all(isinstance(c, str) for c in v)
                              and set(v) <= CHANNELS),
                             "telegram, email, or [telegram, email]"),
    "watch_page":           (lambda v: isinstance(v, bool), "true or false"),
    "watch_page_url":       (lambda v: isinstance(v, str) and safe_page_url(v if v.endswith("/") else v + "/"),
                             "an https:// address, e.g. https://you.github.io/Dailywatch-agent/"),
    "days":                 (lambda v: isinstance(v, dict) and v, "a map of weekdays (monday: …)"),
}
DAY_KEYS = {"theme", "keywords", "creators", "search", "prompt"}


def _close_match(word, options):
    import difflib
    m = difflib.get_close_matches(word, list(options), n=2, cutoff=0.75)
    return (" — did you mean " + " or ".join(f"`{x}`" for x in m) + "?") if m else ""


def validate_config(cfg):
    """Raise ConfigError listing every problem; return a list of warnings."""
    if not isinstance(cfg, dict):
        raise ConfigError(["the file is empty or isn't a set of `key: value` lines"])
    problems, warnings = [], []

    for key, value in cfg.items():
        if key not in TOP_LEVEL:
            warnings.append(f"unknown setting `{key}` is ignored{_close_match(key, TOP_LEVEL)}")
            continue
        check, desc = TOP_LEVEL[key]
        if not check(value):
            problems.append(f"`{key}` must be {desc} (got {value!r})")

    days = cfg.get("days")
    if "days" not in cfg:
        problems.append("`days:` is missing — add at least one weekday with a theme")
    if isinstance(days, dict):
        for day, d in days.items():
            where = f"days.{day}"
            if str(day).lower() not in WEEKDAYS:
                problems.append(f"`{day}` is not a weekday name{_close_match(str(day).lower(), WEEKDAYS)}")
                continue
            if not isinstance(d, dict):
                problems.append(f"`{where}` needs `theme:` and `creators:` under it")
                continue
            for k in d:
                if k not in DAY_KEYS:
                    warnings.append(f"`{where}.{k}` is ignored{_close_match(k, DAY_KEYS)}")
            theme = d.get("theme")
            if not isinstance(theme, str) or not theme.strip() or len(theme) > 100:
                problems.append(f"`{where}.theme` must be text, 1–100 characters")
            kw = d.get("keywords")
            if kw is not None and (not isinstance(kw, str) or len(kw) > 200):
                problems.append(f"`{where}.keywords` must be text up to 200 characters")
            creators = d.get("creators") or []
            if not isinstance(creators, list):
                problems.append(f"`{where}.creators` must be a list, e.g. [TED, marktilbury]")
                creators = []
            if len(creators) > MAX_CREATORS_PER_DAY:
                problems.append(f"`{where}.creators` has {len(creators)}; max {MAX_CREATORS_PER_DAY} "
                                f"(each costs ~100 YouTube units)")
            for h in creators:
                if not isinstance(h, str) or not HANDLE_RE.match(h):
                    problems.append(f"`{where}.creators`: {h!r} isn't a valid handle — use the part after "
                                    f"@ in youtube.com/@handle")
            search = d.get("search") or []
            if not isinstance(search, list) or len(search) > MAX_SEARCH_PER_DAY or \
                    not all(isinstance(q, str) and 0 < len(q) <= 100 for q in search):
                problems.append(f"`{where}.search` must be a list of up to {MAX_SEARCH_PER_DAY} phrases")
            p = d.get("prompt")
            if p is not None and (not isinstance(p, str) or len(p) > 300):
                problems.append(f"`{where}.prompt` must be text up to 300 characters")
            if not creators and (cfg.get("creators_only", False) or not search):
                warnings.append(f"`{where}` has no creators, so it will always be empty")

    if problems:
        raise ConfigError(problems, warnings)
    return warnings


# ── input: environment ───────────────────────────────────────────────────────

def validate_env(channels):
    """Return problems with secrets for the given delivery channels (format only, not validity)."""
    problems = []
    env = os.environ
    if "telegram" in channels:
        tok = env.get("TELEGRAM_BOT_TOKEN", "")
        if tok and not re.match(r"^\d{5,15}:[A-Za-z0-9_-]{30,}$", tok):
            problems.append("TELEGRAM_BOT_TOKEN doesn't look like a bot token (123456:ABC…)")
        cid = env.get("TELEGRAM_CHAT_ID", "")
        if cid and not CHAT_ID_RE.match(cid.strip()):
            problems.append("TELEGRAM_CHAT_ID should be a number like 123456789 (or @channelname)")
    if "email" in channels:
        for k in ("SMTP_USER", "EMAIL_TO"):
            v = env.get(k, "")
            if v and not EMAIL_RE.match(v.strip()):
                problems.append(f"{k} should be a single email address")
        port = env.get("SMTP_PORT", "")
        if port and not (port.isdigit() and 1 <= int(port) <= 65535):
            problems.append("SMTP_PORT must be a port number such as 587")
        host = env.get("SMTP_HOST", "")
        if host and not re.match(r"^[A-Za-z0-9.-]{1,253}$", host):
            problems.append("SMTP_HOST should be a hostname such as smtp.gmail.com")
    return problems


# ── input: API data ──────────────────────────────────────────────────────────

def safe_video_id(vid):
    """Only IDs made of YouTube's URL-safe alphabet get through, so links can't be steered elsewhere."""
    return isinstance(vid, str) and bool(VIDEO_ID_RE.match(vid))


PAGE_URL_RE = re.compile(r"^https://[A-Za-z0-9.-]{1,253}(:\d{2,5})?(/[A-Za-z0-9._~%-]*)*/$")


def safe_page_url(url):
    """The watch page link must be a plain https URL (no query, fragment, quotes or spaces)."""
    return isinstance(url, str) and len(url) <= 300 and bool(PAGE_URL_RE.match(url))


def clean_text(s, limit):
    """Remove control, zero-width and bidi-override characters; collapse whitespace; cap length."""
    s = "".join(ch if unicodedata.category(ch)[0] != "C" else " " for ch in str(s or ""))
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > limit:
        s = s[: limit - 1].rstrip() + "…"
    return s


# ── output ───────────────────────────────────────────────────────────────────

def is_blocked(title, blocked_words):
    t = (title or "").casefold()
    return any(w.casefold() in t for w in list(ALWAYS_BLOCKED) + list(blocked_words or []))


def header_safe(s, limit=200):
    """Email headers: no CR/LF (header injection) and a sane length."""
    return clean_text(s, limit)


def fit_telegram(build, videos):
    """Call build(videos) dropping videos from the end until it fits Telegram's limit."""
    vids = list(videos)
    text = build(vids)
    while len(text) > TELEGRAM_LIMIT and vids:
        vids.pop()
        text = build(vids)
    return text[:TELEGRAM_LIMIT], vids


# ── code / runtime ───────────────────────────────────────────────────────────

SECRET_ENV = ("YOUTUBE_API_KEY", "TELEGRAM_BOT_TOKEN", "SMTP_PASS")
_PATTERNS = [
    (re.compile(r"(key=)[^&\s'\"]+"), r"\1[REDACTED]"),
    (re.compile(r"(bot)\d{5,15}:[A-Za-z0-9_-]{20,}"), r"\1[REDACTED]"),
]


def redact(text):
    """Scrub secrets from anything we're about to print (error messages often embed URLs)."""
    text = str(text)
    for k in SECRET_ENV:
        v = os.environ.get(k)
        if v and len(v) >= 6:
            text = text.replace(v, "[REDACTED]")
    for pat, repl in _PATTERNS:
        text = pat.sub(repl, text)
    return text


class QuotaExceeded(Exception):
    pass


class Quota:
    """Hard cap on YouTube API units spent in one run."""
    COST = {"search": 100}

    def __init__(self, limit=DEFAULT_MAX_API_UNITS):
        self.limit, self.used, self.exhausted = limit, 0, False

    def spend(self, endpoint):
        if self.exhausted:
            raise QuotaExceeded("YouTube's daily quota is used up — try again tomorrow")
        cost = self.COST.get(endpoint, 1)
        if self.used + cost > self.limit:
            raise QuotaExceeded(f"run stopped at its {self.limit}-unit safety cap (max_api_units)")
        self.used += cost


def atomic_write(path, text):
    """Write to a temp file then rename, so a crash never leaves a half-written state file."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
