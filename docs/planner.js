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
    v: 1, perDay: 3, budget: 0, fill: true,
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
      budget: Math.min(600, Math.max(0, parseInt(raw.budget, 10) || 0)),
      fill: raw.fill !== false,
      reminderTime: /^([01]\d|2[0-3]):[0-5]\d$/.test(raw.reminderTime || "") ? raw.reminderTime : "12:00",
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
      budget.appendChild(h("option", { value: String(o[0]), text: o[1], selected: (plan ? plan.budget : 0) === o[0] }));
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

  // ── calendar reminder ─────────────────────────────────────────────────────

  var ICS_DAY = { monday: "MO", tuesday: "TU", wednesday: "WE", thursday: "TH", friday: "FR", saturday: "SA", sunday: "SU" };

  function plannedDays(plan) {
    return DAYS.filter(function (d) { return plan.days[d]; });
  }

  function pad(n) { return String(n).padStart(2, "0"); }

  /** Next planned day at the reminder time (local, "floating" so calendars use the viewer's timezone). */
  function firstOccurrence(plan, now) {
    var t = plan.reminderTime.split(":");
    var days = plannedDays(plan);
    for (var i = 0; i < 8; i++) {
      var d = new Date(now.getFullYear(), now.getMonth(), now.getDate() + i, +t[0], +t[1]);
      var name = DAYS[(d.getDay() + 6) % 7];
      if (days.indexOf(name) !== -1 && d > now) return d;
    }
    return new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, +t[0], +t[1]);
  }

  function stamp(d) {
    return d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate()) + "T" + pad(d.getHours()) + pad(d.getMinutes()) + "00";
  }

  function rrule(plan) {
    var days = plannedDays(plan).map(function (d) { return ICS_DAY[d]; });
    return days.length === 7 ? "FREQ=DAILY" : "FREQ=WEEKLY;BYDAY=" + days.join(",");
  }

  function icsText(s) { return String(s).replace(/\\/g, "\\\\").replace(/[,;]/g, "\\$&").replace(/\r?\n/g, "\\n"); }

  // ── timezone support (Outlook needs a real TZID + VTIMEZONE, not a floating time) ──

  function viewerZone() {
    try {
      var z = Intl.DateTimeFormat().resolvedOptions().timeZone;
      return /^[A-Za-z0-9_+\-\/]{1,64}$/.test(z || "") ? z : "";
    } catch (e) { return ""; }
  }

  function offsetMinutes(date) { return -date.getTimezoneOffset(); }

  function fmtOffset(min) {
    var sign = min >= 0 ? "+" : "-", a = Math.abs(min);
    return sign + pad(Math.floor(a / 60)) + pad(a % 60);
  }

  /** Find the exact local moments this year when the UTC offset changes (DST transitions). */
  function transitions(year) {
    var out = [];
    var prev = offsetMinutes(new Date(year, 0, 1, 12));
    for (var day = 1; day <= 366; day++) {
      var noon = new Date(year, 0, day, 12);
      if (noon.getFullYear() !== year) break;
      var off = offsetMinutes(noon);
      if (off !== prev) {
        // narrow to the hour within the previous 24h
        var lo = new Date(noon.getTime() - 86400000).getTime(), hi = noon.getTime();
        while (hi - lo > 60000) {
          var mid = Math.floor((lo + hi) / 2);
          if (offsetMinutes(new Date(mid)) === prev) lo = mid; else hi = mid;
        }
        out.push({ at: new Date(hi), from: prev, to: off });
        prev = off;
      }
    }
    return out;
  }

  /** RRULE for a yearly transition, e.g. 2nd Sunday of March or last Sunday of October. */
  function yearlyRule(d) {
    var dayCode = ["SU", "MO", "TU", "WE", "TH", "FR", "SA"][d.getDay()];
    var lastWeek = new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate() - d.getDate() < 7;
    var nth = lastWeek ? -1 : Math.ceil(d.getDate() / 7);
    return "FREQ=YEARLY;BYMONTH=" + (d.getMonth() + 1) + ";BYDAY=" + nth + dayCode;
  }

  /** VTIMEZONE lines for the viewer's zone (DST rules derived from this year's transitions). */
  function vtimezone(tzid, year) {
    var lines = ["BEGIN:VTIMEZONE", "TZID:" + tzid];
    var tr = transitions(year);
    if (tr.length < 2) {
      var off = fmtOffset(offsetMinutes(new Date(year, 0, 1, 12)));
      lines.push("BEGIN:STANDARD", "DTSTART:19700101T000000", "TZOFFSETFROM:" + off, "TZOFFSETTO:" + off, "END:STANDARD");
    } else {
      tr.slice(0, 2).forEach(function (t) {
        var kind = t.to > t.from ? "DAYLIGHT" : "STANDARD";
        // wall-clock time of the change, on the clock it changes FROM (e.g. 02:00)
        var w = new Date(t.at.getTime() + t.from * 60000);
        var wallStamp = w.getUTCFullYear() + pad(w.getUTCMonth() + 1) + pad(w.getUTCDate()) + "T" +
          pad(w.getUTCHours()) + pad(w.getUTCMinutes()) + "00";
        var wallDate = new Date(w.getUTCFullYear(), w.getUTCMonth(), w.getUTCDate());
        lines.push("BEGIN:" + kind, "DTSTART:" + wallStamp, "RRULE:" + yearlyRule(wallDate),
          "TZOFFSETFROM:" + fmtOffset(t.from), "TZOFFSETTO:" + fmtOffset(t.to), "END:" + kind);
      });
    }
    lines.push("END:VTIMEZONE");
    return lines;
  }

  /** Fold lines longer than 75 bytes, as RFC 5545 requires (Outlook is strict about this). */
  function fold(line) {
    var enc = new TextEncoder(), out = [], cur = "", limit = 75;
    Array.from(line).forEach(function (ch) {
      if (enc.encode(cur + ch).length > limit) { out.push(cur); cur = " " + ch; limit = 75; }
      else cur += ch;
    });
    out.push(cur);
    return out.join("\r\n");
  }

  var REMINDER_TITLE = "Daily Watch: today's session";
  function reminderDetails(pageUrl) {
    return "Your short list for today is ready. Open it here: " + pageUrl + "\nWatch, try the task, and you're done.";
  }

  /** iCalendar file with one repeating 15-minute event on the planned days, alerting at its start. */
  function buildIcs(plan, pageUrl, now) {
    now = now || new Date();
    var start = firstOccurrence(plan, now);
    var end = new Date(start.getTime() + 15 * 60000);
    var utc = now.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
    var uid = "dailywatch-" + utc + "-" + Math.random().toString(36).slice(2, 10) + "@dailywatch";
    var tzid = viewerZone();
    var at = tzid ? ";TZID=" + tzid + ":" : ":";
    return [
      "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Daily Watch//Reminder//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH"
    ].concat(tzid ? vtimezone(tzid, start.getFullYear()) : []).concat([
      "BEGIN:VEVENT",
      "UID:" + uid,
      "DTSTAMP:" + utc,
      "DTSTART" + at + stamp(start),
      "DTEND" + at + stamp(end),
      "RRULE:" + rrule(plan),
      "SUMMARY:" + icsText(REMINDER_TITLE),
      "DESCRIPTION:" + icsText(reminderDetails(pageUrl)),
      "URL:" + pageUrl,
      "BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:" + icsText(REMINDER_TITLE), "TRIGGER:PT0M", "END:VALARM",
      "END:VEVENT", "END:VCALENDAR"
    ]).map(fold).join("\r\n") + "\r\n";
  }

  function googleCalendarUrl(plan, pageUrl, now) {
    var start = firstOccurrence(plan, now || new Date());
    var end = new Date(start.getTime() + 15 * 60000);
    var q = new URLSearchParams({
      action: "TEMPLATE", text: REMINDER_TITLE, dates: stamp(start) + "/" + stamp(end),
      details: reminderDetails(pageUrl), recur: "RRULE:" + rrule(plan)
    });
    if (viewerZone()) q.set("ctz", viewerZone());
    return "https://calendar.google.com/calendar/render?" + q.toString();
  }

  function dayRange(plan) {
    var days = plannedDays(plan);
    if (days.length === 7) return "every day";
    return days.map(function (d) { return titleCase(d).slice(0, 3); }).join(", ");
  }

  /** The "Daily reminder" box for a visitor's session page. */
  function renderReminder(root, plan, pageUrl) {
    root.textContent = "";
    var time = h("select", { id: "reminder-time", "aria-label": "Reminder time" });
    for (var m = 5 * 60; m <= 22 * 60; m += 30) {
      var v = pad(Math.floor(m / 60)) + ":" + pad(m % 60);
      var label = new Date(2000, 0, 1, Math.floor(m / 60), m % 60).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
      time.appendChild(h("option", { value: v, text: label, selected: plan.reminderTime === v }));
    }
    var ics = h("button", { class: "btn btn--primary btn--sm", type: "button", text: "Apple / Outlook (.ics)" });
    var google = h("a", { class: "btn btn--outline btn--sm", target: "_blank", rel: "noopener noreferrer", text: "Google Calendar" });
    var when = h("p", { class: "reminder__when" });
    var help = h("div", { class: "reminder__help", hidden: "hidden" },
      h("p", { text: "Saved as daily-watch-reminder.ics. Open it to add the reminder." }),
      h("p", { text: "Outlook on the web: Calendar → Add calendar → Upload from file → choose the file." }),
      h("p", { text: "Outlook for Windows or Mac: double-click the file, then Save & Close." }));

    function refresh() {
      google.href = googleCalendarUrl(plan, pageUrl);
      when.textContent = "Repeats " + dayRange(plan) + ". If you change your days later, add it again.";
    }
    time.addEventListener("change", function () {
      plan.reminderTime = time.value;
      save(plan);
      refresh();
    });
    ics.addEventListener("click", function () {
      var blob = new Blob([buildIcs(plan, pageUrl)], { type: "text/calendar;charset=utf-8" });
      var a = h("a", { href: URL.createObjectURL(blob), download: "daily-watch-reminder.ics" });
      document.body.appendChild(a);
      a.click();
      setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
      help.hidden = false;
    });
    refresh();

    root.appendChild(h("div", { class: "box__head" },
      h("h2", { class: "box__title", text: "Daily reminder" })));
    root.appendChild(h("div", { class: "reminder__body" },
      h("p", { class: "reminder__lead", text: "Let today's list come to you. Add one repeating reminder to your calendar, and tap it to open this page." }),
      h("label", { class: "reminder__row" }, h("span", { text: "Remind me at" }), time),
      h("div", { class: "reminder__actions" }, ics, google),
      help,
      when));
    root.hidden = false;
  }

  window.DWPlanner = {
    DAYS: DAYS, load: load, save: save, clear: clear, normalize: normalize, parseHandle: parseHandle,
    toWeek: toWeek, pick: pick, buildToday: buildToday, render: render, EXAMPLE: EXAMPLE,
    buildIcs: buildIcs, googleCalendarUrl: googleCalendarUrl, renderReminder: renderReminder
  };
})();
