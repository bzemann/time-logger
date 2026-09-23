"""Weekly and monthly HTML reports for WorkTime Logger.

Self-contained, no external resources: everything (CSS, SVG charts) is
inline. All numbers come from :mod:`worktime.stats`; this module only
formats and lays them out (mirroring ``web/app.js``'s formatting helpers)
and draws the SVG charts.
"""

from __future__ import annotations

import calendar
import html
import math
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Sequence

from worktime import stats, store
from worktime.store import Entry

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

# Plot geometry shared by both SVG charts.
SVG_W = 720
SVG_H = 240
X0 = 44
X1 = 708
Y0 = 12
Y1 = 212

_STATUS_RE = re.compile(
    r'<meta name="worktime:status" content="(final|in-progress)">'
)


# ---------------------------------------------------------------------
# Period
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class Period:
    kind: str  # "week" | "month"
    start: date
    end: date
    title: str
    stem: str


def period_for(kind: str, ref_date: date) -> Period:
    if kind == "week":
        start = ref_date - timedelta(days=ref_date.weekday())
        end = start + timedelta(days=6)
        iso_year, iso_week, _ = start.isocalendar()
        title = f"Week {iso_week}, {iso_year}"
        stem = f"week-{iso_year}-W{iso_week:02d}"
    elif kind == "month":
        start = ref_date.replace(day=1)
        _, last_day = calendar.monthrange(ref_date.year, ref_date.month)
        end = ref_date.replace(day=last_day)
        title = f"{MONTH_NAMES[ref_date.month - 1]} {ref_date.year}"
        stem = f"month-{ref_date.year}-{ref_date.month:02d}"
    else:
        raise ValueError(f"unknown period kind: {kind!r}")

    return Period(kind=kind, start=start, end=end, title=title, stem=stem)


def previous_period(kind: str, today: date) -> Period:
    current_start = period_for(kind, today).start
    return period_for(kind, current_start - timedelta(days=1))


def report_path(cfg, period: Period) -> Path:
    return Path(cfg.reports_dir) / f"{period.stem}.html"


def read_status(path: Path) -> str | None:
    path = Path(path)
    if not path.exists():
        return None
    try:
        with path.open("rb") as f:
            chunk = f.read(4096)
    except OSError:
        return None
    text = chunk.decode("utf-8", errors="replace")
    m = _STATUS_RE.search(text)
    return m.group(1) if m else None


def due_reports(cfg, now: datetime, entries: Sequence[Entry] | None = None) -> list[Period]:
    if entries is None:
        entries = store.read_entries(cfg.data_file)

    ts = stats.tracking_start(entries)
    if ts is None:
        return []

    result: list[Period] = []
    for kind in ("week", "month"):
        p = previous_period(kind, now.date())
        if p.end < ts:
            continue
        if read_status(report_path(cfg, p)) != "final":
            result.append(p)
    return result


# ---------------------------------------------------------------------
# Report data
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class DayRow:
    date: date
    sessions: int
    worked: timedelta
    target: timedelta
    future: bool


@dataclass(frozen=True)
class Report:
    period: Period
    generated: datetime
    final: bool
    summary: "stats.PeriodSummary"
    days: list[DayRow]
    cumulative: list[tuple[date, timedelta, timedelta]]
    running: Entry | None
    daily_target: timedelta


def build_report(
    entries: Sequence[Entry], now: datetime, cfg, period: Period
) -> Report:
    target = stats.Target.from_config(cfg)
    summary = stats.summarize(entries, now, period.start, period.end, target)

    today = now.date()
    final = today > period.end

    worked_map = stats.daily_totals(entries, now, period.start, period.end)
    target_map = stats.daily_targets(entries, now, period.start, period.end, target)

    days: list[DayRow] = []
    d = period.start
    while d <= period.end:
        sessions = sum(1 for e in entries if e.date == d)
        days.append(
            DayRow(
                date=d,
                sessions=sessions,
                worked=worked_map[d],
                target=target_map[d],
                future=d > today,
            )
        )
        d += timedelta(days=1)

    cumulative: list[tuple[date, timedelta, timedelta]] = []
    limit = min(period.end, today)
    cum_worked = timedelta(0)
    cum_target = timedelta(0)
    d = period.start
    while d <= limit:
        cum_worked += worked_map[d]
        cum_target += target_map[d]
        cumulative.append((d, cum_worked, cum_target))
        d += timedelta(days=1)

    run = store.running_entry(entries)
    running = run if (run is not None and period.start <= run.date <= period.end) else None

    daily_target = timedelta(minutes=cfg.daily_target_min)

    return Report(
        period=period,
        generated=now,
        final=final,
        summary=summary,
        days=days,
        cumulative=cumulative,
        running=running,
        daily_target=daily_target,
    )


