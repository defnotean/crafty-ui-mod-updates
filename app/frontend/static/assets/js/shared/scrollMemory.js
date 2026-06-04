/*
 * Per-page scroll memory. Server-detail tabs (Terminal, Logs, Files, Mods,
 * Config, …) are full page loads, so switching tabs normally snaps you to the
 * top. This remembers each page/tab's scroll position (keyed by URL) and
 * restores it on return, instead of jumping to the top.
 */
(function () {
  if (!("sessionStorage" in window)) return;
  if ("scrollRestoration" in history) {
    try { history.scrollRestoration = "manual"; } catch (e) { /* ignore */ }
  }

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
    var y = targetY();
    if (y === null || y <= 0) return;

    var aborted = false;
    function onUser() { aborted = true; teardown(); }
    function teardown() {
      window.removeEventListener("wheel", onUser);
      window.removeEventListener("touchmove", onUser);
      window.removeEventListener("keydown", onUser, true);
      window.removeEventListener("mousedown", onUser, true);
    }
    window.addEventListener("wheel", onUser, { passive: true });
    window.addEventListener("touchmove", onUser, { passive: true });
    window.addEventListener("keydown", onUser, true);
    window.addEventListener("mousedown", onUser, true);

    // Content (tables, etc.) may still be laying out, so retry briefly until the
    // page is tall enough to reach the saved position — unless the user scrolls.
    var tries = 0;
    (function attempt() {
      if (aborted) return;
      var maxY = (document.documentElement.scrollHeight || document.body.scrollHeight || 0) - window.innerHeight;
      window.scrollTo(0, Math.max(0, Math.min(y, maxY)));
      tries++;
      if (tries < 15 && Math.abs(currentY() - y) > 2 && maxY < y) {
        setTimeout(attempt, 70);
      } else {
        setTimeout(teardown, 200);
      }
    })();
  }

  window.addEventListener("pagehide", save);
  window.addEventListener("beforeunload", save);
  // capture clicks on tab/nav links so we record the position before navigating
  document.addEventListener("click", function (e) {
    if (e.target && e.target.closest && e.target.closest('a[href]')) save();
  }, true);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", restore);
  } else {
    restore();
  }
  window.addEventListener("load", restore);
})();
