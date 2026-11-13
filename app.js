(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const base = document.body.dataset.base || "./";
  const page = document.body.dataset.page || "overview";
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  const number = (value) => new Intl.NumberFormat("en-IN").format(Number(value || 0));
  const crore = (value) => `₹ ${new Intl.NumberFormat("en-IN", { maximumFractionDigits: 1 }).format(Number(value || 0) / 10000000)} Cr`;
  const humanize = (key) => key.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
  const nonempty = (value) => value !== null && value !== undefined && value !== "" && !(Array.isArray(value) && !value.length);

  try {
    const dark = localStorage.getItem("nagrik-theme") === "dark";
    document.documentElement.classList.toggle("dark", dark);
    document.documentElement.style.colorScheme = dark ? "dark" : "light";
    $$("[data-theme-toggle]").forEach((button) => button.setAttribute("aria-label", dark ? "Use light theme" : "Use dark theme"));
  } catch {}
  $$("[data-theme-toggle]").forEach((button) => button.addEventListener("click", () => {
    const dark = document.documentElement.classList.toggle("dark");
    document.documentElement.style.colorScheme = dark ? "dark" : "light";
    button.setAttribute("aria-label", dark ? "Use light theme" : "Use dark theme");
    window.dispatchEvent(new CustomEvent("nagrik:themechange", { detail: { dark } }));
    try { localStorage.setItem("nagrik-theme", dark ? "dark" : "light"); } catch {}
  }));

  const load = async (name) => {
    const response = await fetch(`${base}data/${name}.json`);
    if (!response.ok) throw new Error(`Could not load ${name} data (${response.status})`);
    return response.json();
  };
  const recordRows = (record, omit = []) => Object.entries(record)
    .filter(([key, value]) => !omit.includes(key) && nonempty(value))
    .map(([key, value]) => {
      let shown = value;
      if (typeof value === "object") shown = JSON.stringify(value, null, 2);
      if (key.includes("budget") || key === "value" || key.includes("total")) {
        const numeric = typeof value === "number" ? value : null;
        if (numeric !== null) shown = `${number(numeric)} (${crore(numeric)})`;
      }
      if (key === "source_url" || key === "email") {
        const safe = String(shown);
        shown = key === "source_url" && /^https?:\/\//.test(safe)
          ? `<a href="${esc(safe)}" target="_blank" rel="noreferrer">${esc(safe)} ↗</a>`
          : key === "email" ? `<a href="mailto:${esc(safe)}">${esc(safe)}</a>` : esc(safe);
      } else shown = `<span>${esc(shown)}</span>`;
      return `<div class="detail-row"><dt>${esc(humanize(key))}</dt><dd>${shown}</dd></div>`;
    }).join("");
  const pageError = (target, error) => {
    const node = $(target);
    if (node) node.innerHTML = `<div class="empty-state"><strong>Could not load this directory.</strong><p>${esc(error.message)}</p></div>`;
  };

  async function overview() {
    try {
      const [summary, people, wardsData] = await Promise.all([load("summary"), load("people"), load("wards")]);
      const wards = wardsData.items;
      const agencies = summary.by_agency;
      const stats = [
        ["Wards mapped", number(wards.length)],
        ["Public works tracked", number(summary.total)],
        ["Public money tracked", crore(summary.tendered_total)],
        ["Civic bodies", number(agencies.length)],
      ];
      $("#overview-stats").innerHTML = stats.map(([label, value], index) => `<article class="city-stat-card"><span class="stat-index">0${index + 1}</span><p>${esc(label)}</p><strong>${esc(value)}</strong></article>`).join("");
      const bodySelect = $("#ward-body");
      [...new Set(wards.map((ward) => ward.corporation))].sort().forEach((body) => bodySelect.insertAdjacentHTML("beforeend", `<option value="${esc(body)}">${esc(body)}</option>`));
      const search = $("#ward-search");
      const bodyFilter = $("#ward-body");
      const wardResults = $("#ward-results");
      const renderWards = () => {
        const query = search.value.trim().toLowerCase();
        const filtered = wards.filter((ward) => (!bodyFilter.value || ward.corporation === bodyFilter.value) && `${ward.name} ${ward.name_local || ""} ${ward.ward_number} ${ward.corporation}`.toLowerCase().includes(query));
        $("#ward-count").textContent = `${number(filtered.length)} wards`;
        const grouped = Object.groupBy ? Object.groupBy(filtered, (ward) => ward.corporation) : filtered.reduce((acc, ward) => ((acc[ward.corporation] ||= []).push(ward), acc), {});
        wardResults.innerHTML = Object.entries(grouped).sort(([a], [b]) => a.localeCompare(b)).map(([corporation, list]) => `<details class="civic-ward-group" open><summary class="civic-ward-group-summary">${esc(corporation)} <span>${list.length} wards</span></summary><div class="ward-grid">${list.slice().sort((a, b) => a.ward_number - b.ward_number).map((ward) => `<button class="ward-chip" type="button" data-ward="${ward.ward_id}"><span>${esc(ward.name)}</span><small>${esc(ward.corporation)} · Ward ${esc(ward.ward_number)}${ward.name_local ? ` · ${esc(ward.name_local)}` : ""}</small></button>`).join("")}</div></details>`).join("") || `<div class="empty-state">No wards match this search.</div>`;
        $$("[data-ward]", wardResults).forEach((button) => button.addEventListener("click", () => {
          const ward = wards.find((item) => item.ward_id === Number(button.dataset.ward));
          const officers = people.officials.filter((person) => person.agency === ward.corporation && (person.ward_number === ward.ward_number || person.jurisdiction?.wards?.includes(ward.ward_number)));
          $("#ward-detail").innerHTML = `<p class="home-kicker">${esc(ward.corporation)} · WARD ${esc(ward.ward_number)}</p><h2>${esc(ward.name)}</h2>${ward.name_local ? `<p class="local-name">${esc(ward.name_local)}</p>` : ""}<dl class="detail-list">${recordRows(ward, ["city", "ward_id"])}</dl><h3>Published officers for this corporation / ward</h3>${officers.length ? `<ul class="dialog-list">${officers.map((person) => `<li><strong>${esc(person.name)}</strong><span>${esc(person.department)}${person.phone ? ` · ${esc(person.phone)}` : ""}</span></li>`).join("")}</ul>` : `<p class="section-copy">No officer record is directly assigned to this ward in the public directory.</p>`}`;
          $("#ward-dialog").showModal();
        }));
      };
      search.addEventListener("input", renderWards);
      bodyFilter.addEventListener("change", renderWards);
      renderWards();

      const cityOfficials = people.officials.filter((person) => person.level === "city" || person.level === "hq").slice(0, 6);
      $("#official-preview").innerHTML = cityOfficials.map((person) => `<article class="preview-card"><p class="home-kicker">${esc(person.agency)} · ${esc(person.department)}</p><h3>${esc(person.name)}</h3><p>${person.phone ? `<a href="tel:${esc(person.phone)}">${esc(person.phone)}</a>` : "Contact not published"}${person.email ? ` · <a href="mailto:${esc(person.email)}">Email</a>` : ""}</p></article>`).join("");
      $("#money-summary").innerHTML = `<article class="money-stat"><span>Estimated by authorities</span><strong>${crore(summary.tendered_total)}</strong></article><article class="money-stat"><span>Awarded value published</span><strong>${crore(summary.awarded_total)}</strong></article><article class="money-stat"><span>Works tracked</span><strong>${number(summary.total)}</strong></article><article class="money-stat"><span>Open records</span><strong>${number(summary.active)}</strong></article>`;
      const maxAgency = Math.max(...agencies.map((item) => item.count));
      $("#agency-bars").innerHTML = agencies.slice().sort((a, b) => b.count - a.count).map((item) => `<a class="bar-row" href="./works/?agency=${encodeURIComponent(item.agency)}"><span>${esc(item.agency)} <small>${esc(item.name)}</small></span><span class="bar-track"><i style="width:${Math.max(2, item.count / maxAgency * 100)}%"></i></span><strong>${number(item.count)}</strong></a>`).join("");
      const maxStatus = Math.max(...summary.by_status.map((item) => item.count));
      $("#status-bars").innerHTML = summary.by_status.map((item) => `<a class="bar-row" href="./works/?status=${encodeURIComponent(item.status)}"><span>${esc(item.status)}</span><span class="bar-track"><i style="width:${item.count / maxStatus * 100}%"></i></span><strong>${number(item.count)}</strong></a>`).join("");
    } catch (error) { pageError("#ward-results", error); }
  }

  async function worksPage() {
    try {
      const dataset = await load("works");
      const works = dataset.items;
      const agencies = [...new Set(works.map((work) => work.executing_agency).filter(Boolean))].sort();
      const statuses = [...new Set(works.map((work) => work.status).filter(Boolean))].sort();
      const summary = dataset.summary;
      $("#work-stats").innerHTML = [["Works tracked", number(dataset.total)], ["Open works", number(summary.active)], ["Awarded value", crore(summary.awarded_total)], ["Estimated value", crore(summary.tendered_total)]].map(([label, value]) => `<article class="city-stat-card"><p>${esc(label)}</p><strong>${esc(value)}</strong></article>`).join("");
      const agencySelect = $("#work-agency");
      agencies.forEach((agency) => agencySelect.insertAdjacentHTML("beforeend", `<option value="${esc(agency)}">${esc(agency)}</option>`));
      const statusSelect = $("#work-status");
      statuses.forEach((status) => statusSelect.insertAdjacentHTML("beforeend", `<option value="${esc(status)}">${esc(status)}</option>`));
      const params = new URLSearchParams(location.search);
      if (params.has("agency")) agencySelect.value = params.get("agency");
      if (params.has("status")) statusSelect.value = params.get("status");
      let currentPage = Math.max(1, Number(params.get("page") || 1));
      const render = () => {
        const query = $("#work-search").value.trim().toLowerCase();
        const selectedAgency = agencySelect.value;
        const selectedStatus = statusSelect.value;
        const sort = $("#work-sort").value;
        const pageSize = Number($("#work-page-size").value);
        let list = works.filter((work) => (!selectedAgency || work.executing_agency === selectedAgency) && (!selectedStatus || work.status === selectedStatus) && (!query || [work.title, work.title_local, work.tender_ref, work.executing_agency, work.division, work.location_text, work.work_location_text, work.ward_name, work.contractor_name].some((value) => String(value || "").toLowerCase().includes(query))));
        list.sort((a, b) => sort === "title" ? String(a.title).localeCompare(String(b.title)) : sort === "budget" ? (b.budget || b.awarded_budget || 0) - (a.budget || a.awarded_budget || 0) : sort === "oldest" ? String(a.tender_date || "9999").localeCompare(String(b.tender_date || "9999")) : String(b.tender_date || "").localeCompare(String(a.tender_date || "")));
        const pages = Math.max(1, Math.ceil(list.length / pageSize));
        currentPage = Math.min(currentPage, pages);
        $("#work-count").textContent = `${number(list.length)} matching works · ${number(dataset.total)} total`;
        $("#work-page").textContent = `Page ${number(currentPage)} of ${number(pages)}`;
        $("#work-prev").disabled = currentPage <= 1;
        $("#work-next").disabled = currentPage >= pages;
        const pageRows = list.slice((currentPage - 1) * pageSize, currentPage * pageSize);
        $("#work-results").innerHTML = pageRows.length ? `<ul class="work-list">${pageRows.map((work) => `<li><button type="button" class="work-row" data-work="${work.id}"><span class="work-row-main"><strong>${esc(work.title)}</strong><small>${esc(work.executing_agency)} · ${esc(work.tender_ref || "Reference not published")}${work.ward_name ? ` · ${esc(work.ward_name)}` : ""}</small></span><span class="work-row-side"><span class="status-badge">${esc(work.status)}</span><strong>${work.budget ? crore(work.budget) : work.awarded_budget ? crore(work.awarded_budget) : "Not published"}</strong></span></button></li>`).join("")}</ul>` : `<div class="empty-state">No works match those filters.</div>`;
        $$("[data-work]", $("#work-results")).forEach((button) => button.addEventListener("click", () => {
          const work = works.find((item) => item.id === Number(button.dataset.work));
          const omit = ["id", "city", "status_normalized", "source_status"];
          const detailRows = recordRows(work, omit);
          $("#work-detail").innerHTML = `<p class="home-kicker">${esc(work.executing_agency)} · ${esc(work.status)}</p><h2>${esc(work.title)}</h2>${work.title_local ? `<p class="local-name">${esc(work.title_local)}</p>` : ""}<p class="record-id">Record ${esc(work.id)} · ${esc(work.tender_ref || "Tender reference not published")}</p><dl class="detail-list">${detailRows}</dl><a class="source-record-link" href="${esc(work.source_url || "https://kppp.karnataka.gov.in/")}" target="_blank" rel="noreferrer">Open official tender source ↗</a>`;
          $("#work-dialog").showModal();
        }));
        const next = new URLSearchParams(location.search);
        next.set("page", currentPage);
        history.replaceState({}, "", `${location.pathname}?${next}`);
      };
      ["#work-search", "#work-agency", "#work-status", "#work-sort", "#work-page-size"].forEach((selector) => $(selector).addEventListener(selector.includes("search") ? "input" : "change", () => { currentPage = 1; render(); }));
      $("#work-prev").addEventListener("click", () => { currentPage--; render(); window.scrollTo({ top: 0, behavior: "smooth" }); });
      $("#work-next").addEventListener("click", () => { currentPage++; render(); window.scrollTo({ top: 0, behavior: "smooth" }); });
      render();
    } catch (error) { pageError("#work-results", error); }
  }

  async function peoplePage() {
    try {
      const dataset = await load("people");
      const people = dataset.officials;
      const agencies = [...new Set(people.map((person) => person.agency).filter(Boolean))].sort();
      const agencySelect = $("#people-agency");
      agencies.forEach((agency) => agencySelect.insertAdjacentHTML("beforeend", `<option value="${esc(agency)}">${esc(agency)}</option>`));
      const params = new URLSearchParams(location.search);
      if (params.has("agency")) agencySelect.value = params.get("agency");
      let currentPage = 1;
      const render = () => {
        const query = $("#people-search").value.trim().toLowerCase();
        const agency = agencySelect.value;
        const level = $("#people-level").value;
        const pageSize = Number($("#people-page-size").value);
        const list = people.filter((person) => (!agency || person.agency === agency) && (!level || person.level === level) && (!query || [person.name, person.name_local, person.department, person.department_local, person.agency, person.address, person.address_local, person.phone, person.email, person.ward_number].some((value) => String(value || "").toLowerCase().includes(query))));
        const pages = Math.max(1, Math.ceil(list.length / pageSize));
        currentPage = Math.min(currentPage, pages);
        $("#people-count").textContent = `${number(list.length)} matching official records · ${number(people.length)} total`;
        $("#people-page").textContent = `Page ${number(currentPage)} of ${number(pages)}`;
        $("#people-prev").disabled = currentPage <= 1;
        $("#people-next").disabled = currentPage >= pages;
        $("#people-results").innerHTML = list.length ? `<div class="people-list">${list.slice((currentPage - 1) * pageSize, currentPage * pageSize).map((person) => `<button type="button" class="person-row" data-person="${person.id}"><span class="person-main"><strong>${esc(person.name)}</strong><small>${esc(person.department)} · ${esc(person.agency)}${person.ward_number ? ` · Ward ${esc(person.ward_number)}` : ""}</small></span><span class="person-contact">${person.phone ? esc(person.phone) : "Contact not published"}<small>${esc(person.level || "")}</small></span></button>`).join("")}</div>` : `<div class="empty-state">No people match those filters.</div>`;
        $$("[data-person]", $("#people-results")).forEach((button) => button.addEventListener("click", () => {
          const person = people.find((item) => item.id === Number(button.dataset.person));
          $("#person-detail").innerHTML = `<p class="home-kicker">${esc(person.agency)} · ${esc(person.category || "Official")}</p><h2>${esc(person.name)}</h2>${person.name_local ? `<p class="local-name">${esc(person.name_local)}</p>` : ""}<p class="person-title">${esc(person.department)}</p><dl class="detail-list">${recordRows(person, ["id", "category_local", "department_local", "name_local"])}</dl><p class="data-disclaimer">This is a public-duty directory entry. Check the listed civic body for current details.</p>`;
          $("#person-dialog").showModal();
        }));
        const next = new URLSearchParams(location.search);
        if (agency) next.set("agency", agency); else next.delete("agency");
        history.replaceState({}, "", `${location.pathname}${next.size ? `?${next}` : ""}`);
      };
      ["#people-search", "#people-agency", "#people-level", "#people-page-size"].forEach((selector) => $(selector).addEventListener(selector.includes("search") ? "input" : "change", () => { currentPage = 1; render(); }));
      $("#people-prev").addEventListener("click", () => { currentPage--; render(); window.scrollTo({ top: 0, behavior: "smooth" }); });
      $("#people-next").addEventListener("click", () => { currentPage++; render(); window.scrollTo({ top: 0, behavior: "smooth" }); });
      $("#helpline-results").innerHTML = dataset.helplines.map((line) => `<article class="preview-card"><p class="home-kicker">${esc(line.corporation || "GREATER BENGALURU AUTHORITY")}</p><h3>${esc(line.name)}</h3><p>${esc(line.description)}</p><a href="tel:${esc(line.phone)}">${esc(line.phone)}</a></article>`).join("");
      render();
    } catch (error) { pageError("#people-results", error); }
  }

  async function sourcesPage() {
    try {
      const [summary, people, wardsData] = await Promise.all([load("summary"), load("people"), load("wards")]);
      $("#snapshot-note").textContent = `Local data snapshot captured 13 November 2026: ${number(wardsData.items.length)} wards, ${number(people.officials.length)} official records, and ${number(summary.total)} works. The source API reports its latest works change at ${summary.latest_change_at ? new Date(summary.latest_change_at).toLocaleString("en-IN") : "a date not provided"}. This static snapshot does not refresh automatically.`;
    } catch (error) { $("#snapshot-note").textContent = `Local snapshot date: 13 November 2026. ${error.message}`; }
  }

  if (page === "overview") overview();
  if (page === "works") worksPage();
  if (page === "people") peoplePage();
  if (page === "sources") sourcesPage();
})();
