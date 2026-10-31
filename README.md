# Daily Watch Agent

**A tiny automation that sends you a few YouTube videos every morning, matched to a weekly growth theme you choose.** No app, no dashboard, no cost — one Telegram message or email at noon from creators you picked, never a repeat.

Built by [Oluwatoba Oyagbemi](https://www.linkedin.com/in/oluwatoba-oyagbemi-24ba52323/) · Python · YouTube Data API · GitHub Actions · Telegram / Email

---

## Why

I had a weekly plan — speaking on Mondays, leadership on Tuesdays, finance on Thursdays — and I never followed it, because following it meant opening YouTube and getting pulled somewhere else.

My first instinct was to build an app with calendar sync and push notifications. I scoped that down to the smallest thing that would actually change my behaviour: a config file, one script, and a scheduled job that puts the right videos in front of me before I've opened anything else.

## What you get

```
🎯 Monday — Public Speaking and Communication

1. How to speak so that people want to listen
   TED
2. The 3-step storytelling framework
   Philipp Humm
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
2. Copy `config.example.yaml` over `config.yaml` and edit it — your themes, your creators. A creator handle is the part after `@` in the channel URL (`youtube.com/@TED` → `TED`).
3. **YouTube API key:** [console.cloud.google.com](https://console.cloud.google.com) → create or pick a project → *APIs & Services → Library* → enable **YouTube Data API v3** → *Credentials → Create credentials → API key*. No billing needed.
4. **Pick how you want it delivered** (one or both), and set `deliver:` in `config.yaml`:

   **Telegram** — message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token. Open your new bot, press **Start**, send it "hi". Then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy the number after `"chat":{"id":`.
   Secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`

   **Email** — works with Gmail, Outlook or any SMTP host. For Gmail: turn on 2-Step Verification, then create an [App Password](https://myaccount.google.com/apppasswords) (not your normal password).
   Secrets: `SMTP_USER` (your address), `SMTP_PASS` (the app password), optionally `EMAIL_TO` (defaults to yourself), `SMTP_HOST` / `SMTP_PORT` (defaults: `smtp.gmail.com` / `587`; Outlook is `smtp.office365.com` / `587`)

5. In your fork: *Settings → Secrets and variables → Actions* → add `YOUTUBE_API_KEY` plus the secrets for your delivery choice.
6. *Actions → Daily Watch → Run workflow.* Check your phone or inbox.

To change the send time, edit the two `cron` lines and the `"12"` check in `.github/workflows/daily.yml` (GitHub cron is UTC).

## Run it locally

```bash
pip install -r requirements.txt
export YOUTUBE_API_KEY=... TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...
python watch_agent.py --preview                # see the whole week's picks, send nothing
python watch_agent.py --dry-run --day monday   # one day, send nothing
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
| `deliver` | `[telegram]`, `[email]`, or `[telegram, email]` |

## Design notes

- **Theme-matched, creator-scoped.** Each day searches *inside* your chosen creators' channels for that day's keywords, then round-robins across creators so one prolific channel can't crowd out the rest. If a creator has nothing on-theme, their latest upload fills in.
- **No strangers.** General search is off by default; the message only ever contains creators you listed.
- **A bad channel never kills the run.** Unknown or unreadable handles are logged and skipped.
- **State lives in the repo.** `sent.json` is committed back by the workflow, so there's no database and nothing to host.

## Ideas for later

- One-line "why this video" note via a free LLM tier
- WhatsApp or SMS delivery
- A hosted version where you just text the bot your weekly plan

## License

MIT
