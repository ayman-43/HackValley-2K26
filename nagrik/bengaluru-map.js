import { a as maplibre } from "./assets/maplibre/maplibre-gl-CnVrYLhK.js";

const styleUrl = (dark) => `https://tiles.openfreemap.org/styles/${dark ? "dark" : "positron"}`;
const center = [77.621972, 12.988055];
const bounds = [[77.45988, 12.833491], [77.784064, 13.14262]];
const fillLayer = "bengaluru-ward-fill";
const lineLayer = "bengaluru-ward-line";
const labelLayer = "bengaluru-ward-label";
const labelSource = "bengaluru-ward-label-points";
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function hsl(hue, saturation, lightness) {
  saturation /= 100;
  lightness /= 100;
  const k = (n) => (n + hue / 30) % 12;
  const a = saturation * Math.min(lightness, 1 - lightness);
  const f = (n) => lightness - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
  return `#${[f(0), f(8), f(4)].map((v) => Math.round(255 * v).toString(16).padStart(2, "0")).join("")}`;
}

function boundsFor(geometry, box = [[Infinity, Infinity], [-Infinity, -Infinity]]) {
  if (!Array.isArray(geometry)) return box;
  if (geometry.length >= 2 && typeof geometry[0] === "number" && typeof geometry[1] === "number") {
    box[0][0] = Math.min(box[0][0], geometry[0]);
    box[0][1] = Math.min(box[0][1], geometry[1]);
    box[1][0] = Math.max(box[1][0], geometry[0]);
    box[1][1] = Math.max(box[1][1], geometry[1]);
  } else geometry.forEach((item) => boundsFor(item, box));
  return box;
}

function wardLabelPoints(geojson) {
  return {
    type: "FeatureCollection",
    features: geojson.features.map((feature) => {
      const coords = [];
      const visit = (geometry) => {
        if (!Array.isArray(geometry)) return;
        if (geometry.length >= 2 && typeof geometry[0] === "number" && typeof geometry[1] === "number") coords.push(geometry);
        else geometry.forEach(visit);
      };
      visit(feature.geometry.coordinates);
      const [lng, lat] = coords.reduce((sum, point) => [sum[0] + point[0] / coords.length, sum[1] + point[1] / coords.length], [0, 0]);
      return { type: "Feature", properties: { ward_number: feature.properties.ward_number }, geometry: { type: "Point", coordinates: [lng, lat] } };
    }),
  };
}

function paintWardLayers(map, geojson, dark, activeWard) {
  const fill = ["match", ["get", "ward_id"]];
  const line = ["match", ["get", "ward_id"]];
  const ids = geojson.features.map((feature) => feature.properties.ward_id);
  ids.forEach((id, index) => {
    const hue = index * 360 / ids.length;
    fill.push(id, hsl(hue, dark ? 80 : 52, dark ? 56 : 42));
    line.push(id, hsl(hue, dark ? 88 : 60, dark ? 70 : 32));
  });
  fill.push("#607080");
  line.push("#526170");
  const active = ["boolean", ["feature-state", "active"], false];
  if (!map.getSource("bengaluru-wards")) map.addSource("bengaluru-wards", { type: "geojson", data: geojson, promoteId: "ward_id" });
  if (!map.getLayer(fillLayer)) map.addLayer({ id: fillLayer, type: "fill", source: "bengaluru-wards", paint: {} });
  map.setPaintProperty(fillLayer, "fill-color", fill);
  map.setPaintProperty(fillLayer, "fill-opacity", ["case", active, 0, dark ? 0.28 : 0.32]);
  if (!map.getLayer(lineLayer)) map.addLayer({ id: lineLayer, type: "line", source: "bengaluru-wards", paint: {} });
  map.setPaintProperty(lineLayer, "line-color", ["case", active, "#ffb547", line]);
  map.setPaintProperty(lineLayer, "line-width", ["case", active, 2.6, 1]);
  map.setPaintProperty(lineLayer, "line-opacity", dark ? 0.9 : 0.7);
  if (!map.getSource(labelSource)) map.addSource(labelSource, { type: "geojson", data: wardLabelPoints(geojson) });
  if (!map.getLayer(labelLayer)) map.addLayer({ id: labelLayer, type: "symbol", source: labelSource, layout: { "text-field": ["to-string", ["get", "ward_number"]], "text-font": ["Noto Sans Bold"], "text-size": 13, "text-allow-overlap": false, "text-ignore-placement": false, "text-padding": 0 }, paint: { "text-color": dark ? "#f4efe3" : "#20262d", "text-halo-color": dark ? "#15181d" : "#f1f3f5", "text-halo-width": 1.6 } });
  if (activeWard !== null) map.setFeatureState({ source: "bengaluru-wards", id: activeWard }, { active: true });
}

