/*
 * Per-page scroll memory. Server-detail tabs (Terminal, Logs, Files, Config, …)
 * are full page loads, so switching tabs normally snaps you to the top. This
 * remembers each page/tab's scroll position (keyed by URL) and restores it on
 * return.
 *
 * It NEVER fights the user: the instant they scroll/type, auto-restore is
 * disabled for this page view — so it can't yank you back or trap you while
 * you're reading down a long settings page.
 */
(function () {
  if (!("sessionStorage" in window)) return;
  if ("scrollRestoration" in history) {
    try { history.scrollRestoration = "manual"; } catch (e) { /* ignore */ }
  }

  // As soon as the user drives the scroll themselves (wheel, touch, keyboard, or
  // grabbing the scrollbar), give up restoring for this page view. Capture-phase
  // + early so we catch it before any restore attempt runs.
  var userDriving = false;
  function markDriving() { userDriving = true; }
  ["wheel", "touchmove", "keydown", "mousedown"].forEach(function (ev) {
    window.addEventListener(ev, markDriving, { passive: true, capture: true });
  });

  function key() { return "cspos:" + location.pathname + location.search; }
  function currentY() {
    return window.scrollY || document.documentElement.scrollTop || document.body.scrollTop || 0;
  }
  function save() {
    try { sessionStorage.setItem(key(), String(currentY())); } catch (e) { /* ignore */ }
  }
  function targetY() {
    try {
      var v = sessionStorage.getItem(key());
      return v === null ? null : (parseInt(v, 10) || 0);
    } catch (e) { return null; }
  }

  function restore() {
    if (userDriving) return;                 // user is already scrolling — leave them be
    var y = targetY();
    if (y === null || y <= 0) return;
    var tries = 0;
    (function attempt() {
      if (userDriving) return;               // bail the instant they touch the scroll
      var maxY = (document.documentElement.scrollHeight || document.body.scrollHeight || 0) - window.innerHeight;
      window.scrollTo(0, Math.max(0, Math.min(y, maxY)));
      // Retry briefly ONLY while the page is still too short to reach the saved
      // spot (content laying out). Bounded, so it can never loop forever.
      if (++tries < 12 && Math.abs(currentY() - y) > 2 && maxY < y) {
        setTimeout(attempt, 60);
      }
    })();
  }

  window.addEventListener("pagehide", save);
  window.addEventListener("beforeunload", save);
  // Record position before a tab/nav link takes us away.
  document.addEventListener("click", function (e) {
    if (e.target && e.target.closest && e.target.closest("a[href]")) save();
  }, true);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", restore);
  } else {
    restore();
  }
  // One late attempt for pages whose content arrives after DOMContentLoaded —
  // but guarded by userDriving, so it never yanks a user who has begun scrolling.
  window.addEventListener("load", restore);
})();
