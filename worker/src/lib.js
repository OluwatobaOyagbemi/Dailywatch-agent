// Pure helpers for the Daily Watch helper service. No network here, so it's easy to test.

export const HANDLE_RE = /^[A-Za-z0-9._-]{3,30}$/;
export const VIDEO_ID_RE = /^[A-Za-z0-9_-]{6,32}$/;
export const CHANNEL_ID_RE = /^UC[A-Za-z0-9_-]{22}$/;
export const MAX_CREATORS = 10;
export const MAX_KEYWORDS_LEN = 200;

export class ValidationError extends Error {}

const STOPWORDS = new Set(["and", "the", "for", "with", "how", "you", "your", "are", "from", "that", "this", "into", "about"]);

/** Accepts "TED", "@TED" or "https://www.youtube.com/@TED/videos" and returns "TED" (or null). */
export function parseHandle(raw) {
  let s = String(raw || "").trim();
  const m = s.match(/youtube\.com\/@([A-Za-z0-9._-]+)/i);
  if (m) s = m[1];
  s = s.replace(/^@/, "");
  return HANDLE_RE.test(s) ? s : null;
}

/** Validate the query string of GET /session. Throws ValidationError with a plain message. */
export function parseSessionParams(params) {
  const rawCreators = (params.get("creators") || "").split(",").map((c) => c.trim()).filter(Boolean);
  if (!rawCreators.length) throw new ValidationError("Add at least one creator.");
  if (rawCreators.length > MAX_CREATORS) throw new ValidationError(`At most ${MAX_CREATORS} creators a day.`);
  const creators = [];
  for (const c of rawCreators) {
    const h = parseHandle(c);
    if (!h) throw new ValidationError(`"${c.slice(0, 40)}" isn't a YouTube handle.`);
    if (!creators.some((x) => x.toLowerCase() === h.toLowerCase())) creators.push(h);
  }
  const q = (params.get("q") || "").trim();
  if (!q) throw new ValidationError("Add a theme or keywords.");
  if (q.length > MAX_KEYWORDS_LEN) throw new ValidationError("Keywords are too long.");
  const int = (name, def, min, max) => {
    const v = params.get(name);
    if (v === null || v === "") return def;
    const n = Number(v);
    if (!Number.isInteger(n) || n < min || n > max) throw new ValidationError(`"${name}" must be ${min}–${max}.`);
    return n;
  };
  return {
    creators,
    keywords: q,
    maxAgeDays: int("age", 60, 1, 3650),
    minMinutes: int("min", 4, 0, 600),
    fill: params.get("fill") !== "0",
  };
}

