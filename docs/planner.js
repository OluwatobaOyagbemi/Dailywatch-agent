/*
 * Daily Watch — weekly planner (no account, no GitHub).
 *
 * The plan lives only in this browser (localStorage). Each day, the creators and theme for
 * that day are sent to the Daily Watch helper service, which returns matching videos.
 * Exposes window.DWPlanner for app.js.
 */
(function () {
  "use strict";

  var DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];
  var HANDLE_RE = /^[A-Za-z0-9._-]{3,30}$/;
  var ID_RE = /^[A-Za-z0-9_-]{1,32}$/;
  var MAX_CREATORS = 10;
  var PLAN_KEY = "dw:plan";
  var HISTORY_KEY = "dw:history";
  var DEFAULT_PROMPT = "Before you close this: write down one idea you'll use this week.";

  var EXAMPLE = {
    v: 1, perDay: 3, budget: 60, fill: true,
    days: {
      monday: { theme: "Public speaking", keywords: "public speaking storytelling presentation", creators: ["TED", "TEDx"], task: "Record a 60-second voice note explaining one idea from today's video." },
      tuesday: { theme: "Leadership", keywords: "leadership leader team", creators: ["MaxwellLeadershipOfficial", "TED"], task: "Write down one leadership habit and practise it once today." },
      wednesday: { theme: "Health and fitness", keywords: "workout fitness health", creators: ["TED"], task: "Do a 15-minute workout." },
      thursday: { theme: "Finance", keywords: "money finance investing budget", creators: ["marktilbury"], task: "Spend 10 minutes on your money: check a balance, a budget line or one subscription." },
      friday: { theme: "Learning and habits", keywords: "learning habits focus productivity", creators: ["TED"], task: "Pick one habit from today's video and try it once before bed." },
      saturday: { theme: "Music", keywords: "guitar lesson", creators: ["andycrowley"], task: "Practise for 15 minutes." }
    }
  };

  // ── storage ───────────────────────────────────────────────────────────────

  var store = {
    get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { localStorage.setItem(k, v); return true; } catch (e) { return false; } },
    del: function (k) { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } }
  };

  function cap(s, n) { s = String(s == null ? "" : s).replace(/[\u0000-\u001f\u007f​-‏‪-‮⁦-⁩]/g, " ").trim(); return s.slice(0, n); }

  function parseHandle(raw) {
    var s = String(raw || "").trim();
    var m = s.match(/youtube\.com\/@([A-Za-z0-9._-]+)/i);
    if (m) s = m[1];
    s = s.replace(/^@/, "");
    return HANDLE_RE.test(s) ? s : null;
  }

  /** Clean a plan read from storage or the form; drops anything malformed. */
  function normalize(raw) {
    if (!raw || typeof raw !== "object") return null;
    var plan = {
      v: 1,
      perDay: Math.min(10, Math.max(1, parseInt(raw.perDay, 10) || 3)),
      budget: raw.budget === undefined ? 60 : Math.min(600, Math.max(0, parseInt(raw.budget, 10) || 0)),
      fill: raw.fill !== false,
      days: {}
    };
    DAYS.forEach(function (d) {
      var x = raw.days && raw.days[d];
      if (!x || typeof x !== "object") return;
      var creators = (Array.isArray(x.creators) ? x.creators : []).map(parseHandle).filter(Boolean).slice(0, MAX_CREATORS);
      var theme = cap(x.theme, 100);
      if (!theme || !creators.length) return;
      plan.days[d] = { theme: theme, keywords: cap(x.keywords, 200), creators: creators, task: cap(x.task, 200) };
    });
    return Object.keys(plan.days).length ? plan : null;
  }

  function load() {
    try { return normalize(JSON.parse(store.get(PLAN_KEY) || "null")); } catch (e) { return null; }
  }
  function save(plan) { return store.set(PLAN_KEY, JSON.stringify(plan)); }
  function clear() {
    store.del(PLAN_KEY);
    clearSessions("");
  }
  function clearSessions(keepDate) {
    try {
      for (var i = localStorage.length - 1; i >= 0; i--) {
        var k = localStorage.key(i);
        if (k && k.indexOf("dw:session:") === 0 && k !== "dw:session:" + keepDate) localStorage.removeItem(k);
      }
    } catch (e) { /* ignore */ }
  }

  function toWeek(plan) {
    return DAYS.map(function (d) {
      var x = plan.days[d];
      return { day: d, theme: x ? x.theme : "", rest: !x };
    });
  }

  // ── building today's session ──────────────────────────────────────────────

  function signature(plan, day) {
    return JSON.stringify([day, plan.perDay, plan.budget, plan.fill]);
  }

  function pick(candidates, n, budget) {
    var out = [], total = 0;
    candidates.forEach(function (v) {
      if (out.length >= n) return;
      if (budget && total + (v.minutes || 0) > budget) return;
      out.push(v); total += v.minutes || 0;
    });
    if (!out.length && candidates.length && budget) {
      // nothing fits: offer the shortest video, but never one far beyond the limit
      var shortest = candidates.slice().sort(function (a, b) { return (a.minutes || 0) - (b.minutes || 0); })[0];
      if ((shortest.minutes || 0) <= budget * 1.5) out = [shortest];
    }
    return out;
  }

  /**
   * Resolve today's session for the visitor's plan. Cached per day so the list doesn't change
   * on refresh; videos shown on earlier days are skipped so the list stays fresh.
   */
  function buildToday(plan, date, weekday, helperUrl) {
    var day = plan.days[weekday];
    var base = {
      date: date, weekday: weekday, theme: day ? day.theme : "Rest day", prompt: DEFAULT_PROMPT,
      task: day ? day.task : "", send_time: "", week: toWeek(plan), editHref: "#plan", visitor: true, notices: []
    };
    if (!day) { base.rest = true; base.videos = []; return Promise.resolve(base); }

    var sig = signature(plan, day);
    try {
      var cached = JSON.parse(store.get("dw:session:" + date) || "null");
      if (cached && cached.sig === sig && Array.isArray(cached.session.videos)) return Promise.resolve(cached.session);
    } catch (e) { /* rebuild */ }

    var qs = new URLSearchParams({ creators: day.creators.join(","), q: day.keywords || day.theme, fill: plan.fill ? "1" : "0" });
    return fetch(helperUrl + "/session?" + qs.toString(), { headers: { Accept: "application/json" } })
      .catch(function () { throw new Error("Couldn't reach the video service. Check your connection and try again."); })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (body) {
          if (!r.ok) throw new Error(cap(body.error, 200) || "Couldn't load videos right now. Please try again in a minute.");
          return body;
        });
      })
      .then(function (body) {
        var vids = (Array.isArray(body.videos) ? body.videos : []).filter(function (v) { return v && ID_RE.test(v.id); });
        var history = [];
        try { history = JSON.parse(store.get(HISTORY_KEY) || "[]"); } catch (e) { history = []; }
        var seen = new Set(history);
        var fresh = vids.filter(function (v) { return !seen.has(v.id); });
        var chosen = pick(fresh.length ? fresh : vids, plan.perDay, plan.budget);
        base.videos = chosen;
        base.notices = (Array.isArray(body.notices) ? body.notices : []).slice(0, 3).map(function (n) { return cap(n, 160); });

        store.set(HISTORY_KEY, JSON.stringify(history.concat(chosen.map(function (v) { return v.id; })).slice(-400)));
        clearSessions(date);
        store.set("dw:session:" + date, JSON.stringify({ sig: sig, session: base }));
        return base;
      });
  }

  // ── editor ────────────────────────────────────────────────────────────────

  function h(tag, attrs) {
    var el = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      if (k === "text") el.textContent = attrs[k];
      else if (k === "class") el.className = attrs[k];
      else if (k === "checked" || k === "selected") el[k] = !!attrs[k];
      else el.setAttribute(k, attrs[k]);
    });
    for (var i = 2; i < arguments.length; i++) if (arguments[i]) el.appendChild(arguments[i]);
    return el;
  }

  function field(id, label, input, hint) {
    return h("div", { class: "pf" },
      h("label", { class: "pf__label", for: id, text: label }),
      input,
      hint ? h("p", { class: "pf__hint", text: hint }) : null,
      h("p", { class: "pf__error", id: id + "-error", role: "alert" }));
  }

  function titleCase(s) { return s.charAt(0).toUpperCase() + s.slice(1); }

  /** Render the plan editor into `root`. opts: { plan, onSave(plan), onCancel() } */
  function render(root, opts) {
    var plan = opts.plan || null;
    var hasSaved = !!load();
    root.textContent = "";

    var intro = h("section", { class: "planner__intro" },
      h("p", { class: "planner__eyebrow", text: "Daily Watch" }),
      h("h1", { class: "planner__title", text: hasSaved ? "Edit your weekly plan" : "Plan your week of learning" }),
      h("p", { class: "planner__lead", text: "Pick a theme for each day and the YouTube creators you trust. Each day, open this page to get a short list of videos on that theme from those creators, watch them, and you're done. No feed, no autoplay." }),
      h("p", { class: "planner__privacy", text: "No account needed. Your plan is saved only in this browser. To find videos, only that day's theme and creators are sent to the Daily Watch video service." }));

    var form = h("form", { class: "planner__form", novalidate: "novalidate" });
    var grid = h("div", { class: "planner__days" });

    DAYS.forEach(function (d) {
      var x = plan && plan.days[d];
      var id = function (f) { return "p-" + d + "-" + f; };
      var rest = h("input", { type: "checkbox", id: id("rest"), checked: !x && !!plan });
      var body = h("div", { class: "pday__body" },
        field(id("theme"), "Theme", h("input", { type: "text", id: id("theme"), maxlength: "100", placeholder: "e.g. Public speaking", value: x ? x.theme : "" })),
        field(id("creators"), "Creators", h("input", { type: "text", id: id("creators"), maxlength: "600", autocapitalize: "off", autocorrect: "off", spellcheck: "false",
          placeholder: "e.g. TED, @marktilbury", value: x ? x.creators.join(", ") : "" }),
          "YouTube handles (the part after @ in youtube.com/@name), separated by commas. Up to 10."),
        (function () {
          var more = h("details", { class: "pday__more" },
            h("summary", { text: "More options" }),
            field(id("keywords"), "Words to look for", h("input", { type: "text", id: id("keywords"), maxlength: "200", placeholder: "Defaults to the theme", value: x ? x.keywords : "" })),
            field(id("task"), "Try this", h("input", { type: "text", id: id("task"), maxlength: "200", placeholder: "One small thing to do after watching", value: x ? x.task : "" })));
          if (x && (x.keywords || x.task)) more.open = true;
          return more;
        })());
      var card = h("fieldset", { class: "pday" + (rest.checked ? " pday--rest" : "") },
        h("legend", { class: "pday__head" },
          h("span", { class: "pday__day", text: titleCase(d) }),
          h("label", { class: "pday__rest" }, rest, document.createTextNode(" Rest day"))),
        body);
      rest.addEventListener("change", function () { card.classList.toggle("pday--rest", rest.checked); });
      grid.appendChild(card);
    });

    var perDay = h("select", { id: "p-perday" });
    for (var n = 1; n <= 10; n++) perDay.appendChild(h("option", { value: String(n), text: n + (n === 1 ? " video" : " videos"), selected: (plan ? plan.perDay : 3) === n }));
    var budget = h("select", { id: "p-budget" });
    [[0, "No limit"], [15, "15 minutes"], [30, "30 minutes"], [45, "45 minutes"], [60, "1 hour"], [90, "1.5 hours"], [120, "2 hours"]].forEach(function (o) {
      budget.appendChild(h("option", { value: String(o[0]), text: o[1], selected: (plan ? plan.budget : 60) === o[0] }));
    });
    var fill = h("input", { type: "checkbox", id: "p-fill", checked: plan ? plan.fill : true });

    var settings = h("section", { class: "psettings" },
      h("h2", { class: "psettings__title", text: "Settings" }),
      h("div", { class: "psettings__row" },
        field("p-perday", "Videos per day", perDay),
        field("p-budget", "Daily time limit", budget)),
      h("label", { class: "pcheck" }, fill, document.createTextNode(" If a creator has nothing on the day's theme, include their latest upload")));

    var formError = h("p", { class: "planner__formerror", role: "alert" });
    var actions = h("div", { class: "planner__actions" },
      h("button", { class: "btn btn--primary", type: "submit", text: hasSaved ? "Save plan" : "Save and start" }),
      h("button", { class: "btn btn--outline", type: "button", id: "p-example", text: "Fill in an example" }),
      hasSaved ? h("button", { class: "btn btn--outline", type: "button", id: "p-cancel", text: "Cancel" }) : null,
      hasSaved ? h("button", { class: "link-btn link-btn--danger", type: "button", id: "p-clear", text: "Delete my plan" }) : null);

    form.appendChild(grid);
    form.appendChild(settings);
    form.appendChild(formError);
    form.appendChild(actions);
    root.appendChild(intro);
    root.appendChild(form);

    function setError(fid, msg) {
      var el = document.getElementById(fid + "-error");
      var input = document.getElementById(fid);
      if (el) el.textContent = msg || "";
      if (input) { if (msg) input.setAttribute("aria-invalid", "true"); else input.removeAttribute("aria-invalid"); }
    }

    function collect() {
      var raw = { perDay: perDay.value, budget: budget.value, fill: fill.checked, days: {} };
      var firstBad = null, any = false;
      DAYS.forEach(function (d) {
        var g = function (f) { return document.getElementById("p-" + d + "-" + f); };
        ["theme", "creators", "keywords", "task"].forEach(function (f) { setError("p-" + d + "-" + f, ""); });
        if (g("rest").checked) return;
        var theme = g("theme").value.trim(), creatorsRaw = g("creators").value.trim();
        if (!theme && !creatorsRaw && !g("task").value.trim()) return;   // left blank = rest day
        var parts = creatorsRaw.split(/[,\s]+/).filter(Boolean);
        var bad = parts.filter(function (p) { return !parseHandle(p); });
        var ok = true;
        if (!theme) { setError("p-" + d + "-theme", "Add a theme, or tick Rest day."); ok = false; }
        if (!parts.length) { setError("p-" + d + "-creators", "Add at least one creator."); ok = false; }
        else if (bad.length) { setError("p-" + d + "-creators", "\"" + cap(bad[0], 40) + "\" isn't a YouTube handle. Use the part after @, like TED."); ok = false; }
        else if (parts.length > MAX_CREATORS) { setError("p-" + d + "-creators", "Up to " + MAX_CREATORS + " creators a day."); ok = false; }
        if (!ok) { firstBad = firstBad || g("theme"); return; }
        any = true;
        raw.days[d] = { theme: theme, creators: parts, keywords: g("keywords").value, task: g("task").value };
      });
      formError.textContent = "";
      if (firstBad) { firstBad.focus(); formError.textContent = "Some days need fixing. See the messages above."; return null; }
      if (!any) { formError.textContent = "Add at least one day with a theme and creators."; return null; }
      return normalize(raw);
    }

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var p = collect();
      if (!p) return;
      if (!save(p)) { formError.textContent = "Couldn't save in this browser (private mode or storage blocked)."; return; }
      clearSessions("");
      opts.onSave(p);
    });
    document.getElementById("p-example").addEventListener("click", function () {
      render(root, { plan: EXAMPLE, onSave: opts.onSave, onCancel: opts.onCancel, isExample: true });
      root.querySelector(".planner__title").textContent = "Here's an example. Change anything, then save";
    });
    var cancel = document.getElementById("p-cancel");
    if (cancel && opts.onCancel) cancel.addEventListener("click", opts.onCancel);
    var del = document.getElementById("p-clear");
    if (del) del.addEventListener("click", function () {
      if (window.confirm("Delete your plan from this browser?")) { clear(); render(root, { plan: null, onSave: opts.onSave, onCancel: null }); }
    });
  }

  window.DWPlanner = {
    DAYS: DAYS, load: load, save: save, clear: clear, normalize: normalize, parseHandle: parseHandle,
    toWeek: toWeek, pick: pick, buildToday: buildToday, render: render, EXAMPLE: EXAMPLE
  };
})();
