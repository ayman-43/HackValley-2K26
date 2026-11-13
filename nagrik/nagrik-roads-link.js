/* Nagrik Roads entry points on Nagrik's own pages (injected by backend/server.mjs).
 * - Home: a "Roads" link in the Bengaluru card (next to Works / People / Sources) and a header button.
 * - Bengaluru pages: a "Roads" link in the city nav and a live road-damage strip from /api/stats.
 * Links are copies of Nagrik's own elements so they keep its styling. React may re-render parts of the
 * page after hydration, so the links are re-added whenever they disappear. */
(function () {
  'use strict';
  const ROADS = '/roads/';
  const MARK = 'data-nagrik-roads';

  function cloneLink(src, text, href) {
    const a = src.cloneNode(false);
    a.textContent = text;
    a.setAttribute('href', href);
    a.removeAttribute('data-discover');
    a.removeAttribute('aria-current');
    a.setAttribute(MARK, '');
    return a;
  }

  function addAfter(selector, text, href) {
    document.querySelectorAll(selector).forEach((src) => {
      const parent = src.parentElement;
      if (!parent || parent.querySelector(`[${MARK}]`)) return;
      src.after(cloneLink(src, text, href));
    });
  }

  function strip() {
    if (!/^\/bengaluru\/?$/.test(location.pathname) || document.getElementById('nagrik-roads-strip')) return;
    const main = document.querySelector('main') || document.getElementById('main-content');
    if (!main) return;
    const box = document.createElement('a');
    box.id = 'nagrik-roads-strip';
    box.href = ROADS;
    box.setAttribute(MARK, '');
    box.style.cssText = 'display:flex;flex-wrap:wrap;align-items:center;gap:6px 18px;max-width:80rem;margin:16px auto 0;'
      + 'padding:12px 18px;border:1px solid var(--border,#d9dee3);border-radius:10px;background:var(--card,#fff);'
      + 'color:inherit;text-decoration:none;font-size:14px;box-shadow:0 1px 2px rgba(0,0,0,.04)';
    box.innerHTML = '<b style="font-weight:650">Nagrik Roads</b>'
      + '<span style="opacity:.7">Potholes and cracks found by phone and dashcam scans, counted once and ranked by risk</span>'
      + '<span id="nr-nums" style="font-family:ui-monospace,monospace;color:#2b6c9b"></span>'
      + '<span style="margin-left:auto;font-weight:600">Open the road desk ↗</span>';
    main.prepend(box);
    fetch('/api/stats').then((r) => (r.ok ? r.json() : null)).then((s) => {
      if (!s) return;
      const risk = s.by_risk || {};
      const high = (risk.High || 0) + (risk.Critical || 0);
      const el = document.getElementById('nr-nums');
      if (el) el.textContent = `${s.open_defects ?? 0} open · ${high} high risk · ${s.pending_review ?? 0} to review`;
    }).catch(() => {});
  }

  function apply() {
    // home: Bengaluru card nav, after "Sources"
    addAfter('.home-city-links a[href="/bengaluru/sources"]', 'Roads', ROADS);
    // home: header button next to "About me"
    addAfter('.home-nav a[href="/about"]', 'Nagrik Roads', ROADS);
    // Bengaluru city pages: header nav, after "Sources"
    addAfter('header a[href="/bengaluru/sources"]', 'Roads', ROADS);
    strip();
  }

  function start() {
    apply();
    let queued = false;
    new MutationObserver(() => {
      if (queued) return;
      queued = true;
      requestAnimationFrame(() => { queued = false; apply(); });
    }).observe(document.body, { childList: true, subtree: true });
  }

  // Wait until React has hydrated Nagrik's server-rendered page; changing the DOM earlier makes
  // hydration fail (React error #418) and React would re-render the page from scratch.
  const later = () => setTimeout(start, 600);
  if (document.readyState === 'complete') later();
  else window.addEventListener('load', later, { once: true });
})();
