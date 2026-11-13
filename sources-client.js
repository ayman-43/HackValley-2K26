(() => {
  const citySlugs = ['bengaluru', 'delhi', 'gurugram', 'mumbai', 'pune'];
  const city = location.pathname.split('/').find((part) => citySlugs.includes(part)) || 'bengaluru';
  const disclosure = document.querySelector('[aria-labelledby="live-disclosures"] .mt-5');
  if (!disclosure) return;

  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
  const date = (value) => {
    if (!value) return null;
    const parsed = new Date(value);
    return Number.isNaN(parsed.valueOf()) ? null : new Intl.DateTimeFormat('en-IN', { dateStyle: 'medium', timeStyle: value.includes('T') ? 'short' : undefined, timeZone: 'Asia/Kolkata' }).format(parsed);
  };
  const countOf = (data, keys) => {
    for (const key of keys) {
      if (Number.isFinite(Number(data?.[key]))) return Number(data[key]);
    }
    for (const key of ['items', 'officials', 'wards']) {
      if (Array.isArray(data?.[key])) return data[key].length;
    }
    return null;
  };
  const value = (count, unit) => count === null ? 'Not reported by source' : `${new Intl.NumberFormat('en-IN').format(count)} ${unit}`;
  const row = (label, result) => `<div class="flex items-start justify-between gap-4 border-t border-border/80 pt-3"><dt class="text-muted-foreground">${esc(label)}</dt><dd class="text-right font-medium">${esc(result)}</dd></div>`;

  async function loadSources() {
    const endpoint = `/api/v1/${city}`;
    const request = async (path) => {
      const response = await fetch(`${endpoint}/${path}`, { headers: { Accept: 'application/json' }, cache: 'no-store' });
      if (!response.ok) throw new Error(`Source request failed (${response.status})`);
      return response.json();
    };
    try {
      const results = await Promise.allSettled([request('summary'), request('people?limit=1'), request('wards')]);
      const [summaryResult, peopleResult, wardsResult] = results;
      const summary = summaryResult.status === 'fulfilled' ? summaryResult.value : null;
      const people = peopleResult.status === 'fulfilled' ? peopleResult.value : null;
      const wards = wardsResult.status === 'fulfilled' ? wardsResult.value : null;
      const snapshots = [summary?.snapshot_date, people?.snapshot_date, wards?.snapshot_date].filter(Boolean);
      const freshest = snapshots.sort().at(-1);
      const latestChange = date(summary?.latest_change_at);
      const rows = [
        row('Latest published snapshot', date(freshest) || 'Not reported by source'),
        row('Public works', value(countOf(summary, ['total', 'total_projects', 'projects_total']), 'records')),
        row('Official directory', value(countOf(people, ['total', 'total_officials', 'count']), 'records')),
        row('Mapped wards', value(countOf(wards, ['total', 'count']), 'wards')),
      ];
      if (summary?.latest_change_at !== undefined) rows.push(row('Latest works change reported', latestChange || 'Date not provided'));
      disclosure.innerHTML = `<dl class="grid gap-3 text-sm" aria-live="polite">${rows.join('')}</dl><p class="mt-4 border-t border-border/80 pt-3 text-xs leading-5 text-muted-foreground">These figures come from this city’s public Nagrik API. A snapshot date is the source data’s published capture date; it does not mean every underlying government record changed on that date. Missing fields are shown as unavailable.</p>`;
      disclosure.setAttribute('aria-busy', 'false');

      const freshness = document.querySelector('#freshness ul');
      if (freshness && freshest) {
        const note = document.createElement('li');
        note.className = 'sources-api-freshness';
        note.textContent = `Latest published API snapshot for this city: ${date(freshest)}. Works: ${value(countOf(summary, ['total', 'total_projects', 'projects_total']), 'records')}; officials: ${value(countOf(people, ['total', 'total_officials', 'count']), 'records')}; wards: ${value(countOf(wards, ['total', 'count']), 'wards')}.`;
        freshness.append(note);
      }
    } catch (error) {
      disclosure.setAttribute('aria-busy', 'false');
      disclosure.innerHTML = `<p class="text-sm leading-6 text-muted-foreground" role="status">Current source details could not be loaded. Visit the cited public source records below; no freshness date is inferred.</p>`;
    }
  }
  loadSources();
})();
