/*
 * Daily Watch — watch page.
 *
 * Data:   today.json (written by watch_agent.py each day). The link in the daily message
 *         also carries ?d=<date>&v=<id,id,…> so the page works even in the minute or two
 *         before GitHub Pages has deployed the new today.json.
 * Safety: all text from data is set with textContent (never innerHTML); video IDs must match
 *         YouTube's URL-safe alphabet before they're used anywhere.
 * Focus:  finite list, no autoplay into the next video, YouTube's end screen and pause
 *         suggestions are covered by our own overlays, and the session ends on a finish screen.
 */
(function () {
  "use strict";

  var ID_RE = /^[A-Za-z0-9_-]{1,32}$/;
  var DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];
  var MAX_VIDEOS = 10;
  var RETRY_MS = 20000;
  var RETRY_FOR_MS = 10 * 60 * 1000;
  var YT_STATE = { ENDED: 0, PLAYING: 1, PAUSED: 2 };
  var AVATAR_COLORS = ["#0b63ce", "#8250df", "#bf3989", "#cf222e", "#bc4c00", "#1a7f37", "#0a7ea4", "#6639ba"];

  var $ = function (id) { return document.getElementById(id); };
  var els = {
    main: $("main"), notice: $("notice"), dateChip: $("date-chip"), crumbDay: $("crumb-day"),
    title: $("session-title"), dayLabel: $("day-label"), meta: $("session-meta"),
    progress: $("progress"), progressBar: $("progress-bar"), progressFill: $("progress-fill"),
    progressLabel: $("progress-label"), progressPct: $("progress-pct"),
    weekCard: $("week-card"), weekList: $("week-list"), editPlan: $("edit-plan"),
    avatar: $("post-avatar"), nowChannel: $("now-channel"), nowPos: $("now-position"),
    nowBadge: $("now-badge"), nowTitle: $("now-title"), nowNote: $("now-note"),
    skip: $("skip"), skipLabel: $("skip-label"), task: $("task"), taskText: $("task-text"),
    finishTask: $("finish-task"), finishTaskText: $("finish-task-text"),
    frame: $("frame"), pause: $("pause-overlay"), nextOverlay: $("next-overlay"),
    nextTitle: $("next-title"), nextPlay: $("next-play"), nextFinish: $("next-finish"),
    blocked: $("blocked-overlay"), blockedOpen: $("blocked-open"), blockedDone: $("blocked-done"),
    finish: $("finish"), finishTitle: $("finish-title"), finishSummary: $("finish-summary"),
    finishPrompt: $("finish-prompt"), finishNext: $("finish-next"), rewatch: $("rewatch"),
    stateOverlay: $("state-overlay"), stateSpinner: $("state-spinner"),
    stateTitle: $("state-title"), stateText: $("state-text"),
    info: $("player-info"), prev: $("prev"), next: $("next"), mark: $("mark"), markLabel: $("mark-label"),
    queue: $("queue"), queueList: $("queue-list"), queueCount: $("queue-count"),
    reflect: $("reflect"), reflectText: $("reflect-text"), themeToggle: $("theme-toggle")
  };

  var session = null;      // { date, weekday, theme, prompt, send_time, week, videos: [...] }
  var current = 0;
  var watched = new Set();
  var skipped = new Set();
  var player = null;
  var playerReady = false;
  var pendingId = null;

  // ── small helpers ─────────────────────────────────────────────────────────

  function h(tag, attrs) {
    var el = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      if (k === "text") el.textContent = attrs[k];
      else if (k === "class") el.className = attrs[k];
      else el.setAttribute(k, attrs[k]);
    });
    for (var i = 2; i < arguments.length; i++) if (arguments[i]) el.appendChild(arguments[i]);
    return el;
  }

  function svgIcon(path, cls) {
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("class", cls || "icon");
    svg.setAttribute("aria-hidden", "true");
    var p = document.createElementNS(ns, "path");
    p.setAttribute("d", path);
    svg.appendChild(p);
    return svg;
  }
  var ICON_CHECK = "M5 12.5l4.5 4.5L19 7.5";
  var ICON_CLOCK = "M12 7v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z";
  var ICON_PLAY = "M10 8.5v7l5.5-3.5z M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z";
  var ICON_CAL = "M7 3v3M17 3v3M4 9h16M5 5h14a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z";
  var ICON_MOON = "M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z";
  var ICON_X = "M6 6l12 12M18 6L6 18";

  function fmtMinutes(m) {
    m = Math.round(m || 0);
    if (m < 60) return Math.max(m, 1) + " min";
    return Math.floor(m / 60) + "h " + String(m % 60).padStart(2, "0") + "m";
  }
  function fmtClock(m) {
    var s = Math.round((m || 0) * 60), hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = s % 60;
    return (hh ? hh + ":" + String(mm).padStart(2, "0") : mm) + ":" + String(ss).padStart(2, "0");
  }
  function localToday() {
    var d = new Date();
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }
  function prettyDate(iso, opts) {
    var p = (iso || "").split("-");
    if (p.length !== 3) return iso || "";
    return new Date(+p[0], +p[1] - 1, +p[2]).toLocaleDateString(undefined, opts || { weekday: "long", month: "short", day: "numeric" });
  }
  function weekdayOf(iso) {
    var p = (iso || "").split("-");
    if (p.length !== 3) return "";
    return DAYS[(new Date(+p[0], +p[1] - 1, +p[2]).getDay() + 6) % 7];
  }
  function titleCase(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : ""; }
  function cap(s, n) { s = String(s == null ? "" : s); return s.length > n ? s.slice(0, n - 1) + "…" : s; }

  function initials(name) {
    var words = String(name || "").replace(/[^\p{L}\p{N} ]/gu, " ").trim().split(/\s+/).filter(Boolean);
    if (!words.length) return "▶";
    return (words[0][0] + (words.length > 1 ? words[words.length - 1][0] : "")).toUpperCase();
  }
  function colorFor(name) {
    var hsh = 0, s = String(name || "");
    for (var i = 0; i < s.length; i++) hsh = (hsh * 31 + s.charCodeAt(i)) >>> 0;
    return AVATAR_COLORS[hsh % AVATAR_COLORS.length];
  }

  var store = {
    get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode: fine */ } }
  };
  function loadSet(kind) {
    try { return new Set(JSON.parse(store.get("dw:" + kind + ":" + session.date) || "[]")); } catch (e) { return new Set(); }
  }
  function saveSets() {
    store.set("dw:watched:" + session.date, JSON.stringify(Array.from(watched)));
    store.set("dw:skipped:" + session.date, JSON.stringify(Array.from(skipped)));
  }
  function isDone(id) { return watched.has(id) || skipped.has(id); }

  // ── data ──────────────────────────────────────────────────────────────────

  function sanitize(raw) {
    if (!raw || typeof raw !== "object") return null;
    var clean = [];
    (Array.isArray(raw.videos) ? raw.videos : []).forEach(function (v) {
      if (!v || !ID_RE.test(v.id) || clean.length >= MAX_VIDEOS) return;
      clean.push({
        id: v.id,
        title: cap(v.title || "", 200),
        channel: cap(v.channel || "", 100),
        minutes: typeof v.minutes === "number" && v.minutes > 0 ? v.minutes : 0,
        embeddable: v.embeddable !== false,
        older: !!v.older,
        published: typeof v.published === "string" ? v.published.slice(0, 10) : "",
        note: cap(v.note || "", 200),
        latest: !!v.latest
      });
    });
    var week = [];
    (Array.isArray(raw.week) ? raw.week : []).forEach(function (d) {
      if (d && DAYS.indexOf(d.day) !== -1 && week.length < 7) week.push({ day: d.day, theme: cap(d.theme || "", 120), rest: !!d.rest });
    });
    return {
      date: /^\d{4}-\d{2}-\d{2}$/.test(raw.date || "") ? raw.date : "",
      weekday: DAYS.indexOf(raw.weekday) !== -1 ? raw.weekday : "",
      theme: cap(raw.theme || "", 120),
      prompt: cap(raw.prompt || "", 300),
      task: cap(raw.task || "", 200),
      send_time: /^\d{2}:\d{2}$/.test(raw.send_time || "") ? raw.send_time : "",
      week: week,
      videos: clean
    };
  }

  function fromUrl() {
    var q = new URLSearchParams(location.search);
    var d = q.get("d") || "";
    var ids = (q.get("v") || "").split(",").filter(function (x) { return ID_RE.test(x); }).slice(0, MAX_VIDEOS);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(d) || !ids.length) return null;
    return {
      date: d, weekday: weekdayOf(d), theme: "", prompt: "", task: "", send_time: "", week: [],
      videos: ids.map(function (id, i) {
        return { id: id, title: "Video " + (i + 1), channel: "", minutes: 0, embeddable: true, older: false, published: "", note: "", placeholder: true };
      })
    };
  }

  function fetchToday() {
    return fetch("today.json?t=" + Date.now(), { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(sanitize)
      .catch(function () { return null; });
  }

  // ── rendering ─────────────────────────────────────────────────────────────

  function showState(title, text, spinning) {
    els.stateOverlay.hidden = false;
    els.stateSpinner.hidden = !spinning;
    els.stateTitle.textContent = title;
    els.stateText.textContent = text || "";
  }

  function metaItem(icon, text) {
    return h("span", { class: "meta-item" }, svgIcon(icon), document.createTextNode(text));
  }

  function renderHeader() {
    var v = session.videos;
    var total = v.reduce(function (s, x) { return s + x.minutes; }, 0);
    var day = session.weekday || weekdayOf(session.date);
    els.title.textContent = session.theme || "Today's session";
    document.title = (session.theme ? session.theme + " · " : "") + "Daily Watch";
    els.crumbDay.textContent = titleCase(day) || "Today";
    var isToday = session.date === localToday();
    els.dayLabel.textContent = isToday ? "Today" : (session.date < localToday() ? "Past session" : titleCase(day));
    els.dayLabel.hidden = !session.date;
    els.dateChip.textContent = prettyDate(session.date, { month: "short", day: "numeric" });
    els.dateChip.hidden = !session.date;

    els.meta.textContent = "";
    if (session.date) els.meta.appendChild(metaItem(ICON_CAL, prettyDate(session.date, { weekday: "long", month: "long", day: "numeric" })));
    els.meta.appendChild(metaItem(ICON_PLAY, v.length + (v.length === 1 ? " video" : " videos")));
    if (total) els.meta.appendChild(metaItem(ICON_CLOCK, fmtMinutes(total) + " total"));

    if (session.prompt) { els.reflectText.textContent = session.prompt; els.reflect.hidden = false; }
    if (session.task) { els.taskText.textContent = session.task; els.task.hidden = false; }
    renderWeek();
  }

  function renderWeek() {
    if (!session.week || !session.week.length) { els.weekCard.hidden = true; return; }
    var today = session.weekday || weekdayOf(session.date);
    els.weekList.textContent = "";
    session.week.forEach(function (d) {
      var li = h("li", { class: "week__item" + (d.rest ? " week__item--rest" : "") },
        h("span", { class: "week__day", text: d.day.slice(0, 3) }),
        h("span", { class: "week__theme", text: d.rest ? "Rest day" : (d.theme || "—"), title: d.rest ? "Rest day" : d.theme }));
      if (d.day === today) li.setAttribute("aria-current", "date");
      els.weekList.appendChild(li);
    });
    // "Edit your plan": the in-browser planner for visitors, config.yaml in the repo for the owner
    var m = location.hostname.match(/^([A-Za-z0-9-]+)\.github\.io$/);
    var repo = location.pathname.split("/").filter(Boolean)[0];
    if (session.editHref) {
      els.editPlan.href = session.editHref;
      els.editPlan.removeAttribute("target");
      els.editPlan.hidden = false;
    } else if (m && repo && /^[A-Za-z0-9._-]+$/.test(repo)) {
      els.editPlan.href = "https://github.com/" + m[1] + "/" + repo + "/blob/main/config.yaml";
      els.editPlan.hidden = false;
    }
    els.weekCard.hidden = false;
  }

  function renderProgress() {
    var v = session.videos;
    var nWatched = v.filter(function (x) { return watched.has(x.id); }).length;
    var nSkipped = v.filter(function (x) { return skipped.has(x.id); }).length;
    var done = nWatched + nSkipped;
    var left = v.filter(function (x) { return !isDone(x.id); }).reduce(function (s, x) { return s + x.minutes; }, 0);
    var pct = v.length ? Math.round((done / v.length) * 100) : 0;
    els.progress.hidden = false;
    els.progressBar.setAttribute("aria-valuemax", String(v.length));
    els.progressBar.setAttribute("aria-valuenow", String(done));
    els.progressFill.style.width = pct + "%";
    els.progressPct.textContent = pct + "% complete";
    var skippedText = nSkipped ? " · " + nSkipped + " skipped" : "";
    els.progressLabel.textContent = done === v.length
      ? (nSkipped ? nWatched + " watched" + skippedText : "All " + v.length + " watched")
      : nWatched + " of " + v.length + " watched" + skippedText + (left ? " · " + fmtMinutes(left) + " left" : "");
    els.queueCount.textContent = done + "/" + v.length;
  }

  function renderQueue() {
    els.queueList.textContent = "";
    session.videos.forEach(function (v, i) {
      var isWatched = watched.has(v.id), isSkipped = skipped.has(v.id), isNow = i === current && els.finish.hidden;
      var thumb = h("span", { class: "thumb" },
        h("img", { src: "https://i.ytimg.com/vi/" + v.id + "/mqdefault.jpg", alt: "", loading: "lazy", width: "320", height: "180" }));
      if (v.minutes) thumb.appendChild(h("span", { class: "thumb__time", text: fmtClock(v.minutes) }));

      var tags = h("span", { class: "queue__tags" });
      if (isNow) tags.appendChild(h("span", { class: "label label--accent", text: "Now playing" }));
      if (v.older && v.published) tags.appendChild(h("span", { class: "label", text: "From " + prettyDate(v.published, { month: "short", year: "numeric" }) }));
      if (isSkipped) tags.appendChild(h("span", { class: "label", text: "Skipped" }));
      if (v.latest) tags.appendChild(h("span", { class: "label", text: "Latest upload" }));
      if (!v.embeddable) tags.appendChild(h("span", { class: "label", text: "Opens on YouTube" }));

      var text = h("span", { class: "queue__text" },
        h("span", { class: "queue__name", text: v.title }),
        v.channel ? h("span", { class: "queue__sub", text: v.channel }) : null,
        v.note && !isSkipped ? h("span", { class: "queue__note", text: v.note }) : null,
        tags.childNodes.length ? tags : null);

      var status = isWatched ? h("span", { class: "status status--done", title: "Watched" }, svgIcon(ICON_CHECK))
        : isSkipped ? h("span", { class: "status status--skipped", title: "Skipped" }, svgIcon(ICON_X))
        : h("span", { class: "status" + (isNow ? " status--now" : ""), title: isNow ? "Now playing" : "Not watched yet" });

      var btn = h("button", { class: "queue__btn", type: "button",
        "aria-label": (i + 1) + ". " + v.title + (isWatched ? " (watched)" : isSkipped ? " (skipped)" : "") }, thumb, text, status);
      btn.addEventListener("click", function () { select(i, true); });
      var li = h("li", { class: "queue__item" + (isWatched ? " queue__item--done" : "") + (isSkipped ? " queue__item--skipped" : "") }, btn);
      if (isNow) li.setAttribute("aria-current", "true");
      els.queueList.appendChild(li);
    });
    els.queue.hidden = false;
  }

  function renderNow() {
    var v = session.videos[current];
    var name = v.channel || "Video " + (current + 1);
    els.avatar.className = "avatar";
    els.avatar.textContent = initials(v.channel);
    els.avatar.style.background = colorFor(v.channel || v.id);
    els.nowChannel.textContent = name;
    els.nowPos.textContent = "Video " + (current + 1) + " of " + session.videos.length + (v.minutes ? " · " + fmtMinutes(v.minutes) : "");
    els.nowTitle.textContent = v.placeholder ? "" : v.title;
    els.nowNote.textContent = v.note || "";
    els.nowNote.hidden = !v.note;
    var isWatched = watched.has(v.id), isSkipped = skipped.has(v.id);
    if (isWatched) { els.nowBadge.textContent = "Watched"; els.nowBadge.className = "label label--success"; }
    else if (isSkipped) { els.nowBadge.textContent = "Skipped"; els.nowBadge.className = "label"; }
    else if (v.older && v.published) { els.nowBadge.textContent = "From " + prettyDate(v.published, { month: "short", year: "numeric" }); els.nowBadge.className = "label"; }
    els.nowBadge.hidden = !(isWatched || isSkipped || (v.older && v.published));
    els.info.hidden = false;
    els.mark.setAttribute("aria-pressed", String(isWatched));
    els.markLabel.textContent = isWatched ? "Watched" : "Mark watched";
    els.skip.setAttribute("aria-pressed", String(isSkipped));
    els.skipLabel.textContent = isSkipped ? "Skipped" : "Skip";
    els.prev.disabled = current === 0;
    els.next.disabled = current === session.videos.length - 1;
  }

  function hideOverlays() {
    [els.pause, els.nextOverlay, els.blocked, els.finish, els.stateOverlay].forEach(function (o) { o.hidden = true; });
    els.frame.classList.remove("is-finished", "is-panel");
  }

  function renderAll() { renderProgress(); renderQueue(); if (els.finish.hidden) renderNow(); }

  // ── player ────────────────────────────────────────────────────────────────

  function loadYouTubeApi() {
    window.onYouTubeIframeAPIReady = createPlayer;
    var s = document.createElement("script");
    s.src = "https://www.youtube.com/iframe_api";
    s.async = true;
    s.onerror = function () {
      showState("The player couldn't load", "Something is blocking YouTube on this network or browser. Use the list to open videos on YouTube.", false);
      session.videos.forEach(function (v) { v.embeddable = false; });
      renderQueue();
    };
    document.head.appendChild(s);
  }

  function createPlayer() {
    var first = session.videos[current];
    player = new window.YT.Player("player", {
      host: "https://www.youtube-nocookie.com",
      videoId: first.embeddable ? first.id : undefined,
      playerVars: { rel: 0, playsinline: 1, iv_load_policy: 3, modestbranding: 1, autoplay: 0, origin: location.origin },
      events: {
        onReady: function () {
          playerReady = true;
          if (pendingId) { player.loadVideoById(pendingId); pendingId = null; }
        },
        onStateChange: onPlayerState,
        onError: function (e) {
          // 100 removed/private · 101/150 embedding disabled
          var v = session.videos[current];
          if (e && (e.data === 101 || e.data === 150 || e.data === 100)) {
            v.embeddable = false;
            showBlocked(v, e.data === 100);
            renderQueue();
          }
        }
      }
    });
  }

  function onPlayerState(e) {
    var v = session.videos[current];
    if (e.data === YT_STATE.PLAYING) {
      els.pause.hidden = true;
      if (v.placeholder && player.getVideoData) {   // fill in titles when today.json is late
        var d = player.getVideoData();
        if (d && d.title) { v.title = cap(d.title, 200); v.channel = cap(d.author || "", 100); v.placeholder = false; renderNow(); renderQueue(); }
      }
    } else if (e.data === YT_STATE.PAUSED) {
      els.pause.hidden = false;
    } else if (e.data === YT_STATE.ENDED) {
      els.pause.hidden = true;
      markWatched(v.id, true);
      afterVideo();
    }
  }

  function nextUnwatched(from) {
    var n = session.videos.length;
    for (var k = 1; k <= n; k++) {
      var i = (from + k) % n;
      if (!isDone(session.videos[i].id)) return i;
    }
    return -1;
  }

  function afterVideo() {
    var i = nextUnwatched(current);
    if (i === -1) return showFinish();
    els.nextTitle.textContent = session.videos[i].title;
    els.nextOverlay.hidden = false;
    els.frame.classList.add("is-panel");
    els.nextPlay.onclick = function () { select(i, true); };
    els.nextPlay.focus({ preventScroll: true });
  }

  function showBlocked(v, removed) {
    hideOverlays();
    if (player && playerReady && player.stopVideo) player.stopVideo();
    els.blocked.querySelector(".panel__label").textContent = removed ? "This video isn't available any more" : "This one plays on YouTube only";
    els.blockedOpen.hidden = !!removed;
    els.blockedOpen.href = "https://www.youtube.com/watch?v=" + v.id;
    els.blockedDone.textContent = removed ? "Skip it" : "I watched it";
    els.blocked.hidden = false;
    els.frame.classList.add("is-panel");
  }

  function poster(v) {
    // Thumbnail behind the player so there's never a black box while YouTube loads
    els.frame.style.backgroundImage = 'url("https://i.ytimg.com/vi/' + v.id + '/hqdefault.jpg")';
  }

  function select(i, play) {
    if (!session || i < 0 || i >= session.videos.length) return;
    current = i;
    hideOverlays();
    poster(session.videos[i]);
    history.replaceState(null, "", location.pathname + location.search + "#" + (i + 1));
    var v = session.videos[i];
    if (!v.embeddable) showBlocked(v, false);
    else if (player && playerReady) { play ? player.loadVideoById(v.id) : player.cueVideoById(v.id); }
    else if (play) pendingId = v.id;
    els.reflect.hidden = !session.prompt;
    els.task.hidden = !session.task;
    renderAll();
    if (play && window.matchMedia("(max-width: 899px)").matches) $("post").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function markWatched(id, value) {
    if (value) { watched.add(id); skipped.delete(id); } else watched.delete(id);
    saveSets();
    renderAll();
  }

  function markSkipped(id, value) {
    if (value) { skipped.add(id); watched.delete(id); } else skipped.delete(id);
    saveSets();
    renderAll();
  }

  function skipCurrent() {
    var id = session.videos[current].id, now = !skipped.has(id);
    markSkipped(id, now);
    if (!now) return;
    if (player && playerReady && player.pauseVideo) player.pauseVideo();
    var i = nextUnwatched(current);
    if (i === -1) showFinish(); else select(i, false);   // cue the next one; you press play
  }

  function showFinish() {
    hideOverlays();
    if (player && playerReady && player.stopVideo) player.stopVideo();
    var v = session.videos, done = v.filter(function (x) { return watched.has(x.id); });
    var nSkipped = v.filter(function (x) { return skipped.has(x.id); }).length;
    var mins = done.reduce(function (s, x) { return s + x.minutes; }, 0);
    els.finishSummary.textContent = (done.length
      ? "You watched " + done.length + " of " + v.length + (mins ? " · " + fmtMinutes(mins) : "")
      : "No videos watched this time, and that's fine")
      + (nSkipped ? " (" + nSkipped + " skipped)" : "") + ". Nothing else to see here.";
    els.finishTask.hidden = !session.task;
    els.finishTaskText.textContent = session.task || "";
    els.finishPrompt.hidden = !session.prompt;
    els.finishPrompt.textContent = session.prompt || "";
    els.finishNext.textContent = "Tomorrow's session arrives " + (session.send_time ? "around " + session.send_time : "tomorrow") + ". You can close this tab.";
    els.info.hidden = true;
    els.nowTitle.textContent = "";
    els.nowNote.hidden = true;
    els.avatar.className = "avatar";
    els.avatar.style.background = "var(--success-emph)";
    els.avatar.textContent = "✓";
    els.nowChannel.textContent = "Session complete";
    els.nowPos.textContent = prettyDate(session.date);
    els.nowBadge.hidden = true;
    els.reflect.hidden = true;
    els.task.hidden = true;
    els.finish.hidden = false;
    els.frame.classList.add("is-finished");
    els.frame.style.backgroundImage = "none";
    renderProgress(); renderQueue();
    els.finishTitle.focus({ preventScroll: true });
  }

  // ── events ────────────────────────────────────────────────────────────────

  els.pause.addEventListener("click", function () { if (player && player.playVideo) player.playVideo(); });
  els.nextFinish.addEventListener("click", showFinish);
  els.blockedDone.addEventListener("click", function () {
    markWatched(session.videos[current].id, true);
    afterVideo();
  });
  els.rewatch.addEventListener("click", function () { select(current, false); });
  els.prev.addEventListener("click", function () { select(current - 1, true); });
  els.next.addEventListener("click", function () { select(current + 1, true); });
  els.mark.addEventListener("click", function () {
    var id = session.videos[current].id, now = !watched.has(id);
    markWatched(id, now);
    if (now && nextUnwatched(current) === -1) showFinish();
  });
  els.skip.addEventListener("click", skipCurrent);
  document.addEventListener("keydown", function (e) {
    if (!session || !session.videos.length || e.metaKey || e.ctrlKey || e.altKey) return;
    var tag = (e.target && e.target.tagName) || "";
    if (tag === "INPUT" || tag === "TEXTAREA") return;
    var k = e.key.toLowerCase();
    if (k === "n") select(current + 1, true);
    else if (k === "p") select(current - 1, true);
    else if (k === "m" && els.finish.hidden) els.mark.click();
    else if (k === "s" && els.finish.hidden) skipCurrent();
  });

  els.themeToggle.addEventListener("click", function () {
    var root = document.documentElement;
    var dark = root.dataset.theme ? root.dataset.theme === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    store.set("dw:theme", root.dataset.theme);
  });

  // ── boot ──────────────────────────────────────────────────────────────────

  function notice(text) { els.notice.textContent = text; els.notice.hidden = !text; }

  function start(s) {
    session = s;
    watched = loadSet("watched");
    skipped = loadSet("skipped");
    var fromHash = parseInt((location.hash || "").slice(1), 10) - 1;
    current = fromHash >= 0 && fromHash < s.videos.length ? fromHash : Math.max(0, nextUnwatched(-1));
    els.main.setAttribute("aria-busy", "false");
    renderHeader();
    hideOverlays();
    poster(s.videos[current]);
    renderAll();
    if ((watched.size || skipped.size) && nextUnwatched(-1) === -1 && !(fromHash >= 0)) showFinish();
    else if (!s.videos[current].embeddable) showBlocked(s.videos[current], false);
    loadYouTubeApi();
    if (s.date && s.date < localToday()) notice("This is the session from " + prettyDate(s.date) + ". Today's arrives " + (s.send_time ? "around " + s.send_time : "later today") + ".");
  }

  function emptyState(s, title, text) {
    els.main.setAttribute("aria-busy", "false");
    session = s && s.date ? s : { date: "", weekday: "", theme: "", prompt: "", week: (s && s.week) || [], videos: [] };
    els.title.textContent = s && s.theme ? s.theme : "No session today";
    els.crumbDay.textContent = s && s.weekday ? titleCase(s.weekday) : "Today";
    els.meta.textContent = "";
    els.avatar.className = "avatar";
    els.avatar.style.background = "var(--accent)";
    els.avatar.textContent = "☾";
    els.nowChannel.textContent = "Daily Watch";
    els.nowNote.hidden = true;
    els.nowPos.textContent = "Nothing scheduled";
    renderWeek();
    showState(title || (s && s.theme ? "Nothing new on this theme today" : "No session right now"),
      text || "Enjoy the time back. The next session arrives with your next daily message.", false);
    els.frame.classList.add("is-empty");
    els.stateOverlay.firstElementChild.insertBefore(svgIcon(ICON_MOON, "icon state__icon"), els.stateTitle);
  }

  function hydrateLater(urlSession) {
    var started = Date.now();
    (function retry() {
      if (Date.now() - started > RETRY_FOR_MS) return;
      setTimeout(function () {
        fetchToday().then(function (d) {
          if (d && d.date === urlSession.date) {
            var byId = {};
            d.videos.forEach(function (v) { byId[v.id] = v; });
            urlSession.videos.forEach(function (v) {
              var m = byId[v.id];
              if (m) { Object.assign(v, m); v.placeholder = false; }
            });
            ["theme", "prompt", "task", "send_time", "weekday", "week"].forEach(function (k) { urlSession[k] = d[k]; });
            notice("");
            renderHeader(); renderAll();
          } else retry();
        });
      }, RETRY_MS);
    })();
  }

  // ── visitor mode: plan in the browser ────────────────────────────────────

  var CONFIG = window.DW_CONFIG || {};
  var HELPER = (/^https:\/\/[A-Za-z0-9.-]+(:\d+)?(\/[A-Za-z0-9._~\/-]*)?$/.test(CONFIG.helperUrl || "") ||
                /^http:\/\/(localhost|127\.0\.0\.1):\d+$/.test(CONFIG.helperUrl || ""))
    ? CONFIG.helperUrl.replace(/\/+$/, "") : "";
  var P = window.DWPlanner;

  function resetHashAndReload() {
    history.replaceState(null, "", location.pathname);
    location.reload();
  }

  function showPlanner() {
    document.querySelector(".pagehead").hidden = true;
    document.querySelector(".notice-wrap").hidden = true;
    els.main.hidden = true;
    var root = $("planner");
    root.hidden = false;
    els.crumbDay.textContent = "Plan";
    document.title = "Plan your week · Daily Watch";
    P.render(root, { plan: P.load(), onSave: resetHashAndReload, onCancel: resetHashAndReload });
    window.scrollTo(0, 0);
  }

  function startVisitor() {
    var today = localToday();
    els.crumbDay.textContent = titleCase(weekdayOf(today));
    try { P.renderReminder($("reminder"), P.load(), location.origin + location.pathname); } catch (e) { /* optional */ }
    P.buildToday(P.load(), today, weekdayOf(today), HELPER).then(function (s) {
      if (s.rest) {
        emptyState(s, "Rest day", "No session today. Enjoy the time back, and come back tomorrow.");
        els.title.textContent = "Rest day";
        return;
      }
      if (!s.videos.length) {
        emptyState(s, "Nothing to watch today", "Nothing from your creators fits this theme and your daily time limit. Try adding a creator, or raise the limit in Edit your plan.");
        return;
      }
      start(s);
      if (s.notices && s.notices.length) notice(s.notices.join(" "));
    }).catch(function (err) {
      els.main.setAttribute("aria-busy", "false");
      els.title.textContent = "Couldn't load today's videos";
      showState("Couldn't load today's videos", err.message, false);
      var retry = h("button", { class: "btn btn--primary", type: "button", text: "Try again" });
      retry.addEventListener("click", function () { location.reload(); });
      els.stateOverlay.firstElementChild.appendChild(retry);
    });
  }

  if (HELPER && P) {
    $("footer-plan").hidden = false;
    window.addEventListener("hashchange", function () {
      if (location.hash === "#plan" || (location.hash === "" && !$("planner").hidden)) location.reload();
    });
  }

  var ownerLink = fromUrl();
  if (!ownerLink && HELPER && P) {
    if (location.hash === "#plan" || !P.load()) showPlanner(); else startVisitor();
  } else fetchToday().then(function (data) {
    var url = ownerLink;
    if (url && (!data || data.date !== url.date)) {
      // Link is newer than the deployed today.json (GitHub Pages lags a minute or two).
      notice("Today's details are still publishing. Titles fill in shortly, and you can start watching now.");
      if (data && data.week && data.week.length) url.week = data.week;
      start(url);
      hydrateLater(url);
    } else if (data && data.videos.length) {
      start(data);
    } else {
      emptyState(data);
    }
  });
})();
