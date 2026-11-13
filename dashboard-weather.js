(() => {
  const snapshot = document.querySelector("#snapshot");
  const coordinates = document.querySelector("[data-dashboard-live]");
  if (!snapshot || !coordinates) return;

  const timeNode = snapshot.querySelector("[data-live-time]");
  const aqiNode = snapshot.querySelector("[data-live-aqi]");
  const aqiLabel = snapshot.querySelector("[data-live-aqi-label]");
  const aqiBadge = snapshot.querySelector("[data-live-aqi-badge]");
  const temperatureNode = snapshot.querySelector("[data-live-temperature]");
  const weatherNode = snapshot.querySelector("[data-live-weather]");
  const cityPath = location.pathname.replace(/\/+$/, "").split("/").pop();
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  let timezone = "Asia/Kolkata";

  const updateClock = () => {
    if (!timeNode) return;
    const now = new Date();
    const date = new Intl.DateTimeFormat("en-IN", { timeZone: timezone, day: "2-digit", month: "short", year: "numeric" }).format(now);
    const time = new Intl.DateTimeFormat("en-IN", { timeZone: timezone, hour: "numeric", minute: "2-digit", hour12: true }).format(now);
    timeNode.textContent = `${date}, ${time}`;
    timeNode.dateTime = now.toISOString();
  };

  function aqiStatus(value) {
    if (value <= 50) return { label: "Good", tone: "text-grow", badge: "bg-grow/12" };
    if (value <= 100) return { label: "Moderate", tone: "text-yellow-400", badge: "bg-yellow-400/10" };
    if (value <= 150) return { label: "Unhealthy for sensitive groups", tone: "text-orange-400", badge: "bg-orange-400/10" };
    if (value <= 200) return { label: "Unhealthy", tone: "text-red-400", badge: "bg-red-400/10" };
    if (value <= 300) return { label: "Very unhealthy", tone: "text-purple-400", badge: "bg-purple-400/10" };
    return { label: "Hazardous", tone: "text-rose-400", badge: "bg-rose-400/10" };
  }

  const weatherDescription = (code) => ({
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog", 51: "Light drizzle", 53: "Drizzle",
    55: "Dense drizzle", 56: "Freezing drizzle", 57: "Dense freezing drizzle",
    61: "Slight rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain",
    67: "Heavy freezing rain", 71: "Slight snow", 73: "Snow", 75: "Heavy snow",
    77: "Snow grains", 80: "Rain showers", 81: "Rain showers", 82: "Heavy rain showers",
    85: "Snow showers", 86: "Heavy snow showers", 95: "Thunderstorm",
    96: "Thunderstorm with hail", 99: "Thunderstorm with heavy hail",
  })[code] || "Conditions unavailable";

  async function loadForCity(city) {
    const { lat, lng } = city.centre || {};
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) return;
    timezone = city.timezone || timezone;
    updateClock();
    const point = `latitude=${encodeURIComponent(lat)}&longitude=${encodeURIComponent(lng)}&timezone=${encodeURIComponent(timezone)}`;
    const weatherUrl = `https://api.open-meteo.com/v1/forecast?${point}&current=temperature_2m,weather_code`;
    const airUrl = `https://air-quality-api.open-meteo.com/v1/air-quality?${point}&current=us_aqi,us_aqi_pm2_5,us_aqi_pm10,us_aqi_ozone,us_aqi_nitrogen_dioxide,us_aqi_sulphur_dioxide,us_aqi_carbon_monoxide`;
    const [weatherResult, airResult] = await Promise.allSettled([
      fetch(weatherUrl, { headers: { Accept: "application/json" }, cache: "no-store" }).then((r) => { if (!r.ok) throw new Error(`Weather service returned ${r.status}`); return r.json(); }),
      fetch(airUrl, { headers: { Accept: "application/json" }, cache: "no-store" }).then((r) => { if (!r.ok) throw new Error(`Air quality service returned ${r.status}`); return r.json(); }),
    ]);

    if (weatherResult.status === "fulfilled") {
      const current = weatherResult.value.current || {};
      if (Number.isFinite(current.temperature_2m)) temperatureNode.textContent = `${Math.round(current.temperature_2m)}°`;
      if (Number.isFinite(current.weather_code)) weatherNode.textContent = weatherDescription(current.weather_code);
      snapshot.querySelector("[data-live-weather-pill]")?.setAttribute("title", `Source: Open-Meteo · Updated ${weatherResult.value.current.time || "currently"}`);
    } else {
      temperatureNode.textContent = "—°";
      weatherNode.textContent = "Weather unavailable";
      snapshot.querySelector("[data-live-weather-pill]")?.setAttribute("title", "Weather data is currently unavailable from Open-Meteo");
    }

    if (airResult.status === "fulfilled") {
      const current = airResult.value.current || {};
      const value = current.us_aqi;
      if (Number.isFinite(value)) {
        const status = aqiStatus(value);
        aqiNode.textContent = String(Math.round(value));
        aqiLabel.textContent = status.label;
        aqiBadge.className = `inline-flex items-center gap-2 rounded-md px-3 py-1.5 ${status.badge}`;
        aqiBadge.querySelector("svg")?.classList.remove("text-grow", "text-yellow-400", "text-orange-400", "text-red-400", "text-purple-400", "text-rose-400");
        aqiBadge.querySelector("svg")?.classList.add(status.tone);
        aqiNode.className = `mono text-lg font-semibold ${status.tone}`;
        aqiLabel.className = `text-[13px] font-medium ${status.tone}`;
        const componentFields = [
          ["us_aqi_pm2_5", "PM2.5"], ["us_aqi_pm10", "PM10"], ["us_aqi_ozone", "Ozone"],
          ["us_aqi_nitrogen_dioxide", "NO₂"], ["us_aqi_sulphur_dioxide", "SO₂"], ["us_aqi_carbon_monoxide", "CO"],
        ];
        const dominant = componentFields.map(([key, name]) => [current[key], name]).filter(([n]) => Number.isFinite(n)).sort((a, b) => b[0] - a[0])[0];
        aqiBadge.title = dominant ? `Dominant pollutant: ${dominant[1]} · Source: Open-Meteo` : "US AQI · Source: Open-Meteo";
      } else {
        aqiNode.textContent = "—";
        aqiLabel.textContent = "AQI unavailable";
        aqiBadge.title = "Air quality data is currently unavailable from Open-Meteo";
      }
    } else {
      aqiNode.textContent = "—";
      aqiLabel.textContent = "AQI unavailable";
      aqiBadge.title = "Air quality data is currently unavailable from Open-Meteo";
    }
  }

  updateClock();
  window.setInterval(updateClock, 15_000);
  fetch("../cities.json", { headers: { Accept: "application/json" }, cache: "no-store" })
    .then((response) => { if (!response.ok) throw new Error("City metadata is unavailable"); return response.json(); })
    .then((cities) => {
      const city = cities.find((item) => item.slug === cityPath);
      if (city) {
        loadForCity(city);
        window.setInterval(() => loadForCity(city), 15 * 60_000);
      }
      else {
        aqiLabel.textContent = "City data unavailable";
        weatherNode.textContent = "City data unavailable";
      }
    })
    .catch(() => {
      aqiNode.textContent = "—";
      aqiLabel.textContent = "AQI unavailable";
      temperatureNode.textContent = "—°";
      weatherNode.textContent = "Weather unavailable";
    });
})();
