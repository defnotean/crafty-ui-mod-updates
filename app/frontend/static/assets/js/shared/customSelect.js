/* ============================================================================
 * Crafty custom <select> — Minecraft-themed dropdown (progressive enhancement).
 *
 * Enhances EVERY native <select> on the page into a styled, searchable dropdown
 * while keeping the native element in the DOM and in sync (sets .value and
 * dispatches native 'change'/'input' events), so all of Crafty's existing
 * jQuery/vanilla handlers keep working untouched. Re-applied after pjax swaps.
 *
 * Defensive: any failure on a given <select> leaves the native one visible and
 * fully functional. Skips multiple-selects and anything bootstrap-select owns
 * (.selectpicker) or opted out with [data-no-custom].
 * ========================================================================== */
(function () {
  "use strict";
  var OPEN = null; // currently-open instance

  function closeOpen(except) {
    if (OPEN && OPEN !== except) OPEN.close();
  }
  document.addEventListener("click", function (e) {
    if (OPEN && !OPEN.wrap.contains(e.target)) OPEN.close();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && OPEN) OPEN.close(true);
  });
  window.addEventListener("resize", function () { if (OPEN) OPEN.position(); });
  window.addEventListener("scroll", function () { if (OPEN) OPEN.position(); }, true);

  function textOf(opt) { return (opt.textContent || "").trim(); }

  // Inline !important hide — beats any page stylesheet (e.g. crafty-wizard.css
  // .select-css) that out-specifies our class. Kept visually-hidden rather than
  // display:none so `required` fields still take part in form validation.
  var HIDE = {
    position: "absolute", width: "1px", height: "1px", "min-height": "0",
    padding: "0", margin: "-1px", overflow: "hidden", clip: "rect(0 0 0 0)",
    "clip-path": "inset(50%)", "white-space": "nowrap", border: "0",
    opacity: "0", "pointer-events": "none"
  };
  function hideNative(el) { try { for (var k in HIDE) el.style.setProperty(k, HIDE[k], "important"); } catch (e) {} }
  function showNative(el) { try { for (var k in HIDE) el.style.removeProperty(k); el.classList.remove("cs-native-hidden"); } catch (e) {} }

  function build(select) {
    if (!select || select.multiple) return;
    if (select.dataset.csEnhanced) return;
    if (select.classList.contains("selectpicker")) return;       // bootstrap-select owns it
    if (select.hasAttribute("data-no-custom")) return;
    select.dataset.csEnhanced = "1";

    var wrap = document.createElement("div");
    wrap.className = "cs-wrap";
    var button = document.createElement("button");
    button.type = "button";
    button.className = "cs-button";
    button.innerHTML = '<span class="cs-label"></span><i class="cs-caret ph-bold ph-caret-down"></i>';
    var panel = document.createElement("div");
    panel.className = "cs-panel";
    panel.innerHTML =
      '<div class="cs-search-wrap"><input type="text" class="cs-search" placeholder="Search…" autocomplete="off"></div>' +
      '<div class="cs-list" role="listbox" tabindex="-1"></div>';
    wrap.appendChild(button);
    wrap.appendChild(panel);

    // place wrapper right after the native select, then tuck the native one away
    select.parentNode.insertBefore(wrap, select.nextSibling);
    select.classList.add("cs-native-hidden");
    wrap.appendChild(select); // keep native inside wrapper so form submission + libs still see it
    hideNative(select);       // inline !important hide — wins over any page CSS

    var label = button.querySelector(".cs-label");
    var list = panel.querySelector(".cs-list");
    var search = panel.querySelector(".cs-search");
    var inst = { wrap: wrap, select: select, open: false };

    function syncDisabled() {
      button.disabled = select.disabled;
      wrap.classList.toggle("cs-disabled", !!select.disabled);
    }

    function renderLabel() {
      var o = select.options[select.selectedIndex];
      var t = o ? textOf(o) : "";
      label.textContent = t || (select.getAttribute("placeholder") || "Select…");
      label.classList.toggle("cs-placeholder", !t);
    }

    function rebuild() {
      list.innerHTML = "";
      var nodes = select.children, i, n;
      function addOption(opt) {
        var item = document.createElement("div");
        item.className = "cs-option";
        item.setAttribute("role", "option");
        item.dataset.value = opt.value;
        item.textContent = textOf(opt) || opt.value;
        if (opt.disabled) item.classList.add("cs-opt-disabled");
        if (opt.selected) item.classList.add("cs-selected");
        item.addEventListener("click", function () {
          if (opt.disabled) return;
          pick(opt.value);
        });
        list.appendChild(item);
      }
      for (i = 0; i < nodes.length; i++) {
        n = nodes[i];
        if (n.tagName === "OPTGROUP") {
          var gl = document.createElement("div");
          gl.className = "cs-group";
          gl.textContent = n.label;
          list.appendChild(gl);
          for (var j = 0; j < n.children.length; j++) addOption(n.children[j]);
        } else if (n.tagName === "OPTION") {
          addOption(n);
        }
      }
      // hide search box for short lists
      var count = select.querySelectorAll("option").length;
      panel.querySelector(".cs-search-wrap").style.display = count > 8 ? "" : "none";
      renderLabel();
      syncDisabled();
    }

    function pick(value, silent) {
      if (select.value !== value) {
        select.value = value;
        if (!silent) {
          select.dispatchEvent(new Event("input", { bubbles: true }));
          select.dispatchEvent(new Event("change", { bubbles: true }));
        }
      }
      list.querySelectorAll(".cs-option").forEach(function (el) {
        el.classList.toggle("cs-selected", el.dataset.value === value);
      });
      renderLabel();
      inst.close();
    }

    inst.position = function () {
      if (!inst.open) return;
      var r = button.getBoundingClientRect();
      var below = window.innerHeight - r.bottom;
      panel.classList.toggle("cs-up", below < 260 && r.top > below);
    };

    inst.openPanel = function () {
      if (select.disabled) return;
      closeOpen(inst);
      inst.open = true;
      OPEN = inst;
      wrap.classList.add("cs-open");
      search.value = "";
      filter("");
      inst.position();
      var sel = list.querySelector(".cs-option.cs-selected") || list.querySelector(".cs-option");
      if (sel) sel.classList.add("cs-active");
      setTimeout(function () { if (panel.querySelector(".cs-search-wrap").style.display !== "none") search.focus(); }, 10);
    };

    inst.close = function (focusBtn) {
      inst.open = false;
      if (OPEN === inst) OPEN = null;
      wrap.classList.remove("cs-open");
      list.querySelectorAll(".cs-active").forEach(function (e) { e.classList.remove("cs-active"); });
      if (focusBtn) button.focus();
    };

    function filter(q) {
      q = (q || "").toLowerCase();
      var any = false;
      list.querySelectorAll(".cs-option").forEach(function (el) {
        var show = el.textContent.toLowerCase().indexOf(q) !== -1;
        el.style.display = show ? "" : "none";
        if (show) any = true;
      });
      list.querySelectorAll(".cs-group").forEach(function (g) { g.style.display = q ? "none" : ""; });
    }

    function activeMove(dir) {
      var opts = Array.prototype.filter.call(list.querySelectorAll(".cs-option"), function (e) {
        return e.style.display !== "none" && !e.classList.contains("cs-opt-disabled");
      });
      if (!opts.length) return;
      var idx = opts.findIndex(function (e) { return e.classList.contains("cs-active"); });
      opts.forEach(function (e) { e.classList.remove("cs-active"); });
      idx = idx + dir;
      if (idx < 0) idx = opts.length - 1;
      if (idx >= opts.length) idx = 0;
      opts[idx].classList.add("cs-active");
      opts[idx].scrollIntoView({ block: "nearest" });
    }

    button.addEventListener("click", function (e) {
      e.preventDefault();
      inst.open ? inst.close() : inst.openPanel();
    });
    button.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") { e.preventDefault(); inst.openPanel(); }
    });
    search.addEventListener("input", function () { filter(search.value); });
    search.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") { e.preventDefault(); activeMove(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); activeMove(-1); }
      else if (e.key === "Enter") {
        e.preventDefault();
        var a = list.querySelector(".cs-option.cs-active");
        if (a) pick(a.dataset.value);
      }
    });

    // keep custom UI in sync when Crafty's own JS changes the value or options
    select.addEventListener("change", function () { renderLabel();
      list.querySelectorAll(".cs-option").forEach(function (el) {
        el.classList.toggle("cs-selected", el.dataset.value === select.value);
      });
    });
    var mo = new MutationObserver(function () { rebuild(); });
    try { mo.observe(select, { childList: true, subtree: true, attributes: true, attributeFilter: ["disabled"] }); } catch (e) {}

    rebuild();
  }

  function enhance(select) {
    try { build(select); }
    catch (e) {
      if (select) { select.classList.remove("cs-native-hidden"); delete select.dataset.csEnhanced; }
      console.warn("customSelect: leaving native select", e);
    }
  }

  function enhanceAll(root) {
    (root || document).querySelectorAll("select").forEach(enhance);
  }

  window.CraftyCustomSelect = { enhance: enhance, enhanceAll: enhanceAll };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { enhanceAll(document); });
  } else {
    enhanceAll(document);
  }
})();
