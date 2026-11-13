(() => {
  const page = document.querySelector(".people-page");
  if (!page) return;
  if (!document.querySelector('link[href^="/assets/people-dashboard.css"]')) {
    const stylesheet = document.createElement("link");
    stylesheet.rel = "stylesheet";
    stylesheet.href = "/assets/people-dashboard.css?v=1";
    document.head.append(stylesheet);
  }

  const corporationCodes = ["BWCC", "BCCC", "BNCC", "BSCC", "BECC"];
  const corporationNames = {
    BCCC: "Bengaluru Central City Corporation",
    BECC: "Bengaluru East City Corporation",
    BNCC: "Bengaluru North City Corporation",
    BSCC: "Bengaluru South City Corporation",
    BWCC: "Bengaluru West City Corporation",
  };
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const text = (value) => String(value ?? "").normalize("NFKD").toLocaleLowerCase("en");
  page.innerHTML = `<header class="civic-page-heading people-page-heading"><h1>People</h1></header>
    <div class="people-dashboard mt-6 min-w-0">
      <div class="people-tabs flex max-w-full flex-nowrap gap-1 overflow-x-auto rounded-xl bg-muted p-1" role="tablist" aria-label="Civic bodies">
        ${corporationCodes.map((code) => `<button class="people-tab rounded-md px-3 py-1.5 text-sm font-medium" type="button" role="tab" aria-selected="${code === "BWCC"}" data-body="${code}">${code}</button>`).join("")}
        <button class="people-tab rounded-md px-3 py-1.5 text-sm font-medium" type="button" role="tab" aria-selected="false" data-body="citywide">City-wide bodies</button>
      </div>
      <div class="people-search mt-4"><label class="sr-only" for="people-search">Search this body</label><input class="w-full rounded-lg border border-rule bg-background px-3 py-2 text-sm text-foreground placeholder:text-faint focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" id="people-search" type="search" placeholder="Search this body" aria-label="Search this body"></div>
      <div id="people-directory-content" aria-live="polite" class="mt-6"><p>Loading published officials…</p></div>
      <p id="people-source-note" class="mt-6 text-xs text-muted-foreground"></p>
    </div>
    <dialog class="people-profile-dialog rounded-xl border border-border bg-card p-0 text-card-foreground shadow-xl backdrop:bg-black/60" id="people-profile">
      <div class="w-[min(38rem,calc(100vw-2rem))] p-6"><button class="float-right rounded-md border border-border px-3 py-1 text-sm" data-close-profile type="button">Close</button><div id="people-profile-content"></div></div>
    </dialog>`;

  let dataset;
  let activeBody = "BWCC";
  const root = page.querySelector("#people-directory-content");
  const searchInput = page.querySelector("#people-search");
  const profile = page.querySelector("#people-profile");
  const profileContent = page.querySelector("#people-profile-content");

  function contactLinks(person) {
    return `<span class="people-contact-links">${person.phone ? `<a class="text-accent-amber underline-offset-4 hover:underline" href="tel:${esc(person.phone)}">Call ${esc(person.name)}</a>` : ""}${person.phone && person.email ? `<span aria-hidden="true"> · </span>` : ""}${person.email ? `<a class="text-accent-amber underline-offset-4 hover:underline" href="mailto:${esc(person.email)}">Email</a>` : ""}</span>`;
  }
  function personButton(person) {
    return `<button class="people-person-card group flex w-full items-start justify-between gap-3 rounded-lg border border-rule bg-panel px-3.5 py-3 text-left transition-colors hover:border-accent-line hover:bg-accent-wash" type="button" data-person="${esc(person.id)}"><span class="min-w-0 flex-1"><span class="block truncate text-[15px] font-semibold leading-tight text-paper">${esc(person.name)}</span><span class="mt-1 block truncate text-[13px] text-dim">${esc(person.department)}</span></span><span class="shrink-0 text-xs text-faint" aria-hidden="true">↗</span></button>`;
  }
  function showMoreList(people, key, emptyText) {
    if (!people.length) return `<p class="people-empty py-2 text-sm text-muted-foreground">${esc(emptyText)}</p>`;
    const expanded = root.dataset[`expanded${key}`] === "true";
    const shown = expanded ? people : people.slice(0, 3);
    return `<div class="grid gap-2 sm:grid-cols-2">${shown.map(personButton).join("")}</div>${people.length > 3 ? `<button class="people-show-more mt-3 rounded-md border border-rule px-3 py-1.5 text-sm text-dim hover:border-accent-line hover:text-paper" type="button" data-more="${esc(key)}">${expanded ? "Show less" : `Show all ${people.length}`}</button>` : ""}`;
  }
  function section(title, body, description = "") {
    return `<section class="people-section mt-8"><h2 class="font-heading text-lg font-semibold tracking-tight text-paper">${esc(title)}</h2>${description ? `<p class="mt-1 text-sm text-muted-foreground">${esc(description)}</p>` : ""}<div class="mt-3">${body}</div></section>`;
  }
  function filtered(rows, query) {
    if (!query) return rows;
    return rows.filter((person) => text([person.name, person.name_local, person.department, person.department_local, person.phone, person.email, person.address, person.agency, person.ward_number, ...(person.jurisdiction?.wards || [])].join(" ")).includes(query));
  }
  function renderBody(code, query) {
    const bodyOfficials = filtered(dataset.officials.filter((person) => person.agency === code), query);
    const bodyAgency = dataset.agencies.find((agency) => agency.code === code);
    const commissioner = bodyOfficials.filter((person) => person.category === "commissioner" || /commissioner/i.test(person.department || "")).slice(0, 1);
    const zoneHeads = bodyOfficials.filter((person) => person.level === "hq" && person.category === "city-officials/zone" && /chief engineer|joint commissioner/i.test(person.department || ""));
    const contacts = (dataset.helplines || []).filter((line) => !line.corporation || line.corporation === code);
    const contactCards = contacts.length ? `<div class="grid gap-2 sm:grid-cols-2">${contacts.map((line) => `<article class="people-contact-card rounded-lg border border-rule bg-panel p-3"><strong class="text-sm text-paper">${esc(line.name)}</strong><p class="mt-1 text-xs text-dim">${esc(line.description || "")}</p>${line.phone ? `<a class="mt-2 inline-block text-sm text-accent-amber" href="tel:${esc(line.phone)}">Call ${esc(line.phone)}</a>` : ""}</article>`).join("")}</div>` : `<p class="text-sm text-muted-foreground">No public helpline is listed for this body.</p>`;
    const leadership = commissioner.length || zoneHeads.length
      ? `<div class="grid gap-2 sm:grid-cols-2">${[...commissioner, ...zoneHeads].map(personButton).join("")}</div>`
      : `<p class="text-sm text-muted-foreground">No leadership contact is listed for this body.</p>`;

    const elected = dataset.councillors.filter((person) => person.agency === code);
    const councillors = elected.length ? showMoreList(elected, `councillors-${code}`, "No councillors are currently listed.") : `<p class="text-sm text-muted-foreground">This city has no sitting council. Its corporation is run by an administrator, so no ward has an elected councillor to name.</p>`;

    const headquarters = bodyOfficials.filter((person) => person.level === "hq" && person.category === "city-officials/corporation");
    const officerRecords = bodyOfficials.filter((person) => person.level === "hq" && (person.category === "officer" || person.category === "commissioner"));
    const zones = bodyOfficials.filter((person) => person.level === "hq" && person.category === "city-officials/zone");
    const wardStaff = bodyOfficials.filter((person) => person.level === "ward");
    const wardNumbers = [...new Set(wardStaff.flatMap((person) => person.jurisdiction?.wards?.length ? person.jurisdiction.wards : person.ward_number ? [person.ward_number] : []))].sort((a, b) => Number(a) - Number(b));
    const wardNames = new Map(dataset.wards.map((ward) => [Number(ward.ward_number), ward.name]));
    const wardGroups = wardNumbers.map((number) => {
      const staff = wardStaff.filter((person) => (person.jurisdiction?.wards || [person.ward_number]).map(Number).includes(Number(number)));
      return `<details class="people-ward-group rounded-lg border border-rule bg-panel px-3.5 py-2.5"><summary class="cursor-pointer text-sm font-medium text-paper">Ward ${esc(number)}${wardNames.has(Number(number)) ? ` · ${esc(wardNames.get(Number(number)))}` : ""}<span class="ml-2 text-xs text-faint">${staff.length} contacts</span></summary><div class="grid gap-2 py-3 sm:grid-cols-2">${staff.map(personButton).join("")}</div></details>`;
    }).join("");
    const emptySearch = query && !bodyOfficials.length ? `<p class="mt-5 rounded-lg border border-rule p-4 text-sm text-muted-foreground">No published officers match “${esc(searchInput.value.trim())}”.</p>` : "";

    return `<div class="people-body-panel" role="tabpanel" aria-label="${esc(bodyAgency?.name || code)}">
      <p class="text-sm text-muted-foreground">${esc(bodyAgency?.name || corporationNames[code] || code)}${bodyAgency?.note ? ` · ${esc(bodyAgency.note)}` : ""}</p>
      ${section("HOW TO REACH THEM", contactCards)}
      ${section("LEADERSHIP", leadership, "The executive who answers for this corporation, and the cluster heads responsible for its zones.")}
      ${section("COUNCILLORS", councillors)}
      ${section("HEADQUARTERS", showMoreList(headquarters, `hq-${code}`, "No headquarters contacts are listed."))}
      ${section("OFFICER", showMoreList(officerRecords, `officer-${code}`, "No additional officer records are listed."))}
      ${section("CITY-OFFICIALS/ZONE", showMoreList(zones, `zone-${code}`, "No zone-level officers are listed."))}
      ${section("WARD OFFICERS", wardGroups || `<p class="text-sm text-muted-foreground">No ward-level contacts are listed for this body.</p>`, `${wardNumbers.length} wards with published contacts.`)}
      ${emptySearch}
    </div>`;
  }
  function renderCitywide(query) {
    const grouped = new Map();
    dataset.officials.filter((person) => !corporationCodes.includes(person.agency) && filtered([person], query).length).forEach((person) => {
      if (!grouped.has(person.agency)) grouped.set(person.agency, []);
      grouped.get(person.agency).push(person);
    });
    const agencyOrder = dataset.agencies.filter((agency) => !corporationCodes.includes(agency.code) && grouped.has(agency.code));
    if (!agencyOrder.length) return `<p class="rounded-lg border border-rule p-4 text-sm text-muted-foreground">No city-wide officials match “${esc(searchInput.value.trim())}”.</p>`;
    return `<div class="space-y-8" role="tabpanel" aria-label="City-wide bodies">${agencyOrder.map((agency) => `<section class="people-section"><h2 class="font-heading text-lg font-semibold text-paper">${esc(agency.name)} <span class="text-sm font-normal text-dim">${esc(agency.code)}</span></h2>${agency.note ? `<p class="mt-1 text-sm text-muted-foreground">${esc(agency.note)}</p>` : ""}<div class="mt-3">${showMoreList(grouped.get(agency.code), `city-${agency.code}`, "No officials are listed.")}</div></section>`).join("")}</div>`;
  }
  function render() {
    const query = text(searchInput.value.trim());
    root.innerHTML = activeBody === "citywide" ? renderCitywide(query) : renderBody(activeBody, query);
    page.querySelectorAll("[data-body]").forEach((button) => button.setAttribute("aria-selected", String(button.dataset.body === activeBody)));
    page.querySelector("#people-source-note").textContent = `Public directory snapshot: ${dataset.snapshot_date || "date not provided"}. Records link to source agencies where published.`;
  }
  function showProfile(id) {
    const person = dataset.officials.find((item) => String(item.id) === String(id));
    if (!person) return;
    const body = dataset.agencies.find((agency) => agency.code === person.agency);
    const jurisdiction = person.jurisdiction?.wards?.length ? `Ward${person.jurisdiction.wards.length === 1 ? "" : "s"} ${person.jurisdiction.wards.join(", ")}` : person.level === "hq" ? "City-level officers" : person.ward_number ? `Ward ${person.ward_number}` : "Not published";
    const source = (person.address || "").match(/https?:\/\/[^\s]+/g) || [];
    profileContent.innerHTML = `<p class="home-kicker">${esc(person.category_local || person.category || person.level || "OFFICIAL")}</p><h2 class="mt-2 text-xl font-semibold text-paper">${esc(person.name)}</h2><p class="mt-1 text-sm text-dim">${esc(person.department)}</p><dl class="mt-5 grid gap-3 text-sm"><div><dt class="text-xs font-semibold uppercase tracking-wide text-faint">Body</dt><dd class="mt-1">${esc(body?.code || person.agency)}${body?.name ? ` — ${esc(body.name)}` : ""}</dd></div><div><dt class="text-xs font-semibold uppercase tracking-wide text-faint">Wards covered</dt><dd class="mt-1">${esc(jurisdiction)}</dd></div>${person.charge_status ? `<div><dt class="text-xs font-semibold uppercase tracking-wide text-faint">Charge status</dt><dd class="mt-1">${esc(person.charge_status)}${person.charge_since ? ` · since ${esc(person.charge_since)}` : ""}</dd></div>` : ""}${person.address ? `<div><dt class="text-xs font-semibold uppercase tracking-wide text-faint">Office / source area</dt><dd class="mt-1 whitespace-pre-line">${esc(person.address.replace(/https?:\/\/[^\s]+/g, "").trim())}</dd></div>` : ""}${person.office_timings ? `<div><dt class="text-xs font-semibold uppercase tracking-wide text-faint">Office hours</dt><dd class="mt-1">${esc(person.office_timings)}</dd></div>` : ""}</dl><div class="mt-5 flex flex-wrap gap-x-4 gap-y-2 text-sm">${contactLinks(person)}${source.map((url) => `<a class="text-accent-amber underline-offset-4 hover:underline" href="${esc(url)}" target="_blank" rel="noreferrer">Open published source</a>`).join("")}</div>`;
    profile.showModal();
  }

  page.addEventListener("click", (event) => {
    const tab = event.target.closest("[data-body]");
    if (tab) { activeBody = tab.dataset.body; searchInput.value = ""; render(); return; }
    const more = event.target.closest("[data-more]");
    if (more) { const key = `expanded${more.dataset.more}`; root.dataset[key] = String(root.dataset[key] !== "true"); render(); return; }
    const person = event.target.closest("[data-person]");
    if (person) showProfile(person.dataset.person);
    if (event.target.closest("[data-close-profile]")) profile.close();
  });
  profile.addEventListener("click", (event) => { if (event.target === profile) profile.close(); });
  searchInput.addEventListener("input", render);

  fetch("/api/v1/bengaluru/people?all=1")
    .then((response) => { if (!response.ok) throw new Error(`People API returned ${response.status}`); return response.json(); })
    .then((data) => {
      dataset = data;
      dataset.officials ||= data.items || [];
      dataset.councillors ||= [];
      dataset.helplines ||= [];
      dataset.wards = [];
      return fetch("/api/v1/bengaluru/wards").then((response) => response.json()).then((wardData) => { dataset.wards = wardData.items || wardData; render(); });
    })
    .catch((error) => {
      console.error("Could not load Bengaluru people directory:", error);
      root.innerHTML = `<p class="rounded-lg border border-rule p-4 text-sm text-muted-foreground">The published directory could not be loaded. Check the local API connection and reload this page.</p>`;
    });
})();
