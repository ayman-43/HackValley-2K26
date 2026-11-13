(() => {
  const slugs = ['bengaluru', 'delhi', 'gurugram', 'mumbai', 'pune'];
  const city = location.pathname.split('/').find((part) => slugs.includes(part)) || 'bengaluru';
  const section = document.querySelector('#news');
  const content = section?.querySelector('[data-testid="loading-state"]')?.parentElement;
  if (!section || !content) return;
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const cityNames = { bengaluru:'Bengaluru', delhi:'Delhi', gurugram:'Gurugram', mumbai:'Mumbai', pune:'Pune' };
  const searchUrl = `https://news.google.com/search?q=${encodeURIComponent(`${cityNames[city]} civic works municipal corporation`)}&hl=en-IN&gl=IN&ceid=IN:en`;
  const formatDate = (value) => {
    const date = new Date(value);
    return Number.isNaN(date.valueOf()) ? 'Date not provided' : new Intl.DateTimeFormat('en-IN', { dateStyle:'medium', timeZone:'Asia/Kolkata' }).format(date);
  };
  fetch(`/api/v1/${city}/news`, { headers:{ Accept:'application/json' }, cache:'no-store' })
    .then((response) => { if (!response.ok) throw new Error('The news feed is temporarily unavailable.'); return response.json(); })
    .then((data) => {
      if (!data.items?.length) throw new Error('No matching news stories were returned by the feed.');
      content.innerHTML = `<div class="grid gap-3 sm:grid-cols-2">${data.items.map((item) => `<article class="rounded-xl border border-border bg-card/50 p-4"><p class="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">${esc(item.publisher)} · ${esc(formatDate(item.published_at))}</p><h3 class="mt-2 text-base font-medium leading-6"><a class="underline-offset-4 hover:text-primary hover:underline" href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">${esc(item.title)}</a></h3></article>`).join('')}</div><p class="mt-3 text-xs text-muted-foreground">News coverage via <a class="underline underline-offset-4" href="${esc(searchUrl)}" target="_blank" rel="noopener noreferrer">Google News</a>. Stories open at their publishers.</p>`;
      content.setAttribute('aria-busy', 'false');
    })
    .catch(() => {
      content.innerHTML = `<div class="rounded-xl border border-border bg-card/50 p-4"><p class="text-sm text-muted-foreground">The news feed is not responding right now.</p><a class="mt-2 inline-flex text-sm font-medium text-primary underline underline-offset-4" href="${esc(searchUrl)}" target="_blank" rel="noopener noreferrer">Search ${esc(cityNames[city])} news on Google News ↗</a></div>`;
      content.setAttribute('aria-busy', 'false');
    });
})();