async function openWard(wardId, wards, people) {
  const ward = wards.find((item) => item.ward_id === Number(wardId));
  if (!ward) return;
  document.querySelector("#map-selection").textContent = `${ward.name} · ${ward.corporation} · Ward ${ward.ward_number}`;
  const detail = document.querySelector("#ward-detail");
  detail.hidden = false;
  const officials = people.officials.filter((person) => person.agency === ward.corporation);
  const officers = officials.filter((person) => person.level === "ward" && (person.ward_number === ward.ward_number || person.jurisdiction?.wards?.includes(ward.ward_number)));
  const commissioner = officials.find((person) => person.level === "hq" && /^commissioner$/i.test(person.department || "") && /I\.A\.S/i.test(person.name || ""));
  const corporationNames = { BCCC:"Bengaluru Central City Corporation", BECC:"Bengaluru East City Corporation", BNCC:"Bengaluru North City Corporation", BSCC:"Bengaluru South City Corporation", BWCC:"Bengaluru West City Corporation" };
  const helplines = people.helplines || [];
  const tel = (person) => person.phone ? `<a href="tel:${esc(person.phone)}">Call ${esc(person.name)}</a>` : "";
  const mail = (person) => person.email ? `<a href="mailto:${esc(person.email)}">Email</a>` : "";
  detail.innerHTML = `<header class="ward-selection-heading"><div><p class="home-kicker">${esc(ward.corporation)} · WARD ${esc(ward.ward_number)}</p><h2>${esc(ward.name.toUpperCase())}</h2>${ward.name_local ? `<p class="local-name">${esc(ward.name_local)}</p>` : ""}</div><button class="ward-clear" type="button" data-clear-ward>Clear this area</button></header><dl class="detail-list"><div class="detail-row"><dt>Municipal body</dt><dd>${esc(ward.corporation)} — ${esc(corporationNames[ward.corporation] || ward.corporation)}</dd></div><div class="detail-row"><dt>Resolved from</dt><dd>Chosen from the published ward map</dd></div><div class="detail-row"><dt>Ward number</dt><dd>${esc(ward.ward_number)}</dd></div></dl>
    <section class="ward-detail-section"><h3>Commissioner's office</h3><p>Contact the head of ${esc(ward.corporation)} for help with this area.</p>${commissioner ? `<p><strong>${esc(commissioner.name)}</strong><br>${esc(commissioner.department)} · ${esc(ward.corporation)}</p><p>${tel(commissioner)} ${mail(commissioner)}</p>` : `<p>Commissioner contact details are not listed in this snapshot.</p>`}</section>
    <section class="ward-detail-section"><h3>Work near this governing area</h3><p>Projects explicitly linked to this ward in Nagrik's public API.</p><div id="ward-projects">Loading published works…</div></section>
    <section class="ward-detail-section"><h3>Get help now</h3><p>Numbers published by bodies covering your area.</p><ul class="dialog-list">${helplines.map((item) => `<li><strong>${esc(item.name)}</strong><span>${esc(item.description || "")}${item.phone ? ` · <a href="tel:${esc(item.phone)}">${esc(item.phone)}</a>` : ""}</span></li>`).join("")}</ul></section>
    <section class="ward-detail-section"><h3>Officers for your ward</h3><p>Published ward level contacts responsible for this area.</p>${officers.length ? `<ul class="dialog-list">${officers.map((person) => `<li><strong>${esc(person.name)}</strong><span>${esc(person.department)} · ${esc(person.agency)}${person.phone ? ` · <a href="tel:${esc(person.phone)}">${esc(person.phone)}</a>` : ""}${person.email ? ` · <a href="mailto:${esc(person.email)}">Email</a>` : ""}</span></li>`).join("")}</ul>` : `<p>No ward-assigned officer is listed in the published directory.</p>`}</section>
    <section class="ward-detail-section"><h3>Elected councillor</h3><p>This city has no sitting council. Its corporation is run by an administrator, so no elected councillor is listed for this ward.</p></section>`;
  const projectContainer = detail.querySelector("#ward-projects");
  fetch(`/api/v1/bengaluru/works?ward_id=${encodeURIComponent(ward.ward_id)}&per_page=3`).then((response) => {
    if (!response.ok) throw new Error(`Ward works returned ${response.status}`);
    return response.json();
  }).then((payload) => {
    const projects = payload.items || [];
    projectContainer.innerHTML = projects.length ? projects.map((project) => `<article class="ward-project"><h4>${esc(project.title)}</h4><p>${esc(project.executing_agency || ward.corporation)} · ${esc(project.division || project.location_text || "")} · ${esc(project.status || "Status not published")}</p><dl class="detail-list"><div class="detail-row"><dt>Tender reference</dt><dd>${esc(project.tender_ref || "Not published")}</dd></div><div class="detail-row"><dt>Awarded value</dt><dd>${project.awarded_budget ? `₹ ${esc(project.awarded_budget)}` : "Not published"}</dd></div><div class="detail-row"><dt>Location</dt><dd>${esc(project.location_text || project.division || "Not published")}</dd></div></dl><details><summary>View more details</summary><p>${esc(project.scope_of_work || project.title)}</p><p>Inviting authority: ${esc(project.inviting_authority || "Not published")}</p><p>Tender date: ${esc(project.tender_date || "Not published")} · Bid close: ${esc(project.bid_close_date || "Not published")}</p><p>${project.source_url ? `<a href="${esc(project.source_url)}" target="_blank" rel="noreferrer">Open official tender source</a>` : "Official source link not published."}</p></details></article>`).join("") : `<p>No works are explicitly linked to this ward in the public API.</p>`;
  }).catch((error) => {
    console.warn("Could not load live ward works:", error.message);
    projectContainer.innerHTML = `<p>Ward-specific works could not be loaded. See the <a href="${esc(`https://nagrik.in.net/bengaluru/issues/roads-potholes?ward=${ward.ward_id}`)}">original Nagrik ward view</a>.</p>`;
  });
  detail.querySelector("[data-clear-ward]").addEventListener("click", () => {
    detail.hidden = true;
    document.querySelector("#map-selection").textContent = "Click a coloured ward to see its name and corporation.";
  });
}

