/*
 * Aesthetic skin switcher — a floating palette button (bottom-right) that lets
 * you instantly re-tint the Minecraft redesign between several moods. The choice
 * is saved per browser. Only shown on the Minecraft skin family; an inline head
 * snippet in base.html applies the saved skin early to avoid a flash.
 */
(function () {
  if (window.__skinSwitcher) return;
  window.__skinSwitcher = true;
  var html = document.documentElement;
  if (!html.classList.contains("minecraft")) return; // skins only layer on the Minecraft redesign

  var SKINS = [
    { id: "grass",    name: "Grass",    bg: "#1a2016", accent: "#4caf50" },
    { id: "nether",   name: "Nether",   bg: "#211210", accent: "#e0533a" },
    { id: "end",      name: "The End",  bg: "#181426", accent: "#a986e0" },
    { id: "ocean",    name: "Ocean",    bg: "#0f1a24", accent: "#2fa8d4" },
    { id: "cherry",   name: "Cherry",   bg: "#20151a", accent: "#e8709e" },
    { id: "gold",     name: "Gold",     bg: "#1e1810", accent: "#e0a93a" },
    { id: "midnight", name: "Midnight", bg: "#11141c", accent: "#5b8def" },
    { id: "sculk",    name: "Sculk",    bg: "#0c1214", accent: "#1fb8b0" },
    { id: "lava",     name: "Lava",     bg: "#1f1208", accent: "#ff7a1a" },
    { id: "slime",    name: "Slime",    bg: "#141c10", accent: "#8fd44a" },
    { id: "amethyst", name: "Amethyst", bg: "#1c1226", accent: "#c850f0" }
  ];

  function current() { try { return localStorage.getItem("mcSkin") || "grass"; } catch (e) { return "grass"; } }
  function apply(id) {
    if (id && id !== "grass") html.setAttribute("data-skin", id);
    else html.removeAttribute("data-skin");
    try { localStorage.setItem("mcSkin", id); } catch (e) { /* ignore */ }
  }
  apply(current());

  function build() {
    if (document.getElementById("skin-switcher")) return;
    var wrap = document.createElement("div");
    wrap.id = "skin-switcher";

    var btn = document.createElement("button");
    btn.className = "ss-toggle";
    btn.type = "button";
    btn.title = "Change aesthetic";
    btn.setAttribute("aria-label", "Change aesthetic");
    btn.innerHTML = '<i class="ph-fill ph-palette"></i>';

    var panel = document.createElement("div");
    panel.className = "ss-panel";
    var grid = '<div class="ss-title">Aesthetic</div><div class="ss-grid">';
    SKINS.forEach(function (s) {
      grid += '<div class="ss-swatch" data-skin-id="' + s.id + '" title="' + s.name +
        '" style="background:' + s.bg + ';"><span class="ss-accent" style="background:' + s.accent + ';"></span></div>';
    });
    grid += '</div><div class="ss-name"></div>';
    panel.innerHTML = grid;

    wrap.appendChild(panel);
    wrap.appendChild(btn);
    document.body.appendChild(wrap);

    var nameEl = panel.querySelector(".ss-name");
    function mark() {
      var cur = current();
      panel.querySelectorAll(".ss-swatch").forEach(function (sw) {
        sw.classList.toggle("active", sw.getAttribute("data-skin-id") === cur);
      });
      var meta = SKINS.filter(function (s) { return s.id === cur; })[0];
      if (nameEl) nameEl.textContent = meta ? meta.name : "";
    }
    mark();

    btn.addEventListener("click", function (e) { e.stopPropagation(); wrap.classList.toggle("open"); });
    document.addEventListener("click", function (e) { if (!wrap.contains(e.target)) wrap.classList.remove("open"); });

    panel.querySelectorAll(".ss-swatch").forEach(function (sw) {
      var id = sw.getAttribute("data-skin-id");
      var meta = SKINS.filter(function (s) { return s.id === id; })[0];
      sw.addEventListener("mouseenter", function () { if (nameEl) nameEl.textContent = meta ? meta.name : ""; });
      sw.addEventListener("click", function () { apply(id); mark(); });
    });
    wrap.addEventListener("mouseleave", mark);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", build);
  else build();
})();
