const cities = [
  { name: "Gurugram", value: "₹ 12688.4 Cr" },
  { name: "Delhi", value: "₹ 40265.7 Cr" },
  { name: "Mumbai", value: "₹ 76246 Cr" },
  { name: "Bengaluru", value: "₹ 6156.3 Cr" },
  { name: "Pune", value: "₹ 3910.7 Cr" },
];

document.querySelectorAll(".home-city-card").forEach((card, index) => {
  const value = card.querySelector(".home-city-inline-value strong");
  if (value && cities[index]) {
    value.textContent = cities[index].value;
    value.removeAttribute("aria-busy");
  }
  const link = card.querySelector(".home-city-card-head");
  if (link) link.setAttribute("aria-label", `${cities[index]?.name || "City"} public works, ${cities[index]?.value || ""}`);
  const landmark = card.querySelector(".home-city-landmark");
  if (landmark && !landmark.querySelector("svg") && !landmark.querySelector("img") && !landmark.querySelector('[role="presentation"]')) {
    landmark.innerHTML = `
      <svg class="clone-city-icon" viewBox="0 0 72 56" aria-hidden="true" focusable="false">
        <path d="M5 51h62M10 51V24h12v27M24 51V9h14v42M41 51V18h12v33M56 51V29h8v22" />
        <path d="M13 29h3m3 0h1m-4 6h3m-3 6h3m9-27h3m5 0h3m-11 7h3m5 0h3m-11 7h3m5 0h3m-11 7h3m5 0h3m-11 7h3m5 0h3m5-15h3m4 0h3m-10 7h3m4 0h3m-10 7h3m4 0h3m-6 7h3m8-14h2m-2 7h2" />
        <path d="M29 51V41a2.5 2.5 0 0 1 5 0v10" />
      </svg>`;
  }
});

const stats = [
  ["22,683", "76246 Cr"],
  ["38,305", "40265.7 Cr"],
  ["4,974", "6156.3 Cr"],
];
document.querySelectorAll(".home-product-stats > div").forEach((row, index) => {
  row.querySelectorAll("dd").forEach((value, valueIndex) => {
    if (!stats[index]) return;
    value.firstChild.nodeValue = stats[index][valueIndex];
  });
});

const themeToggle = document.querySelector('[data-testid="theme-toggle"]');
try {
  const savedTheme = localStorage.getItem("nagrik-theme");
  const isDark = savedTheme === "dark";
  document.documentElement.classList.toggle("dark", isDark);
  document.documentElement.style.colorScheme = isDark ? "dark" : "light";
  themeToggle?.setAttribute("aria-label", isDark ? "Use light theme" : "Use dark theme");
} catch {}
themeToggle?.addEventListener("click", () => {
  const isDark = document.documentElement.classList.toggle("dark");
  document.documentElement.style.colorScheme = isDark ? "dark" : "light";
  localStorage.setItem("nagrik-theme", isDark ? "dark" : "light");
  themeToggle.setAttribute("aria-label", isDark ? "Use light theme" : "Use dark theme");
});

document.querySelectorAll('a[href="/#cities"]').forEach((link) => link.setAttribute("href", "#cities"));
document.querySelectorAll('a[href^="/"]').forEach((link) => {
  const path = link.getAttribute("href");
  if (path === "/") link.setAttribute("href", "./");
  else if (path === "/bengaluru" || path === "/bengaluru/") link.setAttribute("href", "./bengaluru/");
  else if (path.startsWith("/bengaluru/")) {
    const rest = path.slice("/bengaluru/".length);
    const [route, suffix = ""] = rest.split(/(?=[?#])/, 2);
    const localRoute = ["works", "people", "sources"].includes(route) ? `${route}/` : route;
    link.setAttribute("href", `./bengaluru/${localRoute}${suffix}`);
  }
  else link.setAttribute("href", `https://nagrik.in.net${path}`);
});

const emailLabel = document.querySelector(".__cf_email__");
if (emailLabel) {
  emailLabel.textContent = "nagrikadmin@gmail.com";
  emailLabel.removeAttribute("data-cfemail");
  const emailLink = emailLabel.closest("a");
  emailLink?.setAttribute("href", "mailto:nagrikadmin@gmail.com?subject=Nagrik%20%E2%80%94%20correction%20or%20issue");
}
