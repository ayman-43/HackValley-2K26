"""Road-damage reports: printable complaint per defect, officer digest per ward, CSV export.
Plain f-strings (no template engine). Every optional field may be missing."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, Response

from . import db
from .review import media_url

router = APIRouter(prefix="/api")

OVERDUE_DAYS = 15
CLOSED_WORKFLOWS = ("repaired", "closed", "rejected")
RISK_LABELS = ["Critical", "High", "Medium", "Low"]
RISK_COLORS = {"Critical": "#7f1d1d", "High": "#c2410c", "Medium": "#a16207", "Low": "#15803d"}


def e(v: Any, default: str = "—") -> str:
    """HTML-escape, with a dash for missing values."""
    if v is None or v == "":
        return default
    return escape(str(v))


def nice_class(c: Optional[str]) -> str:
    return (c or "unknown").replace("_", " ").title()


def parse_ts(s: Any) -> Optional[datetime]:
    if not s:
        return None
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def fmt_date(s: Any) -> str:
    t = parse_ts(s)
    return t.strftime("%d %b %Y, %H:%M UTC") if t else e(s)


def _num(v: Any, nd: int = 0) -> str:
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return "—"


BASE_CSS = """
*{box-sizing:border-box} body{font-family:Segoe UI,Roboto,Arial,sans-serif;color:#111;background:#f3f4f6;margin:0;
line-height:1.45;font-size:14px} .page{max-width:210mm;margin:16px auto;background:#fff;padding:18mm 16mm;
box-shadow:0 1px 4px rgba(0,0,0,.15)} h1{font-size:22px;margin:0 0 4px} h2{font-size:15px;margin:18px 0 6px;
border-bottom:2px solid #111;padding-bottom:2px;text-transform:uppercase;letter-spacing:.04em}
.muted{color:#555} .small{font-size:12px} table{width:100%;border-collapse:collapse;margin:4px 0}
th,td{border:1px solid #d1d5db;padding:5px 7px;text-align:left;vertical-align:top} th{background:#f3f4f6;width:32%}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;color:#fff;font-weight:600;font-size:12px}
.head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;border-bottom:3px solid #111;padding-bottom:8px}
.code{font-family:Consolas,monospace;font-size:20px;font-weight:700} .note{background:#fff7ed;border-left:4px solid #c2410c;padding:6px 10px}
.disc{margin-top:18px;padding:8px 10px;background:#f9fafb;border:1px dashed #9ca3af;font-size:12px}
.tl{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px}
.tl figure{margin:0;border:1px solid #d1d5db;padding:4px;page-break-inside:avoid} .tl img{width:100%;height:110px;object-fit:cover;display:block}
.tl figcaption{font-size:11px} .noimg{height:110px;display:flex;align-items:center;justify-content:center;background:#e5e7eb;color:#6b7280;font-size:11px}
a{color:#1d4ed8} .toolbar{max-width:210mm;margin:12px auto 0;text-align:right}
.toolbar button{padding:6px 14px;font-size:14px;cursor:pointer}
@page{size:A4;margin:12mm} @media print{body{background:#fff}.page{box-shadow:none;margin:0;padding:0;max-width:none}
.toolbar{display:none} a{color:#111;text-decoration:none} h2{page-break-after:avoid}}
"""


def _page(title: str, body: str) -> str:
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
            f"content='width=device-width,initial-scale=1'><title>{escape(title)}</title><style>{BASE_CSS}</style>"
            f"</head><body><div class='toolbar'><button onclick='window.print()'>Print / Save as PDF</button></div>"
            f"<div class='page'>{body}</div></body></html>")


def _badge(label: Optional[str]) -> str:
    if not label:
        return "—"
    return f"<span class='badge' style='background:{RISK_COLORS.get(label, '#374151')}'>{e(label)}</span>"


def _tenders(nag: dict) -> list[dict]:
    for k in ("works", "tenders", "ward_works"):
        v = nag.get(k)
        if isinstance(v, list):
            return [w for w in v if isinstance(w, dict)]
    return []


def _verification(obs: list[dict]) -> str:
    rev = [o for o in obs if o.get("review_status") in ("confirmed", "corrected") and o.get("reviewed_at")]
    if not rev:
        return "AI-assisted report, pending verification"
    last = max(rev, key=lambda o: o["reviewed_at"])
    t = parse_ts(last["reviewed_at"])
    return f"AI-assisted report, verified by {last.get('reviewer') or 'admin'} on {t.strftime('%d %b %Y') if t else last['reviewed_at']}"


def _load_defect(defect_id: int) -> tuple[dict, list[dict]]:
    with db.connect() as c:
        d = db.row(c.execute("SELECT * FROM defects WHERE id=?", (defect_id,)).fetchone())
        if d is None:
            raise HTTPException(404, "defect not found")
        obs = [db.row(r) for r in c.execute(
            "SELECT * FROM observations WHERE defect_id=? ORDER BY observed_at, id", (defect_id,)).fetchall()]
    return d, obs


def render_complaint(d: dict, obs: list[dict]) -> str:
    nag = d.get("nagrik") if isinstance(d.get("nagrik"), dict) else {}
    lat, lon = d.get("lat"), d.get("lon")
    if lat is not None and lon is not None:
        osm = f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=19/{lat}/{lon}"
        loc = f"{float(lat):.6f}, {float(lon):.6f} &nbsp; <a href='{escape(osm)}'>Open in OpenStreetMap</a>"
    else:
        loc = "GPS location not available"
    road = ", ".join(x for x in [d.get("road_name"), d.get("road_type")] if x)
    ward = d.get("ward_name") or nag.get("ward_name")
    corp = d.get("corporation") or nag.get("corporation")
    city = d.get("city") or nag.get("city")

    # risk factors
    rf = d.get("risk_factors") if isinstance(d.get("risk_factors"), list) else []
    rf_rows = "".join(
        f"<tr><td>{e(f.get('factor'))}</td><td>{e(f.get('detail'), '')}</td><td style='text-align:right'>+{_num(f.get('points'))}</td></tr>"
        for f in rf if isinstance(f, dict))
    rf_html = (f"<table><tr><th>Factor</th><th>Detail</th><th style='width:60px'>Points</th></tr>{rf_rows}</table>"
               if rf_rows else "<p class='muted'>Risk factor breakdown not available.</p>")

    fc = d.get("forecast")
    fc_text = fc.get("text") if isinstance(fc, dict) else (fc if isinstance(fc, str) else None)

    # officials
    offs = [o for o in (nag.get("officials") or []) if isinstance(o, dict)]
    off_rows = "".join(f"<tr><td>{e(o.get('department'))}</td><td>{e(o.get('name'))}</td><td>{e(o.get('phone'))}</td></tr>"
                       for o in offs)
    off_html = (f"<table><tr><th>Department</th><th>Officer</th><th>Phone</th></tr>{off_rows}</table>"
                if off_rows else "<p class='muted'>Responsible officer not resolved for this location.</p>")

    tenders = _tenders(nag)
    tender_html = ""
    if tenders:
        items = "".join(
            f"<li>A road/pothole works tender exists for this ward: <b>{e(t.get('title'))}</b> "
            f"({e(t.get('tender_ref'), 'ref n/a')}, {e(t.get('tender_date'), 'date n/a')})"
            + (f" — status: {e(t.get('status'))}" if t.get("status") else "")
            + (f" — contractor: {e(t.get('contractor_name'))}" if t.get("contractor_name") else "")
            + "</li>" for t in tenders[:5])
        tender_html = (f"<h2>Existing works in this ward</h2><div class='note'><ul style='margin:0;padding-left:18px'>{items}</ul>"
                       f"<div class='small muted'>Listed for reference only; it is not confirmed that these works cover this specific defect.</div></div>")

    # photo timeline
    figs = []
    for o in obs:
        url = media_url(o.get("crop_path")) or media_url(o.get("frame_path"))
        if o.get("absent"):
            img = "<div class='noimg'>Not found on re-inspection</div>"
        elif url:
            img = f"<img src='{escape(url)}' alt='observation {o['id']}'>"
        else:
            img = "<div class='noimg'>No photo</div>"
        cap = (f"{fmt_date(o.get('observed_at'))}<br>Severity {e(o.get('severity_label'))} "
               f"({_num(o.get('severity_score'))}) · conf {_num(o.get('confidence'), 2)}<br>Review: {e(o.get('review_status'))}")
        figs.append(f"<figure>{img}<figcaption>{cap}</figcaption></figure>")
    if d.get("repair_photo"):
        figs.append(f"<figure><img src='{escape(media_url(d['repair_photo']) or '')}' alt='repair'>"
                    f"<figcaption>After repair · {fmt_date(d.get('repaired_at'))}</figcaption></figure>")
    tl_html = f"<div class='tl'>{''.join(figs)}</div>" if figs else "<p class='muted'>No photos recorded.</p>"

    bad = "<tr><th>Repair quality</th><td><b>Re-appeared within 90 days of repair</b></td></tr>" if d.get("bad_repair") else ""
    today = datetime.now(timezone.utc).strftime("%d %b %Y")
    body = f"""
<div class='head'><div><div class='muted small'>Nagrik Roads · Road damage complaint</div>
<h1>{e(nice_class(d.get('damage_class')))} at {e(road or ward or 'reported location')}</h1>
<div class='small muted'>To: {e(corp, 'Concerned road authority')}{', ' + e(ward) + ' ward' if ward else ''} · Date: {today}</div></div>
<div style='text-align:right'><div class='code'>{e(d.get('code'))}</div>{_badge(d.get('risk_label'))}</div></div>

<h2>Defect</h2><table>
<tr><th>Defect code</th><td>{e(d.get('code'))}</td></tr>
<tr><th>Damage type</th><td>{e(nice_class(d.get('damage_class')))}</td></tr>
<tr><th>Severity</th><td>{e(d.get('severity_label'))} ({_num(d.get('severity_score'))}/100)</td></tr>
<tr><th>Risk score</th><td>{_num(d.get('risk_score'))}/100 &nbsp; {_badge(d.get('risk_label'))}</td></tr>
<tr><th>Trend</th><td>{e((d.get('trend') or '').replace('_', ' '))}</td></tr>
<tr><th>Times reported</th><td>{e(d.get('report_count'), '1')}</td></tr>
<tr><th>First seen</th><td>{fmt_date(d.get('first_seen'))}</td></tr>
<tr><th>Last seen</th><td>{fmt_date(d.get('last_seen'))}</td></tr>
<tr><th>Workflow status</th><td>{e(d.get('workflow'))}</td></tr>{bad}
</table>

<h2>Location</h2><table>
<tr><th>Coordinates</th><td>{loc}</td></tr>
<tr><th>Road</th><td>{e(road)}{' · speed limit ' + e(d.get('speed_limit')) + ' km/h' if d.get('speed_limit') else ''}</td></tr>
<tr><th>Ward</th><td>{e(ward)}{' (#' + e(d.get('ward_id') or nag.get('ward_id')) + ')' if (d.get('ward_id') or nag.get('ward_id')) else ''}</td></tr>
<tr><th>Corporation / City</th><td>{e(corp)} / {e(city)}</td></tr>
</table>

<h2>Why this is urgent (risk factors)</h2>{rf_html}
<h2>Forecast</h2><p>{e(fc_text, 'No forecast available yet (needs at least two observations).')}</p>
<h2>Responsible officials</h2>{off_html}
{tender_html}
<h2>Photo timeline</h2>{tl_html}
<h2>Request</h2><p>We request inspection and repair of the above defect. Please quote defect code
<b>{e(d.get('code'))}</b> in any correspondence so that repeat reports are linked to this complaint.</p>
<div class='disc'>{escape(_verification(obs))}. Detection by a computer-vision model; severity and risk are estimates.</div>
"""
    return _page(f"Complaint {d.get('code') or d.get('id')}", body)


@router.get("/defects/{defect_id}/complaint", response_class=HTMLResponse)
def complaint(defect_id: int) -> HTMLResponse:
    d, obs = _load_defect(defect_id)
    return HTMLResponse(render_complaint(d, obs))


# ---------------------------------------------------------------- digest
def _is_open(d: dict) -> bool:
    return (d.get("workflow") or "reported") not in CLOSED_WORKFLOWS


def render_digest(defects: list[dict], period: str, ward: Optional[str], now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=7 if period == "weekly" else 1)
    overdue_cut = now - timedelta(days=OVERDUE_DAYS)
    groups: dict[str, list[dict]] = {}
    for d in defects:
        groups.setdefault(d.get("ward_name") or "Unassigned ward", []).append(d)

    sections = []
    tot = {"open": 0, "new": 0, "worse": 0, "overdue": 0}
    for wname in sorted(groups, key=lambda w: -max((x.get("risk_score") or 0) for x in groups[w])):
        ds = groups[wname]
        op = [d for d in ds if _is_open(d)]
        new = [d for d in ds if (parse_ts(d.get("first_seen")) or datetime.min.replace(tzinfo=timezone.utc)) >= start]
        worse = [d for d in op if d.get("trend") == "worsening"]
        overdue = [d for d in op if (t := parse_ts(d.get("first_seen"))) and t < overdue_cut]
        repaired = [d for d in ds if d.get("workflow") in ("repaired", "closed")
                    and (t := parse_ts(d.get("repaired_at"))) and t >= start]
        for k, v in (("open", op), ("new", new), ("worse", worse), ("overdue", overdue)):
            tot[k] += len(v)
        counts = {lbl: sum(1 for d in op if d.get("risk_label") == lbl) for lbl in RISK_LABELS}
        counts_html = " ".join(f"{_badge(l)} {n}" for l, n in counts.items())
        top = sorted(op, key=lambda d: -(d.get("risk_score") or 0))[:5]
        rows = "".join(
            f"<tr><td><a href='/api/defects/{d['id']}/complaint'>{e(d.get('code'))}</a></td>"
            f"<td>{e(nice_class(d.get('damage_class')))}</td><td>{_num(d.get('risk_score'))} {_badge(d.get('risk_label'))}</td>"
            f"<td>{e(d.get('road_name'))}</td><td>{e((d.get('trend') or '').replace('_', ' '))}</td>"
            f"<td>{e(d.get('workflow'))}</td><td><a href='/roads/dashboard#/defect/{d['id']}'>details</a></td></tr>" for d in top)
        top_html = (f"<table><tr><th style='width:auto'>Code</th><th style='width:auto'>Type</th><th style='width:auto'>Risk</th>"
                    f"<th style='width:auto'>Road</th><th style='width:auto'>Trend</th><th style='width:auto'>Status</th><th style='width:auto'></th></tr>{rows}</table>"
                    if rows else "<p class='muted'>No open defects.</p>")
        officers = ""
        nag = next((d.get("nagrik") for d in ds if isinstance(d.get("nagrik"), dict)), None)
        if nag and nag.get("officials"):
            officers = "<div class='small muted'>Officials: " + "; ".join(
                f"{e(o.get('name'))} ({e(o.get('department'))})" for o in nag["officials"] if isinstance(o, dict)) + "</div>"
        sections.append(f"""
<h2>{e(wname)}</h2>{officers}
<table><tr><th>Open defects by risk</th><td>{counts_html} &nbsp; (total open {len(op)})</td></tr>
<tr><th>New since {start.strftime('%d %b %H:%M')}</th><td>{len(new)}</td></tr>
<tr><th>Worsening</th><td>{len(worse)}</td></tr>
<tr><th>Overdue (&gt;{OVERDUE_DAYS} days open)</th><td>{len(overdue)}</td></tr>
<tr><th>Repaired this period</th><td>{len(repaired)}</td></tr></table>
<div class='small' style='margin-top:6px'><b>Top {len(top)} to fix first</b></div>{top_html}""")

    scope = f"ward {ward}" if ward else "all wards"
    body = f"""
<div class='head'><div><div class='muted small'>Nagrik Roads · Officer digest (one summary instead of individual alerts)</div>
<h1>{'Weekly' if period == 'weekly' else 'Daily'} road-damage digest — {e(scope)}</h1>
<div class='small muted'>Period {start.strftime('%d %b %Y %H:%M')} – {now.strftime('%d %b %Y %H:%M')} UTC</div></div></div>
<p><b>{tot['open']}</b> open defects · <b>{tot['new']}</b> new · <b>{tot['worse']}</b> worsening · <b>{tot['overdue']}</b> overdue.</p>
{''.join(sections) or "<p class='muted'>No defects recorded for this scope.</p>"}
<div class='disc'>Preview of the digest email; nothing has been sent. AI-assisted data — detections pending verification are included.</div>
"""
    return _page("Nagrik Roads digest", body)


@router.get("/digest", response_class=HTMLResponse)
def digest(ward: Optional[str] = None, period: str = "daily") -> HTMLResponse:
    period = "weekly" if period == "weekly" else "daily"
    q, args = "SELECT * FROM defects", []
    if ward:
        q += " WHERE ward_name = ? OR CAST(ward_id AS TEXT) = ?"
        args = [ward, ward]
    with db.connect() as c:
        ds = [db.row(r) for r in c.execute(q, args).fetchall()]
    return HTMLResponse(render_digest(ds, period, ward))


# ---------------------------------------------------------------- CSV
CSV_FIELDS = ["id", "code", "damage_class", "lat", "lon", "road_name", "road_type", "ward_name", "ward_id",
              "corporation", "city", "severity_score", "severity_label", "risk_score", "risk_label", "trend",
              "report_count", "workflow", "status", "first_seen", "last_seen", "repaired_at", "bad_repair",
              "forecast", "verified"]


@router.get("/report/summary.csv")
def summary_csv() -> Response:
    with db.connect() as c:
        ds = [db.row(r) for r in c.execute("SELECT * FROM defects ORDER BY COALESCE(risk_score,0) DESC, id").fetchall()]
        verified = {r[0] for r in c.execute(
            "SELECT DISTINCT defect_id FROM observations WHERE review_status IN ('confirmed','corrected')").fetchall()}
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
    w.writeheader()
    for d in ds:
        fc = d.get("forecast")
        d["forecast"] = fc.get("text") if isinstance(fc, dict) else fc
        d["verified"] = "yes" if d["id"] in verified else "no"
        w.writerow(d)
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="roadsense_summary.csv"'})
