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
