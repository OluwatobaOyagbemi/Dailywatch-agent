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
  var MAX_VIDEOS = 10;
  var RETRY_MS = 20000;
  var RETRY_FOR_MS = 10 * 60 * 1000;
  var YT_STATE = { ENDED: 0, PLAYING: 1, PAUSED: 2 };

  var $ = function (id) { return document.getElementById(id); };
  var els = {
    main: $("main"), notice: $("notice"), dateChip: $("date-chip"),
    eyebrow: $("session-eyebrow"), title: $("session-title"), meta: $("session-meta"),
    progress: $("progress"), progressBar: $("progress-bar"), progressFill: $("progress-fill"),
    progressLabel: $("progress-label"),
    frame: $("frame"), pause: $("pause-overlay"), nextOverlay: $("next-overlay"),
    nextTitle: $("next-title"), nextPlay: $("next-play"), nextFinish: $("next-finish"),
    blocked: $("blocked-overlay"), blockedOpen: $("blocked-open"), blockedDone: $("blocked-done"),
    finish: $("finish"), finishTitle: $("finish-title"), finishSummary: $("finish-summary"),
    finishPrompt: $("finish-prompt"), finishNext: $("finish-next"), rewatch: $("rewatch"),
    stateOverlay: $("state-overlay"), stateSpinner: $("state-spinner"),
    stateTitle: $("state-title"), stateText: $("state-text"),
    info: $("player-info"), nowPos: $("now-position"), nowTitle: $("now-title"), nowChannel: $("now-channel"),
    prev: $("prev"), next: $("next"), mark: $("mark"), markLabel: $("mark-label"),
    queue: $("queue"), queueList: $("queue-list"), queueCount: $("queue-count"),
    reflect: $("reflect"), reflectText: $("reflect-text"), themeToggle: $("theme-toggle")
  };

  var session = null;      // { date, weekday, theme, prompt, send_time, videos: [...] }
  var current = 0;
  var watched = new Set();
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

  function svgIcon(path) {
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    var p = document.createElementNS(ns, "path");
    p.setAttribute("d", path);
    svg.appendChild(p);
    return svg;
  }
  var ICON_CHECK = "M5 12.5l4.5 4.5L19 7.5";
  var ICON_CLOCK = "M12 7v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z";
  var ICON_PLAY = "M10 8.5v7l5.5-3.5z M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z";

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
  function cap(s, n) { s = String(s == null ? "" : s); return s.length > n ? s.slice(0, n - 1) + "…" : s; }

  var store = {
    get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode: fine */ } }
  };
  function loadWatched() {
    try { return new Set(JSON.parse(store.get("dw:watched:" + session.date) || "[]")); } catch (e) { return new Set(); }
  }
  function saveWatched() { store.set("dw:watched:" + session.date, JSON.stringify(Array.from(watched))); }

  // ── data ──────────────────────────────────────────────────────────────────

  function sanitize(raw) {
    if (!raw || typeof raw !== "object") return null;
    var vids = Array.isArray(raw.videos) ? raw.videos : [];
    var clean = [];
    vids.forEach(function (v) {
      if (!v || !ID_RE.test(v.id) || clean.length >= MAX_VIDEOS) return;
      clean.push({
        id: v.id,
        title: cap(v.title || "", 200),
        channel: cap(v.channel || "", 100),
        minutes: typeof v.minutes === "number" && v.minutes > 0 ? v.minutes : 0,
        embeddable: v.embeddable !== false,
        older: !!v.older,
        published: typeof v.published === "string" ? v.published.slice(0, 10) : ""
      });
    });
    return {
      date: /^\d{4}-\d{2}-\d{2}$/.test(raw.date || "") ? raw.date : "",
      weekday: cap(raw.weekday || "", 20),
      theme: cap(raw.theme || "", 120),
      prompt: cap(raw.prompt || "", 300),
      send_time: cap(raw.send_time || "", 20),
      videos: clean
    };
  }

  function fromUrl() {
    var q = new URLSearchParams(location.search);
    var d = q.get("d") || "";
    var ids = (q.get("v") || "").split(",").filter(function (x) { return ID_RE.test(x); }).slice(0, MAX_VIDEOS);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(d) || !ids.length) return null;
    return {
      date: d, weekday: "", theme: "", prompt: "", send_time: "",
      videos: ids.map(function (id, i) {
        return { id: id, title: "Video " + (i + 1), channel: "", minutes: 0, embeddable: true, older: false, published: "", placeholder: true };
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

  function renderHeader() {
    var v = session.videos;
    var total = v.reduce(function (s, x) { return s + x.minutes; }, 0);
    els.eyebrow.textContent = prettyDate(session.date, { weekday: "long", month: "long", day: "numeric" });
    els.title.textContent = session.theme || "Today's session";
    document.title = (session.theme ? session.theme + " · " : "") + "Daily Watch";
    els.dateChip.textContent = prettyDate(session.date, { month: "short", day: "numeric" });
    els.dateChip.hidden = false;

    els.meta.textContent = "";
    els.meta.appendChild(h("span", { class: "meta-item" }, svgIcon(ICON_PLAY),
      document.createTextNode(v.length + (v.length === 1 ? " video" : " videos"))));
    if (total) els.meta.appendChild(h("span", { class: "meta-item" }, svgIcon(ICON_CLOCK),
      document.createTextNode(fmtMinutes(total) + " total")));

    if (session.prompt) { els.reflectText.textContent = session.prompt; els.reflect.hidden = false; }
  }

  function renderProgress() {
    var v = session.videos, done = v.filter(function (x) { return watched.has(x.id); });
    var left = v.filter(function (x) { return !watched.has(x.id); }).reduce(function (s, x) { return s + x.minutes; }, 0);
    els.progress.hidden = false;
    els.progressBar.setAttribute("aria-valuemax", String(v.length));
    els.progressBar.setAttribute("aria-valuenow", String(done.length));
    els.progressFill.style.width = (v.length ? (done.length / v.length) * 100 : 0) + "%";
    els.progressLabel.textContent = done.length === v.length
      ? "All " + v.length + " watched. You're done for today."
      : done.length + " of " + v.length + " watched" + (left ? " · about " + fmtMinutes(left) + " left" : "");
    els.queueCount.textContent = done.length + "/" + v.length;
  }

  function renderQueue() {
    els.queueList.textContent = "";
    session.videos.forEach(function (v, i) {
      var isDone = watched.has(v.id), isNow = i === current && els.finish.hidden;
      var thumb = h("span", { class: "thumb" },
        h("img", { src: "https://i.ytimg.com/vi/" + v.id + "/mqdefault.jpg", alt: "", loading: "lazy", width: "320", height: "180" }));
      if (v.minutes) thumb.appendChild(h("span", { class: "thumb__time", text: fmtClock(v.minutes) }));
      if (isDone) thumb.appendChild(h("span", { class: "thumb__state" }, svgIcon(ICON_CHECK)));
      if (isNow) thumb.appendChild(h("span", { class: "thumb__now", text: "NOW" }));

      var badge = isDone ? h("span", { class: "queue__badge queue__badge--done", text: "Watched" })
        : !v.embeddable ? h("span", { class: "queue__badge", text: "Opens on YouTube" })
        : v.older && v.published ? h("span", { class: "queue__badge", text: "From " + prettyDate(v.published, { month: "short", year: "numeric" }) })
        : null;
      var text = h("span", { class: "queue__text" },
        h("span", { class: "queue__name", text: v.title }),
        v.channel ? h("span", { class: "queue__sub", text: v.channel }) : null,
        badge);

      var btn = h("button", { class: "queue__btn", type: "button",
        "aria-label": (i + 1) + ". " + v.title + (isDone ? " (watched)" : "") }, thumb, text);
      btn.addEventListener("click", function () { select(i, true); });
      var li = h("li", { class: "queue__item" + (isDone ? " queue__item--done" : "") }, btn);
      if (isNow) li.setAttribute("aria-current", "true");
      els.queueList.appendChild(li);
    });
    els.queue.hidden = false;
  }

  function renderNow() {
    var v = session.videos[current];
    els.info.hidden = false;
    els.nowPos.textContent = "Video " + (current + 1) + " of " + session.videos.length + (v.minutes ? " · " + fmtMinutes(v.minutes) : "");
    els.nowTitle.textContent = v.title;
    els.nowChannel.textContent = "";
    if (v.channel) els.nowChannel.appendChild(h("span", { text: v.channel }));
    if (v.older && v.published) els.nowChannel.appendChild(h("span", { class: "queue__badge",
      text: "From " + prettyDate(v.published, { month: "short", year: "numeric" }) }));
    var isDone = watched.has(v.id);
    els.mark.setAttribute("aria-pressed", String(isDone));
    els.markLabel.textContent = isDone ? "Watched" : "Mark watched";
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
      if (!watched.has(session.videos[i].id)) return i;
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
    renderAll();
    if (play && window.matchMedia("(max-width: 1023px)").matches) els.frame.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function markWatched(id, value) {
    if (value) watched.add(id); else watched.delete(id);
    saveWatched();
    renderAll();
  }

  function showFinish() {
    hideOverlays();
    if (player && playerReady && player.stopVideo) player.stopVideo();
    var v = session.videos, done = v.filter(function (x) { return watched.has(x.id); });
    var mins = done.reduce(function (s, x) { return s + x.minutes; }, 0);
    els.finishSummary.textContent = done.length
      ? "You watched " + done.length + " of " + v.length + (mins ? " · " + fmtMinutes(mins) : "") + ". Nothing else to see here."
      : "No videos watched this time — that's fine. Nothing else to see here.";
    els.finishPrompt.hidden = !session.prompt;
    els.finishPrompt.textContent = session.prompt ? "💭 " + session.prompt : "";
    els.finishNext.textContent = "Tomorrow's session arrives " + (session.send_time ? "around " + session.send_time : "tomorrow") + ". You can close this tab.";
    els.info.hidden = true;
    els.reflect.hidden = true;
    els.finish.hidden = false;
    els.frame.classList.add("is-finished");
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
  els.rewatch.addEventListener("click", function () { els.reflect.hidden = !session.prompt; select(current, false); });
  els.prev.addEventListener("click", function () { select(current - 1, true); });
  els.next.addEventListener("click", function () { select(current + 1, true); });
  els.mark.addEventListener("click", function () {
    var id = session.videos[current].id, now = !watched.has(id);
    markWatched(id, now);
    if (now && nextUnwatched(current) === -1) showFinish();
  });
  document.addEventListener("keydown", function (e) {
    if (!session || !session.videos.length || e.metaKey || e.ctrlKey || e.altKey) return;
    var tag = (e.target && e.target.tagName) || "";
    if (tag === "INPUT" || tag === "TEXTAREA") return;
    var k = e.key.toLowerCase();
    if (k === "n") select(current + 1, true);
    else if (k === "p") select(current - 1, true);
    else if (k === "m") els.mark.click();
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
    watched = loadWatched();
    var fromHash = parseInt((location.hash || "").slice(1), 10) - 1;
    current = fromHash >= 0 && fromHash < s.videos.length ? fromHash : Math.max(0, nextUnwatched(-1));
    els.main.setAttribute("aria-busy", "false");
    renderHeader();
    hideOverlays();
    poster(s.videos[current]);
    renderAll();
    if (watched.size && nextUnwatched(-1) === -1 && !(fromHash >= 0)) showFinish();
    else if (!s.videos[current].embeddable) showBlocked(s.videos[current], false);
    loadYouTubeApi();
    var today = localToday();
    if (s.date && s.date < today) notice("This is the session from " + prettyDate(s.date) + ". Today's arrives " + (s.send_time ? "around " + s.send_time : "later today") + ".");
  }

  function emptyState(s) {
    els.main.setAttribute("aria-busy", "false");
    els.eyebrow.textContent = s && s.date ? prettyDate(s.date, { weekday: "long", month: "long", day: "numeric" }) : "Daily Watch";
    els.title.textContent = s && s.theme ? s.theme : "No session today";
    els.meta.textContent = "";
    showState(s && s.theme ? "Nothing new on this theme today" : "No session right now",
      "Enjoy the time back. The next session arrives with your next daily message.", false);
    els.frame.classList.add("is-empty");
    var icon = svgIcon("M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z");
    icon.setAttribute("class", "icon state__icon");
    els.stateOverlay.firstElementChild.insertBefore(icon, els.stateTitle);
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
            urlSession.theme = d.theme; urlSession.prompt = d.prompt; urlSession.send_time = d.send_time;
            urlSession.weekday = d.weekday;
            notice("");
            renderHeader(); renderAll();
          } else retry();
        });
      }, RETRY_MS);
    })();
  }

  fetchToday().then(function (data) {
    var url = fromUrl();
    if (url && (!data || data.date !== url.date)) {
      // Link is newer than the deployed today.json (GitHub Pages lags a minute or two).
      notice("Today's details are still publishing — titles fill in shortly. You can start watching now.");
      start(url);
      hydrateLater(url);
    } else if (data && data.videos.length) {
      start(data);
    } else {
      emptyState(data);
    }
  });
})();
