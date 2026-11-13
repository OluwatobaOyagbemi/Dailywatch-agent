// Run with: node --test worker/test
import { test } from "node:test";
import assert from "node:assert/strict";
import * as lib from "../src/lib.js";
import worker from "../src/index.js";

const FEED = (channel, entries) => `<?xml version="1.0"?><feed xmlns:yt="http://www.youtube.com/xml/schemas/2015">
${entries.map((e) => `<entry><yt:videoId>${e.id}</yt:videoId><title>${e.title}</title>
<author><name>${channel}</name></author><published>${e.published}</published>
<media:group><media:description>${e.desc || ""}</media:description></media:group></entry>`).join("")}</feed>`;

const now = new Date().toISOString();
const old = "2020-01-01T00:00:00+00:00";

// ── pure helpers ───────────────────────────────────────────────────────────

test("parseHandle accepts handles and channel URLs, rejects junk", () => {
  assert.equal(lib.parseHandle("@TED"), "TED");
  assert.equal(lib.parseHandle("https://www.youtube.com/@philipp-humm/videos"), "philipp-humm");
  assert.equal(lib.parseHandle("has space"), null);
  assert.equal(lib.parseHandle("<script>"), null);
});

test("parseSessionParams validates input", () => {
  const p = lib.parseSessionParams(new URLSearchParams("creators=TED,@ted,marktilbury&q=public speaking"));
  assert.deepEqual(p.creators, ["TED", "marktilbury"]);
  assert.equal(p.maxAgeDays, 60);
  for (const bad of ["q=x", "creators=TED", "creators=bad handle&q=x", `creators=${"a1b,".repeat(11)}&q=x`, "creators=TED&q=x&age=0"]) {
    assert.throws(() => lib.parseSessionParams(new URLSearchParams(bad)), lib.ValidationError, bad);
  }
});

test("parseFeed reads entries, unescapes, cleans and skips bad ids", () => {
  const xml = FEED("TED &amp; Friends", [
    { id: "abcdefghijk", title: "Speak &quot;well&quot;‮", published: now, desc: "A talk on &lt;speaking&gt;" },
    { id: "bad/id", title: "x", published: now },
  ]);
  const v = lib.parseFeed(xml);
  assert.equal(v.length, 1);
  assert.equal(v[0].title, 'Speak "well"');
  assert.equal(v[0].channel, "TED & Friends");
  assert.equal(v[0].description, "A talk on <speaking>");
});

test("theme matching, age filter and round robin", () => {
  const words = lib.keywordsOf("public speaking and storytelling");
  assert.deepEqual(words, ["public", "speaking", "storytelling"]);
  const vids = [
    { id: "a1", title: "Cooking pasta", published: now },
    { id: "a2", title: "Storytelling basics", published: now },
    { id: "a3", title: "Public speaking", published: old },
  ];
  assert.deepEqual(lib.onThemeRecent(vids, words, 60).map((v) => v.id), ["a2"]);
  assert.deepEqual(lib.roundRobin([[1, 3, 5], [2], [4]]), [1, 2, 4, 3, 5]);
  assert.equal(lib.isoDurationToMinutes("PT1H5M30S"), 65.5);
});

// ── full request with fake YouTube ─────────────────────────────────────────

function setup({ quota = false } = {}) {
  const calls = [];
  const store = new Map();
  globalThis.caches = { default: {
    match: async (req) => store.get(req.url)?.clone(),
    put: async (req, res) => { store.set(req.url, res); },
  } };
  globalThis.fetch = async (url) => {
    url = String(url);
    calls.push(url);
    const u = new URL(url);
    if (quota && u.hostname === "www.googleapis.com") {
      return new Response(JSON.stringify({ error: { errors: [{ reason: "quotaExceeded" }] } }), { status: 403 });
    }
    if (u.pathname.endsWith("/channels")) {
      const h = u.searchParams.get("forHandle");
      return Response.json(h === "missing" ? { items: [] } : { items: [{ id: "UC" + h.padEnd(22, "x").slice(0, 22) }] });
    }
    if (u.pathname === "/feeds/videos.xml") {
      const ch = u.searchParams.get("channel_id");
      if (ch.startsWith("UCspeak")) return new Response(FEED("Speak", [
        { id: "speak000001", title: "Public speaking tips", published: now },
        { id: "speak000002", title: "My lunch", published: now },
        { id: "speakshort1", title: "Speaking in 30 seconds #shorts", published: now },
      ]));
      return new Response(FEED("Quiet", [{ id: "quiet000001", title: "Gardening vlog", published: now }]));
    }
    if (u.pathname.endsWith("/search")) {
      return Response.json({ items: [{ id: { videoId: "quietold001" }, snippet: { title: "Speaking on stage", channelTitle: "Quiet", publishedAt: old } }] });
    }
    if (u.pathname.endsWith("/videos")) {
      return Response.json({ items: u.searchParams.get("id").split(",").map((id) => ({
        id, contentDetails: { duration: id.includes("short") ? "PT30S" : "PT12M" }, status: { embeddable: true } })) });
    }
    throw new Error("unexpected " + url);
  };
  return { calls };
}

const env = { YOUTUBE_API_KEY: "AIzaSECRET", ALLOWED_ORIGINS: "https://site.github.io" };
const ctx = { waitUntil: () => {} };
const req = (qs, origin = "https://site.github.io") => new Request(`https://helper.example/session?${qs}`, { headers: { Origin: origin } });

test("session: on-theme recent, older on-theme search, shorts dropped, CORS", async () => {
  setup();
  const res = await worker.fetch(req("creators=speak,quiet&q=public speaking"), env, ctx);
  assert.equal(res.status, 200);
  assert.equal(res.headers.get("Access-Control-Allow-Origin"), "https://site.github.io");
  const body = await res.json();
  assert.deepEqual(body.videos.map((v) => v.id), ["speak000001", "quietold001"]);
  assert.equal(body.videos[1].older, true);
  assert.equal(body.videos[0].minutes, 12);
  assert.ok(!JSON.stringify(body).includes("AIzaSECRET"));
});

test("session: missing creator is reported, not fatal", async () => {
  setup();
  const body = await (await worker.fetch(req("creators=speak,missing&q=public speaking"), env, ctx)).json();
  assert.equal(body.videos.length, 1);
  assert.match(body.notices.join(" "), /@missing wasn't found/);
});

test("session: second identical request is served from cache", async () => {
  const { calls } = setup();
  await worker.fetch(req("creators=speak&q=public speaking"), env, ctx);
  const n = calls.length;
  const res = await worker.fetch(req("creators=Speak&q=Public Speaking"), env, ctx);
  assert.equal(res.status, 200);
  assert.equal(calls.length, n, "no upstream calls on a cache hit");
});

test("session: quota exhaustion gives a friendly 503 without leaking the key", async () => {
  setup({ quota: true });
  const res = await worker.fetch(req("creators=speak&q=x y z"), env, ctx);
  assert.equal(res.status, 503);
  const text = await res.text();
  assert.match(text, /try again tomorrow/);
  assert.ok(!text.includes("AIzaSECRET"));
});

test("bad input -> 400; unknown origin gets no CORS header; no key -> 503", async () => {
  setup();
  assert.equal((await worker.fetch(req("creators=bad handle&q=x"), env, ctx)).status, 400);
  const res = await worker.fetch(req("creators=speak&q=public", "https://evil.example"), env, ctx);
  assert.equal(res.headers.get("Access-Control-Allow-Origin"), null);
  assert.equal((await worker.fetch(req("creators=speak&q=x"), {}, ctx)).status, 503);
});
