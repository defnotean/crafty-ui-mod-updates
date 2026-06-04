/*
 * Smart create-wizard defaults — shared by the Java, Bedrock, Hytale and Steam
 * wizards. Keyed on field NAMES (mem_min / mem_max / port) so it adapts to each
 * wizard automatically:
 *   - prefills recommended RAM from the host where memory fields exist
 *   - live-checks port availability (TCP, or UDP on the Bedrock wizard) and
 *     blocks the build when the chosen port is already taken
 */
(function () {
  function init() {
    const slice = (nl) => Array.prototype.slice.call(nl);
    const memMins = slice(document.querySelectorAll('input[name="mem_min"]'));
    const memMaxs = slice(document.querySelectorAll('input[name="mem_max"]'));
    const ports = slice(document.querySelectorAll('input[name="port"]'));
    if (!memMins.length && !ports.length) return;

    // Bedrock talks UDP; everything else here is TCP.
    const proto = (location.pathname || '').toLowerCase().indexOf('bedrock') !== -1 ? 'udp' : 'tcp';

    function noteFor(input, cls) {
      const group = input.closest('.form-group') || input.parentElement;
      let note = group.querySelector('.' + cls);
      if (!note) { note = document.createElement('small'); note.className = cls + ' wiz-note'; group.appendChild(note); }
      return note;
    }

    // ---------------------------------------------------------- recommended RAM
    let memTouched = false;
    memMins.concat(memMaxs).forEach(function (el) {
      el.addEventListener('input', function () { memTouched = true; });
    });
    function fillRec(rec) {
      memMins.forEach(function (e) { e.value = rec.min_gb; });
      memMaxs.forEach(function (e) { e.value = rec.max_gb; });
    }
    if (memMins.length || memMaxs.length) {
      fetch('/api/v2/crafty/host-info', { headers: { 'Accept': 'application/json' } })
        .then(function (r) { return r.json(); })
        .then(function (j) {
          if (!j || j.status !== 'ok') return;
          const d = j.data, rec = d.recommended;
          if (!memTouched) fillRec(rec);
          (memMaxs.length ? memMaxs : memMins).forEach(function (mx) {
            const note = noteFor(mx, 'mem-rec-hint');
            note.innerHTML =
              '<i class="ph-fill ph-cpu"></i> Recommended for this device: <b>' +
              rec.min_gb + '–' + rec.max_gb + ' GB</b> ' +
              '<span class="muted">(' + d.mem_total_gb + ' GB RAM · ' + d.cpu_cores + ' cores)</span>' +
              '<a class="rec-apply" href="javascript:void(0)">use</a>';
            const a = note.querySelector('.rec-apply');
            if (a) a.addEventListener('click', function () { fillRec(rec); });
          });
        }).catch(function () { /* non-fatal: keep static defaults */ });
    }

    // -------------------------------------------------------- port availability
    const portState = new WeakMap();
    function debounce(fn, ms) { let t; return function () { clearTimeout(t); const a = arguments, c = this; t = setTimeout(function () { fn.apply(c, a); }, ms); }; }

    async function checkPort(input) {
      const note = noteFor(input, 'port-status');
      input.classList.remove('is-port-invalid', 'is-port-valid');
      const port = parseInt(input.value, 10);
      if (!port || port < 1 || port > 65535) { portState.set(input, 'unknown'); note.className = 'port-status wiz-note'; note.textContent = ''; return; }
      note.className = 'port-status wiz-note checking'; note.innerHTML = '<i class="ph ph-circle-notch ph-spin"></i> Checking port…';
      try {
        const res = await fetch('/api/v2/crafty/port-check?port=' + port + '&proto=' + proto);
        const j = await res.json();
        if (!j || j.status !== 'ok') { portState.set(input, 'unknown'); note.textContent = ''; return; }
        const d = j.data;
        if (parseInt(input.value, 10) !== d.port) return; // stale response
        if (d.available) {
          portState.set(input, 'ok'); input.classList.add('is-port-valid');
          note.className = 'port-status wiz-note ok'; note.innerHTML = '<i class="ph-fill ph-check-circle"></i> Port ' + d.port + ' is available';
        } else {
          portState.set(input, 'bad'); input.classList.add('is-port-invalid');
          const c = (d.conflicts || []).find(function (x) { return x.type === 'crafty_server'; });
          const msg = c
            ? 'Port ' + d.port + ' is already used by server “' + c.server_name + '”'
            : 'Port ' + d.port + ' is already in use on this host';
          note.className = 'port-status wiz-note bad'; note.innerHTML = '<i class="ph-fill ph-x-circle"></i> ' + msg;
        }
      } catch (e) { portState.set(input, 'unknown'); note.textContent = ''; }
    }

    ports.forEach(function (input) {
      const run = debounce(function () { checkPort(input); }, 350);
      input.addEventListener('input', run);
      input.addEventListener('change', function () { checkPort(input); });
      if (input.offsetParent !== null) checkPort(input); // initial check for visible fields
    });

    // Block submit when the submitting form's VISIBLE port is taken. Capture
    // phase runs before the wizard's own bubble-phase jQuery submit handler.
    document.addEventListener('submit', function (e) {
      const form = e.target;
      if (!form || !form.querySelectorAll) return;
      const bad = Array.prototype.slice.call(form.querySelectorAll('input[name="port"]'))
        .find(function (p) { return portState.get(p) === 'bad' && p.offsetParent !== null; });
      if (bad) {
        e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
        if (window.bootbox) bootbox.alert({ title: 'Port already in use', message: 'The server port you entered is already taken. Pick a different port before building the server.' });
        bad.focus();
        return false;
      }
    }, true);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