export function keywordsOf(text) {
  const words = String(text || "").toLowerCase().match(/[\p{L}\p{N}']{3,}/gu) || [];
  return [...new Set(words.filter((w) => !STOPWORDS.has(w)))].slice(0, 20);
}

export function themeScore(video, words) {
  const hay = `${video.title} ${video.description || ""}`.toLowerCase();
  return words.reduce((n, w) => n + (hay.includes(w) ? 1 : 0), 0);
}

const XML_ENTITIES = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'" };
export function xmlUnescape(s) {
  return String(s || "").replace(/&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos);/gi, (m, e) => {
    if (e[0] === "#") {
      const code = e[1].toLowerCase() === "x" ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
      return Number.isFinite(code) && code > 0 && code < 0x110000 ? String.fromCodePoint(code) : "";
    }
    return XML_ENTITIES[e.toLowerCase()] ?? m;
  });
}

/** Strip control / zero-width / bidi characters, collapse whitespace, cap length. */
export function cleanText(s, limit) {
  let out = String(s || "").replace(/[\p{Cc}\p{Cf}]/gu, " ").replace(/\s+/g, " ").trim();
  if (out.length > limit) out = out.slice(0, limit - 1).trimEnd() + "…";
  return out;
}

const tag = (xml, name) => {
  const m = xml.match(new RegExp(`<${name}[^>]*>([\\s\\S]*?)</${name}>`));
  return m ? xmlUnescape(m[1].replace(/^<!\[CDATA\[|\]\]>$/g, "")) : "";
};

/** Parse a YouTube channel RSS feed into video records. Malformed entries are skipped. */
export function parseFeed(xml) {
  const out = [];
  for (const entry of String(xml || "").match(/<entry>[\s\S]*?<\/entry>/g) || []) {
    const id = tag(entry, "yt:videoId");
    const published = Date.parse(tag(entry, "published"));
    if (!VIDEO_ID_RE.test(id) || !Number.isFinite(published)) continue;
    out.push({
      id,
      title: cleanText(tag(entry, "title"), 150) || "(untitled)",
      channel: cleanText(tag(entry, "name"), 80),
      published: new Date(published).toISOString(),
      description: cleanText(tag(entry, "media:description"), 500),
    });
  }
  return out;
}

export function isoDurationToMinutes(iso) {
  const m = String(iso || "").match(/^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$/);
  if (!m) return 0;
  const [d, h, mi, s] = m.slice(1).map((x) => (x ? Number(x) : 0));
  return d * 1440 + h * 60 + mi + s / 60;
}

/** Take one from each list in turn so one prolific creator can't crowd out the rest. */
export function roundRobin(lists) {
  const queues = lists.map((l) => l.slice());
  const out = [];
  while (queues.some((q) => q.length)) for (const q of queues) if (q.length) out.push(q.shift());
  return out;
}

/** For one creator: on-theme recent uploads, newest first. */
export function onThemeRecent(videos, words, maxAgeDays, now = Date.now()) {
  const cutoff = now - maxAgeDays * 86400000;
  return videos
    .filter((v) => Date.parse(v.published) >= cutoff && themeScore(v, words) > 0)
    .sort((a, b) => Date.parse(b.published) - Date.parse(a.published));
}

/** The public shape of a video in the response: only these fields leave the service. */
export function publicVideo(v) {
  return {
    id: v.id,
    title: v.title,
    channel: v.channel,
    minutes: Math.round((v.minutes || 0) * 100) / 100,
    embeddable: v.embeddable !== false,
    published: (v.published || "").slice(0, 10),
    older: !!v.older,
    latest: !!v.latest,
  };
}

// ── calendar reminder feed (for "Subscribe in Outlook") ────────────────────

export const ICS_DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"];

/** Validate GET /reminder.ics params. */
export function parseReminderParams(params) {
  const days = (params.get("days") || "").split(",").map((d) => d.trim().toUpperCase()).filter(Boolean);
  if (!days.length || days.length > 7 || !days.every((d) => ICS_DAYS.includes(d)) || new Set(days).size !== days.length) {
    throw new ValidationError("days must be a comma-separated list like MO,TU,WE.");
  }
  const t = params.get("t") || "";
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(t)) throw new ValidationError("t must be a time like 07:30.");
  const tz = params.get("tz") || "";
  if (!/^[A-Za-z0-9_+\-\/]{1,64}$/.test(tz)) throw new ValidationError("tz must be a timezone like Africa/Lagos.");
  try { new Intl.DateTimeFormat("en-US", { timeZone: tz }); } catch { throw new ValidationError("Unknown timezone."); }
  return { days: ICS_DAYS.filter((d) => days.includes(d)), time: t, tz };
}

const pad2 = (n) => String(n).padStart(2, "0");

/** Local wall-clock fields of a UTC instant in `tz`. */
function wall(tz, ms) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
    timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  }).formatToParts(new Date(ms)).map((p) => [p.type, p.value]));
  return { y: +parts.year, mo: +parts.month, d: +parts.day, h: +parts.hour % 24, mi: +parts.minute, s: +parts.second };
}

/** UTC offset of `tz` at instant `ms`, in minutes. */
export function offsetAt(tz, ms) {
  const w = wall(tz, ms);
  return Math.round((Date.UTC(w.y, w.mo - 1, w.d, w.h, w.mi, w.s) - Math.floor(ms / 1000) * 1000) / 60000);
}

const fmtOffset = (min) => `${min >= 0 ? "+" : "-"}${pad2(Math.floor(Math.abs(min) / 60))}${pad2(Math.abs(min) % 60)}`;

function transitionsIn(tz, year) {
  const out = [];
  let prev = offsetAt(tz, Date.UTC(year, 0, 1, 12));
  for (let day = 1; day <= 366; day++) {
    const ms = Date.UTC(year, 0, day, 12);
    if (new Date(ms).getUTCFullYear() !== year) break;
    const off = offsetAt(tz, ms);
    if (off !== prev) {
      let lo = ms - 86400000, hi = ms;
      while (hi - lo > 60000) { const mid = Math.floor((lo + hi) / 2); if (offsetAt(tz, mid) === prev) lo = mid; else hi = mid; }
      out.push({ at: hi, from: prev, to: off });
      prev = off;
    }
  }
  return out;
}

