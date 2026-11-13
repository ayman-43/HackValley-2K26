/* Nagrik Roads repair route planner (standalone page, plain JS). Backend: POST /api/crew/route */
(function () {
  'use strict';

  const $ = (s) => document.querySelector(s);
  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const CLASS = { pothole: 'Pothole', alligator_crack: 'Alligator crack',
    longitudinal_crack: 'Longitudinal crack', transverse_crack: 'Transverse crack' };
  const RISK_COLOR = { Low: '#2a7d4f', Medium: '#c79300', High: '#d9601a', Critical: '#b4232a' };
  const riskOf = (d) => d.risk_label || (d.risk_score >= 75 ? 'Critical' : d.risk_score >= 55 ? 'High'
    : d.risk_score >= 35 ? 'Medium' : d.risk_score != null ? 'Low' : null);
  const fmtMin = (m) => (m >= 60 ? `${Math.floor(m / 60)} h ${Math.round(m % 60)} min` : `${Math.round(m)} min`);

  function toast(msg, isError) {
    const t = $('#toast');
    t.textContent = msg;
    t.className = 'toast show' + (isError ? ' error' : '');
    clearTimeout(toast.t);
    toast.t = setTimeout(() => { t.className = 'toast'; }, 3200);
  }
  async function api(path, opts) {
    const r = await fetch(path, opts);
    const js = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(js.detail || `HTTP ${r.status}`);
    return js;
  }

  // ------------------------------------------------------------ map
  const map = L.map('map', { scrollWheelZoom: true }).setView([12.9716, 77.5946], 12);
  // same base map as Nagrik and the Nagrik Roads dashboard (OpenFreeMap Positron vector tiles)
  if (L.maplibreGL) {
    L.maplibreGL({ style: 'https://tiles.openfreemap.org/styles/positron',
      attribution: '&copy; <a href="https://openfreemap.org">OpenFreeMap</a> &copy; OpenStreetMap contributors' }).addTo(map);
  } else {
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, attribution: '&copy; OpenStreetMap contributors',
    }).addTo(map);
  }
  const defectLayer = L.layerGroup().addTo(map);
  const routeLayer = L.layerGroup().addTo(map);

  let depot = { lat: 12.9667, lon: 77.5871, name: 'BBMP head office, N R Square' };
  let depotMarker;
  const picked = new Set();
  let defects = [];
  let lastRoute = null;

  const pinIcon = (label, color, extra) => L.divIcon({
    className: '', iconSize: [26, 26], iconAnchor: [13, 13],
    html: `<div class="rp-pin ${extra || ''}" style="background:${color}">${esc(label)}</div>`,
  });

  function drawDepot() {
    if (depotMarker) depotMarker.remove();
    depotMarker = L.marker([depot.lat, depot.lon], {
      icon: L.divIcon({ className: '', iconSize: [30, 30], iconAnchor: [15, 15],
        html: '<div class="rp-pin rp-depot" style="width:30px;height:30px">D</div>' }),
      zIndexOffset: 1000,
    }).bindTooltip('Depot: ' + esc(depot.name || 'custom')).addTo(map);
    $('#depot-label').textContent = depot.name || `Custom: ${depot.lat.toFixed(5)}, ${depot.lon.toFixed(5)}`;
  }

  function drawDefects(order) {
    defectLayer.clearLayers();
    const seqById = new Map((order || []).map((s) => [s.id, s.seq]));
    defects.forEach((d) => {
      const risk = riskOf(d);
      const seq = seqById.get(d.id);
      const label = seq != null ? seq : '';
      const m = L.marker([d.lat, d.lon], {
        icon: pinIcon(label, RISK_COLOR[risk] || '#6b7280', picked.has(d.id) ? 'sel' : ''),
        opacity: order && seq == null ? 0.45 : 1,
      });
      m.bindTooltip(`${esc(CLASS[d.damage_class] || d.damage_class)} · ${esc(risk || 'risk pending')}`
        + `${d.risk_score != null ? ' ' + Math.round(d.risk_score) : ''} · ${esc(d.workflow || 'reported')}`);
      m.on('click', () => {
        if ($('#pick-depot').checked) return;
        picked.has(d.id) ? picked.delete(d.id) : picked.add(d.id);
        $('#pick-count').textContent = picked.size;
        if (picked.size > 0) document.querySelector('input[name=mode][value=pick]').checked = true;
        drawDefects(lastRoute && lastRoute.stops);
      });
      m.addTo(defectLayer);
    });
  }

  map.on('click', (e) => {
    if (!$('#pick-depot').checked) return;
    depot = { lat: e.latlng.lat, lon: e.latlng.lng, name: null };
    $('#pick-depot').checked = false;
    drawDepot();
    toast('Depot moved');
  });

  // ------------------------------------------------------------ plan
  async function plan() {
    const mode = document.querySelector('input[name=mode]:checked').value;
    if (mode === 'pick' && picked.size === 0) { toast('Click defects on the map to pick them first', true); return; }
    const body = {
      top_n: Number($('#top-n').value),
      include_unverified: $('#incl-unverified').checked,
      start: depot,
    };
    if (mode === 'pick') body.defect_ids = Array.from(picked);

    $('#plan-btn').disabled = true;
    $('#status').textContent = 'Planning route on the road network…';
    try {
      const r = await api('/api/crew/route', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      lastRoute = r;
      render(r);
      $('#status').textContent = '';
    } catch (e) {
      $('#status').textContent = '';
      toast(e.message, true);
    } finally {
      $('#plan-btn').disabled = false;
    }
  }
  function render(r) {
    routeLayer.clearLayers();
    const line = L.polyline(r.path, { color: '#1b5ca3', weight: 5, opacity: 0.85 }).addTo(routeLayer);
    map.fitBounds(line.getBounds(), { padding: [40, 40] });
    drawDefects(r.stops);

    $('#summary').innerHTML = `
      <div><b>${r.stops.length}</b><span>stops</span></div>
      <div><b>${r.distance_km.toFixed(1)} km</b><span>driving</span></div>
      <div><b>${fmtMin(r.drive_min)}</b><span>drive time</span></div>
      <div><b>${fmtMin(r.total_min)}</b><span>incl. repair work</span></div>`;
    $('#engine-note').textContent = (r.engine === 'osrm'
      ? 'Optimised on the real road network (OSRM).'
      : 'Offline estimate: routing server unreachable, order is nearest-first with straight-line distances.')
      + (r.unverified_stops ? ` ${r.unverified_stops} stop(s) are not yet verified by an admin.` : '')
      + (r.nav_note ? ` ${r.nav_note}.` : '');

    $('#stops').innerHTML = r.stops.map((s) => {
      const risk = s.risk_label || riskOf(s);
      return `<li>
        <span class="rp-num">${s.seq}</span>
        <span><a href="/roads/dashboard#/defect/${s.id}">${esc(s.code || '#' + s.id)}</a> · ${esc(CLASS[s.damage_class] || s.damage_class)}
          <br><span class="rp-note">${esc(s.road_name || s.ward_name || `${s.lat.toFixed(5)}, ${s.lon.toFixed(5)}`)}
          · arrive +${fmtMin(s.arrive_min)} · fix ~${s.repair_min} min · ${esc(s.workflow)}</span>
          ${s.officer ? `<br><span class="rp-note">Ward engineer: ${esc(s.officer.name || s.officer.department)}
            ${s.officer.phone ? `· <a href="tel:${esc(s.officer.phone)}">${esc(s.officer.phone)}</a>` : ''}</span>` : ''}</span>
        <span class="badge risk-${esc(risk || 'Low')}">${esc(risk || '–')}${s.risk_score != null ? ' ' + Math.round(s.risk_score) : ''}</span>
      </li>`;
    }).join('');
    const person = (o) => (o ? `${esc(o.name || '')}${o.department ? ` (${esc(o.department)})` : ''}`
      + `${o.phone ? ` · <a href="tel:${esc(o.phone)}">${esc(o.phone)}</a>` : ''}` : '');
    $('#wards').innerHTML = (r.wards || []).map((w) => `<div class="rp-ward">
        <b>${esc(w.ward_name)}${w.corporation ? ` · ${esc(w.corporation)}` : ''}</b>
        Stops ${w.stops.join(', ')} · repair work ~${fmtMin(w.repair_min)}<br>
        ${w.officer ? `Engineer: ${person(w.officer)}<br>` : '<span class="rp-note">No ward engineer on Nagrik.</span><br>'}
        ${w.control_room ? `Control room: ${person(w.control_room)}<br>` : ''}
        ${w.tender ? `Road tender: ${esc(w.tender.tender_ref || w.tender.title)} (${esc(w.tender.status || 'status unknown')})`
          + `${w.tender.contractor_name ? ` · contractor ${esc(w.tender.contractor_name)}` : ''}`
          + ' <span class="rp-note">Check whether these defects fall under this tender before raising new work.</span>' : ''}
      </div>`).join('') || '<p class="rp-note">No ward data yet (Nagrik covers Bengaluru, Delhi, Gurugram, Ghaziabad, Mumbai, Pune).</p>';
    $('#nav-link').href = r.nav_url;
    $('#result').hidden = false;
  }

  async function scheduleVerified() {
    if (!lastRoute) return;
    const todo = lastRoute.stops.filter((s) => s.workflow === 'verified');
    const skipped = lastRoute.stops.length - todo.length;
    let ok = 0;
    for (const s of todo) {
      try {
        await api(`/api/defects/${s.id}/workflow`, {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ workflow: 'scheduled', note: 'Added to repair round' }),
        });
        s.workflow = 'scheduled';
        ok++;
      } catch (e) { /* keep going */ }
    }
    render(lastRoute);
    toast(`${ok} scheduled` + (skipped ? ` · ${skipped} skipped (not verified yet or already scheduled)` : ''));
  }

  // ------------------------------------------------------------ init
  $('#plan-btn').addEventListener('click', plan);
  $('#print-btn').addEventListener('click', () => window.print());
  $('#schedule-btn').addEventListener('click', scheduleVerified);
  drawDepot();
  api('/api/defects?limit=1000')
    .then((rows) => {
      defects = rows.filter((d) => d.lat != null && d.lon != null
        && !['repaired', 'rejected'].includes(d.status) && !['repaired', 'closed', 'rejected'].includes(d.workflow));
      drawDefects();
      if (defects.length) {
        map.fitBounds(L.latLngBounds(defects.map((d) => [d.lat, d.lon]).concat([[depot.lat, depot.lon]])),
          { padding: [40, 40] });
      }
      $('#status').textContent = `${defects.length} open defects with GPS. Choose a selection and press Plan route.`;
      if (new URLSearchParams(location.search).has('autoplan') && defects.length) plan();
    })
    .catch((e) => toast('Could not load defects: ' + e.message, true));
})();