def write_report(cfg, period: Period, now: datetime) -> Path:
    entries = store.read_entries(cfg.data_file)
    report = build_report(entries, now, cfg, period)
    body = render_html(report)

    reports_dir = Path(cfg.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = report_path(cfg, period)

    fd, tmp_path = tempfile.mkstemp(
        dir=reports_dir, prefix="." + period.stem + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    return path


# ---------------------------------------------------------------------
# Formatting helpers (mirror web/app.js)
# ---------------------------------------------------------------------


def fmt_duration(td: timedelta) -> str:
    total = max(0.0, td.total_seconds())
    m = int(total // 60)
    h = m // 60
    mm = m % 60
    if h > 0:
        return f"{h}h {mm:02d}m"
    return f"{mm}m"


def fmt_balance(td: timedelta) -> str:
    s = td.total_seconds()
    m = int(abs(s) // 60)
    if m == 0:
        return "±0:00"
    sign = "+" if s > 0 else "−"
    hh = m // 60
    mm = m % 60
    return f"{sign}{hh}:{mm:02d}"


def _balance_class(td: timedelta) -> str:
    m = int(abs(td.total_seconds()) // 60)
    if m == 0:
        return "zero"
    return "pos" if td.total_seconds() > 0 else "neg"


def fmt_date(d: date) -> str:
    return f"{d.day:02d}.{d.month:02d}.{d.year}"


def fmt_date_long(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()]} {fmt_date(d)}"


def fmt_day_short(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()]} {d.day:02d}.{d.month:02d}."


def fmt_hm(td: timedelta) -> str:
    total = int(td.total_seconds())
    h = total // 3600
    m = (total % 3600) // 60
    return f"{h}:{m:02d}"


# ---------------------------------------------------------------------
# SVG charts
# ---------------------------------------------------------------------


def nice_scale(max_hours: float) -> tuple[float, float]:
    steps = [0.5, 1, 2, 4, 5, 10, 20, 25, 50, 100, 200, 500]
    value = max(max_hours, 1)
    for s in steps:
        k = math.ceil(value / s)
        if k <= 5:
            return (k * s, s)
    s = steps[-1]
    k = math.ceil(value / s)
    return (k * s, s)


def _axes(ymax: float, step: float) -> str:
    parts = []
    k = round(ymax / step)
    for i in range(k + 1):
        v = i * step
        y = Y1 - (v / ymax) * (Y1 - Y0)
        color = "#c3c2b7" if i == 0 else "#e1e0d9"
        parts.append(
            f'<line x1="{X0}" y1="{y:.1f}" x2="{X1}" y2="{y:.1f}" '
            f'stroke="{color}" stroke-width="1"/>'
        )
        label = html.escape(f"{v:g}h")
        parts.append(
            f'<text x="{X0 - 6:.1f}" y="{y:.1f}" fill="#898781" font-size="11" '
            f'text-anchor="end" dominant-baseline="middle">{label}</text>'
        )
    return "".join(parts)


def _x_labels(days: Sequence[DayRow], kind: str, band: float) -> str:
    parts = []
    for i, day in enumerate(days):
        x = X0 + (i + 0.5) * band
        y = Y1 + 16
        if kind == "week":
            text = html.escape(fmt_day_short(day.date))
            size = 11
        else:
            text = html.escape(str(day.date.day))
            size = 10
        parts.append(
            f'<text x="{x:.1f}" y="{y}" fill="#898781" font-size="{size}" '
            f'text-anchor="middle">{text}</text>'
        )
    return "".join(parts)


def _svg_open(aria_label: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SVG_W} {SVG_H}" '
        f'width="100%" role="img" aria-label="{html.escape(aria_label)}" '
        f'font-family="system-ui">'
    )


def svg_daily_bars(days: Sequence[DayRow], daily_target: timedelta, kind: str) -> str:
    n = len(days)
    band = (X1 - X0) / n if n else 0.0
    w = min(24.0, band * 0.7)

    max_bar_hours = max((d.worked.total_seconds() / 3600 for d in days), default=0.0)
    dt_hours = daily_target.total_seconds() / 3600
    ymax, step = nice_scale(max(max_bar_hours, dt_hours * 1.1))

    parts = [_svg_open("Hours worked per day"), _axes(ymax, step), _x_labels(days, kind, band)]

    for i, day in enumerate(days):
        band_x = X0 + i * band
        hours = day.worked.total_seconds() / 3600
        target_txt = (
            fmt_duration(day.target) if day.target.total_seconds() > 0 else "none"
        )
        title = (
            f"{fmt_date_long(day.date)}: {fmt_duration(day.worked)} worked "
            f"· target {target_txt}"
        )
        parts.append(f"<g><title>{html.escape(title)}</title>")
        parts.append(
            f'<rect x="{band_x:.1f}" y="{Y0}" width="{band:.1f}" '
            f'height="{Y1 - Y0}" fill="transparent"/>'
        )
        h = hours / ymax * (Y1 - Y0)
        if h > 0:
            x = band_x + (band - w) / 2
            y_top = Y1 - h
            r = min(4.0, w / 2, h)
            path = (
                f"M {x:.1f},{Y1:.1f} "
                f"L {x:.1f},{y_top + r:.1f} "
                f"Q {x:.1f},{y_top:.1f} {x + r:.1f},{y_top:.1f} "
                f"L {x + w - r:.1f},{y_top:.1f} "
                f"Q {x + w:.1f},{y_top:.1f} {x + w:.1f},{y_top + r:.1f} "
                f"L {x + w:.1f},{Y1:.1f} Z"
            )
            parts.append(f'<path d="{path}" fill="#2a78d6"/>')
        parts.append("</g>")

    if daily_target.total_seconds() > 0:
        line_y = Y1 - dt_hours / ymax * (Y1 - Y0)
        parts.append(
            f'<line x1="{X0}" y1="{line_y:.1f}" x2="{X1}" y2="{line_y:.1f}" '
            f'stroke="#52514e" stroke-width="1.5" stroke-dasharray="6 4"/>'
        )
        label = html.escape(f"Target {fmt_hm(daily_target)}")
        parts.append(
            f'<text x="{X1 - 4}" y="{line_y - 4:.1f}" font-size="11" '
            f'fill="#52514e" text-anchor="end">{label}</text>'
        )

    parts.append("</svg>")
    return "".join(parts)


def svg_cumulative(
    cumulative: Sequence[tuple[date, timedelta, timedelta]],
    days: Sequence[DayRow],
    kind: str,
) -> str:
    n = len(days)
    band = (X1 - X0) / n if n else 0.0

    all_hours = []
    for _, w, t in cumulative:
        all_hours.append(w.total_seconds() / 3600)
        all_hours.append(t.total_seconds() / 3600)
    ymax, step = nice_scale(max(all_hours, default=0.0))

    parts = [
        _svg_open("Cumulative worked versus target"),
        _axes(ymax, step),
        _x_labels(days, kind, band),
    ]

    def _pt(j: int, hours: float) -> tuple[float, float]:
        x = X0 + (j + 0.5) * band
        y = Y1 - (hours / ymax) * (Y1 - Y0)
        return x, y

    worked_points = [
        _pt(j, w.total_seconds() / 3600) for j, (_, w, _t) in enumerate(cumulative)
    ]
    target_points = [
        _pt(j, t.total_seconds() / 3600) for j, (_, _w, t) in enumerate(cumulative)
    ]

    if worked_points:
        poly_pts = (
            [f"{worked_points[0][0]:.1f},{Y1:.1f}"]
            + [f"{x:.1f},{y:.1f}" for x, y in worked_points]
            + [f"{worked_points[-1][0]:.1f},{Y1:.1f}"]
        )
        parts.append(
            f'<polygon points="{" ".join(poly_pts)}" fill="#2a78d6" '
            f'fill-opacity="0.10"/>'
        )
        parts.append(
            '<polyline points="'
            + " ".join(f"{x:.1f},{y:.1f}" for x, y in worked_points)
            + '" stroke="#2a78d6" stroke-width="2" stroke-linejoin="round" '
              'stroke-linecap="round" fill="none"/>'
        )
        for j, (x, y) in enumerate(worked_points):
            d, w, t = cumulative[j]
            title = (
                f"{fmt_day_short(d)}: worked {fmt_duration(w)} "
                f"· target {fmt_duration(t)} (cumulative)"
            )
            parts.append(
                f"<g><title>{html.escape(title)}</title>"
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="#2a78d6" '
                f'stroke="#ffffff" stroke-width="2"/></g>'
            )

        parts.append(
            '<polyline points="'
            + " ".join(f"{x:.1f},{y:.1f}" for x, y in target_points)
            + '" stroke="#52514e" stroke-width="1.5" stroke-dasharray="6 4" '
              'fill="none"/>'
        )
        for x, y in target_points:
            parts.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#52514e" '
                f'stroke="#ffffff" stroke-width="1.5"/>'
            )

        last_worked_x, last_worked_y = worked_points[-1]
        last_target_x, last_target_y = target_points[-1]
        last_cum_worked = cumulative[-1][1]
        last_cum_target = cumulative[-1][2]

        if last_cum_worked.total_seconds() >= last_cum_target.total_seconds():
            worked_label_y = last_worked_y - 10
            target_label_y = last_target_y + 14
        else:
            worked_label_y = last_worked_y + 14
            target_label_y = last_target_y - 10

        worked_label = html.escape(f"Worked {fmt_duration(last_cum_worked)}")
        target_label = html.escape(f"Target {fmt_duration(last_cum_target)}")
        parts.append(
            f'<text x="{last_worked_x - 8:.1f}" y="{worked_label_y:.1f}" '
            f'font-size="11" fill="#52514e" text-anchor="end">{worked_label}</text>'
        )
        parts.append(
            f'<text x="{last_target_x - 8:.1f}" y="{target_label_y:.1f}" '
            f'font-size="11" fill="#52514e" text-anchor="end">{target_label}</text>'
        )

    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------


_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  background: #ffffff;
  color: #0b0b0b;
}
main { max-width: 820px; margin: 0 auto; padding: 32px; }
.kicker {
  text-transform: uppercase;
  letter-spacing: 0.06em;
  font-size: 11px;
  color: #898781;
}
h1 { margin: 4px 0 4px; font-size: 26px; }
p.meta { margin: 0; color: #52514e; font-size: 13px; }
p.note { margin: 8px 0 0; color: #52514e; font-size: 13px; }
p.empty { color: #52514e; }
p.footer { margin-top: 40px; color: #898781; font-size: 11px; }
.tiles {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  grid-template-rows: repeat(2, auto);
  gap: 12px;
  margin-top: 20px;
}
.tile {
  border: 1px solid #e1e0d9;
  border-radius: 8px;
  padding: 10px 12px;
}
.tile-label {
  text-transform: uppercase;
  letter-spacing: 0.04em;
  font-size: 11px;
  color: #898781;
}
.tile-value { font-size: 24px; font-weight: 600; margin-top: 4px; }
.tile-sub { font-size: 12px; color: #52514e; margin-top: 2px; }
.pos { color: #006300; }
.neg { color: #d03b3b; }
.zero { color: #0b0b0b; }
section { margin-top: 28px; }
h2 { font-size: 14px; font-weight: 600; margin: 0 0 8px; }
.legend { display: flex; gap: 16px; font-size: 12px; color: #52514e; margin-bottom: 4px; }
.legend-item { display: inline-flex; align-items: center; gap: 6px; }
.legend .dot {
  width: 8px; height: 8px; border-radius: 50%; background: #2a78d6; display: inline-block;
}
.legend .dash {
  width: 16px; height: 0; border-top: 2px dashed #52514e; display: inline-block;
}
table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
th, td { padding: 6px 8px; border-bottom: 1px solid #e1e0d9; text-align: right; }
th:first-child, td:first-child { text-align: left; }
th { text-transform: uppercase; font-size: 11px; color: #898781; font-weight: 600; }
tr.future td { color: #898781; }
tr.total td { font-weight: 700; }
@page { size: A4; margin: 14mm; }
@media print {
  main { padding: 0; }
  section, .tiles { break-inside: avoid; }
  * { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
}
""".strip()


def _tile(label: str, value: str, sub: str = "", cls: str = "") -> str:
    value_cls = f" {cls}" if cls else ""
    out = (
        f'<div class="tile"><div class="tile-label">{html.escape(label)}</div>'
        f'<div class="tile-value{value_cls}">{html.escape(value)}</div>'
    )
    if sub:
        out += f'<div class="tile-sub">{html.escape(sub)}</div>'
    out += "</div>"
    return out


def render_html(report: Report) -> str:
    period = report.period
    summary = report.summary
    status = "final" if report.final else "in-progress"

    parts: list[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="en"><head>')
    parts.append('<meta charset="utf-8">')
    parts.append(f'<meta name="worktime:status" content="{status}">')
    parts.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    parts.append(f"<title>WorkTime report · {html.escape(period.title)}</title>")
    parts.append(f"<style>{_CSS}</style>")
    parts.append("</head><body><main>")

    parts.append('<div class="kicker">WorkTime report</div>')
    parts.append(f"<h1>{html.escape(period.title)}</h1>")
    gen = report.generated
    gen_str = f"{gen.day:02d}.{gen.month:02d}.{gen.year} {gen.hour:02d}:{gen.minute:02d}"
    meta_line = f"{fmt_date(period.start)} – {fmt_date(period.end)} · Generated {gen_str}"
    parts.append(f'<p class="meta">{html.escape(meta_line)}</p>')

    if not report.final:
        today = report.generated.date()
        note = f"In progress: through {fmt_date(today)}."
        if report.running is not None:
            elapsed = report.running.elapsed(report.generated)
            note += f" Includes the running session ({fmt_duration(elapsed)} so far)."
        parts.append(f'<p class="note">{html.escape(note)}</p>')

    # ---- tiles -----------------------------------------------------
    balance_cls = _balance_class(summary.balance)
    balance_sub = {"pos": "Over target", "neg": "Under target", "zero": "On target"}[
        balance_cls
    ]

    parts.append('<div class="tiles">')
    parts.append(_tile("Worked", fmt_duration(summary.worked)))
    parts.append(_tile("Target", fmt_duration(summary.target)))
    parts.append(
        _tile("Balance", fmt_balance(summary.balance), balance_sub, balance_cls)
    )
    parts.append(_tile("Sessions", str(summary.sessions)))
    parts.append(_tile("Ø Per day worked", fmt_duration(summary.avg_per_day_worked)))
    parts.append(_tile("Ø Per target day", fmt_duration(summary.avg_per_target_day)))
    parts.append(
        _tile(
            "Days worked",
            str(summary.days_worked),
            f"of {summary.target_days} target days",
        )
    )
    if summary.longest is not None:
        longest = summary.longest
        sub = (
            f"{fmt_date_long(longest.date)} "
            f"{longest.start.hour:02d}:{longest.start.minute:02d}"
            f"–{longest.end.hour:02d}:{longest.end.minute:02d}"
        )
        parts.append(_tile("Longest session", fmt_duration(longest.duration), sub))
    else:
        parts.append(_tile("Longest session", "—"))
    parts.append("</div>")

    # ---- charts / table ---------------------------------------------
    if summary.sessions == 0:
        parts.append('<p class="empty">No sessions in this period.</p>')
    else:
        parts.append("<section>")
        parts.append("<h2>Hours per day</h2>")
        parts.append(svg_daily_bars(report.days, report.daily_target, period.kind))
        parts.append("</section>")

        parts.append("<section>")
        parts.append("<h2>Cumulative worked vs. target</h2>")
        parts.append(
            '<div class="legend">'
            '<span class="legend-item"><span class="dot"></span>Worked</span>'
            '<span class="legend-item"><span class="dash"></span>Target</span>'
            "</div>"
        )
        parts.append(svg_cumulative(report.cumulative, report.days, period.kind))
        parts.append("</section>")

        parts.append("<section>")
        parts.append("<h2>Daily breakdown</h2>")
        parts.append("<table><thead><tr>")
        parts.append(
            "<th>Date</th><th>Sessions</th><th>Worked</th><th>Target</th>"
            "<th>Balance</th>"
        )
        parts.append("</tr></thead><tbody>")
        for day in report.days:
            if day.future:
                parts.append(
                    f'<tr class="future"><td>{html.escape(fmt_date_long(day.date))}</td>'
                    "<td>—</td><td>—</td><td>—</td><td>—</td></tr>"
                )
                continue
            balance = day.worked - day.target
            bcls = _balance_class(balance)
            parts.append(
                f"<tr><td>{html.escape(fmt_date_long(day.date))}</td>"
                f"<td>{day.sessions}</td>"
                f"<td>{html.escape(fmt_duration(day.worked))}</td>"
                f"<td>{html.escape(fmt_duration(day.target))}</td>"
                f'<td class="{bcls}">{html.escape(fmt_balance(balance))}</td></tr>'
            )
        parts.append(
            '<tr class="total"><td>Total</td>'
            f"<td>{summary.sessions}</td>"
            f"<td>{html.escape(fmt_duration(summary.worked))}</td>"
            f"<td>{html.escape(fmt_duration(summary.target))}</td>"
            f'<td class="{_balance_class(summary.balance)}">'
            f"{html.escape(fmt_balance(summary.balance))}</td></tr>"
        )
        parts.append("</tbody></table>")
        parts.append("</section>")

    parts.append('<p class="footer">Generated by WorkTime Logger</p>')
    parts.append("</main></body></html>")

    return "".join(parts)