function yearlyRule(y, mo, d) {
  const date = new Date(Date.UTC(y, mo - 1, d));
  const code = ["SU", "MO", "TU", "WE", "TH", "FR", "SA"][date.getUTCDay()];
  const last = new Date(Date.UTC(y, mo, 0)).getUTCDate() - d < 7;
  return `FREQ=YEARLY;BYMONTH=${mo};BYDAY=${last ? -1 : Math.ceil(d / 7)}${code}`;
}

export function vtimezoneLines(tz, year) {
  const lines = ["BEGIN:VTIMEZONE", `TZID:${tz}`];
  const tr = transitionsIn(tz, year);
  if (tr.length < 2) {
    const off = fmtOffset(offsetAt(tz, Date.UTC(year, 0, 1, 12)));
    lines.push("BEGIN:STANDARD", "DTSTART:19700101T000000", `TZOFFSETFROM:${off}`, `TZOFFSETTO:${off}`, "END:STANDARD");
  } else {
    for (const t of tr.slice(0, 2)) {
      const kind = t.to > t.from ? "DAYLIGHT" : "STANDARD";
      const w = new Date(t.at + t.from * 60000);
      const y = w.getUTCFullYear(), mo = w.getUTCMonth() + 1, d = w.getUTCDate();
      lines.push(`BEGIN:${kind}`, `DTSTART:${y}${pad2(mo)}${pad2(d)}T${pad2(w.getUTCHours())}${pad2(w.getUTCMinutes())}00`,
        `RRULE:${yearlyRule(y, mo, d)}`, `TZOFFSETFROM:${fmtOffset(t.from)}`, `TZOFFSETTO:${fmtOffset(t.to)}`, `END:${kind}`);
    }
  }
  lines.push("END:VTIMEZONE");
  return lines;
}

export function foldLine(line) {
  const enc = new TextEncoder();
  const out = [];
  let cur = "";
  for (const ch of line) {
    if (enc.encode(cur + ch).length > 75) { out.push(cur); cur = " " + ch; } else cur += ch;
  }
  out.push(cur);
  return out.join("\r\n");
}

const icsText = (s) => String(s).replace(/\\/g, "\\\\").replace(/[,;]/g, "\\$&").replace(/\r?\n/g, "\\n");

/** A subscribable calendar with one repeating reminder event (stable UID, so refreshes don't duplicate). */
export function buildReminderIcs({ days, time, tz }, siteUrl, nowMs = Date.now()) {
  const [hh, mm] = time.split(":").map(Number);
  // first planned day (in the viewer's zone), starting today
  let start = null;
  for (let i = 0; i < 8 && !start; i++) {
    const w = wall(tz, nowMs + i * 86400000);
    const code = ICS_DAYS[(new Date(Date.UTC(w.y, w.mo - 1, w.d)).getUTCDay() + 6) % 7];
    if (days.includes(code)) start = w;
  }
  const date = `${start.y}${pad2(start.mo)}${pad2(start.d)}`;
  const endMin = hh * 60 + mm + 15;
  const end = endMin >= 1440 ? `${date}T235900` : `${date}T${pad2(Math.floor(endMin / 60))}${pad2(endMin % 60)}00`;
  const rule = days.length === 7 ? "FREQ=DAILY" : `FREQ=WEEKLY;BYDAY=${days.join(",")}`;
  const stamp = new Date(nowMs).toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
  const title = "Daily Watch: today's session";
  const details = `Your short list for today is ready. Open it here: ${siteUrl}\nWatch, try the task, and you're done.`;
  return [
    "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Daily Watch//Reminder//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
    "X-WR-CALNAME:Daily Watch", "X-PUBLISHED-TTL:P1D", "REFRESH-INTERVAL;VALUE=DURATION:P1D",
    ...vtimezoneLines(tz, start.y),
    "BEGIN:VEVENT",
    `UID:dailywatch-${days.join("")}-${time.replace(":", "")}-${tz.replace(/[^A-Za-z0-9]/g, "")}@dailywatch`,
    `DTSTAMP:${stamp}`,
    `DTSTART;TZID=${tz}:${date}T${pad2(hh)}${pad2(mm)}00`,
    `DTEND;TZID=${tz}:${end}`,
    `RRULE:${rule}`,
    `SUMMARY:${icsText(title)}`,
    `DESCRIPTION:${icsText(details)}`,
    `URL:${siteUrl}`,
    "BEGIN:VALARM", "ACTION:DISPLAY", `DESCRIPTION:${icsText(title)}`, "TRIGGER:PT0M", "END:VALARM",
    "END:VEVENT", "END:VCALENDAR",
  ].map(foldLine).join("\r\n") + "\r\n";
}
