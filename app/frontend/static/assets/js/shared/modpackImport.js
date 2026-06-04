/*
 * Modpack import card — shared by the Discover hub and the create-server wizard.
 * Activates only when an #imp-go button is present on the page. Accepts a
 * Modrinth/CurseForge link OR an uploaded .mrpack / CurseForge .zip and kicks
 * off an automatic server setup.
 */
(function () {
  function init() {
    const el = (id) => document.getElementById(id);
    if (!el('imp-go')) return; // card not on this page

    const xsrf = () => (typeof getCookie === 'function' ? getCookie('_xsrf') : '');
    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

    function detectSource(link) {
      const l = link.toLowerCase();
      if (l.includes('curseforge.com')) return 'curseforge';
      if (l.includes('modrinth.com') || l.split('?')[0].endsWith('.mrpack')) return 'modrinth';
      return null;
    }
    function prettySlug(s) {
      return (s || '').replace(/[-_]+/g, ' ').replace(/\b\w/g, c => c.toUpperCase()).slice(0, 40);
    }
    function slugName(link) {
      try {
        const u = new URL(link);
        const parts = u.pathname.split('/').filter(Boolean);
        const i = parts.findIndex(p => p === 'modpack' || p === 'modpacks');
        const slug = (i >= 0 && parts[i + 1]) ? parts[i + 1] : parts[parts.length - 1];
        return prettySlug(slug);
      } catch (e) { return ''; }
    }

    el('imp-link').addEventListener('input', function () {
      const link = el('imp-link').value.trim();
      const src = detectSource(link);
      const badge = el('imp-src');
      if (badge) {
        if (src) { badge.style.display = ''; badge.textContent = src === 'curseforge' ? 'CurseForge' : 'Modrinth'; badge.className = 'badge ' + (src === 'curseforge' ? 'badge-warning' : 'badge-success'); }
        else { badge.style.display = 'none'; }
      }
      if (link && !el('imp-name').value.trim()) { const n = slugName(link); if (n) el('imp-name').value = n; }
    });
    el('imp-file').addEventListener('change', function () {
      const f = el('imp-file').files[0];
      if (f && !el('imp-name').value.trim()) { el('imp-name').value = prettySlug(f.name.replace(/\.(mrpack|zip)$/i, '')); }
    });

    async function go() {
      const msg = el('imp-msg'), btn = el('imp-go');
      const link = el('imp-link').value.trim();
      const file = el('imp-file').files[0];
      const name = el('imp-name').value.trim();
      const port = parseInt(el('imp-port').value, 10) || 25565;
      const mem_min = parseFloat(el('imp-min').value) || 2;
      const mem_max = parseFloat(el('imp-max').value) || 4;
      if (!name) { msg.innerHTML = '<span class="text-danger">Enter a server name.</span>'; return; }
      if (!link && !file) { msg.innerHTML = '<span class="text-danger">Paste a modpack link or choose a file.</span>'; return; }
      btn.disabled = true;
      msg.innerHTML = '<i class="ph ph-spinner-gap ph-spin"></i> Setting up — downloading the pack &amp; creating the server… (large packs take a moment)';
      try {
        let r;
        if (file) {
          const fd = new FormData();
          fd.append('file', file); fd.append('name', name);
          fd.append('server_properties_port', port); fd.append('mem_min', mem_min); fd.append('mem_max', mem_max);
          r = await fetch('/api/v2/servers/import-pack', { method: 'POST', headers: { 'X-XSRFToken': xsrf() }, body: fd });
        } else {
          const src = detectSource(link) || 'modrinth';
          const endpoint = src === 'curseforge' ? '/api/v2/servers/from-curseforge' : '/api/v2/servers/from-modrinth';
          r = await fetch(endpoint, {
            method: 'POST', headers: { 'X-XSRFToken': xsrf(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, url: link, mem_min, mem_max, server_properties_port: port })
          });
        }
        const d = await r.json();
        if (d.status === 'ok') {
          msg.innerHTML = '<span class="text-success"><i class="ph-fill ph-check-circle"></i> Server created (' + esc(d.data.loader) + ' &middot; MC ' + esc(d.data.minecraft) + ')' + (d.data.mods ? (' &middot; ' + d.data.mods + ' mods') : '') + '. Mods are downloading in the background — redirecting…</span>';
          setTimeout(() => location.href = '/panel/dashboard', 2300);
        } else {
          btn.disabled = false; msg.innerHTML = '<span class="text-danger">' + esc(d.error_data || d.error || 'Setup failed') + '</span>';
        }
      } catch (e) { btn.disabled = false; msg.innerHTML = '<span class="text-danger">' + esc(e) + '</span>'; }
    }
    el('imp-go').addEventListener('click', go);

    // prefill recommended RAM for this device
    fetch('/api/v2/crafty/host-info').then(r => r.json()).then(d => {
      if (d && d.status === 'ok' && d.data && d.data.recommended) {
        el('imp-min').value = d.data.recommended.min_gb;
        el('imp-max').value = d.data.recommended.max_gb;
      }
    }).catch(() => { });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
