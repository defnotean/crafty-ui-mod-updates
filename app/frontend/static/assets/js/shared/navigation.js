/* ============================================================================
 * Crafty fast navigation — client-side "tab switching" (pjax-style).
 *
 * Intercepts internal link clicks, fetches the target, swaps only the
 * .main-panel content (the navbar, sidebar and the live WebSocket stay alive),
 * re-runs the new page's scripts, and updates history — so switching feels
 * instant instead of a full reload.
 *
 * SAFETY:
 *  - Hard fallback: ANY problem → ordinary full navigation (nothing breaks).
 *  - Opt-out (full load) for live-update-heavy / fragile pages (dashboard,
 *    server detail/console, server wizard, file editor, metrics) so their
 *    page-scoped WebSocket + heavy widgets always init cleanly.
 *  - Handler-leak guard: trims page-registered WebSocket handlers back to the
 *    core set on each switch so they don't stack up.
 *  - Kill switch: localStorage.setItem('craftyPjax','off') disables it.
 * ========================================================================== */
(function () {
  "use strict";
  if (window.__craftyNav) return;
  window.__craftyNav = true;

  // Pages that must do a full reload (page-scoped WS / heavy init).
  var DENY = [
    "/panel/dashboard",
    "/panel/server_detail",
    "/panel/server_metrics",
    "/panel/server_term",
    "/server/",            // creation wizards (step1, bedrock, etc.)
    "/panel/edit_user_apikeys"
  ];

  var coreHandlers = null; // snapshot of base.html's WebSocket handler count

  function disabled() { try { return localStorage.getItem("craftyPjax") === "off"; } catch (e) { return false; } }

  function denied(path) {
    for (var i = 0; i < DENY.length; i++) if (path.indexOf(DENY[i]) === 0) return true;
    return false;
  }

  // ---- top loading bar ----
  var bar = document.createElement("div");
  bar.className = "pjax-bar";
  document.addEventListener("DOMContentLoaded", function () { document.body.appendChild(bar); });
  var barTimer = null;
  function barStart() { bar.style.opacity = "1"; bar.style.width = "8%"; var w = 8;
    barTimer = setInterval(function () { w += (90 - w) * 0.18; bar.style.width = w + "%"; }, 180); }
  function barDone() { clearInterval(barTimer); bar.style.width = "100%";
    setTimeout(function () { bar.style.opacity = "0"; bar.style.width = "0%"; }, 250); }

  function shouldIntercept(a, e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return false;
    if (!a || !a.href) return false;
    if (a.target && a.target !== "_self") return false;
    if (a.hasAttribute("download") || a.hasAttribute("data-no-pjax")) return false;
    if (a.getAttribute("data-toggle") || a.getAttribute("data-bs-toggle")) return false; // collapse/dropdown/tab/modal
    var rel = a.getAttribute("rel"); if (rel && rel.indexOf("external") !== -1) return false;
    var url;
    try { url = new URL(a.href, location.href); } catch (x) { return false; }
    if (url.origin !== location.origin) return false;
    var href = a.getAttribute("href") || "";
    if (href.charAt(0) === "#") return false;
    if (/^(mailto:|tel:|javascript:)/i.test(href)) return false;
    if (url.pathname.indexOf("/static/") === 0 || url.pathname.indexOf("/api/") === 0) return false;
    if (url.pathname === location.pathname && url.search === location.search && url.hash) return false;
    if (denied(url.pathname)) return false;
    return url;
  }

  function runScripts(container) {
    container.querySelectorAll("script").forEach(function (old) {
      var s = document.createElement("script");
      for (var i = 0; i < old.attributes.length; i++) s.setAttribute(old.attributes[i].name, old.attributes[i].value);
      if (old.src) s.src = old.src; else s.textContent = old.textContent;
      old.parentNode.replaceChild(s, old);
    });
  }

  function trimHandlers() {
    try {
      if (typeof listenEvents !== "undefined" && listenEvents) {
        if (coreHandlers === null) coreHandlers = listenEvents.length;
        else if (listenEvents.length > coreHandlers) listenEvents.length = coreHandlers;
      }
    } catch (e) {}
  }

  function setActiveNav(path) {
    try {
      document.querySelectorAll(".sidebar a.nav-link").forEach(function (a) {
        var href = a.getAttribute("href") || "";
        a.classList.toggle("active", href !== "#" && href.split("?")[0] === path);
      });
    } catch (e) {}
  }

  function fullLoad(url) { window.location.assign(typeof url === "string" ? url : url.href); }

  function navigate(url, push) {
    var current = document.querySelector(".main-panel");
    if (!current) return fullLoad(url);
    barStart();
    current.classList.add("pjax-leaving");

    fetch(url.href, { headers: { "X-Requested-With": "XMLHttpRequest", "X-Pjax": "true" }, credentials: "same-origin" })
      .then(function (res) {
        var ct = res.headers.get("content-type") || "";
        if (!res.ok || ct.indexOf("text/html") === -1) throw new Error("non-html");
        if (res.redirected && new URL(res.url).pathname !== url.pathname) { fullLoad(res.url); throw new Error("redirected"); }
        return res.text();
      })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        var fresh = doc.querySelector(".main-panel");
        if (!fresh) throw new Error("no main-panel");

        trimHandlers();                       // drop previous page's WS handlers
        if (push) history.pushState({ pjax: true }, "", url.href);
        document.title = doc.title || document.title;

        current.innerHTML = fresh.innerHTML;   // swap content (scripts inert here)
        runScripts(current);                   // execute the new page's scripts
        // Drop any modal backdrop / scroll-lock left behind by the previous page
        // so an invisible overlay can't trap clicks after navigating.
        try {
          document.querySelectorAll(".modal-backdrop").forEach(function (b) { b.remove(); });
          document.body.classList.remove("modal-open");
          document.body.style.removeProperty("overflow");
          document.body.style.removeProperty("padding-right");
        } catch (e) {}
        setActiveNav(url.pathname);

        try { if (window.jQuery) jQuery('[data-toggle="tooltip"]').tooltip(); } catch (e) {}
        try { if (window.CraftyCustomSelect) window.CraftyCustomSelect.enhanceAll(current); } catch (e) {}

        current.classList.remove("pjax-leaving");
        window.scrollTo(0, 0);
        barDone();
        document.dispatchEvent(new CustomEvent("crafty:navigated", { detail: { path: url.pathname } }));
      })
      .catch(function (err) {
        if (err && (err.message === "redirected")) return; // already handled
        current.classList.remove("pjax-leaving");
        barDone();
        fullLoad(url);
      });
  }

  document.addEventListener("click", function (e) {
    if (disabled()) return;
    var a = e.target.closest ? e.target.closest("a[href]") : null;
    if (!a) return;
    var url = shouldIntercept(a, e);
    if (!url) return;
    e.preventDefault();
    if (coreHandlers === null) trimHandlers(); // snapshot core handler count on first nav
    navigate(url, true);
  });

  window.addEventListener("popstate", function (e) {
    if (disabled()) return;
    if (denied(location.pathname)) return; // let it be a normal load
    navigate(new URL(location.href), false);
  });
})();
