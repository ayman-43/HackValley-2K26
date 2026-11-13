import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { extname, resolve, sep, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const dataDir = resolve(root, 'bengaluru', 'data');
const upstream = 'https://nagrik.in.net';
const port = Number(process.env.PORT || process.argv[2] || 5501);
const cache = new Map();

const mime = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
  '.geojson': 'application/geo+json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.xml': 'application/xml; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8'
};

async function dataset(name) {
  if (!cache.has(name)) {
    cache.set(name, readFile(resolve(dataDir, name), 'utf8').then(JSON.parse));
  }
  return cache.get(name);
}

const int = (value, fallback, min, max) => Math.min(max, Math.max(min, Number.parseInt(value, 10) || fallback));
const norm = (value) => String(value ?? '').normalize('NFKD').toLocaleLowerCase('en').trim();
const includes = (item, q, fields) => !q || fields.some((key) => norm(item[key]).includes(q));

const json = (res, status, body) => {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(payload),
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff'
  });
  res.end(payload);
};

const notFound = (res) => json(res, 404, { error: { code: 'NOT_FOUND', message: 'No matching record or endpoint.' } });

const decodeXml = (value) => value
  .replace(/<!\[CDATA\[|\]\]>/g, '')
  .replace(/&amp;/g, '&')
  .replace(/&lt;/g, '<')
  .replace(/&gt;/g, '>')
  .replace(/&quot;/g, '"')
  .replace(/&#39;|&apos;/g, "'")
  .replace(/&#(\d+);/g, (_, code) => String.fromCodePoint(Number(code)))
  .replace(/&#x([0-9a-f]+);/gi, (_, code) => String.fromCodePoint(parseInt(code, 16)))
  .trim();

const xmlField = (xml, tag) => {
  const match = xml.match(new RegExp(`<${tag}(?:\\s[^>]*)?>([\\s\\S]*?)<\\/${tag}>`, 'i'));
  return match ? decodeXml(match[1]) : '';
};

async function upstreamJson(path) {
  const response = await fetch(`${upstream}${path}`, {
    headers: { Accept: 'application/json' },
    signal: AbortSignal.timeout(15000)
  });
  const body = await response.text();
  if (!response.ok) {
    throw Object.assign(new Error(`Nagrik public API returned ${response.status}`), { status: response.status });
  }
  return { body, contentType: response.headers.get('content-type') || 'application/json; charset=utf-8' };
}

function sendBody(res, status, body, contentType = 'application/json; charset=utf-8') {
  res.writeHead(status, {
    'Content-Type': contentType,
    'Content-Length': Buffer.byteLength(body),
    'Cache-Control': 'public, max-age=60',
    'X-Content-Type-Options': 'nosniff'
  });
  res.end(body);
}

const citySlugToCode = {
  bengaluru: 'BLR',
  delhi: 'DEL',
  gurugram: 'GGN',
  mumbai: 'MUM',
  pune: 'PUN'
};

const cityCodeToSlug = {
  BLR: 'bengaluru',
  DEL: 'delhi',
  GGN: 'gurugram',
  MUM: 'mumbai',
  PUN: 'pune'
};

async function api(url, res) {
  const path = url.pathname.replace(/\/+$/, '') || '/';
  if (path === '/api/health') {
    return json(res, 200, { status: 'ok', service: 'nagrik-local-api', cities: 5, bengaluruSnapshotDate: '2026-11-13' });
  }
  if (path === '/api/v1/cities') {
    return json(res, 200, await dataset('cities.json'));
  }
  if (path === '/api/v1/connections') {
    return json(res, 200, {
      application: { kind: 'local-frontend-and-city-aware-api', database: 'Bengaluru snapshot JSON', authentication: null, writeEndpoints: false },
      cityCodes: { bengaluru: 'BLR', delhi: 'DEL', gurugram: 'GGN', mumbai: 'MUM', pune: 'PUN' },
      cityRoutes: ['/api/v1/:city/summary', '/api/v1/:city/works', '/api/v1/:city/people', '/api/v1/:city/wards', '/api/v1/:city/wards.geojson'],
      upstreamRoutes: ['/api/v1/projects', '/api/v1/projects/summary', '/api/v1/people', '/api/v1/wards', '/api/v1/wards/geojson', '/api/v1/news', '/api/v1/conditions', '/api/v1/stats', '/api/v1/localities', '/api/v1/map/layers', '/api/v1/resolve'],
      localData: { bengaluru: '/bengaluru/data/*.json' },
      upstreams: [
        { name: 'Nagrik public API', source: 'https://nagrik.in.net/api/v1', usedFor: 'Live city metadata, summaries, projects, people, wards, and ward boundaries for Delhi, Gurugram, Mumbai, Pune' },
        { name: 'OpenFreeMap', source: 'https://tiles.openfreemap.org/styles/', usedFor: 'Map basemap styles and vector tiles' },
        { name: 'OpenStreetMap contributors', source: 'https://www.openstreetmap.org/copyright', usedFor: 'Map data attribution' },
        { name: 'Karnataka Public Procurement Portal', source: 'https://kppp.karnataka.gov.in/#/portal/searchTender', usedFor: 'Official tender source links in Bengaluru work records' }
      ],
      credentialsRequired: false,
      note: 'Bengaluru reads its captured snapshot except ward_id-filtered works, which use the public Nagrik API for current ward-linked records. The other four cities are forwarded to the public Nagrik API. No requests contain secrets.'
    });
  }

  if (!path.startsWith('/api/v1/')) {
    return path.startsWith('/api/') ? notFound(res) : false;
  }

  const subPath = path.slice('/api/v1/'.length);
  const segments = subPath.split('/');
  const [first, second, third] = segments;

  let cityCode = (url.searchParams.get('city') || '').toUpperCase();
  let citySlug = '';
  let section = '';
  let recordId = null;

  // Check if first segment is a city slug
  if (citySlugToCode[first.toLowerCase()]) {
    citySlug = first.toLowerCase();
    cityCode = citySlugToCode[citySlug];
    section = second || 'summary';
    recordId = third || null;
  } else {
    // Upstream standard path: /api/v1/projects, /api/v1/people, /api/v1/wards, etc.
    section = first;
    if (second === 'summary') {
      section = 'summary';
    } else if (second === 'geojson') {
      section = 'geojson';
    } else if (second) {
      recordId = second;
    }
    if (!cityCode) cityCode = 'BLR';
    citySlug = cityCodeToSlug[cityCode] || 'bengaluru';
  }

  // Handle news endpoint
  if (section === 'news' && !recordId) {
    const queryMap = {
      bengaluru: 'Bengaluru civic works city corporation',
      delhi: 'Delhi civic works municipal corporation',
      gurugram: 'Gurugram civic works municipal corporation',
      mumbai: 'Mumbai civic works municipal corporation',
      pune: 'Pune civic works municipal corporation'
    };
    const query = queryMap[citySlug] || `${citySlug} civic works municipal corporation`;
    try {
      const feedUrl = `https://news.google.com/rss/search?q=${encodeURIComponent(query)}&hl=en-IN&gl=IN&ceid=IN:en`;
      const response = await fetch(feedUrl, { headers: { Accept: 'application/rss+xml, application/xml, text/xml' }, signal: AbortSignal.timeout(12000) });
      if (!response.ok) throw new Error(`News source returned ${response.status}`);
      const xml = await response.text();
      const items = [...xml.matchAll(/<item>([\s\S]*?)<\/item>/gi)].slice(0, 8).map(([, item]) => {
        const sourceMatch = item.match(/<source(?:\s[^>]*)?>([\s\S]*?)<\/source>/i);
        return {
          title: xmlField(item, 'title'),
          url: xmlField(item, 'link'),
          published_at: xmlField(item, 'pubDate'),
          publisher: sourceMatch ? decodeXml(sourceMatch[1]) : 'Google News'
        };
      }).filter((item) => item.title && /^https?:\/\//i.test(item.url));
      return json(res, 200, { city: citySlug, provider: 'Google News RSS', query, fetched_at: new Date().toISOString(), items });
    } catch (error) {
      // Fall back to upstream
      try {
        const proxied = await upstreamJson(`${url.pathname}${url.search}`);
        return sendBody(res, 200, proxied.body, proxied.contentType);
      } catch {
        return json(res, 502, { error: { code: 'NEWS_UNAVAILABLE', message: 'The news feed is temporarily unavailable.' } });
      }
    }
  }

  const isBengaluru = cityCode === 'BLR';

  // Bengaluru local data handling
  if (isBengaluru) {
    if (section === 'summary' && !recordId) {
      return json(res, 200, await dataset('summary.json'));
    }
    if ((section === 'wards' && recordId === 'geojson') || (section === 'geojson' && first === 'wards')) {
      const body = await readFile(resolve(dataDir, 'wards.geojson'), 'utf8');
      return sendBody(res, 200, body, 'application/geo+json; charset=utf-8');
    }
    if ((section === 'works' || section === 'projects') && !recordId) {
      const wardId = url.searchParams.get('ward_id');
      const issue = url.searchParams.get('issue');
      if (wardId || issue) {
        // Forward to live API for ward-specific or issue-specific queries
        const params = new URLSearchParams(url.searchParams);
        params.set('city', 'BLR');
        const live = await upstreamJson(`/api/v1/projects?${params}`);
        return sendBody(res, 200, live.body, live.contentType);
      }
      const data = await dataset('works.json');
      const q = norm(url.searchParams.get('q'));
      const agency = norm(url.searchParams.get('agency'));
      const statuses = [...url.searchParams.getAll('status'), ...url.searchParams.getAll('statuses')].map(norm).filter(Boolean);
      const sort = url.searchParams.get('sort') || 'newest';
      const page = int(url.searchParams.get('page'), 1, 1, 100000);
      const limit = int(url.searchParams.get('limit') || url.searchParams.get('per_page'), 20, 1, 100);
      let rows = data.items.filter((x) =>
        includes(x, q, ['title', 'title_local', 'executing_agency', 'executing_agency_local', 'tender_ref', 'contractor_name', 'location_text', 'ward_name', 'scope_of_work']) &&
        (!agency || norm(x.executing_agency) === agency || norm(x.inviting_authority) === agency) &&
        (!statuses.length || statuses.some((s) => norm(x.status) === s || norm(x.status_normalized) === s))
      );
      if (sort === 'oldest') rows.sort((a, b) => String(a.tender_date || '').localeCompare(String(b.tender_date || '')));
      else if (sort === 'value') rows.sort((a, b) => Number(b.budget ?? b.awarded_budget ?? 0) - Number(a.budget ?? a.awarded_budget ?? 0));
      else if (sort === 'closing') rows.sort((a, b) => String(a.bid_close_date || '9999').localeCompare(String(b.bid_close_date || '9999')));
      else rows.sort((a, b) => String(b.tender_date || '').localeCompare(String(a.tender_date || '')));
      const total = rows.length;
      return json(res, 200, {
        items: rows.slice((page - 1) * limit, page * limit),
        total,
        page,
        limit,
        per_page: limit,
        pages: Math.ceil(total / limit),
        snapshot_date: data.snapshot_date,
        source: data.source
      });
    }
    if (section === 'people' && !recordId) {
      const data = await dataset('people.json');
      const q = norm(url.searchParams.get('q'));
      const agency = norm(url.searchParams.get('agency'));
      let rows = data.officials.filter((x) =>
        includes(x, q, ['name', 'name_local', 'department', 'department_local', 'agency', 'address', 'email', 'phone']) &&
        (!agency || norm(x.agency) === agency || norm(x.agency_name) === agency)
      );
      const total = rows.length;
      if (url.searchParams.get('all') === '1' && !q && !agency) {
        return json(res, 200, {
          city: 'BLR',
          officials: rows,
          agencies: data.agencies,
          councillors: data.councillors,
          helplines: data.helplines,
          snapshot_date: data.snapshot_date,
          source: data.source
        });
      }
      const page = int(url.searchParams.get('page'), 1, 1, 100000);
      const limit = int(url.searchParams.get('limit') || url.searchParams.get('per_page'), 50, 1, 100);
      return json(res, 200, {
        city: 'BLR',
        officials: rows.slice((page - 1) * limit, page * limit),
        items: rows.slice((page - 1) * limit, page * limit),
        total,
        page,
        limit,
        per_page: limit,
        agencies: data.agencies,
        snapshot_date: data.snapshot_date,
        source: data.source
      });
    }
    if (section === 'wards' && !recordId) {
      const data = await dataset('wards.json');
      const q = norm(url.searchParams.get('q'));
      const items = data.items.filter((x) => includes(x, q, ['name', 'name_local', 'corporation', 'corporation_local', 'ward_number']));
      return json(res, 200, { items, total: items.length, snapshot_date: data.snapshot_date, source: data.source });
    }
    if (recordId) {
      const name = (section === 'works' || section === 'projects') ? 'works.json' : section === 'people' ? 'people.json' : section === 'wards' ? 'wards.json' : null;
      if (name) {
        const data = await dataset(name);
        const rows = section === 'people' ? data.officials : data.items;
        const item = rows.find((x) => Number(x.id ?? x.ward_id ?? x.project_id) === Number(recordId) || String(x.ward_number) === recordId || norm(x.name) === norm(decodeURIComponent(recordId)));
        if (item) {
          return json(res, 200, { item, snapshot_date: data.snapshot_date, source: data.source });
        }
      }
    }
  }

  // Upstream proxying for other cities or unhandled paths
  let upstreamEndpoint = url.pathname;
  // If the path was like /api/v1/bengaluru/summary, convert to standard upstream path /api/v1/projects/summary?city=BLR
  if (citySlugToCode[first.toLowerCase()]) {
    if (section === 'summary') upstreamEndpoint = '/api/v1/projects/summary';
    else if (section === 'works') upstreamEndpoint = recordId ? `/api/v1/projects/${recordId}` : '/api/v1/projects';
    else if (section === 'people') upstreamEndpoint = recordId ? `/api/v1/people/${recordId}` : '/api/v1/people';
    else if (section === 'wards') upstreamEndpoint = recordId === 'geojson' ? '/api/v1/wards/geojson' : '/api/v1/wards';
  }

  const params = new URLSearchParams(url.searchParams);
  if (!params.has('city') && cityCode) params.set('city', cityCode);
  if (params.has('limit') && !params.has('per_page')) {
    params.set('per_page', params.get('limit'));
    params.delete('limit');
  }

  try {
    const proxied = await upstreamJson(`${upstreamEndpoint}?${params}`);
    return sendBody(res, 200, proxied.body, proxied.contentType);
  } catch (error) {
    return json(res, error.status || 502, {
      error: { code: 'UPSTREAM_ERROR', message: error.message }
    });
  }
}

async function resolveStaticFile(pathname) {
  let cleanPath = pathname;
  if (cleanPath === '/outputs/nagrik-clone') cleanPath = '/';
  else if (cleanPath.startsWith('/outputs/nagrik-clone/')) cleanPath = cleanPath.slice('/outputs/nagrik-clone'.length);

  // Strip trailing slashes except for root
  if (cleanPath.length > 1 && cleanPath.endsWith('/')) {
    cleanPath = cleanPath.slice(0, -1);
  }

  const candidates = [];

  if (cleanPath === '/') {
    candidates.push(resolve(root, 'index.html'));
  } else {
    // 1. Direct file
    candidates.push(resolve(root, '.' + cleanPath));
    // 2. Directory index.html
    candidates.push(resolve(root, '.' + cleanPath, 'index.html'));
    // 3. .html file
    candidates.push(resolve(root, '.' + cleanPath + '.html'));

    // 4. Fallback for /fonts/... -> check in assets/
    if (cleanPath.startsWith('/fonts/')) {
      candidates.push(resolve(root, 'assets', cleanPath.slice('/fonts/'.length)));
    }
    // 5. Fallback for /landmarks/... -> check in assets/landmarks/
    if (cleanPath.startsWith('/landmarks/')) {
      candidates.push(resolve(root, 'assets', 'landmarks', cleanPath.slice('/landmarks/'.length)));
    }
    // 6. Fallback for root assets -> check in assets/
    const rootBasename = cleanPath.slice(1);
    if (!rootBasename.includes('/')) {
      candidates.push(resolve(root, 'assets', rootBasename));
    }
  }

  for (const candidate of candidates) {
    if (candidate !== root && !candidate.startsWith(root + sep)) continue;
    const info = await stat(candidate).catch(() => null);
    if (info?.isFile()) {
      return { file: candidate, size: info.size };
    }
  }

  return null;
}

const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      return json(res, 405, { error: { code: 'METHOD_NOT_ALLOWED', message: 'This read-only server accepts GET/HEAD only.' } });
    }

    // React router loader data requests (.data)
    if (url.pathname.endsWith('.data')) {
      try {
        const proxied = await fetch(`${upstream}${url.pathname}${url.search}`, {
          headers: { Accept: req.headers.accept || '*/*' },
          signal: AbortSignal.timeout(10000)
        });
        const text = await proxied.text();
        return sendBody(res, proxied.status, text, proxied.headers.get('content-type') || 'text/x-script; charset=utf-8');
      } catch {
        return notFound(res);
      }
    }

    // React router lazy route discovery manifest
    if (url.pathname === '/__manifest') {
      try {
        const proxied = await fetch(`${upstream}${url.pathname}${url.search}`, {
          headers: { Accept: 'application/json' },
          signal: AbortSignal.timeout(5000)
        });
        if (proxied.ok) {
          const text = await proxied.text();
          return sendBody(res, 200, text, 'application/json; charset=utf-8');
        }
      } catch {}
      // Fallback to local manifest-routes.json
      try {
        const fallback = await readFile(resolve(root, 'manifest-routes.json'), 'utf8');
        return sendBody(res, 200, fallback, 'application/json; charset=utf-8');
      } catch {
        return json(res, 200, {});
      }
    }

    // Cloudflare stubs
    if (url.pathname.startsWith('/cdn-cgi/challenge-platform') || url.pathname.startsWith('/cdn-cgi/rum')) {
      res.writeHead(200, { 'Content-Type': 'application/javascript; charset=utf-8' });
      return res.end('/* cloudflare challenge stub */');
    }

    const handled = await api(url, res);
    if (handled !== false) return;

    let pathname;
    try { pathname = decodeURIComponent(url.pathname); } catch { return notFound(res); }

    const resolved = await resolveStaticFile(pathname);
    if (!resolved) {
      return notFound(res);
    }

    const { file, size } = resolved;
    const type = mime[extname(file).toLowerCase()] || 'application/octet-stream';
    const isHtml = type.startsWith('text/html');

    res.writeHead(200, {
      'Content-Type': type,
      'Content-Length': size,
      'X-Content-Type-Options': 'nosniff',
      'Cache-Control': isHtml ? 'no-cache' : 'public, max-age=3600'
    });

    if (req.method === 'HEAD') return res.end();

    const stream = (await import('node:fs')).createReadStream(file);
    stream.on('error', () => {
      if (!res.headersSent) res.writeHead(500);
      res.end();
    });
    stream.pipe(res);
  } catch (error) {
    console.error(error);
    json(res, 500, { error: { code: 'INTERNAL_ERROR', message: 'The local data service failed to complete the request.' } });
  }
});

server.listen(port, '127.0.0.1', () => console.log(`Nagrik local app and API listening at http://127.0.0.1:${port}`));
