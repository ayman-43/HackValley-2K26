(() => {
  const citySlugs = ['bengaluru', 'delhi', 'gurugram', 'mumbai', 'pune'];
  const city = location.pathname.split('/').find((part) => citySlugs.includes(part)) || 'bengaluru';
  const chart = document.querySelector('.agency-chart-panel');
  if (!chart) return;

  const controls = [...chart.querySelectorAll('.chart-measure button')];
  const svg = chart.querySelector('.agency-ring svg');
  const arcs = [...(svg?.querySelectorAll('circle[role="button"]') || [])];
  const legendRows = [...chart.querySelectorAll('.agency-chart-legend > div')];
  const centerValue = chart.querySelector('.agency-ring-total strong');
  const centerLabel = chart.querySelector('.agency-ring-total span');
  const preview = chart.querySelector('.agency-preview-name');
  const number = (value) => new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(Number(value || 0));
  const crore = (value) => `₹ ${new Intl.NumberFormat('en-IN', { maximumFractionDigits: 1 }).format(Number(value || 0) / 10000000)} Cr`;
  const amount = (value) => {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return 'Not published';
    const rupees = Number(value);
    if (!rupees) return 'Not published';
    if (rupees >= 10000000) return crore(rupees);
    if (rupees >= 100000) return `₹ ${new Intl.NumberFormat('en-IN', { maximumFractionDigits: 1 }).format(rupees / 100000)} L`;
    return `₹ ${number(rupees)}`;
  };

  let agencies = [];
  let measure = 'works';
  let selected = -1;
  const values = () => agencies.map((agency) => measure === 'awarded' ? Number(agency.awarded_total || 0) : Number(agency.count || 0));
  const labelValue = (agency) => measure === 'awarded' ? amount(agency.awarded_total) : number(agency.count);
  function render() {
    const data = values();
    const total = data.reduce((sum, value) => sum + value, 0);
    let offset = 0;
    arcs.forEach((arc, index) => {
      const value = data[index] || 0;
      const share = total > 0 ? value / total * 100 : 0;
      const agency = agencies[index];
      arc.setAttribute('stroke-dasharray', `${share} ${100 - share}`);
      arc.setAttribute('stroke-dashoffset', `${-offset}`);
      arc.setAttribute('aria-label', agency ? `${agency.name}: ${labelValue(agency)}, ${share.toFixed(1)}%` : 'No agency data');
      arc.setAttribute('data-active', String(index === selected));
      offset += share;
    });
    legendRows.forEach((row, index) => {
      const agency = agencies[index];
      const button = row.querySelector('button');
      if (!agency || !button) return;
      button.setAttribute('aria-label', agency.name);
      button.setAttribute('data-active', String(index === selected));
      const valueNode = button.querySelector('strong');
      if (valueNode) valueNode.textContent = labelValue(agency);
    });
    if (centerValue) centerValue.textContent = measure === 'awarded' ? amount(total) : number(total);
    if (centerLabel) centerLabel.textContent = measure === 'awarded' ? 'Awarded value' : 'Works';
    if (preview) {
      const agency = agencies[selected];
      preview.textContent = agency ? `${agency.name} · ${labelValue(agency)}` : '\u00a0';
    }
    controls.forEach((button) => button.setAttribute('aria-pressed', String(button.textContent.trim().toLowerCase() === measure)));
    chart.dataset.measure = measure;
  }

  controls.forEach((button) => button.addEventListener('click', () => {
    const next = button.textContent.trim().toLowerCase();
    if (next !== 'works' && next !== 'awarded') return;
    measure = next;
    selected = -1;
    render();
  }));
  [...arcs, ...legendRows.map((row) => row.querySelector('button')).filter(Boolean)].forEach((node, index) => {
    const agencyIndex = index < arcs.length ? index : index - arcs.length;
    node.addEventListener('click', () => { selected = selected === agencyIndex ? -1 : agencyIndex; render(); });
    if (node.tagName.toLowerCase() === 'circle') node.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selected = selected === agencyIndex ? -1 : agencyIndex; render(); }
    });
  });

  fetch(`/api/v1/${city}/summary`, { headers: { Accept: 'application/json' }, cache: 'no-store' })
    .then((response) => { if (!response.ok) throw new Error(`Summary request failed (${response.status})`); return response.json(); })
    .then((summary) => {
      if (!Array.isArray(summary.by_agency)) throw new Error('Agency breakdown is not published.');
      agencies = summary.by_agency;
      render();
    })
    .catch((error) => {
      console.warn('Agency chart data is temporarily unavailable:', error);
      const awardedButton = controls.find((button) => button.textContent.trim().toLowerCase() === 'awarded');
      if (awardedButton) {
        awardedButton.disabled = true;
        awardedButton.title = 'Awarded values are unavailable from the city summary API.';
      }
    });
})();
