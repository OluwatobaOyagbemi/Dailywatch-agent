# Daily Watch Agent

[![Tests](https://github.com/OluwatobaOyagbemi/Dailywatch-agent/actions/workflows/tests.yml/badge.svg)](https://github.com/OluwatobaOyagbemi/Dailywatch-agent/actions/workflows/tests.yml)
[![Daily Watch](https://github.com/OluwatobaOyagbemi/Dailywatch-agent/actions/workflows/daily.yml/badge.svg)](https://github.com/OluwatobaOyagbemi/Dailywatch-agent/actions/workflows/daily.yml)

**A tiny automation that sends you a few YouTube videos every day, matched to a weekly growth theme you choose — so you watch on purpose instead of scrolling.** No app, no dashboard, no cost. One Telegram message or email at the hour you pick, from creators you chose, inside a time budget you set, never a repeat.

Built by [Oluwatoba Oyagbemi](https://www.linkedin.com/in/oluwatoba-oyagbemi-24ba52323/) · Python · YouTube Data API · GitHub Actions · Telegram / Email

---

## Why

I had a weekly plan — speaking on Mondays, leadership on Tuesdays, finance on Thursdays — and I never followed it, because following it meant opening YouTube and getting pulled somewhere else.

My first instinct was to build an app with calendar sync and push notifications. I scoped that down to the smallest thing that would actually change my behaviour: a config file, one script, and a scheduled job that puts the right videos in front of me before I've opened anything else.

## What you get

```
🎯 Monday — Public Speaking and Communication
3 videos · 38 min total

1. How to speak so that people want to listen · 10 min
   TED
2. The 3-step storytelling framework · 14 min
   Philipp Humm
3. ...

💭 Before you close this: write down one idea you'll use this week.
```

## Built to stop the scroll

- **Distraction-free links** (`distraction_free: true`) open YouTube's bare player: no recommendations sidebar, no comments, no Shorts feed, no autoplay into the next thing.
- **A daily time budget** (`daily_minutes_budget: 45`) caps the total. Every video shows its length and the header shows the total, so you know the commitment before you tap.
- **A finish line.** The list ends, followed by one reflection prompt. Set a global one or one per day.
- **Only your creators.** No algorithm picks for you. General search is off unless you turn it on.
- **Rest days.** Leave a weekday out of `config.yaml` and nothing is sent that day.

<!-- Replace this comment with a screenshot of the Telegram message:
![Telegram message](docs/screenshot.png) -->

## How it works

1. `config.yaml` holds your weekly plan: one theme per weekday and the creators you follow.
2. A GitHub Actions job checks every hour. The first run at or after your send hour runs `watch_agent.py`.
3. The script searches each creator's channel for the day's keywords, drops Shorts, skips anything already sent, and fits the picks into your time budget.
4. It sends them to Telegram and/or email and records the video IDs in `sent.json` so you never get repeats.

Everything runs on free tiers. The script uses a few hundred YouTube API units a day against a 10,000 limit.

## Set it up for yourself (~15 min)

1. **Fork** this repo (keep it public so Actions stays free).
2. **Start fresh.** Copy `config.example.yaml` over `config.yaml`, then clear the previous owner's history:
   ```bash
   cp config.example.yaml config.yaml && echo "[]" > sent.json && rm -f last_sent.txt
   ```
   Edit `config.yaml`: your themes, your creators. A creator handle is the part after `@` in the channel URL (`youtube.com/@TED` → `TED`).
3. **YouTube API key:** [console.cloud.google.com](https://console.cloud.google.com) → create or pick a project → *APIs & Services → Library* → enable **YouTube Data API v3** → *Credentials → Create credentials → API key*. No billing needed.
4. **Pick how you want it delivered** (one or both), and set `deliver:` in `config.yaml`:

   **Telegram** — message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token. Open your new bot, press **Start**, send it "hi". Then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy the number after `"chat":{"id":`.
   Secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`

   **Email** — works with Gmail, Outlook or any SMTP host. For Gmail: turn on 2-Step Verification, then create an [App Password](https://myaccount.google.com/apppasswords) (not your normal password).
   Secrets: `SMTP_USER` (your address), `SMTP_PASS` (the app password), optionally `EMAIL_TO` (defaults to yourself), `SMTP_HOST` / `SMTP_PORT` (defaults: `smtp.gmail.com` / `587`; Outlook is `smtp.office365.com` / `587`)

5. In your fork: *Settings → Secrets and variables → Actions*:
   - **Secrets** tab: add `YOUTUBE_API_KEY` plus the secrets for your delivery choice.
   - **Variables** tab: add `TIMEZONE` (e.g. `Europe/London`, `Africa/Lagos`, `America/Los_Angeles`; [list](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones)) and `SEND_HOUR` (`0`–`23`, local). Defaults: `America/New_York`, `12`.
6. *Actions* tab → enable workflows on your fork → *Daily Watch → Run workflow.* Check your phone or inbox.

**About timing:** GitHub often starts scheduled jobs late (sometimes hours late), so the job checks every hour and sends on the first run at or after `SEND_HOUR`. If you want it on the minute, point a free scheduler such as [cron-job.org](https://cron-job.org) at the [workflow dispatch API](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event). Duplicate triggers are safe because a day is never sent twice.

## Run it locally

```bash
pip install -r requirements.txt
export YOUTUBE_API_KEY=... TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...
python watch_agent.py --check                  # validate config, creator handles and secrets
python watch_agent.py --preview                # see the whole week's picks, send nothing
python watch_agent.py --dry-run --day monday   # one day, send nothing
python watch_agent.py                          # send for real
python watch_agent.py --force                  # send again even if today already went out
```

Something not arriving? Run `--check` first. It lists every handle it can't find and every missing secret.

## Tests

```bash
pip install pytest
pytest -q
```

Offline tests use a fake YouTube API, so no key or network is needed. They cover the picking logic (round-robin, no repeats, Shorts filter, off-theme skipping, missing channels), the time budget, distraction-free links, message escaping for Telegram and email, delivery fallbacks and `--check`. They run on every push.

## Settings (`config.yaml`)

| Key | What it does |
|---|---|
| `videos_per_day` | how many links per message |
| `max_video_age_days` | ignore uploads older than this |
| `min_duration_minutes` | `0` allows Shorts |
| `creators_only` | `true` = only your listed creators; `false` = fill gaps with search |
| `match_theme` | `true` = search each creator's channel for the day's `keywords`; `false` = just their latest uploads |
| `fallback_to_latest` | `false` = skip a creator with nothing on-theme today; `true` = send their latest upload anyway |
| `fill_empty_day` | `true` = if the whole day comes up empty, send each creator's latest uploads instead (any age, never repeats) |
| `distraction_free` | `true` = links open the bare player with no feed, comments or autoplay |
| `daily_minutes_budget` | cap on the day's total watch time in minutes (`0` = no cap) |
| `prompt` | reflection line at the end of each message (a day's `prompt:` overrides it) |
| `deliver` | `[telegram]`, `[email]`, or `[telegram, email]`. If one channel fails, the others still send |

## Design notes

- **Theme-matched, creator-scoped.** Each day searches *inside* your chosen creators' channels for that day's keywords, then round-robins across creators so one prolific channel can't crowd out the rest. A creator with nothing on-theme is left out that day (or, with `fallback_to_latest: true`, their latest upload fills in).
- **No strangers.** General search is off by default; the message only ever contains creators you listed.
- **A bad channel never kills the run.** Unknown or unreadable handles are logged and skipped.
- **State lives in the repo.** `sent.json` is committed back by the workflow, so there's no database and nothing to host.

## Ideas for later

- "✅ Watched" buttons on the Telegram message, with a weekly streak in Sunday's message
- One-line "why this video" note via a free LLM tier
- WhatsApp or SMS delivery
- A hosted version where you just text the bot your weekly plan

## License

MIT
