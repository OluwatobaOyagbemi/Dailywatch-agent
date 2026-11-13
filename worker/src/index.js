// Daily Watch helper service (Cloudflare Worker).
//
// GET /session?creators=TED,marktilbury&q=public+speaking&age=60&min=4
//   -> { videos: [...], notices: [...] }
//
// Videos come from each creator's public RSS feed, which uses no API quota. The YouTube
// Data API (key kept as a Worker secret, never sent to browsers) is used only for:
//   - handle -> channel lookups (1 unit, cached for 30 days)
//   - video lengths, to drop Shorts (1 unit per 50 videos)
//   - a theme search of older videos when a creator has nothing recent on the theme
//     (100 units, cached for a day, at most MAX_SEARCHES per request)
// Responses are cached at the edge for an hour, so repeat visits cost nothing.

import {
  CHANNEL_ID_RE, ValidationError, keywordsOf, onThemeRecent, parseFeed, parseSessionParams,
  publicVideo, roundRobin, isoDurationToMinutes, cleanText, VIDEO_ID_RE,
} from "./lib.js";

const YT = "https://www.googleapis.com/youtube/v3";
const MAX_SEARCHES = 3;
const SESSION_TTL = 3600;          // 1 hour
const HANDLE_TTL = 30 * 86400;     // 30 days
const SEARCH_TTL = 86400;          // 1 day

class QuotaError extends Error {}

export default {
  async fetch(request, env, ctx) {
    const cors = corsHeaders(request, env);
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    if (request.method !== "GET") return json({ error: "Method not allowed." }, 405, cors);

    const url = new URL(request.url);
    if (url.pathname === "/health") return json({ ok: true }, 200, cors);
    if (url.pathname !== "/session") return json({ error: "Not found." }, 404, cors);
    if (!env.YOUTUBE_API_KEY) return json({ error: "The helper isn't configured yet." }, 503, cors);

    let params;
    try {
      params = parseSessionParams(url.searchParams);
    } catch (e) {
      if (e instanceof ValidationError) return json({ error: e.message }, 400, cors);
      throw e;
    }

    // Edge cache keyed on the normalised request, so the same plan shares one result for an hour.
    const cacheKey = new Request(`https://cache.dailywatch/session?${new URLSearchParams({
      c: params.creators.map((c) => c.toLowerCase()).join(","), q: params.keywords.toLowerCase(),
      a: params.maxAgeDays, m: params.minMinutes, f: params.fill ? 1 : 0,
    })}`);
    const cache = caches.default;
    const hit = await cache.match(cacheKey);
    if (hit) return withHeaders(hit, cors);

    try {
      const body = await buildSession(params, env, ctx);
      const res = json(body, 200, { "Cache-Control": `public, max-age=${SESSION_TTL}` });
      ctx.waitUntil(cache.put(cacheKey, res.clone()));
      return withHeaders(res, cors);
    } catch (e) {
      // Never echo upstream errors: they can contain the API key in a URL.
      if (e instanceof QuotaError) return json({ error: "Daily video limit reached. Please try again tomorrow." }, 503, cors);
      return json({ error: "Couldn't load videos right now. Please try again in a minute." }, 502, cors);
    }
  },
};

async function buildSession(params, env, ctx) {
  const words = keywordsOf(params.keywords);
  const notices = [];
  let searches = 0;

  const perCreator = await Promise.all(params.creators.map(async (handle) => {
    const channelId = await resolveHandle(handle, env, ctx).catch((e) => { if (e instanceof QuotaError) throw e; return null; });
    if (!channelId) { notices.push(`@${handle} wasn't found on YouTube.`); return []; }

    const feed = await fetchFeed(channelId).catch(() => []);
    let picks = onThemeRecent(feed, words, params.maxAgeDays);
    if (!picks.length && searches < MAX_SEARCHES) {
      searches += 1;
      const cutoff = Date.now() - params.maxAgeDays * 86400000;
      picks = (await searchOlder(channelId, params.keywords, env, ctx).catch(() => []))
        .map((v) => ({ ...v, older: Date.parse(v.published) < cutoff }));
    }
    if (!picks.length && params.fill) {
      picks = feed.slice(0, 3).map((v) => ({ ...v, latest: true }));
      if (picks.length) notices.push(`Nothing on this theme from @${handle} lately, so their latest uploads are included.`);
    }
    return picks.slice(0, 6);
  }));

  // Lengths + embeddability, and drop Shorts / clips under the minimum.
  const all = perCreator.flat();
  const details = await videoDetails(all.map((v) => v.id), env).catch((e) => { if (e instanceof QuotaError) throw e; return {}; });
  const keep = (v) => {
    const d = details[v.id];
    if (!d) return params.minMinutes === 0;
    v.minutes = d.minutes;
    v.embeddable = d.embeddable;
    return d.minutes >= params.minMinutes;
  };
  const filtered = perCreator.map((list) => list.filter(keep));
  return { videos: roundRobin(filtered).slice(0, 20).map(publicVideo), notices };
}

