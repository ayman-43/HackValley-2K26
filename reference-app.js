(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const citySlugs = ['bengaluru','delhi','gurugram','mumbai','pune'];
  const city = location.pathname.split('/').find((part) => citySlugs.includes(part)) || 'bengaluru';

  const themeButtons = $$('[data-testid="theme-toggle"]');
  const setTheme = (dark) => {
    document.documentElement.classList.toggle("dark", dark);
    document.documentElement.style.colorScheme = dark ? "dark" : "light";
    themeButtons.forEach((button) => button.setAttribute("aria-label", dark ? "Use light theme" : "Use dark theme"));
    try { localStorage.setItem("nagrik-theme", dark ? "dark" : "light"); } catch {}
    window.dispatchEvent(new CustomEvent("nagrik:themechange", { detail: { dark } }));
  };
  let savedDarkTheme = false;
  try { savedDarkTheme = localStorage.getItem("nagrik-theme") === "dark"; } catch {}
  setTheme(savedDarkTheme);
  themeButtons.forEach((button) => button.addEventListener("click", () => setTheme(!document.documentElement.classList.contains("dark"))));

  const popover = (button, title, content) => {
    if (!button) return;
    const panel = document.createElement("div");
    panel.className = "map-popover";
    panel.hidden = true;
    panel.innerHTML = `<strong>${escape(title)}</strong>${content}`;
    button.insertAdjacentElement("afterend", panel);
    button.addEventListener("click", () => {
      const open = panel.hidden;
      panel.hidden = !open;
      button.setAttribute("aria-expanded", String(open));
    });
  };

  const layersButton = $('[data-testid="layer-drawer-trigger"]');
  const helpButton = $('[aria-label="How to find your ward"]');
  if (layersButton) {
    const panel = $("#map-layers-panel");
    layersButton.addEventListener("click", () => {
      const open = layersButton.getAttribute("aria-expanded") !== "true";
      layersButton.setAttribute("aria-expanded", String(open));
      panel.hidden = !open;
    });
  }
  if (helpButton) {
    const panel = $("#map-help-panel");
    helpButton.addEventListener("click", () => {
      const open = helpButton.getAttribute("aria-expanded") !== "true";
      helpButton.setAttribute("aria-expanded", String(open));
      panel.hidden = !open;
    });
  }

  const cityButton = $('[aria-label="Change city"]');
  if (cityButton) {
    const menu = document.createElement("div");
    menu.className = "map-popover";
    menu.hidden = true;
    menu.id = "city-picker-menu";
    menu.setAttribute("role", "presentation");
    const cities = [
      ["Gurugram", "gurugram"], ["Delhi", "delhi"], ["Mumbai", "mumbai"], ["Bengaluru", "bengaluru"], ["Pune", "pune"],
    ];
    menu.innerHTML = `<strong>Choose a city</strong><nav aria-label="Cities" role="menu"><a class="city-picker-all" href="/outputs/nagrik-clone/" role="menuitem">All cities <small>Explore every city</small></a><span class="city-picker-divider" aria-hidden="true"></span>${cities.map(([name, slug]) => `<a href="/outputs/nagrik-clone/${slug}/" role="menuitem"${city === slug ? ' aria-current="page"' : ""}>${name}${city === slug ? '<span class="city-picker-current">Current city</span>' : ""}</a>`).join("")}</nav>`;
    cityButton.setAttribute("aria-controls", menu.id);
    cityButton.setAttribute("aria-expanded", "false");
    cityButton.insertAdjacentElement("afterend", menu);
    const closeMenu = (restoreFocus = false) => {
      menu.hidden = true;
      cityButton.setAttribute("aria-expanded", "false");
      if (restoreFocus) cityButton.focus();
    };
    cityButton.addEventListener("click", () => {
      menu.hidden = !menu.hidden;
      cityButton.setAttribute("aria-expanded", String(!menu.hidden));
    });
    menu.addEventListener("click", (event) => { if (event.target.closest("a")) closeMenu(); });
    document.addEventListener("click", (event) => {
      if (!menu.hidden && !menu.contains(event.target) && event.target !== cityButton) closeMenu();
    });
    document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !menu.hidden) closeMenu(true); });
  }
  const languageButton = $('[aria-label="Choose language"]');
  if (languageButton) {
    const panel = document.createElement("div");
    panel.className = "map-popover";
    panel.hidden = true;
    panel.innerHTML = '<strong>Language</strong><p>The captured Bengaluru page is available in English.</p>';
    languageButton.insertAdjacentElement("afterend", panel);
    languageButton.addEventListener("click", () => { panel.hidden = !panel.hidden; languageButton.setAttribute("aria-expanded", String(!panel.hidden)); });
  }
  const sectionsButton = $('[aria-label="City sections"]');
  if (sectionsButton) {
    const links = $$(".city-section-index a");
    const panel = document.createElement("div");
    panel.className = "map-popover";
    panel.hidden = true;
    panel.innerHTML = `<strong>On this page</strong><nav>${links.map((link) => `<a href="${escape(link.getAttribute("href"))}">${escape(link.textContent.trim())}</a>`).join("")}</nav>`;
    sectionsButton.insertAdjacentElement("afterend", panel);
    sectionsButton.addEventListener("click", () => { panel.hidden = !panel.hidden; sectionsButton.setAttribute("aria-expanded", String(!panel.hidden)); });
  }

  const recentWorksButton = $$('button').find((button) => button.textContent.trim() === 'Browse recent works');
  recentWorksButton?.addEventListener('click', () => {
    window.location.assign(`/outputs/nagrik-clone/${city}/works/?sort=newest`);
  });
  const cityInput = $("#ward-search") || $('[role="combobox"][aria-label^="Find an area"]') || $('input[placeholder*="Ward number"]');
  const wardMap = $("#ward-map") || $(".ward-map-frame > div") || $(".ward-map-canvas");
  const selection = $("#map-selection");
  const coordinateButton = $$('button').find((button) => button.textContent.trim() === "Coordinates");
  const locationButton = $$('button').find((button) => button.textContent.trim() === "Use my location");
  const mapInstance = () => window.nagrikWardMap;
  const chooseWard = (ward) => {
    const applySelection = () => {
      const instance = mapInstance();
      if (!instance) return false;
      instance.focusWard(ward.ward_id);
      if (instance.openWardById && !instance.focusWardOpensDetails) instance.openWardById(ward.ward_id);
      if (selection) selection.textContent = `${ward.name} · ${ward.corporation} · Ward ${ward.ward_number}`;
      if (cityInput) cityInput.value = `${ward.name} · ${ward.corporation} · Ward ${ward.ward_number}`;
      return true;
    };
    if (applySelection()) return;
    let attempts = 0;
    const timer = window.setInterval(() => {
      if (applySelection() || ++attempts >= 60) window.clearInterval(timer);
    }, 100);
  };
  if (cityInput && wardMap) {
    const suggestions = document.createElement("div");
    suggestions.className = "map-popover";
    suggestions.id = "ward-search-suggestions";
    suggestions.hidden = true;
    cityInput.setAttribute("aria-controls", suggestions.id);
    cityInput.parentElement.append(suggestions);
    fetch(`/api/v1/${city}/wards`).then((response) => response.json()).then((payload) => {
      const items = Array.isArray(payload) ? payload : payload.items;
      let currentMatches = [];
      const fold = (value) => String(value ?? "").normalize("NFKD").toLocaleLowerCase("en").trim();
      const search = () => {
        const q = fold(cityInput.value);
        if (!q) { suggestions.hidden = true; cityInput.setAttribute("aria-expanded", "false"); return; }
        const bareNumber = q.match(/^(\d+)$/);
        const wardQuery = q.match(/^(.*?)\s*ward\s*(?:no\.?\s*|number\s*)?#?\s*(\d+)$/) || (bareNumber ? [bareNumber[0], "", bareNumber[1]] : null);
        if (wardQuery) {
          const bodyQuery = fold(wardQuery[1]);
          const number = Number(wardQuery[2]);
          currentMatches = items.filter((ward) => Number(ward.ward_number) === number && (!bodyQuery || [ward.corporation,ward.body,ward.municipal_body,city].some((value) => fold(value).includes(bodyQuery))));
        } else {
          currentMatches = items.filter((ward) => `${ward.name} ${ward.name_local || ""} ${ward.corporation} ${ward.ward_number}`.toLocaleLowerCase("en").includes(q));
        }
        currentMatches = currentMatches.slice(0, 10);
        suggestions.innerHTML = currentMatches.length ? `<strong>Published wards</strong><div>${currentMatches.map((ward) => `<button type="button" data-suggestion="${ward.ward_id}">${escape(ward.name)} <small>${escape(ward.corporation)} · Ward ${escape(ward.ward_number)}</small></button>`).join("")}</div>` : `<p>No published ward matches “${escape(cityInput.value)}”.</p>`;
        suggestions.hidden = false;
        cityInput.setAttribute("aria-expanded", "true");
      };
      cityInput.addEventListener("input", search);
      cityInput.addEventListener("keydown", (event) => {
        if (event.key === "Escape") { suggestions.hidden = true; cityInput.setAttribute("aria-expanded", "false"); }
        if (event.key === "Enter" && !suggestions.hidden && currentMatches.length === 1) {
          event.preventDefault();
          chooseWard(currentMatches[0]);
          suggestions.hidden = true;
          cityInput.setAttribute("aria-expanded", "false");
        }
      });
      suggestions.addEventListener("click", (event) => {
        const button = event.target.closest("[data-suggestion]");
        if (!button) return;
        const ward = items.find((item) => item.ward_id === Number(button.dataset.suggestion));
        if (ward) chooseWard(ward);
        suggestions.hidden = true;
        cityInput.setAttribute("aria-expanded", "false");
      });
    }).catch(() => {});
  }
  coordinateButton?.addEventListener("click", () => {
    if (mapInstance()?.showCoordinates) { mapInstance().showCoordinates(); return; }
    const map = mapInstance()?.map;
    if (!map || !selection) return;
    const point = map.getCenter();
    selection.textContent = `Map center · ${point.lat.toFixed(5)}, ${point.lng.toFixed(5)}`;
  });
  locationButton?.addEventListener("click", () => {
    if (mapInstance()?.locate) { mapInstance().locate(locationButton); return; }
    if (!navigator.geolocation) { if (selection) selection.textContent = "Location is not available in this browser."; return; }
    locationButton.disabled = true;
    navigator.geolocation.getCurrentPosition(({ coords }) => {
      const map = mapInstance()?.map;
      map?.flyTo({ center: [coords.longitude, coords.latitude], zoom: 14 });
      if (selection) selection.textContent = `Your location · ${coords.latitude.toFixed(5)}, ${coords.longitude.toFixed(5)}`;
      locationButton.disabled = false;
    }, () => {
      if (selection) selection.textContent = "Your location could not be read. Search for a ward or choose one from the list.";
      locationButton.disabled = false;
    }, { enableHighAccuracy: true, timeout: 10000 });
  });
})();

