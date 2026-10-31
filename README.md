# Daily Watch Agent

**A tiny automation that sends you a few YouTube videos every morning, matched to a weekly growth theme you choose.** No app, no dashboard, no cost — one Telegram message at noon from creators you picked, never a repeat.

Built by [Oluwatoba Oyagbemi](https://www.linkedin.com/in/oluwatoba-oyagbemi-24ba52323/) · Python · YouTube Data API · GitHub Actions · Telegram Bot API

---

## Why

I had a weekly plan — communication on Mondays, leadership on Tuesdays, finance on Wednesdays — and I never followed it, because following it meant opening YouTube and getting pulled somewhere else.

My first instinct was to build an app with calendar sync and push notifications. I scoped that down to the smallest thing that would actually change my behaviour: a config file, one script, and a scheduled job that puts the right videos in front of me before I've opened anything else.

## What you get

```
🎯 Thursday — Relationship and Marriage

1. How to choose well before you commit
   Pastor Dolapo Lawal
2. The conversation every couple avoids
   Ezinne Zara
3. ...
```

<!-- Replace this comment with a screenshot of the Telegram message:
![Telegram message](docs/screenshot.png) -->

## How it works

1. `config.yaml` holds your weekly plan: one theme per weekday and the creators you follow.
2. Every morning a GitHub Actions job runs `watch_agent.py`.
3. The script pulls recent uploads from your creators, drops Shorts, skips anything already sent, and picks the newest few.
4. It sends them to Telegram and records the video IDs in `sent.json` so you never get repeats.

Everything runs on free tiers. The script uses a few hundred YouTube API units a day against a 10,000 limit.

## Set it up for yourself (~15 min)

1. **Fork** this repo (keep it public so Actions stays free).
2. Edit `config.yaml` — your themes and your creators. A creator handle is the part after `@` in the channel URL (`youtube.com/@MaxwellLeadershipOfficial` → `MaxwellLeadershipOfficial`).
3. **YouTube API key:** [console.cloud.google.com](https://console.cloud.google.com) → create or pick a project → *APIs & Services → Library* → enable **YouTube Data API v3** → *Credentials → Create credentials → API key*. No billing needed.
4. **Telegram bot:** message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token. Open your new bot, press **Start**, send it "hi". Then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy the number after `"chat":{"id":`.
5. In your fork: *Settings → Secrets and variables → Actions* → add `YOUTUBE_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
6. *Actions → Daily Watch → Run workflow.* Check your phone.

To change the send time, edit the two `cron` lines and the `"12"` check in `.github/workflows/daily.yml` (GitHub cron is UTC).

## Run it locally

```bash
pip install -r requirements.txt
export YOUTUBE_API_KEY=... TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...
python watch_agent.py --dry-run --day monday   # preview, send nothing
python watch_agent.py                          # send for real
```

## Settings (`config.yaml`)

| Key | What it does |
|---|---|
| `videos_per_day` | how many links per message |
| `max_video_age_days` | ignore uploads older than this |
| `min_duration_minutes` | `0` allows Shorts |
| `creators_only` | `true` = only your listed creators; `false` = fill gaps with search |
| `match_theme` | `true` = search each creator's channel for the day's `keywords`; `false` = just their latest uploads |

## Design notes

- **Theme-matched, creator-scoped.** Each day searches *inside* your chosen creators' channels for that day's keywords, then round-robins across creators so one prolific channel can't crowd out the rest. If a creator has nothing on-theme, their latest upload fills in.
- **No strangers.** General search is off by default; the message only ever contains creators you listed.
- **A bad channel never kills the run.** Unknown or unreadable handles are logged and skipped.
- **State lives in the repo.** `sent.json` is committed back by the workflow, so there's no database and nothing to host.

## Ideas for later

- One-line "why this video" note via a free LLM tier
- WhatsApp, SMS or email delivery
- A hosted version where you just text the bot your weekly plan

## License

MIT
