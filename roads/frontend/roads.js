/* Nagrik Roads road health (standalone page, plain JS). Backend: GET /api/roads/health */
(function () {
  'use strict';

  const $ = (s) => document.querySelector(s);
  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const CLASS = { pothole: 'Pothole', alligator_crack: 'Alligator crack',
    longitudinal_crack: 'Longitudinal crack', transverse_crack: 'Transverse crack' };
  const COLOR = { Good: '#2a7d4f', Fair: '#c79300', Poor: '#b4232a' };
  const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
  const fmtLen = (m) => (m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${Math.round(m)} m`);

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
  const roadLayer = L.layerGroup().addTo(map);
  const pinLayer = L.layerGroup().addTo(map);
  const lines = new Map();   // segment id -> leaflet layer

  const segName = (s) => s.name || (s.ward_name ? `Unnamed road, ${s.ward_name}` : 'Unnamed road');
  const classList = (s) => Object.entries(s.by_class || {})
    .map(([k, n]) => `${n} ${esc(CLASS[k] || k).toLowerCase()}${n === 1 ? '' : 's'}`).join(', ');

  function popupHtml(s) {
    return `<div class="rh-popup">
      <h3>${esc(segName(s))}</h3>
      <span class="rh-chip" style="background:${COLOR[s.label]}">${esc(s.label)} · ${Math.round(s.health)}/100</span>
      <dl>
        <dt>Defects</dt><dd>${s.defect_count} (${classList(s)})</dd>
        <dt>Density</dt><dd>${s.density_per_km} per km</dd>
        <dt>Risk</dt><dd>max ${Math.round(s.max_risk)}, mean ${Math.round(s.mean_risk)}</dd>
        <dt>Worst severity</dt><dd>${esc(s.worst_severity_label || '–')} (${Math.round(s.worst_severity)})</dd>
        <dt>Length</dt><dd>${fmtLen(s.length_m)}${s.highway ? ` · ${esc(String(s.highway).replace(/_/g, ' '))}` : ''}</dd>
      </dl>
      ${s.geometry === 'approx' ? '<p class="rh-note">Approximate line through the defects (road shape not available).</p>' : ''}
      <a href="/roads/dashboard#/defects">Open the defect list →</a>
    </div>`;
  }

  function drawRoads(segments) {
    roadLayer.clearLayers();
    lines.clear();
    // draw best first so the worst roads end up on top
    segments.slice().reverse().forEach((s) => {
      const color = COLOR[s.label] || '#6b7280';
      const isPoint = s.length_m < 1;
      const layer = isPoint
        ? L.circleMarker(s.coordinates[0], { radius: 11, color, weight: 4, fillColor: color, fillOpacity: 0.25 })
        : L.polyline(s.coordinates, { color, weight: 8, opacity: 0.85, lineCap: 'round',
          dashArray: s.geometry === 'approx' ? '2 13' : null });
      layer.bindPopup(popupHtml(s), { maxWidth: 300 });
      layer.bindTooltip(`${esc(segName(s))}: ${esc(s.label)} ${Math.round(s.health)}`, { sticky: true });
      layer.on('popupopen', () => markActive(s.id));
      layer.addTo(roadLayer);
      lines.set(s.id, layer);
    });
  }

  function drawPins(defects) {
    pinLayer.clearLayers();
    defects.forEach((d) => {
      L.circleMarker([d.lat, d.lon], { radius: 3.5, color: '#fff', weight: 1, fillColor: '#7b838c', fillOpacity: 0.95 })
        .bindTooltip(`${esc(d.code || '#' + d.id)} · ${esc(CLASS[d.damage_class] || d.damage_class)}`
          + `${d.risk_label ? ` · ${esc(d.risk_label)} risk` : ''}`)
        .on('click', () => { location.href = `/roads/dashboard#/defect/${d.id}`; })
        .addTo(pinLayer);
    });
  }

  function markActive(id) {
    document.querySelectorAll('.rh-row').forEach((b) => b.classList.toggle('active', b.dataset.id === id));
  }

  function focusSegment(id) {
    const layer = lines.get(id);
    if (!layer) return;
    if (layer.getBounds) map.fitBounds(layer.getBounds().pad(0.6), { maxZoom: 18 });
    else map.setView(layer.getLatLng(), 18);
    layer.openPopup();
    if (window.matchMedia('(max-width: 900px)').matches) $('#map').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function renderList(segments) {
    $('#list').innerHTML = segments.map((s) => `<li>
        <button type="button" class="rh-row" data-id="${esc(s.id)}">
          <span class="rh-score" style="background:${COLOR[s.label]}" aria-label="Health ${Math.round(s.health)} of 100, ${esc(s.label)}">${Math.round(s.health)}</span>
          <span><span class="rh-name">${esc(segName(s))}</span>
            <span class="rh-meta">${esc(s.label)} · ${s.density_per_km} defects/km · ${plural(s.defect_count, 'defect')} · ${fmtLen(s.length_m)}</span>
            <span class="rh-meta">max risk ${Math.round(s.max_risk)}${s.worst_severity_label ? ` · worst severity ${esc(s.worst_severity_label)}` : ''}${s.geometry === 'approx' ? ' · approx. line' : ''}</span>
          </span>
        </button>
        <div class="rh-links"><a href="/roads/dashboard#/defects">View defects →</a></div>
      </li>`).join('');
    document.querySelectorAll('.rh-row').forEach((b) => b.addEventListener('click', () => focusSegment(b.dataset.id)));
  }

  function render(data) {
    const segs = data.segments || [];
    const by = (data.summary && data.summary.by_label) || {};
    $('#summary').innerHTML = ['Poor', 'Fair', 'Good'].map((k) =>
      `<div class="${k}"><b>${by[k] || 0}</b><span>${k}</span></div>`).join('');
    if (!segs.length) {
      $('#status').textContent = 'No open defects with GPS yet, so there are no roads to score. Run an inspection first.';
      $('#list').innerHTML = '';
      return;
    }
    drawRoads(segs);
    drawPins(data.defects || []);
    renderList(segs);
    const approx = segs.some((s) => s.geometry === 'approx');
    $('#legend-approx').hidden = !approx;
    $('#status').textContent = `${plural(segs.length, 'road segment')} with ${plural(data.summary.defects, 'open defect')}, worst first. `
      + (data.source === 'osm' ? 'Road shapes from OpenStreetMap.'
        : data.source === 'mixed' ? 'Road shapes from OpenStreetMap; defects with no road within 30 m are shown as dashed lines.'
          : 'OpenStreetMap unreachable: roads are drawn as dashed lines through their defects.');
    const pts = segs.flatMap((s) => s.coordinates).concat((data.defects || []).map((d) => [d.lat, d.lon]));
    map.fitBounds(L.latLngBounds(pts), { padding: [40, 40], maxZoom: 17 });
  }

  // ------------------------------------------------------------ init
  api('/api/roads/health' + (new URLSearchParams(location.search).has('offline') ? '?osm=false' : ''))
    .then(render)
    .catch((e) => {
      $('#status').textContent = 'Could not load road health.';
      toast('Could not load road health: ' + e.message, true);
    });
})();
