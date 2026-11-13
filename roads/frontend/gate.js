/* Page gate for standalone pages: <body data-need="crew officer admin"> shows a sign-in / no-access screen to
   anyone else. The server enforces the same rules (backend/app/auth.py); this only keeps the page honest. */
(function () {
  'use strict';
  const need = (document.body.dataset.need || '').split(' ').filter(Boolean);
  if (!need.length) return;
  const NAMES = { citizen: 'Citizen', crew: 'Repair crew', officer: 'Ward officer', admin: 'Administrator' };
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const who = need.map((r) => NAMES[r] || r).join(', ').replace(/, ([^,]*)$/, ' or $1');
  document.documentElement.style.visibility = 'hidden';
  function lock(html) {
    const main = document.querySelector('main') || document.body;
    main.innerHTML = `<div class="state signin-state" style="max-width:560px;margin:56px auto;padding:0 16px">${html}</div>`;
  }
  fetch('/api/auth/me', { cache: 'no-store' }).then((r) => r.json()).then((d) => {
    const u = d.user;
    if (!u) {
      const next = encodeURIComponent(location.pathname + location.search + location.hash);
      lock(`<h2>Sign in to use this page</h2><p>This page is for ${esc(who)} accounts.</p>
        <p><a class="btn btn-primary" href="/roads/login.html?next=${next}">Sign in</a>
        <a class="btn" href="/roads/">Back to Nagrik Roads</a></p>`);
    } else if (!need.includes(u.role)) {
      lock(`<h2>${esc(NAMES[u.role] || u.role)} accounts can't use this page</h2>
        <p>You are signed in as ${esc(u.name)}. This page is for ${esc(who)} accounts.${u.requested_role
          ? ` Your ${esc(NAMES[u.requested_role] || u.requested_role)} request is waiting for an administrator.` : ' An administrator can change your role.'}</p>
        <p><a class="btn" href="/roads/">Back to Nagrik Roads</a></p>`);
    }
  }).catch(() => {}).finally(() => { document.documentElement.style.visibility = ''; });
})();
