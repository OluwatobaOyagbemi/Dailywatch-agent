# Daily Watch Agent

A tiny agent that sends you a handful of YouTube videos every morning, matched to a weekly growth theme you choose. No app, no dashboard, no cost — just a Telegram message at the time you pick.

```
🎯 Monday — Communication

1. How to speak so people listen
   John Maxwell
2. ...
```

## How it works

1. `config.yaml` holds your weekly plan: one theme per weekday, the creators you want to follow, and fallback search terms.
2. Every morning a GitHub Actions job runs `watch_agent.py`.
3. The script pulls the latest uploads from your creators (falling back to search if needed), skips anything it already sent, drops Shorts, and picks the newest few.
4. It sends them to you on Telegram and records the video IDs in `sent.json` so you never get repeats.

Everything runs on free tiers: YouTube Data API (10,000 units/day, this uses a few hundred), Telegram bots (free), GitHub Actions (free for public repos).

## Make it yours (15 minutes)

### 1. Fork this repo

Click **Fork** on GitHub. Keep it public so Actions minutes stay free.

### 2. Edit `config.yaml`

Set your themes, creators, and search phrases. Creator handles are the part after `@` in a channel URL (`youtube.com/@JohnMaxwellCo` → `JohnMaxwellCo`). Leave `creators` empty for any day and the agent will rely on search.

### 3. Get a YouTube API key

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and create a project (any name).
2. **APIs & Services → Library** → search *YouTube Data API v3* → **Enable**.
3. **APIs & Services → Credentials → Create credentials → API key**. Copy it.

No billing account is needed for this API.

### 4. Create a Telegram bot

1. In Telegram, message [@BotFather](https://t.me/BotFather) and send `/newbot`. Follow the prompts; copy the **bot token**.
2. Open a chat with your new bot and send it any message (e.g. "hi").
3. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser. Find `"chat":{"id": 123456789, ...}` — that number is your **chat id**.

### 5. Add the three secrets to GitHub

In your fork: **Settings → Secrets and variables → Actions → New repository secret**. Add:

| Name | Value |
|---|---|
| `YOUTUBE_API_KEY` | from step 3 |
| `TELEGRAM_BOT_TOKEN` | from step 4 |
| `TELEGRAM_CHAT_ID` | from step 4 |

### 6. Test it

**Actions** tab → **Daily Watch** → **Run workflow**. You should get a Telegram message within a minute. If not, open the run and read the log.

### 7. Set your time

The workflow fires at 7:00 AM `America/New_York`. To change it, edit `.github/workflows/daily.yml`: update the two `cron` lines (GitHub cron is UTC) and the `"07"` hour check and `TZ` in the gate step.

## Run it locally instead

```bash
pip install -r requirements.txt
export YOUTUBE_API_KEY=...
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...

python watch_agent.py --dry-run        # preview today's picks, send nothing
python watch_agent.py --day saturday   # preview a specific day
python watch_agent.py                  # send for real
```

## Tweaks

All in `config.yaml`:

- `videos_per_day` — how many links per message
- `max_video_age_days` — ignore older uploads
- `min_duration_minutes` — set to `0` to allow Shorts
- `creators_only` — `true` sends only your listed creators; `false` fills gaps with search

## Ideas for later

- Add a one-line "why this video" note using a free LLM tier
- Pull from a YouTube playlist instead of a channel
- Send to WhatsApp or SMS instead of Telegram

## License

MIT