// ── upstream calls ─────────────────────────────────────────────────────────

async function cached(key, ttl, ctx, produce) {
  const req = new Request(`https://cache.dailywatch/${key}`);
  const hit = await caches.default.match(req);
  if (hit) return hit.json();
  const value = await produce();
  ctx.waitUntil(caches.default.put(req, new Response(JSON.stringify(value), {
    headers: { "Content-Type": "application/json", "Cache-Control": `public, max-age=${ttl}` },
  })));
  return value;
}

async function ytApi(path, params, env) {
  const url = `${YT}/${path}?${new URLSearchParams({ ...params, key: env.YOUTUBE_API_KEY })}`;
  const r = await fetch(url);
  if (r.status === 403) {
    const body = await r.json().catch(() => ({}));
    const reason = body?.error?.errors?.[0]?.reason || "";
    if (reason === "quotaExceeded" || reason === "dailyLimitExceeded") throw new QuotaError();
  }
  if (!r.ok) throw new Error(`YouTube API ${r.status}`);
  return r.json();
}

async function resolveHandle(handle, env, ctx) {
  return cached(`handle/${handle.toLowerCase()}`, HANDLE_TTL, ctx, async () => {
    const data = await ytApi("channels", { part: "id", forHandle: handle }, env);
    const id = data?.items?.[0]?.id;
    return CHANNEL_ID_RE.test(id || "") ? id : null;
  });
}

async function fetchFeed(channelId) {
  const r = await fetch(`https://www.youtube.com/feeds/videos.xml?channel_id=${channelId}`, {
    cf: { cacheTtl: 1800, cacheEverything: true },
  });
  if (!r.ok) return [];
  return parseFeed(await r.text());
}

async function searchOlder(channelId, keywords, env, ctx) {
  return cached(`search/${channelId}/${encodeURIComponent(keywords.toLowerCase())}`, SEARCH_TTL, ctx, async () => {
    const data = await ytApi("search", {
      part: "snippet", channelId, q: keywords, type: "video", order: "date", maxResults: "8",
    }, env);
    return (data?.items || []).map((it) => ({
      id: it?.id?.videoId,
      title: cleanText(decodeEntities(it?.snippet?.title), 150) || "(untitled)",
      channel: cleanText(decodeEntities(it?.snippet?.channelTitle), 80),
      published: it?.snippet?.publishedAt || "",
      description: "",
    })).filter((v) => VIDEO_ID_RE.test(v.id || ""));
  });
}

async function videoDetails(ids, env) {
  const unique = [...new Set(ids)].slice(0, 50);
  if (!unique.length) return {};
  const data = await ytApi("videos", { part: "contentDetails,status", id: unique.join(",") }, env);
  const out = {};
  for (const it of data?.items || []) {
    if (!VIDEO_ID_RE.test(it?.id || "")) continue;
    out[it.id] = {
      minutes: isoDurationToMinutes(it?.contentDetails?.duration),
      embeddable: it?.status?.embeddable !== false,
    };
  }
  return out;
}

function decodeEntities(s) {
  return String(s || "").replace(/&(amp|lt|gt|quot|#39);/g, (m, e) => ({ amp: "&", lt: "<", gt: ">", quot: '"', "#39": "'" })[e]);
}

// ── responses ──────────────────────────────────────────────────────────────

function corsHeaders(request, env) {
  const allowed = (env.ALLOWED_ORIGINS || "").split(",").map((s) => s.trim()).filter(Boolean);
  const origin = request.headers.get("Origin") || "";
  const ok = allowed.includes(origin) || /^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(origin);
  return ok ? { "Access-Control-Allow-Origin": origin, "Access-Control-Allow-Methods": "GET, OPTIONS", Vary: "Origin" } : { Vary: "Origin" };
}

function json(body, status, headers = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "X-Content-Type-Options": "nosniff", ...headers },
  });
}

function withHeaders(res, headers) {
  const out = new Response(res.body, res);
  for (const [k, v] of Object.entries(headers)) out.headers.set(k, v);
  return out;
}
