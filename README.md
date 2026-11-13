# Nagrik Local Recreation

An exact, 1:1 recreation of [Nagrik (nagrik.in.net)](https://nagrik.in.net/).

## Features & Parity
- **Exact Pages & Clean URLs**: Homepage (`/`), About (`/about`), Integrations (`/integrations`), plus all 5 cities (Bengaluru, Delhi, Gurugram, Mumbai, Pune) across their Overview, Works, People, and Sources routes. All routes resolve with or without trailing slashes.
- **Assets & Styling**: Pristine Instrument Sans web fonts (`/fonts/`), city landmark silhouettes (`/landmarks/`), high-resolution brand logos, and original CSS styling.
- **Indic Script Support**: Native Kannada (ಕನ್ನಡ), Hindi (हिन्दी), and Marathi (मराठी) text preserved with full UTF-8 fidelity.
- **Theme**: Seamless Light / Dark theme switching persisted across reloads.
- **Client Runtime**: Full React Router client-side hydration with all 53 bundle and route modules.
- **Backend API & Data**: Local snapshot data for Bengaluru, coupled with live upstream Nagrik API proxies for Gurugram, Delhi, Mumbai, and Pune. Includes live weather/AQI telemetry and real-time civic news via Google News RSS.

## Quickstart

Run from `outputs/nagrik-clone`:

```powershell
npm run dev
```

Open [http://127.0.0.1:5501/](http://127.0.0.1:5501/) in your browser.
