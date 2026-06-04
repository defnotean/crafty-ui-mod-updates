/* ============================================================================
 * Right-click context menu for servers — works on the sidebar server list and
 * the dashboard table. Open / Start / Stop / Restart / Clone / Kill / Delete
 * (delete with confirm + optional on-disk file removal). Self-contained:
 * injects its own styles, depends only on bootbox + the existing /api/v2 routes.
 * ========================================================================== */
(function () {
  "use strict";

  var CSS =
    "#server-ctx{position:fixed;z-index:4000;min-width:200px;background:var(--dropdown-bg,#1b1b1b);" +
    "border:1px solid var(--outline,#444);border-radius:8px;box-shadow:0 16px 40px rgba(0,0,0,.5);" +
    "padding:6px;user-select:none;font-size:.875rem;}" +
    "#server-ctx .sc-head{padding:6px 10px 8px;font-weight:600;color:#eaf2e6;border-bottom:1px solid var(--outline,#444);" +
    "margin-bottom:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:240px;}" +
    "#server-ctx .sc-item{padding:8px 11px;border-radius:6px;cursor:pointer;color:var(--base-text,#ddd);" +
    "white-space:nowrap;display:flex;align-items:center;gap:9px;}" +
    "#server-ctx .sc-item:hover{background:rgba(76,175,80,.16);color:#fff;}" +
    "#server-ctx .sc-item i{color:#8fa67d;width:16px;text-align:center;}" +
    "#server-ctx .sc-item.sc-warn:hover{background:rgba(197,139,58,.22);color:#fff;}" +
    "#server-ctx .sc-item.sc-danger{color:#ec8a7e;}" +
    "#server-ctx .sc-item.sc-danger:hover{background:rgba(211,75,58,.25);color:#fff;}" +
    "#server-ctx .sc-item.sc-warn:hover i,#server-ctx .sc-item.sc-danger:hover i,#server-ctx .sc-item.sc-danger i{color:inherit;}" +
    "#server-ctx .sc-sep{height:1px;background:var(--outline,#444);margin:4px 6px;}";

  var menu = document.createElement("div");
  menu.id = "server-ctx";
  menu.style.display = "none";

  function init() {
    var style = document.createElement("style");
    style.textContent = CSS;
    document.head.appendChild(style);
    document.body.appendChild(menu);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();

  function cookie(name) {
    var r = document.cookie.match("\\b" + name + "=([^;]*)\\b");
    return r ? r[1] : undefined;
  }

  function serverFrom(target) {
    if (!target || !target.closest) return null;
    var a = target.closest('a[href*="server_detail?id="]');
    if (a) {
      var m = a.getAttribute("href").match(/[?&]id=([a-z0-9-]+)/i);
      if (m) return { id: m[1], name: (a.textContent || "").trim() };
    }
    var tr = target.closest("#servers_table tr[id]");
    if (tr && tr.id && tr.id !== "first") {
      var cell = tr.querySelector(".col-server");
      return { id: tr.id, name: cell ? cell.textContent.trim() : "" };
    }
    return null;
  }

  function action(id, cmd) {
    fetch("/api/v2/servers/" + id + "/action/" + cmd, { method: "POST", headers: { token: cookie("_xsrf") } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.status !== "ok") bootbox.alert({ title: d.error, message: d.error_data });
        else { try { notify("Command sent: " + cmd.replace("_server", "")); } catch (e) {} }
      })
      .catch(function (e) { bootbox.alert("Request failed: " + e); });
  }

  function confirmKill(id) {
    bootbox.confirm({
      title: "Kill server?",
      message: "Force-kills the server process. This can corrupt files — only for stuck servers. Continue?",
      buttons: { confirm: { label: "Kill", className: "btn-danger" }, cancel: { label: "Cancel", className: "btn-secondary" } },
      callback: function (ok) { if (ok) action(id, "kill_server"); }
    });
  }

  function del(id, name) {
    bootbox.confirm({
      title: "Delete " + (name || "server") + "?",
      message:
        "This removes the server from Crafty.<br><br>" +
        '<label style="font-weight:400;"><input type="checkbox" id="sc-del-files">&nbsp; Also delete all files from disk (world, configs) — <b>cannot be undone</b></label>',
      buttons: { confirm: { label: "Delete", className: "btn-danger" }, cancel: { label: "Cancel", className: "btn-secondary" } },
      callback: function (ok) {
        if (!ok) return;
        var box = document.getElementById("sc-del-files");
        var files = box && box.checked;
        fetch("/api/v2/servers/" + id + (files ? "?files=true" : ""), { method: "DELETE", headers: { "X-XSRFToken": cookie("_xsrf") } })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            if (d.status === "ok") {
              try { notify("Server deleted"); } catch (e) {}
              setTimeout(function () { location.href = "/panel/dashboard"; }, 600);
            } else bootbox.alert({ title: d.error, message: d.error_data });
          })
          .catch(function (e) { bootbox.alert("Delete failed: " + e); });
      }
    });
  }

  function hide() { menu.style.display = "none"; }

  function show(x, y, srv) {
    var items = [
      { icon: "ph-arrow-square-out", label: "Open", fn: function () { location.href = "/panel/server_detail?id=" + srv.id; } },
      { sep: true },
      { icon: "ph-play", label: "Start", fn: function () { action(srv.id, "start_server"); } },
      { icon: "ph-stop", label: "Stop", fn: function () { action(srv.id, "stop_server"); } },
      { icon: "ph-arrows-clockwise", label: "Restart", fn: function () { action(srv.id, "restart_server"); } },
      { icon: "ph-copy-simple", label: "Clone", fn: function () { action(srv.id, "clone_server"); } },
      { sep: true },
      { icon: "ph-skull", label: "Kill", cls: "sc-warn", fn: function () { confirmKill(srv.id); } },
      { icon: "ph-trash", label: "Delete…", cls: "sc-danger", fn: function () { del(srv.id, srv.name); } }
    ];
    menu.innerHTML = "";
    var head = document.createElement("div");
    head.className = "sc-head";
    head.textContent = srv.name || "Server";
    menu.appendChild(head);
    items.forEach(function (it) {
      if (it.sep) { var s = document.createElement("div"); s.className = "sc-sep"; menu.appendChild(s); return; }
      var d = document.createElement("div");
      d.className = "sc-item " + (it.cls || "");
      d.innerHTML = '<i class="ph-fill ' + it.icon + '"></i> ' + it.label;
      d.onclick = function () { hide(); it.fn(); };
      menu.appendChild(d);
    });
    menu.style.display = "block";
    var mw = menu.offsetWidth, mh = menu.offsetHeight;
    if (x + mw > window.innerWidth) x = window.innerWidth - mw - 8;
    if (y + mh > window.innerHeight) y = window.innerHeight - mh - 8;
    menu.style.left = Math.max(4, x) + "px";
    menu.style.top = Math.max(4, y) + "px";
  }

  document.addEventListener("contextmenu", function (e) {
    var srv = serverFrom(e.target);
    if (!srv) return;
    e.preventDefault();
    show(e.clientX, e.clientY, srv);
  });
  document.addEventListener("click", hide);
  document.addEventListener("scroll", hide, true);
  window.addEventListener("resize", hide);
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") hide(); });
})();
