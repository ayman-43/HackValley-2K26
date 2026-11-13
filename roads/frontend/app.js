/* Nagrik Roads dashboard: single-page app, plain JS, no build step.
 * Hash routes: #/upload #/map #/defects #/defect/ID #/review #/reports #/metrics
 * Add ?mock=1 to the URL to read sample data from frontend/mock/ instead of the API.
 */
(function () {
  'use strict';

  // ---------------------------------------------------------------- basics
  const params = new URLSearchParams(location.search);
  const MOCK = params.has('mock') && params.get('mock') !== '0';
  const VIEW = document.getElementById('view');
  const MAIN = document.getElementById('main');

  const CLASS_LABELS = {
    pothole: 'Pothole',
    alligator_crack: 'Alligator crack',
    longitudinal_crack: 'Longitudinal crack',
    transverse_crack: 'Transverse crack',
  };
  const RISK_LEVELS = ['Low', 'Medium', 'High', 'Critical'];
  const FACTOR_LABELS = { severity: 'How bad the damage is', road_class: 'Type of road', speed: 'Speed limit', sensitive_places: 'Schools, hospitals or bus stops nearby',
    reports: 'Times reported', rain_forecast: 'Rain expected', worsening: 'Getting worse', bad_repair: 'Came back after a repair', trend: 'Getting worse' };
  const SEVERITY_LEVELS = ['Low', 'Medium', 'High'];
  const WORKFLOWS = ['reported', 'verified', 'scheduled', 'repaired', 'closed', 'rejected'];
  const WORKFLOW_ACTIONS = [
    { value: 'verified', label: 'Mark verified' },
    { value: 'scheduled', label: 'Schedule repair' },
    { value: 'repaired', label: 'Mark repaired' },
    { value: 'closed', label: 'Close' },
  ];
  const TREND_LABELS = {
    new: 'New',
    worsening: 'Worsening',
    improving: 'Improving',
    stable: 'Stable',
    possibly_repaired: 'Possibly repaired',
    repaired: 'Repaired',
  };
  const ROAD_TYPES = ['motorway', 'trunk', 'primary', 'secondary', 'tertiary', 'residential', 'service', 'unclassified'];
  const BLR = [12.9716, 77.5946];

  const esc = (v) => String(v == null ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const isNum = (v) => typeof v === 'number' && isFinite(v);
  const fmtInt = (n) => (isNum(n) ? Math.round(n).toLocaleString('en-IN') : '–');
  const fmt1 = (n) => (isNum(n) ? (Math.round(n * 10) / 10).toString() : '–');
  const pct = (n) => (isNum(n) ? (n <= 1 ? n * 100 : n).toFixed(1) + '%' : '–');
  const classLabel = (c) => CLASS_LABELS[c] || (c ? String(c).replace(/_/g, ' ') : 'Unknown');
  const trendLabel = (t) => TREND_LABELS[t] || (t ? String(t).replace(/_/g, ' ') : '–');
  const cap = (s) => (s ? String(s).charAt(0).toUpperCase() + String(s).slice(1) : '');

  function parseDate(s) {
    if (!s) return null;
    const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(s) || !/T/.test(s) ? s : s + 'Z');
    return isNaN(d) ? null : d;
  }
  function fmtDate(s, withTime) {
    const d = parseDate(s);
    if (!d) return '–';
    const o = { day: 'numeric', month: 'short', year: 'numeric' };
    if (withTime) { o.hour = '2-digit'; o.minute = '2-digit'; }
    return d.toLocaleString('en-IN', o);
  }
  function ago(s) {
    const d = parseDate(s);
    if (!d) return '–';
    const days = Math.floor((Date.now() - d.getTime()) / 86400000);
    if (days <= 0) return 'today';
    if (days === 1) return 'yesterday';
    if (days < 60) return days + ' days ago';
    return fmtDate(s);
  }

  function riskLabel(d) {
    if (d && d.risk_label) return d.risk_label;
    const s = d && d.risk_score;
    if (!isNum(s)) return null;
    return s >= 75 ? 'Critical' : s >= 55 ? 'High' : s >= 35 ? 'Medium' : 'Low';
  }
  const riskBadge = (d) => {
    const l = riskLabel(d);
    if (!l) return '<span class="badge">Risk pending</span>';
    return `<span class="badge risk-${esc(l)}">${esc(l)}${isNum(d.risk_score) ? ' ' + Math.round(d.risk_score) : ''}</span>`;
  };
  const sevBadge = (label, score) => label
    ? `<span class="badge sev-${esc(label)}">${esc(label)}${isNum(score) ? ' ' + Math.round(score) : ''}</span>` : '–';
  const trendBadge = (t) => t ? `<span class="badge trend-${esc(t)}">${esc(trendLabel(t))}</span>` : '–';
  const wfBadge = (w) => `<span class="badge wf">${esc(w || 'reported')}</span>`;
  const riskVar = (l) => `var(--risk-${String(l || 'low').toLowerCase()})`;

  /** Turn a stored path (e.g. backend/data/media/crops/x.jpg) into a URL under /media/. */
  function mediaUrl(p) {
    if (!p) return null;
    p = String(p).replace(/\\/g, '/');
    if (/^(https?:|data:|blob:|\/)/.test(p) || p.startsWith('mock/')) return p;
    let i = p.indexOf('data/media/');
    if (i >= 0) return '/media/' + p.slice(i + 11);
    i = p.indexOf('media/');
    if (i >= 0) return '/media/' + p.slice(i + 6);
    return '/media/' + p.replace(/^\.?\//, '');
  }
  const obsFrame = (o) => o && (o.frame_url || o.image_url || mediaUrl(o.frame_path) || o.crop_url || mediaUrl(o.crop_path));
  const obsCrop = (o) => o && (o.crop_url || mediaUrl(o.crop_path) || obsFrame(o));
  const defectThumb = (d) => d && (d.thumb_url || d.photo_url || d.crop_url || d.image_url || d.frame_url ||
    mediaUrl(d.crop_path || d.frame_path) || null);
  const asList = (r, ...keys) => {
    if (Array.isArray(r)) return r;
    for (const k of keys.concat(['items', 'results', 'data'])) if (r && Array.isArray(r[k])) return r[k];
    return [];
  };

  // ---------------------------------------------------------------- signed-in official
  let ME = null;  // { username, name, role, role_label } or null for the public
  const can = (...roles) => !!ME && roles.includes(ME.role);
  const loginUrl = () => '/login.html?next=' + encodeURIComponent(location.pathname + location.hash);
  async function loadMe() {
    if (MOCK) { ME = { username: 'demo', name: 'Demo officer', role: 'officer', role_label: 'Ward officer' }; return; }
    try { const r = await fetch('/api/auth/me', { cache: 'no-store' }); ME = (await r.json()).user || null; } catch (e) { ME = null; }
  }
  function renderUserBox() {
    const box = document.getElementById('user-box');
    if (!box) return;
    if (!ME) { box.innerHTML = `<a class="btn btn-small" href="${esc(loginUrl())}">Official sign in</a>`; return; }
    box.innerHTML = `<details class="user-menu"><summary><span class="avatar" aria-hidden="true">${esc((ME.name || ME.username).trim()[0] || '?')}</span>
        <span class="user-txt"><b>${esc(ME.name)}</b><span>${esc(ME.role_label || ME.role)}</span></span></summary>
      <div class="nav-menu user-pop">
        ${ME.role === 'admin' ? '<a href="/users.html">Manage accounts</a>' : ''}
        <button type="button" id="signout">Sign out</button></div></details>`;
    document.getElementById('signout').addEventListener('click', async () => {
      try { await fetch('/api/auth/logout', { method: 'POST' }); } catch (e) { /* ignore */ }
      ME = null; renderUserBox(); route(); toast('Signed out.');
    });
  }
  function signInHtml(what) {
    return `<div class="state signin-state"><h2>Sign in to ${esc(what)}</h2>
      <p>This is for city officials. Anyone can view the map and the defect list, and report a pothole from the phone app.</p>
      <p><a class="btn btn-primary" href="${esc(loginUrl())}">Official sign in</a></p></div>`;
  }

  // ---------------------------------------------------------------- toast
  let toastTimer;
  function toast(msg, isError) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.className = 'toast show' + (isError ? ' error' : '');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.className = 'toast'; }, 3200);
  }

  // ---------------------------------------------------------------- API layer
  class ApiError extends Error {
    constructor(message, status, path) { super(message); this.status = status; this.path = path; }
  }

  function friendlyError(status, path, detail) {
    if (status === 0) return 'Could not reach the Nagrik Roads server. Check that it is running, then reload.';
    if (status === 401) return 'Sign in as a city official to do this.';
    if (status === 403) return detail || 'Your role is not allowed to do this.';
    if (status === 429) return detail || 'Too many requests. Please wait a few minutes.';
    if (status === 404) return `This part of the server is not available yet (${path} returned 404).`;
    if (status === 422 || status === 400) return 'The server rejected the request: ' + (detail || 'check the inputs and try again.');
    if (status >= 500) return `The server hit an error while handling ${path} (${status}).` + (detail ? ' ' + detail : '');
    return `Request to ${path} failed (${status}).` + (detail ? ' ' + detail : '');
  }

  async function api(path, opts) {
    opts = opts || {};
    if (MOCK) return mockApi(path, opts);
    const init = { method: opts.method || 'GET', headers: { Accept: 'application/json' } };
    if (opts.json !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(opts.json);
    } else if (opts.body) {
      init.body = opts.body;
    }
    let res;
    try { res = await fetch(path, init); } catch (e) { throw new ApiError(friendlyError(0, path), 0, path); }
    const short = path.split('?')[0];
    if (!res.ok) {
      let detail = '';
      try {
        const j = await res.json();
        detail = typeof j.detail === 'string' ? j.detail : (j.error || j.message || '');
      } catch (e) { /* not JSON */ }
      throw new ApiError(friendlyError(res.status, short, detail), res.status, short);
    }
    const ct = res.headers.get('content-type') || '';
    return ct.includes('json') ? res.json() : res.text();
  }

  /** URL for links/iframes that open server pages (complaint, digest, CSV, export). */
  function pageUrl(kind, arg) {
    if (MOCK) {
      return { complaint: 'mock/complaint.html', digest: 'mock/digest.html', csv: 'mock/summary.csv', corrections: null }[kind];
    }
    switch (kind) {
      case 'complaint': return `/api/defects/${encodeURIComponent(arg)}/complaint`;
      case 'digest': return '/api/digest?' + new URLSearchParams(arg || {}).toString();
      case 'csv': return '/api/report/summary.csv';
      case 'corrections': return '/api/export/corrections';
    }
    return null;
  }

  // ---------------------------------------------------------------- mock backend (?mock=1)
  const mockCache = {};
  const mockState = { workflow: {}, reviewed: new Set(), jobStart: 0 };
  async function mockFile(name) {
    if (!mockCache[name]) {
      mockCache[name] = fetch('mock/' + name).then((r) => {
        if (!r.ok) throw new ApiError(`Mock file mock/${name} is missing.`, 404, name);
        return r.json();
      });
    }
    return mockCache[name];
  }
  const delay = (ms) => new Promise((r) => setTimeout(r, ms));
  const clone = (o) => JSON.parse(JSON.stringify(o));

  async function mockApi(path, opts) {
    await delay(180);
    const method = (opts.method || 'GET').toUpperCase();
    const [p, qs] = path.split('?');
    const q = new URLSearchParams(qs || '');
    let m;
    const applyWf = (d) => { if (mockState.workflow[d.id]) d.workflow = mockState.workflow[d.id]; return d; };

    if (p === '/api/stats') {
      const s = clone(await mockFile('stats.json'));
      s.pending_review = Math.max(0, (s.pending_review || 0) - mockState.reviewed.size);
      return s;
    }
    if (p === '/api/defects' && method === 'GET') {
      let list = clone(await mockFile('defects.json')).map(applyWf);
      if (q.get('damage_class')) list = list.filter((d) => d.damage_class === q.get('damage_class'));
      if (q.get('severity')) list = list.filter((d) => d.severity_label === q.get('severity'));
      if (q.get('ward')) list = list.filter((d) => d.ward_name === q.get('ward'));
      if (q.get('min_risk')) list = list.filter((d) => d.risk_score >= +q.get('min_risk'));
      return list;
    }
    if ((m = p.match(/^\/api\/defects\/(\d+)$/)) && method === 'GET') {
      const all = await mockFile('defect_details.json');
      if (!all[m[1]]) throw new ApiError(friendlyError(404, p), 404, p);
      return applyWf(clone(all[m[1]]));
    }
    if ((m = p.match(/^\/api\/defects\/(\d+)\/workflow$/))) {
      mockState.workflow[m[1]] = opts.json && opts.json.workflow;
      const all = await mockFile('defect_details.json');
      return applyWf(clone(all[m[1]] || {}));
    }
    if ((m = p.match(/^\/api\/defects\/(\d+)\/repair$/))) {
      mockState.workflow[m[1]] = 'repaired';
      return { ok: true };
    }
    if ((m = p.match(/^\/api\/defects\/(\d+)\/repaired_by$/))) {
      return { id: +m[1], repaired_by: String((opts.json && opts.json.crew_id) || '') };
    }
    if (p === '/api/review/queue') {
      const qd = clone(await mockFile('review_queue.json'));
      return qd.filter((o) => !mockState.reviewed.has(o.id));
    }
    if ((m = p.match(/^\/api\/observations\/(\d+)\/review$/))) {
      mockState.reviewed.add(+m[1]);
      return { ok: true, id: +m[1], review_status: { confirm: 'confirmed', reject: 'rejected', correct: 'corrected' }[opts.json.action] };
    }
    if (p === '/api/inspections' && method === 'POST') {
      mockState.jobStart = Date.now();
      return { inspection_id: 42, job_id: 'mock-job' };
    }
    if (p === '/api/inspections') return [await mockFile('inspection.json')];
    if ((m = p.match(/^\/api\/inspections\/(\d+)$/))) return clone(await mockFile('inspection.json'));
    if (p === '/api/jobs/mock-job') {
      const progress = Math.min(1, (Date.now() - mockState.jobStart) / 5000);
      if (progress < 1) return { status: progress < 0.1 ? 'queued' : 'running', progress };
      const i = await mockFile('inspection.json');
      return {
        status: 'done', progress: 1,
        result: { inspection_id: i.id, raw_detections: i.raw_detections, unique_defects: i.unique_defects, new_defects: i.new_defects, duplicate_defects: i.duplicate_defects },
      };
    }
    if (p === '/api/metrics') return mockFile('metrics.json');
    throw new ApiError(friendlyError(404, p), 404, p);
  }

  // ---------------------------------------------------------------- view helpers
  function stateHtml(kind, title, body, extra) {
    const cls = kind === 'error' ? 'state state-error' : kind === 'loading' ? 'state loading' : 'state';
    const role = kind === 'error' ? ' role="alert"' : kind === 'loading' ? ' role="status"' : '';
    return `<div class="${cls}"${role}><h2>${esc(title)}</h2>${body ? `<p>${esc(body)}</p>` : ''}${extra || ''}</div>`;
  }
  const loadingHtml = (what) => stateHtml('loading', 'Loading ' + what, '');
  const errorHtml = (err, what) => stateHtml('error', `Couldn't load ${what}`,
    (err && err.message) || 'Something went wrong.',
    '<p><button class="btn btn-small" type="button" data-retry>Try again</button></p>');

  function bindRetry(root) {
    $$('[data-retry]', root).forEach((b) => b.addEventListener('click', () => route()));
  }

  // ---------------------------------------------------------------- router
  let routeToken = 0;
  let cleanups = [];
  const onCleanup = (fn) => cleanups.push(fn);

  const routes = {
    upload: viewUpload,
    map: viewMap,
    defects: viewDefects,
    defect: viewDefect,
    review: viewReview,
    reports: viewReports,
    metrics: viewMetrics,
  };
  const TITLES = {
    upload: 'Scan a road', map: 'Map', defects: 'Defects', defect: 'Defect',
    review: 'Review', reports: 'Reports', metrics: 'Model metrics',
  };

  function route() {
    cleanups.forEach((fn) => { try { fn(); } catch (e) { /* ignore */ } });
    cleanups = [];
    const token = ++routeToken;
    const hash = location.hash.replace(/^#\/?/, '') || 'map';
    const [name, arg] = hash.split('/');
    $$('.main-nav a[data-route]').forEach((a) => {
      const active = a.dataset.route === name || (name === 'defect' && a.dataset.route === 'defects');
      if (active) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
    document.title = (TITLES[name] ? TITLES[name] + ' | ' : '') + 'Nagrik Roads Bengaluru';
    VIEW.innerHTML = '';
    const fn = routes[name];
    if (!fn) {
      VIEW.innerHTML = stateHtml('empty', 'Page not found', 'That address does not match any section.',
        '<p><a class="btn" href="#/upload">Go to Inspect</a></p>');
      return;
    }
    const alive = () => token === routeToken;
    Promise.resolve(fn(VIEW, decodeURIComponent(arg || ''), alive)).catch((err) => {
      if (!alive()) return;
      console.error(err);
      VIEW.innerHTML = errorHtml(err, 'this page');
      bindRetry(VIEW);
    });
    if (document.activeElement && document.activeElement.closest && document.activeElement.closest('.main-nav')) {
      MAIN.focus({ preventScroll: true });
    }
  }

  // ---------------------------------------------------------------- header stats
  function pick(o, ...keys) {
    for (const k of keys) {
      const v = k.split('.').reduce((a, part) => (a == null ? a : a[part]), o);
      if (v != null) return v;
    }
    return null;
  }

  async function refreshStats() {
    const el = document.getElementById('live-stats');
    try {
      const s = await api('/api/stats');
      const total = pick(s, 'total_defects', 'defects', 'totals.defects', 'total');
      const byRisk = s.by_risk || {};
      const urgent = isNum(byRisk.Critical) || isNum(byRisk.High) ? (byRisk.Critical || 0) + (byRisk.High || 0) : null;
      const worsening = pick(s, 'by_trend.worsening');
      const pending = pick(s, 'pending_review', 'by_review.pending', 'review_pending');
      const raw = pick(s, 'raw_detections', 'totals.raw_detections', 'raw_vs_unique.raw');
      const uniq = pick(s, 'unique_defects', 'totals.unique_defects', 'raw_vs_unique.unique');
      const items = [];
      if (total != null) items.push(['Defects tracked', fmtInt(total)]);
      if (urgent != null) items.push(['High or critical risk', fmtInt(urgent), 'stat-critical']);
      if (worsening != null) items.push(['Getting worse', fmtInt(worsening)]);
      if (pending != null) items.push(['Awaiting review', fmtInt(pending)]);
      if (raw != null && uniq != null) items.push(['Raw detections to unique', `${fmtInt(raw)} → ${fmtInt(uniq)}`]);
      el.innerHTML = items.length
        ? items.map(([k, v, c]) => `<div class="stat ${c || ''}"><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')
        : '<div class="stat stat-error"><dt>Stats</dt><dd>No data yet</dd></div>';
      const nav = document.getElementById('nav-review-count');
      if (isNum(pending) && pending > 0) { nav.textContent = pending; nav.hidden = false; nav.setAttribute('aria-label', pending + ' awaiting'); } else nav.hidden = true;
    } catch (e) {
      el.innerHTML = `<div class="stat stat-error"><dt>Live stats</dt><dd>${e.status === 0 ? 'Server offline' : 'Unavailable'}</dd></div>`;
    }
  }

  // ================================================================ VIEW: Upload / Inspect
  let lastJob = null; // survives route changes so a running job keeps reporting

  async function viewUpload(root, _arg, alive) {
    root.innerHTML = `
      <section class="hero">
        <h1>Every pothole, counted once.</h1>
        <p>Two ways to add road damage. Either way, Nagrik Roads finds each pothole or crack, counts it once,
        and pins it on the ward map.</p>
      </section>
      <div class="scan-choices">
        <a class="choice" href="/live.html">
          <span class="choice-k">While driving · real time</span>
          <b>Live scan</b>
          <span class="muted">Use a phone or camera. Damage shows up on the map within seconds of being seen.</span>
          <span class="btn btn-primary">Start live scan</span>
        </a>
        <a class="choice" href="#upload-form" id="choice-upload">
          <span class="choice-k">After the drive</span>
          <b>Upload a video or photo</b>
          <span class="muted">Dashcam footage, a phone recording, or a photo of one pothole.</span>
          <span class="btn">Choose a file</span>
        </a>
      </div>
      <div class="grid-2">
        <form class="panel" id="upload-form" novalidate>
          <h2>Upload a video or photo</h2>
          <div class="dropzone" id="dropzone">
            <input type="file" id="f-file" name="file" accept="image/*,video/*" aria-describedby="drop-hint" required>
            <strong>Drop a video or photo here</strong>
            <span class="muted" id="drop-hint">or click to choose. MP4, MOV, WebM, JPG or PNG.</span>
            <div class="file-name" id="file-name" aria-live="polite"></div>
          </div>
          <div class="fields">
            <div class="field">
              <label for="f-lat">Latitude <span class="hint">optional</span></label>
              <input type="number" id="f-lat" name="lat" step="any" inputmode="decimal" placeholder="12.9716">
            </div>
            <div class="field">
              <label for="f-lon">Longitude <span class="hint">optional</span></label>
              <input type="number" id="f-lon" name="lon" step="any" inputmode="decimal" placeholder="77.5946">
            </div>
          </div>
          <div class="row">
            <button type="button" class="btn btn-small" id="btn-locate">Use my location</button>
            <span class="small muted" id="locate-status" aria-live="polite">Photos with GPS in EXIF don't need this.</span>
          </div>
          <div class="fields">
            <div class="field">
              <label for="f-track">GPS track <span class="hint">GPX, CSV or JSON, for videos</span></label>
              <input type="file" id="f-track" name="gps_track" accept=".gpx,.csv,.json,application/json,text/csv">
            </div>
            <div class="field">
              <label for="f-road">Road type</label>
              <select id="f-road" name="road_type">
                <option value="">Detect from map</option>
                ${ROAD_TYPES.map((r) => `<option value="${r}">${cap(r)}</option>`).join('')}
              </select>
            </div>
          </div>
          <div class="field" style="margin-bottom:14px">
            <label for="f-notes">Notes <span class="hint">optional</span></label>
            <input type="text" id="f-notes" name="notes" placeholder="e.g. Morning survey, left lane">
          </div>
          <div class="row">
            <button type="submit" class="btn btn-primary" id="btn-submit">Start inspection</button>
            <a href="/capture" class="small">Record from a phone instead</a>
          </div>
          <p class="small" id="form-error" role="alert" style="color:var(--danger);margin:10px 0 0"></p>
        </form>
        <section class="panel" id="job-panel" aria-labelledby="job-title">
          <h2 id="job-title">Result</h2>
          <div id="job-body">${stateHtml('empty', 'No inspection running',
            'Start one on the left. You will see the annotated footage and how many raw detections collapsed into unique defects.')}</div>
        </section>
      </div>
      <section class="panel" id="recent" hidden>
        <h2>Recent inspections</h2>
        <div id="recent-body"></div>
      </section>`;

    const form = $('#upload-form', root);
    $('#choice-upload', root).addEventListener('click', (e) => { e.preventDefault(); $('#f-file', root).click(); });
    const fileInput = $('#f-file', root);
    const dz = $('#dropzone', root);
    const showName = () => {
      const f = fileInput.files[0];
      $('#file-name', root).textContent = f ? `${f.name} (${(f.size / 1048576).toFixed(1)} MB)` : '';
    };
    fileInput.addEventListener('change', showName);
    ['dragenter', 'dragover'].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add('drag'); }));
    ['dragleave', 'drop'].forEach((ev) => dz.addEventListener(ev, () => dz.classList.remove('drag')));
    dz.addEventListener('drop', (e) => {
      e.preventDefault();
      if (e.dataTransfer && e.dataTransfer.files.length) { fileInput.files = e.dataTransfer.files; showName(); }
    });

    $('#btn-locate', root).addEventListener('click', () => {
      const st = $('#locate-status', root);
      if (!navigator.geolocation) { st.textContent = 'Location is not available in this browser.'; return; }
      st.textContent = 'Getting your location…';
      navigator.geolocation.getCurrentPosition((pos) => {
        $('#f-lat', root).value = pos.coords.latitude.toFixed(6);
        $('#f-lon', root).value = pos.coords.longitude.toFixed(6);
        st.textContent = `Location set (accuracy ${Math.round(pos.coords.accuracy)} m).`;
      }, (err) => {
        st.textContent = err.code === 1 ? 'Location permission was denied. Enter coordinates by hand.' : 'Could not get a location fix. Enter coordinates by hand.';
      }, { enableHighAccuracy: true, timeout: 10000 });
    });

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const errEl = $('#form-error', root);
      errEl.textContent = '';
      const f = fileInput.files[0];
      if (!f) { errEl.textContent = 'Choose a video or photo to inspect.'; fileInput.focus(); return; }
      const lat = $('#f-lat', root).value.trim(), lon = $('#f-lon', root).value.trim();
      if ((lat && !lon) || (!lat && lon)) { errEl.textContent = 'Enter both latitude and longitude, or leave both empty.'; return; }
      const fd = new FormData();
      fd.append('file', f);
      if (lat) fd.append('lat', lat);
      if (lon) fd.append('lon', lon);
      const track = $('#f-track', root).files[0];
      if (track) fd.append('gps_track', track);
      const rt = $('#f-road', root).value;
      if (rt) fd.append('road_type', rt);
      const notes = $('#f-notes', root).value.trim();
      if (notes) fd.append('notes', notes);
      const btn = $('#btn-submit', root);
      btn.disabled = true;
      btn.textContent = 'Uploading…';
      try {
        const r = await api('/api/inspections', { method: 'POST', body: fd });
        lastJob = { job_id: r.job_id, inspection_id: r.inspection_id, file: f.name, status: 'queued', progress: 0 };
        if (alive()) watchJob(root, alive);
      } catch (err) {
        errEl.textContent = err.message;
      } finally {
        btn.disabled = false;
        btn.textContent = 'Start inspection';
      }
    });

    if (lastJob) watchJob(root, alive);
    loadRecent(root, alive);
  }

  function renderProgress(root) {
    const j = lastJob;
    const p = Math.max(0, Math.min(1, +j.progress || 0));
    const label = j.status === 'queued' ? 'Waiting in queue' : 'Detecting, tracking and matching defects';
    $('#job-body', root).innerHTML = `
      <p class="small muted">${esc(j.file || 'Inspection #' + (j.inspection_id || ''))}</p>
      <div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(p * 100)}" aria-label="Inspection progress">
        <span style="width:${(p * 100).toFixed(1)}%"></span>
      </div>
      <div class="progress-label" aria-live="polite"><span>${esc(label)}…</span><span class="num">${Math.round(p * 100)}%</span></div>`;
  }

  function watchJob(root, alive) {
    renderProgress(root);
    let stopped = false;
    let fails = 0;
    const tick = async () => {
      if (stopped || !alive()) return;
      try {
        const j = await api('/api/jobs/' + encodeURIComponent(lastJob.job_id));
        fails = 0;
        Object.assign(lastJob, { status: j.status, progress: j.progress, result: j.result, error: j.error });
        if (!alive()) return;
        if (j.status === 'done') {
          lastJob.progress = 1;
          renderProgress(root);
          const iid = (j.result && (j.result.inspection_id || j.result.id)) || lastJob.inspection_id;
          lastJob = null;
          refreshStats();
          showInspection(root, iid, j.result || {}, alive);
          return;
        }
        if (j.status === 'error') {
          const msg = j.error || 'Processing failed.';
          lastJob = null;
          $('#job-body', root).innerHTML = stateHtml('error', 'Processing failed', msg,
            '<p class="small">Try a shorter clip or a different file.</p>');
          return;
        }
        renderProgress(root);
      } catch (err) {
        fails++;
        if (err.status === 404 || fails >= 5) {
          lastJob = null;
          $('#job-body', root).innerHTML = stateHtml('error', 'Lost track of this job', err.message);
          return;
        }
      }
      setTimeout(tick, 1000);
    };
    onCleanup(() => { stopped = true; });
    setTimeout(tick, 600);
  }

  async function showInspection(root, id, result, alive) {
    const body = $('#job-body', root);
    let insp = Object.assign({}, result);
    if (id != null) {
      try { insp = Object.assign(insp, await api('/api/inspections/' + encodeURIComponent(id))); } catch (e) {
        insp._warn = e.message;
      }
    }
    if (!alive()) return;
    const raw = pick(insp, 'raw_detections');
    const uniq = pick(insp, 'unique_defects');
    const newD = pick(insp, 'new_defects');
    const dup = pick(insp, 'duplicate_defects', 'matched_defects');
    const frames = pick(insp, 'frames_processed');
    const reduction = isNum(raw) && isNum(uniq) && raw > 0 ? (1 - uniq / raw) * 100 : null;
    const media = insp.media_url || insp.annotated_url || mediaUrl(insp.media_path);
    const isVideo = media && /\.(mp4|webm|ogg|mov|avi|mkv)(\?|$)/i.test(media);
    const playable = media && /\.(mp4|webm|ogg)(\?|$)/i.test(media);
    let mediaHtml = '';
    if (media && isVideo && playable) {
      mediaHtml = `<div class="media-frame"><video src="${esc(media)}" controls playsinline muted preload="metadata" aria-label="Annotated inspection video"></video></div>`;
    } else if (media && isVideo) {
      mediaHtml = `<p class="callout">The annotated video was saved in a format browsers can't play. <a href="${esc(media)}" download>Download it</a>.</p>`;
    } else if (media) {
      mediaHtml = `<div class="media-frame"><img src="${esc(media)}" alt="Annotated inspection image with detected damage outlined"></div>`;
    }
    const obs = asList(insp.observations);
    body.innerHTML = `
      ${insp._warn ? `<p class="callout small">${esc(insp._warn)} Showing the job summary only.</p>` : ''}
      ${isNum(raw) || isNum(uniq) ? tallyHtml(raw, uniq, { frames, newD, dup, reduction }) : ''}
      <div style="margin-top:16px">${mediaHtml || '<p class="muted small">No annotated media was returned for this inspection.</p>'}</div>
      ${obs.length ? `<h3 style="margin-top:16px">Defects in this inspection</h3>
        <ul class="obs-list">${obs.slice(0, 24).map(obsCardHtml).join('')}</ul>
        ${obs.length > 24 ? `<p class="small muted">and ${obs.length - 24} more.</p>` : ''}` : ''}
      <div class="row" style="margin-top:14px">
        <a class="btn" href="#/map">See them on the map</a>
        <a class="btn" href="#/defects">Open defect list</a>
        <a class="btn" href="#/review">Review detections</a>
      </div>`;
    $('#job-title', root).textContent = 'Result' + (insp.filename ? ': ' + insp.filename : '');
  }

  function tallyHtml(raw, uniq, o) {
    o = o || {};
    return `
      <div class="tally" role="group" aria-label="Raw detections compared with unique defects">
        <div class="tally-inner">
          <div class="tally-col tally-raw">
            <div class="tally-num">${fmtInt(raw)}</div>
            <div class="tally-label">raw detections across frames</div>
          </div>
          <svg class="tally-arrow" viewBox="0 0 54 32" aria-hidden="true"><path d="M2 16h40M32 5l14 11-14 11" fill="none" stroke="currentColor" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/></svg>
          <div class="tally-col">
            <div class="tally-num">${fmtInt(uniq)}</div>
            <div class="tally-label">unique defects after tracking</div>
          </div>
          <div class="tally-foot">
            ${o.reduction != null ? `<span><b>${o.reduction.toFixed(1)}%</b> duplicate sightings removed</span>` : ''}
            ${isNum(o.frames) ? `<span><b>${fmtInt(o.frames)}</b> frames analysed</span>` : ''}
            ${isNum(o.newD) ? `<span><b>${fmtInt(o.newD)}</b> new</span>` : ''}
            ${isNum(o.dup) ? `<span><b>${fmtInt(o.dup)}</b> matched to existing records</span>` : ''}
          </div>
        </div>
      </div>`;
  }

  function matchText(mi) {
    if (!mi || typeof mi !== 'object') return '';
    const bits = [];
    if (isNum(mi.distance_m)) bits.push(`${fmt1(mi.distance_m)} m away`);
    if (isNum(mi.similarity) && mi.similarity > 0) bits.push(`${Math.round(mi.similarity * 100)}% image match`);
    return (mi.reason || '') + (bits.length ? ` (${bits.join(', ')})` : '');
  }

  function obsCardHtml(o) {
    const img = obsCrop(o);
    const did = o.defect_id;
    return `<li>
      ${img ? `<img src="${esc(img)}" alt="${esc(classLabel(o.damage_class))} crop" loading="lazy">` : ''}
      <div class="obs-meta">
        ${did ? `<a href="#/defect/${esc(did)}"><strong>${esc(o.defect_code || 'Defect ' + did)}</strong></a>` : ''}
        <span>${esc(classLabel(o.damage_class))}${isNum(o.confidence) ? `, ${Math.round(o.confidence * 100)}% conf.` : ''}</span>
        <span>${sevBadge(o.severity_label, o.severity_score)}</span>
        ${o.match_info ? `<span class="muted">${esc(matchText(o.match_info))}</span>` : ''}
      </div></li>`;
  }

  async function loadRecent(root, alive) {
    try {
      const list = asList(await api('/api/inspections'), 'inspections');
      if (!alive() || !list.length) return;
      const sec = $('#recent', root);
      sec.hidden = false;
      const rows = list.slice().sort((a, b) => (b.id || 0) - (a.id || 0)).slice(0, 8);
      $('#recent-body', root).innerHTML = `<div class="table-wrap"><table>
        <thead><tr><th>When</th><th>File</th><th>Type</th><th class="num">Raw detections</th><th class="num">Unique defects</th><th><span class="sr-only">Action</span></th></tr></thead>
        <tbody>${rows.map((i) => `<tr>
          <td>${esc(fmtDate(i.created_at, true))}</td>
          <td style="overflow-wrap:anywhere">${esc(i.filename || '–')}</td>
          <td>${esc(i.source_type || '–')}</td>
          <td class="num">${fmtInt(i.raw_detections)}</td>
          <td class="num">${fmtInt(i.unique_defects)}</td>
          <td><button class="btn btn-small" type="button" data-insp="${esc(i.id)}">Show result</button></td>
        </tr>`).join('')}</tbody></table></div>`;
      $$('[data-insp]', sec).forEach((b) => b.addEventListener('click', () => {
        $('#job-body', root).innerHTML = loadingHtml('inspection');
        showInspection(root, b.dataset.insp, {}, alive);
        $('#job-panel', root).scrollIntoView({ behavior: 'smooth', block: 'start' });
      }));
    } catch (e) { /* recent list is optional */ }
  }

  // ================================================================ VIEW: Map
  const mapFilters = { cls: '', sev: '', wf: '', minRisk: 0, heat: false };

  function filterSelects(prefix) {
    return `
      <div class="field"><label for="${prefix}-cls">Damage type</label>
        <select id="${prefix}-cls"><option value="">All types</option>
          ${Object.keys(CLASS_LABELS).map((c) => `<option value="${c}">${CLASS_LABELS[c]}</option>`).join('')}</select></div>
      <div class="field"><label for="${prefix}-sev">Severity</label>
        <select id="${prefix}-sev"><option value="">Any severity</option>
          ${SEVERITY_LEVELS.map((s) => `<option>${s}</option>`).join('')}</select></div>
      <div class="field"><label for="${prefix}-wf">Workflow</label>
        <select id="${prefix}-wf"><option value="">Any stage</option>
          ${WORKFLOWS.map((w) => `<option value="${w}">${cap(w)}</option>`).join('')}</select></div>
      <div class="field field-range"><label for="${prefix}-risk">Minimum risk: <output id="${prefix}-risk-out">0</output></label>
        <input type="range" id="${prefix}-risk" min="0" max="100" step="5" value="0"></div>`;
  }
  const passes = (d, f) => (!f.cls || d.damage_class === f.cls) && (!f.sev || d.severity_label === f.sev) &&
    (!f.wf || (d.workflow || 'reported') === f.wf) && (!f.minRisk || (d.risk_score || 0) >= f.minRisk);

  function popupHtml(d) {
    const img = defectThumb(d);
    return `<div class="popup">
      ${img ? `<img src="${esc(img)}" alt="Photo of ${esc(d.code)}">` : ''}
      <div class="row" style="justify-content:space-between"><span class="code">${esc(d.code || '#' + d.id)}</span>${riskBadge(d)}</div>
      <div>${esc(classLabel(d.damage_class))}, severity ${esc(d.severity_label || '–')}</div>
      <div class="muted small">${esc(d.road_name || d.ward_name || '')}</div>
      <div class="small">${trendBadge(d.trend)} ${wfBadge(d.workflow)}</div>
      <p style="margin:6px 0 0"><a href="#/defect/${esc(d.id)}">Open details</a></p></div>`;
  }

  async function viewMap(root, _arg, alive) {
    root.innerHTML = `
      <div class="view-head"><h1>Road defects in Bengaluru</h1>
        <p>Each pin is one physical defect, coloured by repair priority, on Bengaluru's ward map. Click a pin for details or a ward to see what is in it.</p></div>
      <div class="map-layout">
        <div class="map-main">
          <div class="map-toolbar">
            <div class="seg" role="group" aria-label="Map view">
              <button type="button" id="m-pins" aria-pressed="true">Pins</button>
              <button type="button" id="m-heat" aria-pressed="false">Heatmap</button>
            </div>
            <label class="toggle"><input type="checkbox" id="m-wards" checked> Ward boundaries</label>
            <button type="button" class="btn btn-small" id="m-fit">Zoom to defects</button>
            <button type="button" class="btn btn-small" id="m-city">Whole city</button>
            <span class="live-pill" id="m-live" hidden><i aria-hidden="true"></i><span id="m-live-text">Live</span></span>
          </div>
          <div id="map-wrap">${loadingHtml('map')}</div>
          <div class="legend" id="m-legend" aria-label="Legend"></div>
        </div>
        <aside class="map-side" aria-label="Map tools">
          <div class="side-card">
            <label for="m-search" class="side-label">Find your ward</label>
            <input type="search" id="m-search" placeholder="Ward name or number, e.g. Indiranagar" autocomplete="off">
            <ul class="search-results" id="m-results" hidden></ul>
            <button type="button" class="btn btn-amber" id="m-locate">Use my location</button>
          </div>
          <div class="side-card" id="m-ward" hidden></div>
          <div class="side-card side-live" id="m-livebox" hidden></div>
          <div class="side-card">
            <span class="side-label">Show priority</span>
            <div class="chips" id="m-chips" role="group" aria-label="Priority filter">
              <button type="button" data-level="" aria-pressed="true">All</button>
              ${RISK_LEVELS.slice().reverse().map((l) => `<button type="button" data-level="${l}" aria-pressed="false"><i class="dot" style="background:${riskVar(l)}"></i>${l}</button>`).join('')}
            </div>
            <details class="more-filters"><summary>More filters</summary>
              <div class="filters">${filterSelects('m')}</div></details>
          </div>
          <div class="side-card">
            <span class="side-label">Fix first</span>
            <ol class="top-list" id="m-top"></ol>
          </div>
        </aside>
      </div>`;
    if (typeof maplibregl === 'undefined' || !window.RSMap) {
      $('#map-wrap', root).innerHTML = stateHtml('error', 'The map did not load',
        'The map library is loaded from unpkg.com. Check the internet connection, then reload. The defect list still works offline.',
        '<p><a class="btn" href="#/defects">Open defect list</a></p>');
      return;
    }
    let defects;
    try { defects = asList(await api('/api/defects'), 'defects'); } catch (e) {
      if (!alive()) return;
      $('#map-wrap', root).innerHTML = errorHtml(e, 'defects');
      bindRetry(root);
      return;
    }
    if (!alive()) return;
    $('#map-wrap', root).innerHTML = '<div id="map" class="mlmap" role="region" aria-label="Map of road defects"></div><p class="small muted" id="m-note" aria-live="polite"></p>';
    const map = RSMap.create($('#map', root), { zoom: 10.6 });
    onCleanup(() => map.remove());
    const popup = new maplibregl.Popup({ maxWidth: '260px', offset: 10 });
    await RSMap.ready(map);
    if (!alive()) return;
    const wards = await RSMap.addWards(map);
    if (!alive()) return;
    const byId = new Map();
    const fc = (list) => ({ type: 'FeatureCollection', features: list.map((d) => ({
      type: 'Feature', id: d.id, geometry: { type: 'Point', coordinates: [d.lon, d.lat] },
      properties: { id: d.id, risk: riskLabel(d) || 'Low', score: d.risk_score || 0 } })) });
    map.addSource('defects', { type: 'geojson', data: fc([]) });
    const riskColor = ['match', ['get', 'risk'], ...Object.entries(RSMap.RISK).flat(), RSMap.RISK.Low];
    map.addLayer({ id: 'defects-heat', type: 'heatmap', source: 'defects', layout: { visibility: 'none' }, paint: {
      'heatmap-weight': ['interpolate', ['linear'], ['get', 'score'], 0, 0.2, 100, 1],
      'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 10, 18, 15, 40],
      'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 10, 1, 15, 2.5],
      'heatmap-color': ['interpolate', ['linear'], ['heatmap-density'], 0, 'rgba(47,125,85,0)', 0.25, 'rgba(47,125,85,.55)',
        0.5, 'rgba(168,103,15,.7)', 0.75, 'rgba(196,88,31,.8)', 1, 'rgba(184,58,63,.9)'] } });
    map.addLayer({ id: 'defects-halo', type: 'circle', source: 'defects', filter: ['==', ['get', 'risk'], 'Critical'], paint: {
      'circle-color': RSMap.RISK.Critical, 'circle-opacity': 0.18,
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 9, 16, 20] } });
    map.addLayer({ id: 'defects', type: 'circle', source: 'defects', paint: {
      'circle-color': riskColor,
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, ['match', ['get', 'risk'], 'Critical', 7.5, 'High', 7, 6],
        16, ['match', ['get', 'risk'], 'Critical', 11, 'High', 10, 8]],
      'circle-stroke-color': '#ffffff', 'circle-stroke-width': 2 } });

    // ward of each defect (point-in-polygon on Nagrik's boundaries)
    const wardOf = new Map();
    const indexWards = () => {
      wardOf.clear();
      if (!wards) return;
      defects.forEach((d) => { if (isNum(d.lat) && isNum(d.lon)) { const w = RSMap.wardAt(wards, d.lon, d.lat); if (w) wardOf.set(d.id, w.properties.ward_id); } });
    };
    indexWards();

    // restore filters
    $('#m-cls', root).value = mapFilters.cls;
    $('#m-sev', root).value = mapFilters.sev;
    $('#m-wf', root).value = mapFilters.wf;
    $('#m-risk', root).value = mapFilters.minRisk;
    $('#m-risk-out', root).textContent = mapFilters.minRisk;
    mapFilters.level = mapFilters.level || '';
    $$('#m-chips button', root).forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.level === mapFilters.level)));

    let shown = [];
    function draw(fit) {
      byId.clear();
      defects.forEach((d) => byId.set(d.id, d));
      const geo = defects.filter((d) => isNum(d.lat) && isNum(d.lon));
      shown = geo.filter((d) => passes(d, mapFilters) && (!mapFilters.level || (riskLabel(d) || 'Low') === mapFilters.level));
      map.getSource('defects').setData(fc(shown));
      const counts = {};
      geo.forEach((d) => { const l = riskLabel(d) || 'Low'; counts[l] = (counts[l] || 0) + 1; });
      $('#m-legend', root).innerHTML = RISK_LEVELS.slice().reverse().map((l) =>
        `<span><i class="dot" style="background:${riskVar(l)}"></i>${l} <span class="muted num">${counts[l] || 0}</span></span>`).join('') +
        `<span class="muted">${shown.length} of ${defects.length} defects shown</span>` +
        (wards ? `<span class="legend-wards">${Object.values(RSMap.CORPS).map((c) => `<span><i class="sq" style="background:${c.fill}"></i>${esc(c.name.replace('Bengaluru ', ''))}</span>`).join('')}</span>` : '');
      const noGps = defects.length - geo.length;
      $('#m-note', root).textContent = !defects.length ? 'No defects yet. Scan a road to fill the map.'
        : (shown.length ? '' : 'No defects match this filter. ') + (noGps ? `${noGps} defect${noGps > 1 ? 's have' : ' has'} no GPS and ${noGps > 1 ? 'are' : 'is'} only in the list.` : '');
      const top = shown.slice().sort((x, y) => (y.risk_score || 0) - (x.risk_score || 0)).slice(0, 6);
      $('#m-top', root).innerHTML = top.length ? top.map((d) => `<li><button type="button" data-id="${esc(d.id)}">
          <i class="dot" style="background:${riskVar(riskLabel(d) || 'Low')}"></i>
          <span><b class="code">${esc(d.code || '#' + d.id)}</b> ${esc(classLabel(d.damage_class))}<br>
          <span class="muted small">${esc(d.road_name || d.ward_name || '')}</span></span>
          <span class="num score">${isNum(d.risk_score) ? Math.round(d.risk_score) : '–'}</span></button></li>`).join('')
        : '<li class="muted small">Nothing to fix with this filter.</li>';
      $$('#m-top button', root).forEach((btn) => btn.addEventListener('click', () => focusDefect(byId.get(+btn.dataset.id))));
      if (fit && shown.length) {
        const b = new maplibregl.LngLatBounds();
        shown.forEach((d) => b.extend([d.lon, d.lat]));
        map.fitBounds(b, { padding: 60, maxZoom: 15, duration: 0 });
      }
    }
    function openPopup(d) {
      popup.setLngLat([d.lon, d.lat]).setHTML(popupHtml(d)).addTo(map);
    }
    function focusDefect(d) {
      if (!d) return;
      map.flyTo({ center: [d.lon, d.lat], zoom: Math.max(map.getZoom(), 16), duration: 700 });
      openPopup(d);
    }
    function showWard(f, how) {
      const box = $('#m-ward', root);
      if (!f) { box.hidden = true; return; }
      const pr = f.properties;
      const here = defects.filter((d) => wardOf.get(d.id) === pr.ward_id)
        .sort((x, y) => (y.risk_score || 0) - (x.risk_score || 0));
      box.hidden = false;
      box.innerHTML = `<div class="ward-head"><span class="ward-no">${esc(pr.ward_number)}</span>
          <div><b>${esc(pr.name)}</b> <span class="muted">${esc(pr.name_local || '')}</span>
          <div class="muted small">${esc(RSMap.corpName(pr.corporation))} City Corporation${how === 'your location' ? ' · you are here' : ''}</div></div></div>
        <p class="ward-count"><b class="num">${here.length}</b> defect${here.length === 1 ? '' : 's'} recorded in this ward</p>
        ${here.length ? `<ul class="ward-list">${here.slice(0, 5).map((d) => `<li><button type="button" data-id="${esc(d.id)}"><i class="dot" style="background:${riskVar(riskLabel(d) || 'Low')}"></i><span class="code">${esc(d.code)}</span> ${esc(classLabel(d.damage_class))}</button></li>`).join('')}</ul>` : ''}
        <p class="small" style="margin:8px 0 0"><a href="#/defects">Open the defect list</a> · <a href="https://nagrik.in.net/bengaluru" target="_blank" rel="noopener">Ward officials on Nagrik ↗</a></p>`;
      $$('.ward-list button', box).forEach((btn) => btn.addEventListener('click', () => focusDefect(byId.get(+btn.dataset.id))));
      if (how !== 'clicked') map.fitBounds(RSMap.bbox(f), { padding: 50, maxZoom: 15, duration: 700 });
    }

    // clicks: a pin opens its popup; elsewhere inside a ward shows the ward card
    map.on('click', (e) => {
      const hit = map.queryRenderedFeatures(e.point, { layers: ['defects'] });
      if (hit.length) { openPopup(byId.get(hit[0].properties.id)); return; }
      if (map.getLayer('wards-fill')) {
        const w = map.queryRenderedFeatures(e.point, { layers: ['wards-fill'] });
        const f = w.length && wards && wards.features.find((x) => x.properties.ward_id === w[0].properties.ward_id);
        if (f) showWard(f, 'clicked');
      }
    });
    map.on('mouseenter', 'defects', () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', 'defects', () => { map.getCanvas().style.cursor = ''; });

    // ward search
    const search = $('#m-search', root), results = $('#m-results', root);
    search.disabled = !wards;
    if (!wards) search.placeholder = 'Ward boundaries unavailable offline';
    search.addEventListener('input', () => {
      const q = search.value.trim().toLowerCase();
      if (!q || !wards) { results.hidden = true; return; }
      const hits = wards.features.filter((f) => {
        const pr = f.properties;
        return String(pr.ward_number) === q || (pr.name || '').toLowerCase().includes(q) || (pr.name_local || '').includes(search.value.trim());
      }).slice(0, 8);
      results.hidden = false;
      results.innerHTML = hits.length ? hits.map((f, i) => `<li><button type="button" data-i="${i}"><span class="ward-no sm">${esc(f.properties.ward_number)}</span>${esc(f.properties.name)} <span class="muted small">${esc(f.properties.corporation)}</span></button></li>`).join('')
        : '<li class="muted small" style="padding:6px 8px">No ward matches.</li>';
      $$('button', results).forEach((btn) => btn.addEventListener('click', () => {
        results.hidden = true; search.value = hits[+btn.dataset.i].properties.name; showWard(hits[+btn.dataset.i], 'search');
      }));
    });
    let meMarker = null;
    $('#m-locate', root).addEventListener('click', () => {
      if (!navigator.geolocation) { toast('This browser cannot share its location.', true); return; }
      const btn = $('#m-locate', root);
      btn.disabled = true; btn.textContent = 'Finding you…';
      navigator.geolocation.getCurrentPosition((pos) => {
        btn.disabled = false; btn.textContent = 'Use my location';
        const { longitude: lon, latitude: lat } = pos.coords;
        if (!meMarker) { const el = document.createElement('div'); el.className = 'me-dot'; meMarker = new maplibregl.Marker({ element: el }); }
        meMarker.setLngLat([lon, lat]).addTo(map);
        const f = RSMap.wardAt(wards, lon, lat);
        if (f) showWard(f, 'your location');
        else { map.flyTo({ center: [lon, lat], zoom: 15 }); toast('You are outside the Bengaluru wards Nagrik maps.'); }
      }, () => {
        btn.disabled = false; btn.textContent = 'Use my location';
        toast('Location is blocked. Allow location for this site and turn on Windows Location.', true);
      }, { enableHighAccuracy: true, timeout: 15000 });
    });

    // filters
    const onChange = () => {
      mapFilters.cls = $('#m-cls', root).value;
      mapFilters.sev = $('#m-sev', root).value;
      mapFilters.wf = $('#m-wf', root).value;
      mapFilters.minRisk = +$('#m-risk', root).value;
      $('#m-risk-out', root).textContent = mapFilters.minRisk;
      draw(false);
    };
    ['#m-cls', '#m-sev', '#m-wf'].forEach((sel) => $(sel, root).addEventListener('change', onChange));
    $('#m-risk', root).addEventListener('input', onChange);
    $$('#m-chips button', root).forEach((btn) => btn.addEventListener('click', () => {
      mapFilters.level = btn.dataset.level;
      $$('#m-chips button', root).forEach((x) => x.setAttribute('aria-pressed', String(x === btn)));
      draw(false);
    }));
    const setView = (heat) => {
      mapFilters.heat = heat;
      $('#m-heat', root).setAttribute('aria-pressed', String(heat));
      $('#m-pins', root).setAttribute('aria-pressed', String(!heat));
      map.setLayoutProperty('defects-heat', 'visibility', heat ? 'visible' : 'none');
      ['defects', 'defects-halo'].forEach((id) => map.setLayoutProperty(id, 'visibility', heat ? 'none' : 'visible'));
    };
    $('#m-heat', root).addEventListener('click', () => setView(true));
    $('#m-pins', root).addEventListener('click', () => setView(false));
    $('#m-wards', root).addEventListener('change', (e) => {
      ['wards-fill', 'wards-line', 'wards-label'].forEach((id) => map.getLayer(id) && map.setLayoutProperty(id, 'visibility', e.target.checked ? 'visible' : 'none'));
    });
    if (!wards) { $('#m-wards', root).disabled = true; $('#m-wards', root).parentElement.title = 'Ward boundaries could not be loaded'; }
    draw(false);
    setView(!!mapFilters.heat);
    $('#m-fit', root).addEventListener('click', () => draw(true));
    $('#m-city', root).addEventListener('click', () => map.flyTo({ center: RSMap.BLR, zoom: 10.6 }));

    // live: vehicles scanning right now and their newest detections (polls only on the live API)
    if (!MOCK) {
      const vehicles = {};
      let since = 0, first = true;
      const liveTick = async () => {
        let d;
        try { d = await (await fetch('/api/live/feed?since=' + since, { cache: 'no-store' })).json(); } catch (e) { return; }
        if (!alive()) return;
        const fresh = first ? [] : d.items;
        if (d.items.length) since = d.items[d.items.length - 1].id;
        first = false;
        const on = d.active.length > 0;
        $('#m-live', root).hidden = !on;
        $('#m-live-text', root).textContent = on ? `Live · ${d.active.length} vehicle${d.active.length > 1 ? 's' : ''} scanning` : 'Live';
        for (const v of d.vehicles) {
          if (!vehicles[v.session_id]) { const el = document.createElement('div'); el.className = 'vehicle-dot'; vehicles[v.session_id] = new maplibregl.Marker({ element: el }).setLngLat([v.lon, v.lat]).addTo(map); }
          else vehicles[v.session_id].setLngLat([v.lon, v.lat]);
        }
        Object.keys(vehicles).forEach((k) => { if (!d.active.includes(+k)) { vehicles[k].remove(); delete vehicles[k]; } });
        const box = $('#m-livebox', root);
        if (on) {
          const fv = d.vehicles.find((v) => v.frame_at);
          box.hidden = false;
          box.innerHTML = `<span class="side-label"><span class="rec" aria-hidden="true"></span>Scanning now</span>
            ${fv ? `<img class="live-evidence" src="/api/live/${fv.session_id}/latest.jpg?t=${encodeURIComponent(fv.frame_at)}" alt="Latest frame with detected damage; faces and number plates blurred">
            <p class="muted small" style="margin:4px 0 0">Latest evidence · ${esc(fmtDate(fv.frame_at, true))} · faces and plates blurred</p>` : '<p class="muted small">Waiting for the first detection…</p>'}`;
          if (d.vehicles[0] && first === false && !box.dataset.followed) { map.flyTo({ center: [d.vehicles[0].lon, d.vehicles[0].lat], zoom: 16 }); box.dataset.followed = '1'; }
        } else { box.hidden = true; delete box.dataset.followed; }
        if (fresh.length) {
          try { defects = asList(await api('/api/defects'), 'defects'); } catch (e) { return; }
          if (!alive()) return;
          indexWards();
          draw(false);
        }
      };
      liveTick();
      const t = setInterval(liveTick, 2500);
      onCleanup(() => { clearInterval(t); Object.values(vehicles).forEach((m) => m.remove()); });
    }
  }

  // ================================================================ VIEW: Defects table
  const tableState = { key: 'risk_score', dir: -1, q: '', risk: '' };
  const COLUMNS = [
    { key: 'code', label: 'Code' },
    { key: 'damage_class', label: 'Type' },
    { key: 'severity_score', label: 'Severity', num: true },
    { key: 'risk_score', label: 'Risk', num: true },
    { key: 'trend', label: 'Trend' },
    { key: 'report_count', label: 'Reports', num: true },
    { key: 'ward_name', label: 'Ward' },
    { key: 'workflow', label: 'Workflow' },
    { key: 'last_seen', label: 'Last seen' },
  ];

  async function viewDefects(root, _arg, alive) {
    const csv = pageUrl('csv');
    root.innerHTML = `
      <div class="view-head"><h1>Defects</h1>
        <p>One row per physical defect, highest risk first. Select a column heading to sort.</p></div>
      <div class="filters">
        <div class="field" style="flex:2 1 220px"><label for="d-q">Search</label>
          <input type="text" id="d-q" placeholder="Code, road or ward" value="${esc(tableState.q)}"></div>
        <div class="field"><label for="d-risk">Risk</label>
          <select id="d-risk"><option value="">Any risk</option>${RISK_LEVELS.map((l) => `<option>${l}</option>`).join('')}</select></div>
        <a class="btn" href="${esc(csv)}" download>Download CSV</a>
      </div>
      <div id="d-body">${loadingHtml('defects')}</div>`;
    $('#d-risk', root).value = tableState.risk;
    let list;
    try { list = asList(await api('/api/defects'), 'defects'); } catch (e) {
      if (!alive()) return;
      $('#d-body', root).innerHTML = errorHtml(e, 'defects');
      bindRetry(root);
      return;
    }
    if (!alive()) return;
    const body = $('#d-body', root);
    if (!list.length) {
      body.innerHTML = stateHtml('empty', 'No defects recorded yet', 'Upload dashcam footage or a photo to start building the register.',
        '<p><a class="btn btn-primary" href="#/upload">Inspect a road</a></p>');
      return;
    }
    function render() {
      const q = tableState.q.toLowerCase();
      let rows = list.filter((d) => (!tableState.risk || riskLabel(d) === tableState.risk) &&
        (!q || [d.code, d.road_name, d.ward_name, classLabel(d.damage_class)].some((v) => v && String(v).toLowerCase().includes(q))));
      const k = tableState.key, dir = tableState.dir;
      rows = rows.slice().sort((a, b) => {
        const va = a[k], vb = b[k];
        if (va == null && vb == null) return 0;
        if (va == null) return 1;
        if (vb == null) return -1;
        return (typeof va === 'number' && typeof vb === 'number' ? va - vb : String(va).localeCompare(String(vb), 'en', { numeric: true })) * dir;
      });
      body.innerHTML = `<div class="table-wrap"><table class="table-defects">
        <caption class="sr-only">Defects, sorted by ${esc(k)} ${dir > 0 ? 'ascending' : 'descending'}</caption>
        <thead><tr>${COLUMNS.map((c) => `<th scope="col" class="${c.num ? 'num' : ''}" ${c.key === k ? `aria-sort="${dir > 0 ? 'ascending' : 'descending'}"` : ''}>
          <button type="button" class="sort" data-key="${c.key}">${c.label}</button></th>`).join('')}</tr></thead>
        <tbody>${rows.map((d) => `<tr>
          <td data-label="Code"><a class="code" href="#/defect/${esc(d.id)}">${esc(d.code || '#' + d.id)}</a>${d.bad_repair ? ' <span class="badge badge-bad-repair" title="Reappeared after repair">Bad repair</span>' : ''}</td>
          <td data-label="Type">${esc(classLabel(d.damage_class))}</td>
          <td data-label="Severity" class="num">${sevBadge(d.severity_label, d.severity_score)}</td>
          <td data-label="Risk" class="num">${riskBadge(d)}</td>
          <td data-label="Trend">${trendBadge(d.trend)}</td>
          <td data-label="Reports" class="num">${fmtInt(d.report_count)}</td>
          <td data-label="Ward">${esc(d.ward_name || '–')}</td>
          <td data-label="Workflow">${wfBadge(d.workflow)}</td>
          <td data-label="Last seen"><span title="${esc(fmtDate(d.last_seen, true))}">${esc(ago(d.last_seen))}</span></td>
        </tr>`).join('')}</tbody></table></div>
        <p class="small muted" style="margin-top:8px">${rows.length} of ${list.length} defects.</p>`;
      if (!rows.length) body.insertAdjacentHTML('afterbegin', stateHtml('empty', 'No defects match', 'Clear the search or risk filter.'));
      $$('button.sort', body).forEach((b) => b.addEventListener('click', () => {
        const key = b.dataset.key;
        if (tableState.key === key) tableState.dir *= -1;
        else { tableState.key = key; tableState.dir = COLUMNS.find((c) => c.key === key).num || key === 'last_seen' ? -1 : 1; }
        render();
        const nb = $(`button.sort[data-key="${key}"]`, body);
        if (nb) nb.focus();
      }));
    }
    $('#d-q', root).addEventListener('input', (e) => { tableState.q = e.target.value; render(); });
    $('#d-risk', root).addEventListener('change', (e) => { tableState.risk = e.target.value; render(); });
    render();
  }

  // ================================================================ VIEW: Defect detail
  async function viewDefect(root, id, alive) {
    root.innerHTML = loadingHtml('defect');
    if (!id) { root.innerHTML = stateHtml('empty', 'No defect selected', '', '<p><a class="btn" href="#/defects">All defects</a></p>'); return; }
    let d;
    try { d = await api('/api/defects/' + encodeURIComponent(id)); } catch (e) {
      if (!alive()) return;
      root.innerHTML = e.status === 404
        ? stateHtml('empty', 'Defect not found', `There is no defect with id ${id}. It may have been merged or removed.`, '<p><a class="btn" href="#/defects">All defects</a></p>')
        : errorHtml(e, 'this defect');
      bindRetry(root);
      return;
    }
    if (!alive()) return;
    renderDefect(root, d, alive);
  }

  function renderDefect(root, d, alive) {
    const obs = asList(d.observations).slice().sort((a, b) => String(a.observed_at || '').localeCompare(String(b.observed_at || '')));
    const seen = obs.filter((o) => !o.absent && obsFrame(o));
    const first = seen[0];
    const latest = obs.length ? obs[obs.length - 1] : null;
    const latestImg = latest && (obsFrame(latest) || (seen.length ? obsFrame(seen[seen.length - 1]) : null));
    const factors = asList(d.risk_factors, 'factors');
    const maxPts = Math.max(1, ...factors.map((f) => Math.abs(+f.points || 0)));
    const fc = d.forecast && typeof d.forecast === 'object' ? d.forecast : (typeof d.forecast === 'string' ? { text: d.forecast } : null);
    const ng = d.nagrik && typeof d.nagrik === 'object' ? d.nagrik : null;
    const officials = ng ? asList(ng.officials) : [];
    const works = ng ? asList(ng.works || ng.tenders || ng.ward_works) : [];
    const ward = d.ward_name || (ng && ng.ward_name);
    const wf = d.workflow || 'reported';
    const complaint = pageUrl('complaint', d.id);
    // repair evidence: before = earliest sighting with a photo, after = the uploaded repair photo
    const repairImg = d.repair_photo_url || mediaUrl(d.repair_photo);
    const beforeImg = first ? (obsCrop(first) || obsFrame(first)) : null;
    const isRepaired = wf === 'repaired' || wf === 'closed' || d.status === 'repaired';
    const crewId = d.repaired_by ? String(d.repaired_by) : '';

    root.innerHTML = `
      <p class="small" style="margin:0 0 8px"><a href="#/defects">All defects</a></p>
      <div class="view-head">
        <div class="detail-title">
          <h1>${esc(d.code || 'Defect #' + d.id)}</h1>
          ${riskBadge(d)} ${trendBadge(d.trend)} ${wfBadge(wf)}
          ${d.bad_repair ? '<span class="badge badge-bad-repair">Bad repair</span>' : ''}
        </div>
        <p>${esc(classLabel(d.damage_class))}${d.road_name ? ' on ' + esc(d.road_name) : ''}${ward ? ', ' + esc(ward) + ' ward' : ''}</p>
      </div>
      ${d.bad_repair ? `<div class="callout callout-danger" role="note" style="margin-bottom:16px"><strong>This defect came back after a repair.</strong>
        It was marked repaired${d.repaired_at ? ' on ' + esc(fmtDate(d.repaired_at)) : ''} and was seen again within 90 days.
        Ask the contractor to fix it under the defect-liability period before paying for new work.</div>` : ''}
      <div class="grid-detail">
        <div class="stack">
          <section class="panel" aria-labelledby="ba-title">
            <h2 id="ba-title">Before and after</h2>
            ${first && latestImg && obs.length > 1 ? `
              <div class="compare" id="compare" style="--pos:50%">
                <img src="${esc(obsFrame(first))}" alt="First sighting, ${esc(fmtDate(first.observed_at))}">
                <img class="after" id="cmp-after" src="${esc(latestImg)}" alt="Selected sighting">
                <span class="tag tag-before">First seen ${esc(fmtDate(first.observed_at))}</span>
                <span class="tag tag-after" id="cmp-after-tag"></span>
                <input type="range" min="0" max="100" value="50" id="cmp-range" aria-label="Drag to compare first and selected sighting">
                <div class="divider"></div>
              </div>
              <p class="small muted" style="margin:6px 0 0">Drag across the photo, or use the arrow keys, to compare. Pick any sighting below as the "after" image.</p>`
            : latestImg ? `<div class="media-frame"><img src="${esc(latestImg)}" alt="Photo of ${esc(d.code)}"></div>
              <p class="small muted" style="margin:6px 0 0">Only one sighting so far. A before and after comparison appears after the next inspection of this spot.</p>`
            : stateHtml('empty', 'No photos stored', 'This defect has no saved frames.')}
            ${obs.length ? `<h3 style="margin-top:14px">Sightings (${obs.length})</h3>
            <ol class="timeline" id="timeline">${obs.map((o, i) => `<li class="${o.absent ? 'absent' : ''}">
              <button type="button" data-i="${i}" aria-pressed="${o === latest}" ${obsFrame(o) ? '' : 'disabled'}>
                ${obsFrame(o) ? `<img src="${esc(obsCrop(o))}" alt="" loading="lazy">` : '<div class="state small" style="padding:20px 4px">No image</div>'}
              </button>
              <div><strong>${esc(fmtDate(o.observed_at))}</strong></div>
              <div>${o.absent ? '<span class="muted">Not found at this spot</span>' : sevBadge(o.severity_label, o.severity_score)}</div>
              ${o.review_status && o.review_status !== 'pending' ? `<div class="muted">${esc(cap(o.review_status))}</div>` : o.review_status ? '<div class="muted">Awaiting review</div>' : ''}
            </li>`).join('')}</ol>
            <div id="obs-info" class="small muted" aria-live="polite"></div>` : ''}
          </section>
          ${repairImg && beforeImg ? `<section class="panel" aria-labelledby="rc-title">
            <h2 id="rc-title">Before / After repair</h2>
            <div class="row ba-tabs" role="group" aria-label="Choose repair photo view">
              <button type="button" class="btn btn-small" data-ba="before" aria-pressed="false">Before</button>
              <button type="button" class="btn btn-small" data-ba="after" aria-pressed="false">After</button>
              <button type="button" class="btn btn-small" data-ba="compare" aria-pressed="true">Compare</button>
            </div>
            <div class="ba-view" id="ba-view" data-mode="compare">
              <figure class="ba-fig ba-before">
                <img src="${esc(beforeImg)}" alt="Before repair: first sighting of ${esc(d.code || 'defect ' + d.id)}, ${esc(fmtDate(first.observed_at))}" loading="lazy">
                <figcaption><strong>Before</strong> <span class="muted">first seen ${esc(fmtDate(first.observed_at))}</span></figcaption>
              </figure>
              <figure class="ba-fig ba-after">
                <img src="${esc(repairImg)}" alt="After repair: repair photo of ${esc(d.code || 'defect ' + d.id)}${d.repaired_at ? ', ' + esc(fmtDate(d.repaired_at)) : ''}" loading="lazy">
                <figcaption><strong>After</strong> <span class="muted">${d.repaired_at ? 'repaired ' + esc(fmtDate(d.repaired_at)) : 'repair photo'}${crewId ? ' by ' + esc(crewId) : ''}</span></figcaption>
              </figure>
            </div>
          </section>` : ''}
          <section class="panel" aria-labelledby="loc-title">
            <h2 id="loc-title">Location</h2>
            ${isNum(d.lat) && isNum(d.lon) ? '<div id="mini-map" style="height:220px;border-radius:6px;border:1px solid var(--line);margin-bottom:12px;z-index:0"></div>' : ''}
            <dl class="kv">
              <dt>Road</dt><dd>${esc(d.road_name || 'Unknown')}${d.road_type ? ` (${esc(d.road_type)})` : ''}${isNum(d.speed_limit) ? `, ${d.speed_limit} km/h` : ''}</dd>
              <dt>Coordinates</dt><dd>${isNum(d.lat) && isNum(d.lon) ? `<a href="https://www.openstreetmap.org/?mlat=${d.lat}&mlon=${d.lon}#map=18/${d.lat}/${d.lon}" target="_blank" rel="noopener">${d.lat.toFixed(5)}, ${d.lon.toFixed(5)}</a>` : 'No GPS'}</dd>
              <dt>First seen</dt><dd>${esc(fmtDate(d.first_seen, true))}</dd>
              <dt>Last seen</dt><dd>${esc(fmtDate(d.last_seen, true))} (${esc(ago(d.last_seen))})</dd>
              <dt>Reports</dt><dd>${fmtInt(d.report_count)} sighting${d.report_count === 1 ? '' : 's'}</dd>
              <dt>Severity</dt><dd>${sevBadge(d.severity_label, d.severity_score)}</dd>
              ${d.repaired_at ? `<dt>Repaired on</dt><dd>${esc(fmtDate(d.repaired_at, true))}</dd>` : ''}
              ${crewId ? `<dt>Repaired by</dt><dd>${esc(crewId)}</dd>` : ''}
            </dl>
          </section>
        </div>

        <div class="stack">
          <section class="panel" aria-labelledby="risk-title">
            <h2 id="risk-title">Repair priority</h2>
            <div class="risk-score">
              <span class="big" style="color:${riskVar(riskLabel(d))}">${isNum(d.risk_score) ? Math.round(d.risk_score) : '–'}</span>
              <span class="muted">out of 100</span> ${riskBadge(d)}
            </div>
            ${factors.length ? `<ul class="bars">${factors.map((f) => `<li class="bar-row">
                <span class="bar-label">${esc(FACTOR_LABELS[f.factor || f.name] || cap(String(f.factor || f.name || '').replace(/_/g, ' ')))}</span><span class="bar-val num">+${fmt1(+f.points || 0)}</span>
                <div class="bar-track" aria-hidden="true"><div class="bar-fill" style="width:${Math.min(100, Math.abs(+f.points || 0) / maxPts * 100)}%;background:${riskVar(riskLabel(d))}"></div></div>
                ${f.detail ? `<span class="bar-detail">${esc(f.detail)}</span>` : ''}</li>`).join('')}</ul>
                <p class="small muted" style="margin:10px 0 0">Each factor adds points; the total is capped at 100.</p>`
              : '<p class="muted">The risk breakdown is not available for this defect yet.</p>'}
          </section>

          <section class="panel" aria-labelledby="fc-title">
            <h2 id="fc-title">Trend and forecast</h2>
            <p>${trendBadge(d.trend)}</p>
            ${fc && fc.text ? `<p class="forecast">${esc(fc.text)}</p>` : '<p class="muted">Not enough sightings to forecast growth yet.</p>'}
            ${fc && (isNum(fc.days_to_high) || isNum(fc.growth_per_week)) ? `<dl class="kv" style="margin-top:10px">
              ${isNum(fc.days_to_high) ? `<dt>Reaches High severity</dt><dd>in about ${fc.days_to_high} days</dd>` : ''}
              ${isNum(fc.growth_per_week) ? `<dt>Growth</dt><dd>${fc.growth_per_week > 0 ? '+' : ''}${fmt1(fc.growth_per_week)} severity points a week</dd>` : ''}
            </dl>` : ''}
          </section>

          <section class="panel" aria-labelledby="wf-title">
            <h2 id="wf-title">Workflow</h2>
            <p>Current stage: ${wfBadge(wf)}</p>
            <div class="field" style="margin-bottom:10px" ${ME ? '' : 'hidden'}>
              <label for="wf-note">Note <span class="hint">optional, saved with the change</span></label>
              <input type="text" id="wf-note" placeholder="e.g. Work order WO-2291 issued">
            </div>
            <div class="field" style="margin-bottom:10px" ${ME ? '' : 'hidden'}>
              <label for="wf-photo">Repair photo <span class="hint">optional, used with Mark repaired</span></label>
              <input type="file" id="wf-photo" accept="image/*">
            </div>
            <div class="field" style="margin-bottom:10px" ${ME ? '' : 'hidden'}>
              <label for="wf-crew">Repaired by (crew / employee ID) <span class="hint">${isRepaired ? 'who did the repair' : 'optional, saved with Mark repaired'}</span></label>
              <div class="row crew-row">
                <input type="text" id="wf-crew" maxlength="64" autocomplete="off" placeholder="e.g. BBMP-CREW-07" value="${esc(crewId)}">
                ${isRepaired ? `<button type="button" class="btn btn-small" id="wf-crew-save">${crewId ? 'Update crew ID' : 'Save crew ID'}</button>` : ''}
              </div>
            </div>
            ${ME ? `<div class="workflow-steps">${WORKFLOW_ACTIONS.filter((a) => can('officer', 'admin') || a.value === 'repaired').map((a) =>
              `<button type="button" class="btn btn-small${a.value === wf ? ' current' : ''}" data-wf="${a.value}" ${a.value === wf ? 'aria-current="step"' : ''}>${a.label}</button>`).join('')}</div>
              <p class="small muted" style="margin:8px 0 0">Signed in as ${esc(ME.name)} (${esc(ME.role_label || ME.role)}).</p>`
            : `<div class="callout"><b>Sign in to update this defect.</b> Ward officers verify and schedule repairs; repair crews mark them done.
                <p style="margin:8px 0 0"><a class="btn btn-small" href="${esc(loginUrl())}">Official sign in</a></p></div>`}
            <p style="margin:14px 0 0"><a class="btn btn-primary" href="${esc(complaint)}" target="_blank" rel="noopener">Open complaint</a></p>
            <p class="small muted" style="margin:6px 0 0">Opens a printable complaint with photo, location, risk and the responsible officer.</p>
          </section>

          <section class="panel" aria-labelledby="ward-title">
            <h2 id="ward-title">Ward and responsibility</h2>
            ${ng || ward ? `<dl class="kv">
                <dt>Ward</dt><dd>${esc(ward || 'Unknown')}${(d.ward_id || (ng && ng.ward_id)) ? ` (no. ${esc(d.ward_id || ng.ward_id)})` : ''}</dd>
                <dt>Corporation</dt><dd>${esc(d.corporation || (ng && ng.corporation) || 'Unknown')}</dd>
                ${d.city || (ng && ng.city) ? `<dt>City</dt><dd>${esc(d.city || ng.city)}</dd>` : ''}
              </dl>` : ''}
            ${officials.length ? `<h3 style="margin-top:14px">Officials</h3><ul style="padding-left:18px;margin:0">
              ${officials.map((o) => `<li>${esc(o.name || 'Officer')}${o.department ? `, <span class="muted">${esc(o.department)}</span>` : ''}
                ${o.phone ? ` <a href="tel:${esc(String(o.phone).replace(/[^\d+]/g, ''))}">${esc(o.phone)}</a>` : ''}</li>`).join('')}</ul>` : ''}
            ${works.length ? `<h3 style="margin-top:14px">Road works tenders</h3>
              <p class="callout small">A tender exists for this ward. Check whether this defect falls under it before raising new work.</p>
              ${works.map((w) => `<div class="tender">
                <strong>${esc(w.title || 'Road work')}</strong>
                <div class="muted">${[w.agency, w.tender_ref, w.status, w.tender_date && fmtDate(w.tender_date)].filter(Boolean).map(esc).join(', ')}</div>
                <div>${isNum(+w.budget) && w.budget ? 'Budget ₹' + fmtInt(+w.budget) : ''}${w.contractor_name ? `${w.budget ? ', ' : ''}contractor ${esc(w.contractor_name)}` : ''}</div>
                ${w.source_url ? `<a href="${esc(w.source_url)}" target="_blank" rel="noopener" class="small">Source</a>` : ''}
              </div>`).join('')}`
            : ng ? '<p class="muted small" style="margin-top:12px">No road or pothole tender found for this ward on Nagrik.</p>' : ''}
            ${!ng && !ward ? '<p class="muted">Ward details are not available. The location may be outside the covered cities, or the civic data service was offline.</p>' : ''}
          </section>
        </div>
      </div>`;

    // before/after slider + timeline
    const cmp = $('#compare', root);
    if (cmp) {
      const range = $('#cmp-range', root);
      range.addEventListener('input', () => cmp.style.setProperty('--pos', range.value + '%'));
    }
    const info = $('#obs-info', root);
    const selectObs = (i) => {
      const o = obs[i];
      $$('#timeline button', root).forEach((b) => b.setAttribute('aria-pressed', String(+b.dataset.i === i)));
      const after = $('#cmp-after', root);
      if (after && obsFrame(o)) { after.src = obsFrame(o); after.alt = 'Sighting ' + fmtDate(o.observed_at); }
      const tag = $('#cmp-after-tag', root);
      if (tag) tag.textContent = (o.absent ? 'Not found ' : 'Seen ') + fmtDate(o.observed_at);
      if (info) {
        const bits = [];
        if (isNum(o.confidence)) bits.push(`Detector confidence ${Math.round(o.confidence * 100)}%`);
        if (isNum(o.frames_seen)) bits.push(`tracked over ${o.frames_seen} frames`);
        if (o.match_info) bits.push(matchText(o.match_info));
        info.textContent = bits.join('; ');
      }
    };
    $$('#timeline button', root).forEach((b) => b.addEventListener('click', () => selectObs(+b.dataset.i)));
    if (obs.length) selectObs(obs.length - 1);

    // mini map
    if ($('#mini-map', root) && window.RSMap && typeof maplibregl !== 'undefined' && isNum(d.lat) && isNum(d.lon)) {
      const mm = RSMap.create($('#mini-map', root), { center: [d.lon, d.lat], zoom: 16, cooperative: true });
      onCleanup(() => mm.remove());
      RSMap.ready(mm).then(() => RSMap.addWards(mm, { fillOpacity: 0.12 }));
      const l = riskLabel(d) || 'Low';
      const pin = document.createElement('div');
      pin.className = `marker-pin ${l}`;
      pin.style.background = riskVar(l);
      new maplibregl.Marker({ element: pin }).setLngLat([d.lon, d.lat]).addTo(mm);
    } else if ($('#mini-map', root)) {
      $('#mini-map', root).remove();
    }

    // before / after repair tabs
    const baView = $('#ba-view', root);
    if (baView) {
      $$('[data-ba]', root).forEach((b) => b.addEventListener('click', () => {
        baView.dataset.mode = b.dataset.ba;
        $$('[data-ba]', root).forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
      }));
    }

    const reload = async (fallback) => {
      let fresh = null;
      try { fresh = await api('/api/defects/' + encodeURIComponent(d.id)); } catch (e) { /* keep local */ }
      if (!alive()) return;
      cleanups.forEach((fn) => { try { fn(); } catch (e) { /* */ } });
      cleanups = [];
      renderDefect(root, fresh || Object.assign({}, d, fallback), alive);
    };
    const saveCrew = (crew) => api(`/api/defects/${d.id}/repaired_by`, { method: 'POST', json: { crew_id: crew } });

    // set / edit the crew ID on an already repaired defect
    const crewBtn = $('#wf-crew-save', root);
    if (crewBtn) {
      crewBtn.addEventListener('click', async () => {
        const crew = $('#wf-crew', root).value.trim();
        if (!crew) { toast('Enter a crew or employee ID first.', true); $('#wf-crew', root).focus(); return; }
        crewBtn.disabled = true;
        try {
          await saveCrew(crew);
          toast(`Repaired by ${crew} saved.`);
          await reload({ repaired_by: crew });
        } catch (e) {
          toast(e.message, true);
          crewBtn.disabled = false;
        }
      });
      $('#wf-crew', root).addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); crewBtn.click(); } });
    }

    // workflow buttons
    $$('[data-wf]', root).forEach((b) => b.addEventListener('click', async () => {
      const target = b.dataset.wf;
      const note = $('#wf-note', root).value.trim();
      const photo = $('#wf-photo', root).files[0];
      const crew = $('#wf-crew', root).value.trim();
      $$('[data-wf]', root).forEach((x) => { x.disabled = true; });
      try {
        if (target === 'repaired') {
          const fd = new FormData();
          if (photo) fd.append('file', photo);
          if (note) fd.append('note', note);
          await api(`/api/defects/${d.id}/repair`, { method: 'POST', body: fd });
        } else {
          await api(`/api/defects/${d.id}/workflow`, { method: 'PATCH', json: note ? { workflow: target, note } : { workflow: target } });
        }
        // the repair itself succeeded; a crew ID failure must not look like a failed repair
        let crewErr = null;
        if (target === 'repaired' && crew && crew !== crewId) {
          try { await saveCrew(crew); } catch (e) { crewErr = e; }
        }
        if (crewErr) toast(`${d.code || 'Defect'} moved to ${target}, but the crew ID was not saved: ${crewErr.message}`, true);
        else toast(`${d.code || 'Defect'} moved to ${target}.`);
        refreshStats();
        await reload(Object.assign({ workflow: target }, target === 'repaired' && crew && !crewErr ? { repaired_by: crew } : {}));
      } catch (e) {
        toast(e.message, true);
        $$('[data-wf]', root).forEach((x) => { x.disabled = false; });
      }
    }));
  }

  // ================================================================ VIEW: Review queue
  async function viewReview(root, _arg, alive) {
    if (!can('officer', 'admin')) {
      root.innerHTML = `<div class="view-head"><h1>Review detections</h1>
        <p>Ward officers check what the AI found: confirm real damage, reject mistakes, and correct the type or box.</p></div>
        ${ME ? `<div class="state"><h2>Ward officers only</h2><p>You are signed in as ${esc(ME.role_label || ME.role)}. Reviewing detections needs a ward officer or administrator account.</p></div>`
             : signInHtml('review detections')}`;
      return;
    }
    const exp = pageUrl('corrections');
    root.innerHTML = `
      <div class="view-head"><h1>Review detections</h1>
        <p>Is the AI right? Each card is one defect the model was unsure about. Press <b>Confirm</b> if the box shows real damage, <b>Reject</b> if it doesn't, or <b>Correct</b> to fix the type or box. Your answers become training data.</p></div>
      <div class="row" style="justify-content:space-between;margin-bottom:16px">
        <details class="shortcut-help"><summary class="small muted">Keyboard shortcuts</summary>
          <p class="small" style="margin:6px 0 0"><kbd>C</kbd> confirm, <kbd>R</kbd> reject, <kbd>X</kbd> correct,
            <kbd>N</kbd> next, <kbd>P</kbd> previous, <kbd>Enter</kbd> save a correction, <kbd>Esc</kbd> cancel.
            To correct, pick the right type and drag a new box on the image.</p></details>
        ${exp ? `<a class="btn btn-small" href="${esc(exp)}" download>Export corrections for retraining</a>` : ''}
      </div>
      <div id="r-body">${loadingHtml('review queue')}</div>
      <p class="sr-only" id="r-live" aria-live="polite"></p>`;
    let items;
    try {
      // One card per defect: the same pothole seen on several scans would otherwise fill the queue.
      // The queue is sorted lowest confidence first, so the first sighting kept is the most doubtful one.
      const all = asList(await api('/api/review/queue?limit=300'), 'queue', 'observations');
      const byDefect = new Map();
      items = [];
      all.forEach((o) => {
        const g = o.defect_id != null ? byDefect.get(o.defect_id) : null;
        if (g) { g._others.push(o.id); return; }
        o._others = [];
        if (o.defect_id != null) byDefect.set(o.defect_id, o);
        items.push(o);
      });
      items = items.slice(0, 30);
    } catch (e) {
      if (!alive()) return;
      $('#r-body', root).innerHTML = errorHtml(e, 'the review queue');
      bindRetry(root);
      return;
    }
    if (!alive()) return;
    const body = $('#r-body', root);
    if (!items.length) {
      body.innerHTML = stateHtml('empty', 'Nothing to review', 'Every detection has been checked. New inspections will add low-confidence detections here.');
      return;
    }
    body.innerHTML = `<div class="review-grid">${items.map((o, i) => `
      <article class="panel review-card" data-i="${i}" tabindex="-1" aria-labelledby="rc-${i}">
        <div class="row" style="justify-content:space-between">
          <h2 id="rc-${i}" style="margin:0;font-size:1.05rem">${o.defect_id ? `<a href="#/defect/${esc(o.defect_id)}">${esc(o.defect_code || 'Defect ' + o.defect_id)}</a>` : 'Observation ' + esc(o.id)}</h2>
          <span class="row" style="gap:6px">${o._others.length ? `<span class="badge" title="Your answer applies to every sighting of this defect">${o._others.length + 1} sightings</span>` : ''}
            <span class="badge">${isNum(o.confidence) ? Math.round(o.confidence * 100) + '% sure' : 'confidence n/a'}</span></span>
        </div>
        <canvas class="review-canvas" width="640" height="480" aria-label="Detection image with bounding box"></canvas>
        <p class="small muted" style="margin:0">${esc(classLabel(o.damage_class))}${o.road_name ? ', ' + esc(o.road_name) : ''}${o.observed_at ? ', ' + esc(fmtDate(o.observed_at)) : ''}</p>
        <div class="review-actions">
          <button type="button" class="btn btn-small btn-ok" data-act="confirm">Confirm</button>
          <button type="button" class="btn btn-small btn-danger" data-act="reject">Reject</button>
          <button type="button" class="btn btn-small" data-act="correct" aria-expanded="false">Correct</button>
        </div>
        <div class="review-actions correct-panel" hidden>
          <label class="sr-only" for="rc-cls-${i}">Correct damage type</label>
          <select id="rc-cls-${i}">${Object.keys(CLASS_LABELS).map((c) => `<option value="${c}" ${c === o.damage_class ? 'selected' : ''}>${CLASS_LABELS[c]}</option>`).join('')}</select>
          <button type="button" class="btn btn-small btn-primary" data-act="save">Save correction</button>
          <button type="button" class="btn btn-small" data-act="cancel">Cancel</button>
          <span class="small muted" style="flex-basis:100%">Drag on the image to redraw the box.</span>
        </div>
        <p class="small status" role="status" style="margin:0"></p>
      </article>`).join('')}</div>`;

    const cards = $$('.review-card', body);
    const state = items.map((o) => ({ o, done: false, editing: false, box: null, img: null, scale: 1 }));
    let active = 0;

    const toFrame = (s, v) => v / s.scale; // canvas px -> frame px
    function drawCard(i) {
      const s = state[i];
      const cv = $('canvas', cards[i]);
      const ctx = cv.getContext('2d');
      ctx.clearRect(0, 0, cv.width, cv.height);
      if (s.img) ctx.drawImage(s.img, 0, 0, cv.width, cv.height);
      else {
        ctx.fillStyle = '#2b3036'; ctx.fillRect(0, 0, cv.width, cv.height);
        ctx.fillStyle = '#aab1b8'; ctx.font = '20px system-ui, sans-serif'; ctx.fillText('Image not available', 20, 36);
      }
      const b = s.box;
      if (b) {
        const [x1, y1, x2, y2] = b.map((v) => v * s.scale);
        ctx.lineWidth = Math.max(2, cv.width / 220);
        ctx.strokeStyle = s.editing ? '#f2c230' : '#ff5a36';
        ctx.setLineDash(s.editing ? [10, 6] : []);
        ctx.strokeRect(Math.min(x1, x2), Math.min(y1, y2), Math.abs(x2 - x1), Math.abs(y2 - y1));
        ctx.setLineDash([]);
        const label = classLabel(s.editing ? $('select', cards[i]).value : s.o.damage_class);
        ctx.font = `600 ${Math.max(14, cv.width / 40)}px system-ui, sans-serif`;
        const tw = ctx.measureText(label).width + 12, th = Math.max(20, cv.width / 30);
        const ly = Math.max(0, Math.min(y1, y2) - th);
        ctx.fillStyle = s.editing ? '#f2c230' : '#ff5a36';
        ctx.fillRect(Math.min(x1, x2), ly, tw, th);
        ctx.fillStyle = '#111';
        ctx.fillText(label, Math.min(x1, x2) + 6, ly + th * 0.75);
      }
    }

    state.forEach((s, i) => {
      const o = s.o;
      const cv = $('canvas', cards[i]);
      const fw = o.frame_w || 640, fh = o.frame_h || 480;
      s.box = Array.isArray(o.bbox) && o.bbox.length === 4 ? o.bbox.map(Number) : null;
      cv.width = Math.min(fw, 960);
      cv.height = Math.round(cv.width * fh / fw);
      s.scale = cv.width / fw;
      s.frameW = fw; s.frameH = fh;
      const src = obsFrame(o);
      drawCard(i);
      if (src) {
        const img = new Image();
        img.onload = () => { s.img = img; drawCard(i); };
        img.onerror = () => drawCard(i);
        img.src = src;
      }
      // drawing a new box
      let start = null;
      const pt = (e) => {
        const r = cv.getBoundingClientRect();
        return [(e.clientX - r.left) * cv.width / r.width, (e.clientY - r.top) * cv.height / r.height];
      };
      cv.addEventListener('pointerdown', (e) => {
        setActive(i);
        if (!s.editing) return;
        start = pt(e);
        cv.setPointerCapture(e.pointerId);
      });
      cv.addEventListener('pointermove', (e) => {
        if (!start) return;
        const p = pt(e);
        s.box = [toFrame(s, start[0]), toFrame(s, start[1]), toFrame(s, p[0]), toFrame(s, p[1])];
        drawCard(i);
      });
      const end = () => {
        if (!start) return;
        start = null;
        if (s.box) {
          const [x1, y1, x2, y2] = s.box;
          s.box = [Math.max(0, Math.min(x1, x2)), Math.max(0, Math.min(y1, y2)), Math.min(s.frameW, Math.max(x1, x2)), Math.min(s.frameH, Math.max(y1, y2))].map((v) => Math.round(v));
          drawCard(i);
        }
      };
      cv.addEventListener('pointerup', end);
      cv.addEventListener('pointercancel', end);
      $('select', cards[i]).addEventListener('change', () => drawCard(i));
      cards[i].addEventListener('click', (e) => {
        if (e.target.closest('a')) return;
        setActive(i, false);
        const act = e.target.closest('[data-act]');
        if (act) doAction(i, act.dataset.act);
      });
    });

    function setActive(i, focus) {
      if (i < 0 || i >= cards.length) return;
      active = i;
      cards.forEach((c, k) => c.classList.toggle('active', k === i));
      if (focus !== false) {
        cards[i].focus({ preventScroll: true });
        cards[i].scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      }
    }
    function nextOpen(from) {
      for (let k = from + 1; k < cards.length; k++) if (!state[k].done) return k;
      for (let k = 0; k < cards.length; k++) if (!state[k].done) return k;
      return -1;
    }
    function setEditing(i, on) {
      const s = state[i];
      s.editing = on;
      if (!on) s.box = Array.isArray(s.o.bbox) ? s.o.bbox.map(Number) : null;
      $('.correct-panel', cards[i]).hidden = !on;
      $('[data-act="correct"]', cards[i]).setAttribute('aria-expanded', String(on));
      $('canvas', cards[i]).classList.toggle('drawing', on);
      drawCard(i);
      if (on) $('select', cards[i]).focus();
    }
    async function doAction(i, act) {
      const s = state[i];
      if (s.done) return;
      if (act === 'correct') { setEditing(i, !s.editing); return; }
      if (act === 'cancel') { setEditing(i, false); cards[i].focus(); return; }
      const payload = { action: act === 'save' ? 'correct' : act, reviewer: 'dashboard' };
      if (act === 'save') {
        payload.corrected_class = $('select', cards[i]).value;
        if (s.box) payload.corrected_bbox = s.box.map((v) => Math.round(v));
      }
      const status = $('.status', cards[i]);
      $$('button', cards[i]).forEach((b) => { b.disabled = true; });
      status.textContent = 'Saving…';
      try {
        await api(`/api/observations/${encodeURIComponent(s.o.id)}/review`, { method: 'POST', json: payload });
        // same verdict for the other sightings of this defect (their boxes differ, so no corrected box)
        const rest = Object.assign({}, payload);
        delete rest.corrected_bbox;
        // one at a time, so the last rejection can see the others and mark the defect rejected
        for (const id of s.o._others || []) {
          await api(`/api/observations/${encodeURIComponent(id)}/review`, { method: 'POST', json: rest }).catch(() => null);
        }
        s.done = true;
        s.editing = false;
        $('canvas', cards[i]).classList.remove('drawing');
        $('.correct-panel', cards[i]).hidden = true;
        cards[i].classList.add('done');
        const word = { confirm: 'Confirmed', reject: 'Rejected', correct: 'Corrected' }[payload.action];
        status.textContent = word + '.';
        $('#r-live', root).textContent = `${s.o.defect_code || 'Observation ' + s.o.id} ${word.toLowerCase()}.`;
        refreshStats();
        const n = nextOpen(i);
        if (n >= 0) setActive(n);
        else {
          body.insertAdjacentHTML('afterbegin', stateHtml('empty', 'Queue cleared', 'All detections on this page are reviewed. Reload to fetch more.',
            '<p><button class="btn" type="button" data-retry>Load more</button></p>'));
          bindRetry(body);
        }
      } catch (e) {
        status.textContent = e.message;
        $$('button', cards[i]).forEach((b) => { b.disabled = false; });
      }
    }

    const onKey = (e) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const tag = (e.target.tagName || '').toLowerCase();
      const typing = tag === 'input' || tag === 'textarea';
      if (typing) return;
      const k = e.key.toLowerCase();
      const s = state[active];
      if (!s) return;
      if (k === 'escape' && s.editing) { e.preventDefault(); doAction(active, 'cancel'); return; }
      if (k === 'enter' && s.editing) { e.preventDefault(); doAction(active, 'save'); return; }
      if (tag === 'select') return;
      if (k === 'c') { e.preventDefault(); doAction(active, 'confirm'); }
      else if (k === 'r') { e.preventDefault(); doAction(active, 'reject'); }
      else if (k === 'x') { e.preventDefault(); doAction(active, 'correct'); }
      else if (k === 'n' || k === 'j') { e.preventDefault(); setActive(Math.min(cards.length - 1, active + 1)); }
      else if (k === 'p' || k === 'k') { e.preventDefault(); setActive(Math.max(0, active - 1)); }
    };
    document.addEventListener('keydown', onKey);
    onCleanup(() => document.removeEventListener('keydown', onKey));
    setActive(0, false);
  }

  // ================================================================ VIEW: Reports
  async function viewReports(root, _arg, alive) {
    const csv = pageUrl('csv');
    root.innerHTML = `
      <div class="view-head"><h1>Reports</h1>
        <p>Preview the digest a ward engineer would receive, and download the full defect register.</p></div>
      <div class="grid-detail">
        <section class="panel" aria-labelledby="dg-title">
          <h2 id="dg-title">Officer digest</h2>
          <div class="filters">
            <div class="field"><label for="rp-ward">Ward</label><select id="rp-ward"><option value="">All wards</option></select></div>
            <div class="field"><label for="rp-period">Period</label>
              <select id="rp-period"><option value="daily">Daily</option><option value="weekly">Weekly</option></select></div>
            <a class="btn" id="rp-open" target="_blank" rel="noopener">Open in new tab</a>
          </div>
          <div id="rp-frame">${loadingHtml('digest')}</div>
        </section>
        <div class="stack">
          <section class="panel">
            <h2>Defect register</h2>
            <p>Every defect with location, severity, risk, ward and workflow stage, as a spreadsheet.</p>
            <a class="btn btn-primary" href="${esc(csv)}" download>Download CSV</a>
          </section>
          <section class="panel" aria-labelledby="sum-title">
            <h2 id="sum-title">Summary</h2>
            <div id="rp-summary">${loadingHtml('summary')}</div>
          </section>
        </div>
      </div>`;

    const wardSel = $('#rp-ward', root);
    const perSel = $('#rp-period', root);
    let probeToken = 0;
    async function loadDigest() {
      const args = { period: perSel.value };
      if (wardSel.value) args.ward = wardSel.value;
      const url = pageUrl('digest', args);
      $('#rp-open', root).href = url;
      const holder = $('#rp-frame', root);
      const my = ++probeToken;
      if (!MOCK) {
        holder.innerHTML = loadingHtml('digest');
        try {
          const r = await fetch(url, { method: 'GET' });
          if (!r.ok) throw new ApiError(friendlyError(r.status, '/api/digest'), r.status);
        } catch (e) {
          if (my !== probeToken || !alive()) return;
          holder.innerHTML = stateHtml('error', 'Digest preview unavailable', e instanceof ApiError ? e.message : friendlyError(0, '/api/digest'));
          return;
        }
      }
      if (my !== probeToken || !alive()) return;
      holder.innerHTML = `<iframe class="digest-frame" title="Officer digest preview" src="${esc(url)}"></iframe>`;
    }
    wardSel.addEventListener('change', loadDigest);
    perSel.addEventListener('change', loadDigest);
    loadDigest();

    // wards from defect list (optional)
    api('/api/defects').then((r) => {
      if (!alive()) return;
      const wards = Array.from(new Set(asList(r, 'defects').map((d) => d.ward_name).filter(Boolean))).sort();
      wardSel.insertAdjacentHTML('beforeend', wards.map((w) => `<option>${esc(w)}</option>`).join(''));
    }).catch(() => { /* ward list optional */ });

    // summary from stats
    try {
      const s = await api('/api/stats');
      if (!alive()) return;
      const groups = [['By risk', s.by_risk, RISK_LEVELS], ['By workflow stage', s.by_workflow], ['By trend', s.by_trend], ['By damage type', s.by_class]];
      const html = groups.filter((g) => g[1] && Object.keys(g[1]).length).map(([title, obj, order]) => {
        const keys = order ? order.filter((k) => k in obj).concat(Object.keys(obj).filter((k) => !order.includes(k))) : Object.keys(obj);
        const max = Math.max(1, ...keys.map((k) => +obj[k] || 0));
        return `<h3 style="margin-top:12px">${esc(title)}</h3><ul class="bars">${keys.map((k) => `<li class="bar-row">
          <span class="bar-label">${esc(title === 'By damage type' ? classLabel(k) : title === 'By trend' ? trendLabel(k) : cap(k))}</span><span class="bar-val num">${fmtInt(+obj[k])}</span>
          <div class="bar-track" aria-hidden="true"><div class="bar-fill" style="width:${(+obj[k] || 0) / max * 100}%;${title === 'By risk' ? 'background:' + riskVar(k) : ''}"></div></div></li>`).join('')}</ul>`;
      }).join('');
      $('#rp-summary', root).innerHTML = html || '<p class="muted">No defects recorded yet.</p>';
    } catch (e) {
      if (!alive()) return;
      $('#rp-summary', root).innerHTML = `<p class="muted small">${esc(e.message)}</p>`;
    }
  }

  // ================================================================ VIEW: Metrics
  const METRIC_KEYS = [
    ['precision', 'Precision'], ['recall', 'Recall'], ['f1', 'F1'], ['mAP50', 'mAP50'], ['mAP50_95', 'mAP50-95'],
  ];
  const mval = (o, k) => {
    if (!o) return null;
    const alts = { mAP50_95: ['mAP50_95', 'map50_95', 'mAP50-95', 'map'], mAP50: ['mAP50', 'map50'], f1: ['f1', 'F1'], precision: ['precision', 'p'], recall: ['recall', 'r'] }[k] || [k];
    for (const a of alts) if (isNum(o[a])) return o[a];
    return null;
  };
  const barCell = (v) => isNum(v)
    ? `<td><div class="metric-bar"><div class="bar-track" aria-hidden="true"><div class="bar-fill" style="width:${Math.max(0, Math.min(1, v)) * 100}%"></div></div><span class="v num">${v.toFixed(3)}</span></div></td>`
    : '<td class="muted">–</td>';
  const prettySet = (s) => ({ all: 'All validation data', rdd_india: 'RDD2022 India', bharatpothole: 'BharatPotHole', rdd_usa: 'RDD2022 USA', kaggle_potholes: 'Kaggle potholes' }[s] || String(s).replace(/_/g, ' '));
  const prettyName = (n) => String(n).replace(/_metrics$/, '').replace(/\.json$/, '').replace(/_/g, ' ');

  function normalizeMetrics(m) {
    const out = { datasets: [], tracking: [], compare: [], images: [], other: [] };
    const entries = Array.isArray(m) ? [['metrics', m]] : Object.entries(m || {});
    for (const [name, val] of entries) {
      if (val == null) continue;
      if (Array.isArray(val) && val.some((x) => x && typeof x === 'object' && (mval(x, 'mAP50') != null || mval(x, 'precision') != null))) {
        out.datasets.push({ name, rows: val.filter((x) => x && typeof x === 'object') });
      } else if (typeof val === 'object' && !Array.isArray(val) && ('raw_detections' in val || 'unique_defects' in val || 'unique_tracks' in val)) {
        out.tracking.push(Object.assign({ name }, val));
      } else if (typeof val === 'object' && (val.models || /compar/i.test(name))) {
        out.compare.push({ name, val });
      } else if (typeof val === 'object' && !Array.isArray(val) && (mval(val, 'mAP50') != null || mval(val, 'precision') != null)) {
        out.datasets.push({ name, rows: [Object.assign({ set: val.set || 'all' }, val)] });
      } else if (typeof val === 'string' && /\.(png|jpe?g|svg)(\?|$)/i.test(val)) {
        out.images.push({ name, url: mediaUrl(val) });
      } else {
        out.other.push({ name, val });
      }
      if (val && typeof val === 'object' && !Array.isArray(val)) {
        for (const [k, v] of Object.entries(val)) {
          if (typeof v === 'string' && /confusion|\.png$/i.test(k + v) && /\.(png|jpe?g|svg)(\?|$)/i.test(v)) out.images.push({ name: k, url: mediaUrl(v) });
        }
      }
    }
    return out;
  }

  function perClassTable(rows, title) {
    // rows: [{label, perClass: {cls: number | {mAP50,...}}}]
    const classes = Array.from(new Set(rows.flatMap((r) => Object.keys(r.perClass || {}))));
    if (!classes.length) return '';
    const v = (x) => (isNum(x) ? x : mval(x, 'mAP50'));
    return `<h3 style="margin-top:16px">${esc(title)}</h3><div class="table-wrap"><table>
      <thead><tr><th scope="col">Damage type</th>${rows.map((r) => `<th scope="col">${esc(r.label)}</th>`).join('')}</tr></thead>
      <tbody>${classes.map((c) => `<tr><th scope="row">${esc(classLabel(c))}</th>${rows.map((r) => barCell(v((r.perClass || {})[c]))).join('')}</tr>`).join('')}</tbody></table></div>`;
  }

  async function viewMetrics(root, _arg, alive) {
    root.innerHTML = `<div class="view-head"><h1>Model metrics</h1>
      <p>How well the detector finds road damage on held-out images, including Indian roads, and how tracking removes repeat detections.</p></div>
      <div id="mt-body">${loadingHtml('metrics')}</div>`;
    let m;
    try { m = await api('/api/metrics'); } catch (e) {
      if (!alive()) return;
      $('#mt-body', root).innerHTML = errorHtml(e, 'metrics');
      bindRetry(root);
      return;
    }
    if (!alive()) return;
    const n = normalizeMetrics(m);
    const parts = [];

    n.datasets.forEach((ds) => {
      const head = ds.rows.find((r) => r.set === 'all') || ds.rows[0];
      parts.push(`<section class="panel">
        <h2>${esc(prettyName(ds.name))}</h2>
        <div class="metric-cards">${METRIC_KEYS.map(([k, label]) => `<div class="metric-card"><div class="v">${isNum(mval(head, k)) ? mval(head, k).toFixed(3) : '–'}</div><div class="k">${label}${head.set ? ', ' + esc(prettySet(head.set)) : ''}</div></div>`).join('')}</div>
        <h3 style="margin-top:16px">By dataset</h3>
        <div class="table-wrap"><table>
          <thead><tr><th scope="col">Validation set</th>${METRIC_KEYS.map(([, l]) => `<th scope="col">${l}</th>`).join('')}</tr></thead>
          <tbody>${ds.rows.map((r) => `<tr><th scope="row">${esc(prettySet(r.set || r.name || 'set'))}</th>${METRIC_KEYS.map(([k]) => barCell(mval(r, k))).join('')}</tr>`).join('')}</tbody>
        </table></div>
        ${perClassTable(ds.rows.map((r) => ({ label: prettySet(r.set || 'set'), perClass: r.per_class_mAP50 || r.per_class })), 'mAP50 by damage type')}
      </section>`);
    });

    n.tracking.forEach((t) => {
      const raw = t.raw_detections, uniq = isNum(t.unique_defects) ? t.unique_defects : t.unique_tracks;
      const red = isNum(raw) && isNum(uniq) && raw > 0 ? (1 - uniq / raw) * 100 : null;
      parts.push(`<section class="panel">
        <h2>Tracking: ${esc(t.video || prettyName(t.name))}</h2>
        ${tallyHtml(raw, uniq, { frames: t.frames || t.frames_processed, reduction: red })}
        <dl class="kv" style="margin-top:12px">
          ${t.model ? `<dt>Model</dt><dd>${esc(t.model)}</dd>` : ''}
          ${isNum(t.fps) ? `<dt>Speed</dt><dd>${fmt1(t.fps)} frames per second${t.device ? ' on ' + esc(t.device) : ''}</dd>` : ''}
          ${isNum(t.min_frames) ? `<dt>Counted as unique</dt><dd>tracked for at least ${t.min_frames} frames</dd>` : ''}
        </dl></section>`);
    });

    n.compare.forEach((c) => {
      let models = c.val.models || c.val;
      if (!Array.isArray(models)) {
        models = Object.entries(models).filter(([, v]) => v && typeof v === 'object').map(([name, v]) => Object.assign({ name }, v.overall || v));
      } else {
        models = models.map((x) => Object.assign({}, x.overall || {}, x));
      }
      models = models.filter((x) => x && typeof x === 'object' && METRIC_KEYS.some(([k]) => mval(x, k) != null));
      if (!models.length) { n.other.push(c); return; }
      parts.push(`<section class="panel">
        <h2>Model comparison</h2>
        ${c.val.images || c.val.dataset ? `<p class="muted small">Evaluated on ${esc(c.val.dataset || c.val.images)}.</p>` : ''}
        <div class="table-wrap"><table>
          <thead><tr><th scope="col">Model</th>${METRIC_KEYS.map(([, l]) => `<th scope="col">${l}</th>`).join('')}</tr></thead>
          <tbody>${models.map((x) => `<tr><th scope="row" style="overflow-wrap:anywhere">${esc(x.name || x.model)}</th>${METRIC_KEYS.map(([k]) => barCell(mval(x, k))).join('')}</tr>`).join('')}</tbody>
        </table></div>
        ${perClassTable(models.map((x) => ({ label: x.name || x.model, perClass: x.per_class || x.per_class_mAP50 })), 'mAP50 by damage type')}
      </section>`);
    });

    if (n.images.length) {
      parts.push(`<section class="panel"><h2>Plots</h2>${n.images.map((im) => `<figure class="figure" style="margin:0 0 12px">
        <img src="${esc(im.url)}" alt="${esc(prettyName(im.name))}" loading="lazy"><figcaption class="small muted">${esc(prettyName(im.name))}</figcaption></figure>`).join('')}</section>`);
    }
    if (n.other.length) {
      parts.push(`<section class="panel"><h2>Other results</h2>${n.other.map((o) => `<details><summary>${esc(prettyName(o.name))}</summary>
        <pre style="overflow:auto;max-height:320px;font-size:.8rem">${esc(JSON.stringify(o.val, null, 2))}</pre></details>`).join('')}</section>`);
    }
    $('#mt-body', root).innerHTML = parts.length ? parts.join('')
      : stateHtml('empty', 'No evaluation results yet', 'Results appear here after training (scripts/train.py) or after running scripts/evaluate.py and scripts/compare_models.py.');
  }

  // ---------------------------------------------------------------- boot
  if (MOCK) {
    document.getElementById('mock-banner').hidden = false;
    // keep ?mock=1 on internal links that leave the hash router
    $$('a.nav-ext').forEach((a) => { a.title = 'Phone capture uses the live server'; });
  }
  window.addEventListener('hashchange', route);
  loadMe().then(() => { renderUserBox(); route(); });
  refreshStats();
  const statsTimer = setInterval(() => { if (!document.hidden) refreshStats(); }, 30000);
  window.addEventListener('pagehide', () => clearInterval(statsTimer));
})();