export async function createWardMap({ container, dataUrl, onPick }) {
  const oldDialog = document.querySelector("#ward-dialog");
  if (oldDialog) {
    const inlinePanel = document.createElement("section");
    inlinePanel.id = "ward-detail";
    inlinePanel.className = "ward-selection-details";
    inlinePanel.setAttribute("aria-live", "polite");
    inlinePanel.hidden = true;
    oldDialog.replaceWith(inlinePanel);
    document.querySelector(".ward-issues-unresolved")?.before(inlinePanel);
  }
  if (!document.querySelector('link[href^="/assets/ward-details.css"]')) {
    const stylesheet = document.createElement("link");
    stylesheet.rel = "stylesheet";
    stylesheet.href = "/assets/ward-details.css?v=3";
    document.head.append(stylesheet);
  }
  const [geojson, wardData, people] = await Promise.all([
    fetch(dataUrl).then((response) => { if (!response.ok) throw new Error(`Ward boundary data returned ${response.status}`); return response.json(); }),
    fetch("/api/v1/bengaluru/wards").then((response) => response.json()),
    fetch("/api/v1/bengaluru/people?all=1").then((response) => response.json()),
  ]);
  const wards = wardData.items;
  people.officials = people.officials || people.items || [];
  const dark = document.documentElement.classList.contains("dark");
  const map = new maplibre.Map({ container, style: styleUrl(dark), center, zoom: 10, attributionControl: false, minZoom: 8, maxZoom: 17 });
  map.addControl(new maplibre.NavigationControl({ visualizePitch: false }), "bottom-right");
  map.addControl(new maplibre.ScaleControl({ maxWidth: 120, unit: "metric" }), "bottom-left");
  map.addControl(new maplibre.AttributionControl({ compact: true }), "bottom-right");
  let activeWard = null;
  let ready = false;

  const addWards = () => {
    const currentDark = document.documentElement.classList.contains("dark");
    paintWardLayers(map, geojson, currentDark, activeWard);
    if (!ready) {
      ready = true;
      const extent = geojson.features.reduce((box, feature) => boundsFor(feature.geometry.coordinates, box), [[Infinity, Infinity], [-Infinity, -Infinity]]);
      if (extent[0][0] !== Infinity) map.fitBounds(extent, { padding: 42, duration: 0, maxZoom: 12 });
      container.querySelector(".map-loading")?.remove();
    }
    document.querySelector("#map-ward-count").textContent = `${geojson.features.length} wards mapped`;
  };
  map.on("load", addWards);
  map.on("style.load", addWards);
  map.on("mousemove", (event) => {
    if (!ready) return;
    container.style.cursor = map.queryRenderedFeatures(event.point, { layers: [fillLayer] }).length ? "pointer" : "grab";
  });
  map.on("click", (event) => {
    if (!ready) return;
    const feature = map.queryRenderedFeatures(event.point, { layers: [fillLayer] })[0];
    if (!feature) {
      document.querySelector("#map-selection").textContent = `Map point ${event.lngLat.lat.toFixed(5)}, ${event.lngLat.lng.toFixed(5)} · no published ward boundary here.`;
      return;
    }
    const next = Number(feature.properties.ward_id);
    if (activeWard !== null) map.setFeatureState({ source: "bengaluru-wards", id: activeWard }, { active: false });
    activeWard = next;
    map.setFeatureState({ source: "bengaluru-wards", id: activeWard }, { active: true });
    onPick(activeWard);
    openWard(activeWard, wards, people);
  });
  map.on("error", (event) => {
    if (event?.error) console.warn("Bengaluru map resource issue:", event.error.message || event.error);
  });

  window.addEventListener("nagrik:themechange", (event) => {
    const darkMode = event.detail?.dark ?? document.documentElement.classList.contains("dark");
    const camera = { center: map.getCenter(), zoom: map.getZoom(), bearing: map.getBearing(), pitch: map.getPitch() };
    map.setStyle(styleUrl(darkMode));
    map.once("idle", () => {
      addWards();
      if (ready) map.jumpTo(camera);
    });
  });

  const focusWard = (wardId) => {
    const feature = geojson.features.find((item) => Number(item.properties.ward_id) === Number(wardId));
    if (!feature || !ready) return;
    if (activeWard !== null) map.setFeatureState({ source: "bengaluru-wards", id: activeWard }, { active: false });
    activeWard = Number(wardId);
    map.setFeatureState({ source: "bengaluru-wards", id: activeWard }, { active: true });
    map.fitBounds(boundsFor(feature.geometry.coordinates), { padding: 85, maxZoom: 14, duration: 650 });
  };
  document.querySelector("#ward-results").addEventListener("click", (event) => {
    const button = event.target.closest("[data-ward]");
    if (!button) return;
    const ward = wards.find((item) => item.ward_id === Number(button.dataset.ward));
    if (ward) { focusWard(ward.ward_id); onPick(ward.ward_id); openWard(ward.ward_id, wards, people); }
  });

  const layersButton = document.querySelector("#map-layers-toggle");
  const layersPanel = document.querySelector("#map-layers-panel");
  const helpButton = document.querySelector("#map-help-toggle");
  const helpPanel = document.querySelector("#map-help-panel");
  const togglePanel = (button, panel, otherButton, otherPanel) => {
    button.addEventListener("click", () => {
      const opened = button.getAttribute("aria-expanded") !== "true";
      button.setAttribute("aria-expanded", String(opened));
      panel.hidden = !opened;
      otherButton.setAttribute("aria-expanded", "false");
      otherPanel.hidden = true;
    });
  };
  togglePanel(layersButton, layersPanel, helpButton, helpPanel);
  togglePanel(helpButton, helpPanel, layersButton, layersPanel);
  return { focusWard, openWardById: (wardId) => openWard(wardId, wards, people), map };
}

const mapContainer = document.querySelector("#ward-map");
if (mapContainer) {
  createWardMap({ container: mapContainer, dataUrl: "/api/v1/bengaluru/wards/geojson", onPick: () => {} })
    .then((map) => { window.nagrikWardMap = map; })
    .catch((error) => {
      mapContainer.innerHTML = `<div class="map-loading">The ward map could not load. The ward directory is still available below.</div>`;
      document.querySelector("#map-ward-count").textContent = "Map unavailable";
      console.error("Could not initialize Bengaluru ward map:", error);
    });
}
