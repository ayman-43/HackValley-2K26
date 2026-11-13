/* Nagrik Roads shared map: the same base map as Nagrik (MapLibre GL + OpenFreeMap "Positron" vector style)
 * with Nagrik's Bengaluru ward boundaries (served from /api/wards/geojson, cached from nagrik.in.net).
 * Used by the website map, the defect mini-map, the live views and the phone app. */
(function () {
  'use strict';
  const STYLE = 'https://tiles.openfreemap.org/styles/positron';
  const BLR = [77.5946, 12.9716];
  // Bengaluru's five city corporations (2025), pastel fills like Nagrik's ward map
  const CORPS = {
    BCCC: { name: 'Bengaluru Central', fill: '#8fc1d4', line: '#4f8aa3' },
    BECC: { name: 'Bengaluru East', fill: '#b6a8da', line: '#7a68ad' },
    BNCC: { name: 'Bengaluru North', fill: '#e9a9a9', line: '#b86a6a' },
    BSCC: { name: 'Bengaluru South', fill: '#a9d39e', line: '#5f9a55' },
    BWCC: { name: 'Bengaluru West', fill: '#e8cf8e', line: '#b0924a' },
  };
  const RISK = { Critical: '#b83a3f', High: '#c4581f', Medium: '#a8670f', Low: '#2f7d55' };

  let wardsP = null;
  function loadWards() {
    if (!wardsP) {
      const mock = /[?&]mock=1/.test(location.search);
      wardsP = fetch(mock ? 'mock/wards_BLR.geojson' : '/api/wards/geojson?city=BLR')
        .then((r) => (r.ok ? r.json() : null)).catch(() => null);
    }
    return wardsP;
  }

  function create(el, opts = {}) {
    const map = new maplibregl.Map({
      container: el, style: STYLE, center: opts.center || BLR, zoom: opts.zoom ?? 11,
      attributionControl: false, cooperativeGestures: !!opts.cooperative, maxZoom: 19,
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
    map.addControl(new maplibregl.AttributionControl({
      compact: true,
      customAttribution: 'Wards: <a href="https://nagrik.in.net" target="_blank" rel="noopener">Nagrik</a>',
    }), 'bottom-right');
    map.once('load', () => el && (typeof el === 'string' ? document.getElementById(el) : el)
      .querySelectorAll('.maplibregl-ctrl-attrib').forEach((a) => { a.classList.remove('maplibregl-compact-show'); a.removeAttribute('open'); }));
    return map;
  }

  function ready(map) {
    return new Promise((res) => (map.loaded() && map.isStyleLoaded() ? res() : map.once('load', res)));
  }

  const corpExpr = (key) => ['match', ['get', 'corporation'],
    ...Object.entries(CORPS).flatMap(([k, v]) => [k, v[key]]), key === 'fill' ? '#cfd6dd' : '#9aa5b0'];

  async function addWards(map, opts = {}) {
    const gj = await loadWards();
    if (!gj || map.getSource('wards')) return gj;
    map.addSource('wards', { type: 'geojson', data: gj, promoteId: 'ward_id' });
    map.addLayer({ id: 'wards-fill', type: 'fill', source: 'wards', paint: {
      'fill-color': corpExpr('fill'),
      'fill-opacity': ['case', ['boolean', ['feature-state', 'hover'], false], 0.42, opts.fillOpacity ?? 0.22],
    } }, opts.beforeId);
    map.addLayer({ id: 'wards-line', type: 'line', source: 'wards', paint: {
      'line-color': corpExpr('line'), 'line-width': ['interpolate', ['linear'], ['zoom'], 10, 0.5, 14, 1.4], 'line-opacity': 0.8,
    } }, opts.beforeId);
    if (opts.labels !== false) {
      map.addLayer({ id: 'wards-label', type: 'symbol', source: 'wards', minzoom: 11, layout: {
        'text-field': ['step', ['zoom'], ['get', 'ward_number'], 14, ['concat', ['get', 'ward_number'], '\n', ['get', 'name']]],
        'text-font': ['Noto Sans Bold'], 'text-size': ['interpolate', ['linear'], ['zoom'], 11, 10, 15, 12],
        'text-allow-overlap': false,
      }, paint: { 'text-color': '#20262d', 'text-halo-color': 'rgba(255,255,255,.9)', 'text-halo-width': 1.4 } }, opts.beforeId);
    }
    let hovered = null;
    map.on('mousemove', 'wards-fill', (e) => {
      const id = e.features[0] && e.features[0].id;
      if (hovered !== null && hovered !== id) map.setFeatureState({ source: 'wards', id: hovered }, { hover: false });
      if (id != null) { hovered = id; map.setFeatureState({ source: 'wards', id }, { hover: true }); }
    });
    map.on('mouseleave', 'wards-fill', () => {
      if (hovered !== null) map.setFeatureState({ source: 'wards', id: hovered }, { hover: false });
      hovered = null;
    });
    return gj;
  }

  // ---- geometry helpers (no extra library)
  function inRing(x, y, ring) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i], [xj, yj] = ring[j];
      if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }
  function inPoly(x, y, poly) { return inRing(x, y, poly[0]) && !poly.slice(1).some((h) => inRing(x, y, h)); }
  function wardAt(gj, lon, lat) {
    if (!gj) return null;
    return gj.features.find((f) => {
      const g = f.geometry;
      return g && (g.type === 'Polygon' ? inPoly(lon, lat, g.coordinates)
        : g.type === 'MultiPolygon' && g.coordinates.some((p) => inPoly(lon, lat, p)));
    }) || null;
  }
  function bbox(feature) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    const walk = (c) => (typeof c[0] === 'number' ? (x0 = Math.min(x0, c[0]), y0 = Math.min(y0, c[1]), x1 = Math.max(x1, c[0]), y1 = Math.max(y1, c[1])) : c.forEach(walk));
    walk(feature.geometry.coordinates);
    return [[x0, y0], [x1, y1]];
  }
  const corpName = (k) => (CORPS[k] ? CORPS[k].name : k || '');

  window.RSMap = { STYLE, BLR, CORPS, RISK, create, ready, addWards, loadWards, wardAt, bbox, corpName };
})();
