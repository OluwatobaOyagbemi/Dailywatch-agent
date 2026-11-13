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
- **Something to do afterwards.** Give each day a `task:`, one small thing to try with what you watched (*"Record a 60-second voice note explaining one idea"*). It appears in the message and on the watch page as **Try this**.
- **Know before you tap.** With `why_notes` on, each video gets a one-line note on what it offers for today's theme, so you can skip the ones that don't fit without opening them (see [Why-watch notes](#why-watch-notes)).

<!-- Replace this comment with a screenshot of the Telegram message:
![Telegram message](docs/screenshot.png) -->

## Use it in the browser (no GitHub)

Anyone can open the live page, plan their week and start using it straight away:

1. Open the page. First-time visitors see **Plan your week**.
2. For each day, type a theme and the creators you trust (the part after `@` in `youtube.com/@name`). Leave a day blank or tick *Rest day* to skip it. Optionally add words to look for and a small task.
3. Save. Each day, open the page (or add it to your home screen) and you get that day's short list.

The plan is saved only in that browser. There's no account and no sign-up. To find videos, the page sends only that day's theme and creators to the Daily Watch helper service. Browser users don't get Telegram or email messages. For those, set up your own copy below.

### The helper service (for whoever hosts the site)

The browser planner needs the small helper in [`worker/`](worker/), a free Cloudflare Worker. It reads each creator's public YouTube feed, which uses no API allowance. It uses the YouTube API only for cheap lookups (finding a channel, getting video lengths) and for an occasional theme search, and caches results so repeat visits cost nothing. The YouTube key is stored as a Worker secret and never reaches the browser.

```bash
cd worker
npx wrangler login                        # once, opens Cloudflare in your browser
npx wrangler deploy                       # prints your helper URL
npx wrangler secret put YOUTUBE_API_KEY   # paste a YouTube Data API key
```

Then put the printed URL in `docs/config.js` (`helperUrl`) and push. Using a separate YouTube key (from a separate Google Cloud project) keeps visitors from using up the daily allowance of your own Telegram send. Until `helperUrl` is set, the page shows only the owner's daily session.

## Watch page

Each day's message links to your own watch page on GitHub Pages instead of YouTube. It has today's list and nothing else.

- **A list that ends:** today's videos with thumbnails, lengths and progress, then *"End of today's list. Nothing more to scroll."*
- **No YouTube rabbit holes:** videos play in a privacy-enhanced player. When one ends, the page shows its own *Up next* card in place of YouTube's grid of suggestions. When you pause, a resume screen covers the whole player, including YouTube's "More videos" strip.
- **No autoplay:** you choose when the next video starts.
- **Skip without guilt:** *Skip* (or `S`) marks a video "not for me". It counts toward finishing the session, and the next video is lined up, not autoplayed.
- **A finish line:** once every video is watched or skipped (or you tap *Finish for today*), the page shows *"That's today."*, your reflection prompt and when tomorrow's session arrives. No other links.
- **Built to feel familiar and work everywhere:** works on phone and desktop, follows your light or dark setting, keyboard shortcuts (`N` next, `P` previous, `M` mark watched, `S` skip), and screen-reader labels. Progress is remembered on that device.
- **Edge cases handled:** if a creator blocks playback outside YouTube, the page says so and offers a single labelled "Open on YouTube" button. If you tap the link before GitHub has published the day's data, you can start watching straight away and titles fill in when it arrives.

**Turn it on (once):** in your repo, *Settings → Pages → Build and deployment → Deploy from a branch → `main` / `/docs` → Save*. Your page is at `https://<your-username>.github.io/<repo-name>/`. Until it's live, messages link straight to YouTube, so leaving `watch_page: true` is safe. To use another address, set a `WATCH_PAGE_URL` repo variable.

Your daily list is published at that address, as public as the repo itself.

## Why-watch notes

Optional. When an `ANTHROPIC_API_KEY` secret is present, the agent asks Claude for one short note per video based on its title, channel and description, for example *"💡 Three habits that steady your voice in the first minute of a talk."* The notes appear in the message, the email and on the watch page.

- **Setup:** create a key at [console.anthropic.com](https://console.anthropic.com), then add it as a repo secret named `ANTHROPIC_API_KEY`. `python watch_agent.py --check` shows whether notes are on.
- **Cost:** one small request a day to `claude-opus-5-5` at low effort, roughly one to two cents. Server-side refusal fallbacks are enabled, so if a request is declined it's retried on another model automatically.
- **Safe by default:** with no key, or if the request fails for any reason, the message goes out as usual without notes. Video descriptions are treated as data, and every note is cleaned and capped at 160 characters. Set `why_notes: false` to turn it off.

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
   cp config.example.yaml config.yaml && echo "[]" > sent.json && rm -f last_sent.txt && printf '{"date":"","videos":[]}\n' > docs/today.json
   ```
   Edit `config.yaml`: your themes, your creators. A creator handle is the part after `@` in the channel URL (`youtube.com/@TED` → `TED`).
3. **YouTube API key:** [console.cloud.google.com](https://console.cloud.google.com) → create or pick a project → *APIs & Services → Library* → enable **YouTube Data API v3** → *Credentials → Create credentials → API key*. No billing needed.
4. **Pick how you want it delivered** (one or both), and set `deliver:` in `config.yaml`:

   **Telegram** — message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token. Open your new bot, press **Start**, send it "hi". Then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy the number after `"chat":{"id":`.
   Secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`

   **Email** — works with Gmail, Outlook or any SMTP host. For Gmail: turn on 2-Step Verification, then create an [App Password](https://myaccount.google.com/apppasswords) (not your normal password).
   Secrets: `SMTP_USER` (your address), `SMTP_PASS` (the app password), optionally `EMAIL_TO` (defaults to yourself), `SMTP_HOST` / `SMTP_PORT` (defaults: `smtp.gmail.com` / `587`; Outlook is `smtp.office365.com` / `587`)

5. In your fork: *Settings → Secrets and variables → Actions*:
   - **Secrets** tab: add `YOUTUBE_API_KEY` plus the secrets for your delivery choice. Optionally add `ANTHROPIC_API_KEY` for [why-watch notes](#why-watch-notes).
   - **Variables** tab: add `TIMEZONE` (e.g. `Europe/London`, `Africa/Lagos`, `America/Los_Angeles`; [list](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones)) and `SEND_HOUR` (`0`–`23`, local). Defaults: `America/New_York`, `12`.
6. *Settings → Pages* → deploy from branch `main`, folder `/docs` (this turns on the [watch page](#watch-page)).
7. *Actions* tab → enable workflows on your fork → *Daily Watch → Run workflow.* Check your phone or inbox.

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

Offline tests use a fake YouTube API, so no key or network is needed. They cover the picking logic (round-robin, no repeats, Shorts filter, off-theme skipping, missing channels), the time budget, distraction-free links, message escaping for Telegram and email, delivery fallbacks, `--check`, and every guardrail. They run on every push.

## Settings (`config.yaml`)

| Key | What it does |
|---|---|
| `videos_per_day` | how many links per message |
| `max_video_age_days` | ignore uploads older than this |
| `min_duration_minutes` | `0` allows Shorts |
| `creators_only` | `true` = only your listed creators; `false` = fill gaps with search |
| `match_theme` | `true` = search each creator's channel for the day's `keywords`; `false` = just their latest uploads |
| `fallback_to_latest` | `false` = skip a creator with nothing on-theme today; `true` = send their latest upload anyway |
| `older_on_theme` | `true` = if a creator has nothing recent on today's theme, pick their newest older video on that theme instead (shown with its upload month) |
| `distraction_free` | `true` = links open the bare player with no feed, comments or autoplay |
| `daily_minutes_budget` | cap on the day's total watch time in minutes (`0` = no cap) |
| `prompt` | reflection line at the end of each message (a day's `prompt:` overrides it) |
| `days.<day>.task` | one small thing to try after watching, shown as **Try this** |
| `blocked_words` | never send titles containing these words (`#shorts` is always blocked) |
| `max_api_units` | hard cap on YouTube API units per run (default `5000` of the free 10,000/day) |
| `watch_page` | `true` = messages link to your watch page once it's published (see *Watch page*) |
| `watch_page_url` | optional custom address for the watch page (default `https://<owner>.github.io/<repo>/`) |
| `why_notes` | `true` = one-line "why watch this" note per video (needs an `ANTHROPIC_API_KEY` secret) |
| `deliver` | `[telegram]`, `[email]`, or `[telegram, email]`. If one channel fails, the others still send |

## Guardrails

All of these live in [`guardrails.py`](guardrails.py) and are covered by tests.

**Input**
- `config.yaml` is checked before anything runs: types, ranges (1–10 videos a day, at most 10 creators a day), weekday names, handle format and delivery channels. Every problem is listed in plain words. Typos get a suggestion (`videos_per_dya` → *did you mean `videos_per_day`?*). Broken YAML reports the line number.
- Secrets are format-checked (bot token, chat ID, single email address, numeric port) before use.
- YouTube responses are treated as untrusted. Malformed items are skipped, not fatal. Video IDs must use YouTube's URL-safe alphabet, so a link can only ever point at YouTube. Titles have control, zero-width and bidi-override characters removed, and are capped in length.

**Output**
- `blocked_words` (plus `#shorts`, always) are never sent.
- Telegram messages are HTML-escaped and kept under the 4,096-character limit. Email subjects can't carry line breaks, so headers can't be injected.

**Code**
- Retries with backoff on rate limits, 5xx errors and network blips (YouTube and Telegram). If YouTube says the daily quota is used up, the run stops calling it.
- `max_api_units` caps what one run can spend, and each run reports the units it used.
- API keys, bot tokens and passwords are redacted from every warning and error. Unexpected errors print one clean line (`DEBUG=1` gives the full traceback).
- `sent.json` and `last_sent.txt` are written atomically. A corrupt `sent.json` is moved to `sent.json.corrupt` instead of crashing or being silently overwritten.

## Design notes

- **Theme-matched, creator-scoped.** Each day searches *inside* your chosen creators' channels for that day's keywords, then round-robins across creators so one prolific channel can't crowd out the rest. A creator with nothing recent on-theme gets their newest *older* on-theme video instead (`older_on_theme`), so the message stays on topic. Only if they've never covered the theme are they left out (or, with `fallback_to_latest: true`, their latest upload fills in).
- **No strangers.** General search is off by default; the message only ever contains creators you listed.
- **A bad channel never kills the run.** Unknown or unreadable handles are logged and skipped.
- **State lives in the repo.** `sent.json` is committed back by the workflow, so there's no database and nothing to host.

## Ideas for later

- "✅ Watched" buttons on the Telegram message, with a weekly streak in Sunday's message
- WhatsApp or SMS delivery
- A hosted version where you just text the bot your weekly plan

## License

MIT
