(() => {
  const themeButton = document.querySelector('[data-testid="theme-toggle"]');
  const syncTheme = () => {
    let dark = false;
    try { dark = localStorage.getItem('nagrik-theme') === 'dark'; } catch {}
    document.documentElement.classList.toggle('dark', dark);
    document.documentElement.style.colorScheme = dark ? 'dark' : 'light';
    themeButton?.setAttribute('aria-label', dark ? 'Use light theme' : 'Use dark theme');
  };
  syncTheme();
  themeButton?.addEventListener('click', () => {
    const dark = document.documentElement.classList.toggle('dark');
    document.documentElement.style.colorScheme = dark ? 'dark' : 'light';
    try { localStorage.setItem('nagrik-theme', dark ? 'dark' : 'light'); } catch {}
    themeButton.setAttribute('aria-label', dark ? 'Use light theme' : 'Use dark theme');
    window.dispatchEvent(new CustomEvent('nagrik:themechange', {detail:{dark}}));
  });
  const homeNav = document.querySelector('.home-nav');
  const homeNavLinks = homeNav?.querySelector(':scope > div');
  if (homeNavLinks && !homeNavLinks.querySelector('.home-city-picker')) {
    const picker = document.createElement('div');
    picker.className = 'home-city-picker';
    picker.innerHTML = `<button class="home-city-picker-trigger" type="button" aria-haspopup="menu" aria-expanded="false" aria-controls="home-city-picker-menu"><span>Choose city</span><svg aria-hidden="true" viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"></path></svg></button><div class="home-city-picker-menu" id="home-city-picker-menu" hidden><p>Choose a city</p><nav aria-label="Cities" role="menu"><a class="home-city-picker-all" href="/outputs/nagrik-clone/" aria-current="page" role="menuitem">All cities <small>Explore every city</small></a><span class="home-city-picker-divider" aria-hidden="true"></span>${[['Gurugram','gurugram'],['Delhi','delhi'],['Mumbai','mumbai'],['Bengaluru','bengaluru'],['Pune','pune']].map(([name,slug])=>`<a href="/outputs/nagrik-clone/${slug}/" role="menuitem">${name}<span aria-hidden="true">↗</span></a>`).join('')}</nav></div>`;
    const exploreLink = homeNavLinks.querySelector('.home-nav-cta');
    homeNavLinks.insertBefore(picker, exploreLink || null);
    const trigger = picker.querySelector('.home-city-picker-trigger');
    const menu = picker.querySelector('.home-city-picker-menu');
    const closePicker = (restoreFocus = false) => {
      menu.hidden = true;
      trigger.setAttribute('aria-expanded', 'false');
      if (restoreFocus) trigger.focus();
    };
    trigger.addEventListener('click', () => {
      menu.hidden = !menu.hidden;
      trigger.setAttribute('aria-expanded', String(!menu.hidden));
    });
    picker.addEventListener('click', (event) => { if (event.target.closest('a')) closePicker(); });
    document.addEventListener('click', (event) => { if (!picker.contains(event.target)) closePicker(); });
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !menu.hidden) closePicker(true); });
  }
  const citySlugs = ['gurugram','delhi','mumbai','bengaluru','pune'];
  const formatCrore = (amount) => {
    const value = Number(amount || 0) / 10000000;
    return `${Number.isInteger(value) ? value : Number(value.toFixed(1))} Cr`;
  };
  const summaries = Promise.all(citySlugs.map(async (slug) => {
    const response = await fetch(`/api/v1/${slug}/summary`);
    if (!response.ok) throw new Error(`Could not load ${slug} summary`);
    return [slug, await response.json()];
  }));
  summaries.then((entries) => {
    const data = new Map(entries);
    document.querySelectorAll('.home-city-card').forEach((card) => {
      const href = card.querySelector('a[href]')?.getAttribute('href') || '';
      const slug = citySlugs.find((candidate) => href.includes(`/${candidate}/`));
      const summary = data.get(slug);
      const target = card.querySelector('.home-city-inline-value strong');
      if (!summary || !target) return;
      target.textContent = `₹ ${formatCrore(summary.tendered_total)}`;
      target.removeAttribute('aria-busy');
    });
    const statCities = ['mumbai','delhi','bengaluru'];
    document.querySelectorAll('.home-product-stats > div').forEach((row,index) => {
      const summary=data.get(statCities[index]);if(!summary)return;
      const values=row.querySelectorAll('dd');
      if(values[0])values[0].textContent=new Intl.NumberFormat('en-IN').format(summary.total);
      if(values[1])values[1].textContent=formatCrore(summary.tendered_total);
    });
  }).catch((error) => console.warn('City summary data is temporarily unavailable:',error));
  document.querySelectorAll('a[href="https://nagrik.in.net/#cities"]').forEach((link)=>link.setAttribute('href','#cities'));
  document.querySelectorAll('button').forEach((button)=>{
    if(button.textContent.trim()==='Explore your city')button.addEventListener('click',()=>document.querySelector('#cities')?.scrollIntoView({behavior:'smooth'}));
    if(button.textContent.trim()==='Explore public works')button.addEventListener('click',()=>document.querySelector('.home-product-stats')?.scrollIntoView({behavior:'smooth'}));
    if(button.textContent.trim()==='See the evidence')button.addEventListener('click',()=>document.querySelector('#sources')?.scrollIntoView({behavior:'smooth'}));
  });
  const email=document.querySelector('.__cf_email__');
  if(email){email.textContent='nagrikadmin@gmail.com';email.removeAttribute('data-cfemail');}
})();
