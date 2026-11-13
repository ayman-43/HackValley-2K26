(() => {
  const input = document.querySelector('#works-search');
  if (!input) return;
  const citySlugs = ['bengaluru','delhi','gurugram','mumbai','pune'];
  const city = location.pathname.split('/').find((part) => citySlugs.includes(part)) || 'bengaluru';
  const agency = document.querySelector('#works-agency');
  const sort = document.querySelector('#works-sort');
  const stageGroup = document.querySelector('[aria-label="Stage"]');
  const resultsList = [...document.querySelectorAll('main ul')].find((node) => node.querySelector('li button'));
  const pageNav = document.querySelector('nav[aria-label="Public works"]');
  if (!resultsList || !pageNav) return;
  const template = resultsList.querySelector('li');
  const originalItems = [...resultsList.children];
  const pageText = pageNav.querySelector('span');
  const prev = pageNav.querySelector('button:first-child');
  const next = pageNav.querySelector('button:last-child');
  let page = 1;
  let timer;
  let activeRequest;
  let detail = document.createElement('dialog');
  detail.className = 'rounded-xl border border-border bg-card p-0 text-card-foreground shadow-xl backdrop:bg-black/60';
  detail.innerHTML = '<div class="max-h-[85vh] w-[min(42rem,calc(100vw-2rem))] overflow-auto p-6"><button type="button" data-close class="float-right rounded-md border border-border px-3 py-1 text-sm">Close</button><div data-body></div></div>';
  document.body.append(detail);
  detail.querySelector('[data-close]').addEventListener('click', () => detail.close());
  detail.addEventListener('click', (event) => { if (event.target === detail) detail.close(); });
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const currency = (v) => v == null ? 'Not published' : `₹ ${new Intl.NumberFormat('en-IN',{maximumFractionDigits:1}).format(Number(v)/10000000)} Cr`;
  const selectedStages = () => [...(stageGroup?.querySelectorAll('button[aria-pressed="true"]') || [])].map((button) => button.textContent.trim());
  function params() {
    const values = new URLSearchParams({ q:input.value, page:String(page), limit:'20', sort:sort?.value || 'newest' });
    if (agency?.value) values.set('agency', agency.value);
    for (const status of selectedStages()) values.append('status', status);
    return values;
  }
  function recordHtml(work) {
    const fields = [
      ['Executing body',work.executing_agency],['Tender reference',work.tender_ref],['Status',work.status],['Estimated value',currency(work.budget)],['Awarded value',currency(work.awarded_budget)],['Contractor',work.contractor_name],['Tender date',work.tender_date],['Bid close date',work.bid_close_date],['Work period (days)',work.period_of_work_days],['Location',work.location_text],['Ward',work.ward_name],['Scope',work.scope_of_work]
    ].filter(([,value]) => value !== null && value !== undefined && value !== '');
    return `<p class="text-xs font-semibold uppercase tracking-widest text-muted-foreground">${esc(work.executing_agency)} · ${esc(work.status)}</p><h2 class="mt-3 text-xl font-semibold">${esc(work.title)}</h2>${work.title_local ? `<p class="mt-2 text-muted-foreground">${esc(work.title_local)}</p>`:''}<dl class="mt-5 grid gap-3 sm:grid-cols-2">${fields.map(([k,v])=>`<div class="rounded-lg border border-border p-3"><dt class="text-xs uppercase text-muted-foreground">${esc(k)}</dt><dd class="mt-1 text-sm">${esc(v)}</dd></div>`).join('')}</dl><a class="mt-5 inline-flex text-primary underline" href="${esc(work.source_url || 'https://kppp.karnataka.gov.in/#/portal/searchTender')}" target="_blank" rel="noreferrer">Open official source ↗</a>`;
  }
  function renderRows(items) {
    resultsList.replaceChildren();
    for (const work of items) {
      const li = template.cloneNode(true);
      const button = li.querySelector('button');
      const spans = button.querySelectorAll(':scope > span');
      const title = spans[0].querySelector('.line-clamp-2');
      const meta = spans[0].querySelector('.text-muted-foreground');
      const side = spans[1].querySelectorAll(':scope > span');
      title.textContent = work.title;
      meta.textContent = `${work.executing_agency || 'Agency not published'} · ${work.tender_ref || 'Reference not published'}${work.ward_name ? ` · ${work.ward_name}`:''}`;
      side[0].textContent = work.status || 'Status not published';
      side[1].textContent = currency(work.awarded_budget ?? work.budget);
      button.addEventListener('click', async () => {
        detail.querySelector('[data-body]').innerHTML = recordHtml(work);
        detail.showModal();
      });
      resultsList.append(li);
    }
    if (!items.length) resultsList.innerHTML = '<li class="p-5 text-sm text-muted-foreground">No works match those filters.</li>';
  }
  async function load() {
    clearTimeout(timer);
    activeRequest?.abort();
    const controller = new AbortController(); activeRequest = controller;
    resultsList.setAttribute('aria-busy','true');
    try {
      const response = await fetch(`/api/v1/${city}/works?${params()}`, {signal:controller.signal});
      if (!response.ok) throw new Error('The local works service returned an error.');
      const data = await response.json();
      renderRows(data.items);
      const pageSize = data.limit || data.per_page || 20;
      const start = data.total ? (data.page-1)*pageSize+1 : 0;
      const end = Math.min(data.page*pageSize,data.total);
      const summary = pageNav.parentElement.querySelector('p,span');
      if (summary) summary.textContent = `Showing ${start}–${end} of ${new Intl.NumberFormat('en-IN').format(data.total)} works`;
      const pages = data.pages || Math.ceil(data.total / pageSize);
      if (pageText) pageText.textContent = `Page ${data.page} of ${Math.max(pages,1)}`;
      prev.disabled = data.page <= 1;
      next.disabled = data.page >= pages;
      prev.toggleAttribute('data-disabled',prev.disabled);
      next.toggleAttribute('data-disabled',next.disabled);
      const count = [...document.querySelectorAll('main *')].find((n)=>n.children.length===0 && /^([\d,]+)$/.test(n.textContent.trim()) && n.parentElement?.textContent.includes('Works matching'));
      if (count) count.textContent = new Intl.NumberFormat('en-IN').format(data.total);
    } catch (error) {
      if (error.name !== 'AbortError') console.error('Could not load works from local API:',error);
    } finally { resultsList.removeAttribute('aria-busy'); }
  }
  input.addEventListener('input',()=>{page=1;clearTimeout(timer);timer=setTimeout(load,180);});
  agency?.addEventListener('change',()=>{page=1;load();});
  sort?.addEventListener('change',()=>{page=1;load();});
  stageGroup?.addEventListener('click',(event)=>{
    const button = event.target.closest('button');
    if (!button) return;
    button.setAttribute('aria-pressed', button.getAttribute('aria-pressed') === 'true' ? 'false' : 'true');
    page = 1;
    load();
  });
  prev.addEventListener('click',(event)=>{event.preventDefault();if(page>1){page--;load();}});
  next.addEventListener('click',(event)=>{event.preventDefault();page++;load();});
  load();
})();
