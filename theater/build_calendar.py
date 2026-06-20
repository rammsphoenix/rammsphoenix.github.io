#!/usr/bin/env python3
"""Build an .ics calendar of metro-Phoenix youth-theater events and diff it
against the previously committed calendar.

Design goals
------------
* Zero third-party dependencies (Python 3.9+ standard library only) so the
  weekly refresh runs anywhere.
* Deterministic output: re-running with unchanged data produces a byte-identical
  .ics, so `git diff` and the printed semantic diff stay quiet until the
  underlying data actually changes.
* A clear, human/AI-editable data file (``data/events.json``) is the single
  source of truth. The weekly process is: edit the data file, run this script,
  review the printed diff, commit.

Usage
-----
    python3 build_calendar.py                 # build + diff + write .ics
    python3 build_calendar.py --check         # diff only, do not write (CI/preview)
    python3 build_calendar.py --window-months 15
    python3 build_calendar.py --asof 2026-06-10   # pin "today" (testing/repro)

Event model
-----------
Each event has a ``type`` of ``registration``, ``audition`` or ``show`` and a
date (``start``; ``end`` optional for multi-day show runs). Events are emitted
as all-day VEVENTs (VALUE=DATE) because youth-theater listings are date-granular.
Events whose date is unknown (``start`` missing/"TBD"/null) are kept in the data
file as placeholders but skipped from the .ics; the script reports how many were
skipped so they are not silently lost.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DATA = HERE / "data" / "events.json"
DEFAULT_OUT = HERE / "phoenix_youth_theater.ics"

PRODID = "-//rammsphoenix//Metro Phoenix Youth Theater//EN"
UID_DOMAIN = "phoenix-youth-theater"
# Constant DTSTAMP keeps output deterministic (re-runs don't churn the file).
DTSTAMP = "20200101T000000Z"

TYPE_LABEL = {
    "registration": "Registration",
    "audition": "Audition",
    "show": "Show",
}
TYPE_EMOJI = {"registration": "\U0001F4DD", "audition": "\U0001F3AD", "show": "\U0001F3AB"}


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #
def load_data(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    theaters = {t["id"]: t for t in data.get("theaters", [])}
    data["_theaters_by_id"] = theaters
    return data


def parse_date(value):
    """Return a date for a YYYY-MM-DD string, or None if undated/TBD."""
    if not value or str(value).strip().upper() in {"TBD", "NONE", "NULL"}:
        return None
    return dt.date.fromisoformat(str(value).strip())


# --------------------------------------------------------------------------- #
# ICS generation
# --------------------------------------------------------------------------- #
def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def event_uid(ev: dict, start: dt.date) -> str:
    """Stable identity for an occurrence.

    Keyed on theater + title + type + the start *year* so that small date
    shifts within a season surface as a CHANGED event (not remove+add), while
    annually recurring items (e.g. "Summer Camp Registration") in different
    years remain distinct.
    """
    key = "|".join([
        slug(ev.get("theater", "")),
        slug(ev.get("title", "")),
        ev.get("type", ""),
        str(start.year),
    ])
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return f"{digest}@{UID_DOMAIN}"


def fold_line(line: str) -> str:
    """Fold a content line to <=75 octets per RFC 5545 (continuation = space)."""
    out = []
    raw = line.encode("utf-8")
    while len(raw) > 75:
        # find a cut <=75 bytes that doesn't split a multibyte char
        cut = 75
        while cut > 0 and (raw[cut] & 0xC0) == 0x80:
            cut -= 1
        out.append(raw[:cut].decode("utf-8"))
        raw = b" " + raw[cut:]
    out.append(raw.decode("utf-8"))
    return "\r\n".join(out)


def esc(text: str) -> str:
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def fmt_date(d: dt.date) -> str:
    return d.strftime("%Y%m%d")


def build_events(data: dict, asof: dt.date, window_months: int):
    """Return (vevents_dict, skipped_undated, in_window_count).

    vevents_dict maps uid -> dict of fields used for emission and diffing.
    """
    theaters = data["_theaters_by_id"]
    horizon = add_months(asof, window_months)
    vevents = {}
    skipped = []
    for ev in data.get("events", []):
        start = parse_date(ev.get("start"))
        if start is None:
            skipped.append(ev)
            continue
        end = parse_date(ev.get("end")) or start
        # keep events whose run overlaps [asof, horizon]
        if end < asof or start > horizon:
            continue
        t = theaters.get(ev.get("theater"), {})
        tname = t.get("name", ev.get("theater", "Unknown"))
        etype = ev.get("type", "show")
        emoji = TYPE_EMOJI.get(etype, "")
        label = TYPE_LABEL.get(etype, etype.title())
        title = ev.get("title", "").strip()
        summary = f"{emoji} {tname}: {title} ({label})".strip()

        conf = ev.get("confidence", "tentative")
        desc_parts = [
            f"Theater: {tname}",
            f"Type: {label}",
        ]
        if t.get("city"):
            desc_parts.append(f"City: {t['city']}")
        if t.get("address"):
            desc_parts.append(f"Address: {t['address']}")
        if t.get("age_cap"):
            desc_parts.append(f"Cast age cap: {t['age_cap']}")
        if ev.get("end") and parse_date(ev.get("end")):
            desc_parts.append(f"Run: {start.isoformat()} - {end.isoformat()}")
        desc_parts.append(f"Confidence: {conf}")
        if ev.get("notes"):
            desc_parts.append(f"Notes: {ev['notes']}")
        if t.get("website"):
            desc_parts.append(f"Theater site: {t['website']}")
        if ev.get("source"):
            desc_parts.append(f"Source: {ev['source']}")
        description = "\n".join(desc_parts)

        # Prefer a full street address; fall back to venue/city.
        location = t.get("address") or ", ".join(
            p for p in [t.get("venue"), t.get("city"), "AZ"] if p)
        lat, lon = t.get("lat"), t.get("lon")

        uid = event_uid(ev, start)
        # If two source rows collide on uid, keep the earlier/lower start.
        if uid in vevents and vevents[uid]["start"] <= start:
            continue
        vevents[uid] = {
            "uid": uid,
            "summary": summary,
            "start": start,
            # DTEND for all-day events is exclusive -> day after last day.
            "end_exclusive": end + dt.timedelta(days=1),
            "location": location,
            "geo": (lat, lon) if lat is not None and lon is not None else None,
            "description": description,
            "url": ev.get("source") or t.get("website") or "",
            "confidence": conf,
        }
    return vevents, skipped


def render_ics(data: dict, vevents: dict, asof: dt.date) -> str:
    meta = data.get("meta", {})
    calname = meta.get("title", "Metro Phoenix Youth Theater")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{esc(calname)}",
        "X-WR-TIMEZONE:America/Phoenix",
        f"X-WR-CALDESC:{esc(meta.get('description', ''))}",
    ]
    for uid in sorted(vevents, key=lambda u: (vevents[u]["start"], u)):
        ev = vevents[uid]
        lines += [
            "BEGIN:VEVENT",
            f"UID:{ev['uid']}",
            f"DTSTAMP:{DTSTAMP}",
            f"DTSTART;VALUE=DATE:{fmt_date(ev['start'])}",
            f"DTEND;VALUE=DATE:{fmt_date(ev['end_exclusive'])}",
            f"SUMMARY:{esc(ev['summary'])}",
            f"LOCATION:{esc(ev['location'])}",
            f"DESCRIPTION:{esc(ev['description'])}",
        ]
        if ev.get("geo"):
            lines.append(f"GEO:{ev['geo'][0]:.4f};{ev['geo'][1]:.4f}")
        if ev["url"]:
            lines.append(f"URL:{esc(ev['url'])}")
        if ev["confidence"] == "tentative":
            lines.append("STATUS:TENTATIVE")
        else:
            lines.append("STATUS:CONFIRMED")
        lines.append("TRANSP:TRANSPARENT")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold_line(ln) for ln in lines) + "\r\n"


def render_html(data: dict, vevents: dict, skipped: list, asof: dt.date, horizon: dt.date) -> str:
    """Emit a browsable, self-contained calendar page (GitHub Pages friendly)."""
    meta = data.get("meta", {})
    theaters = data["_theaters_by_id"]
    title = meta.get("title", "Metro Phoenix Youth Theater")

    def h(s):
        return (str(s).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))

    # group emitted events by year-month
    by_month = {}
    for ev in sorted(vevents.values(), key=lambda e: (e["start"], e["summary"])):
        by_month.setdefault(ev["start"].strftime("%Y-%m"), []).append(ev)

    rows = []
    for ym in sorted(by_month):
        month_name = dt.datetime.strptime(ym, "%Y-%m").strftime("%B %Y")
        rows.append(f'<h2 class="month">{h(month_name)}</h2>')
        rows.append('<ul class="events">')
        for ev in by_month[ym]:
            badge_map = {"\U0001F4DD": "registration", "\U0001F3AD": "audition", "\U0001F3AB": "show"}
            etype = "show"
            for emo, name in badge_map.items():
                if ev["summary"].startswith(emo):
                    etype = name
                    break
            end = ev["end_exclusive"] - dt.timedelta(days=1)
            when = ev["start"].strftime("%a %b %-d")
            if end != ev["start"]:
                when += " &ndash; " + end.strftime("%b %-d")
            summ = ev["summary"]
            for emo in badge_map:
                summ = summ.replace(emo, "")
            summ = summ.strip()
            tent = " tentative" if ev["confidence"] == "tentative" else ""
            src = f' &middot; <a href="{h(ev["url"])}">source</a>' if ev["url"] else ""
            rows.append(
                f'<li class="evt {etype}{tent}">'
                f'<span class="date">{when}</span>'
                f'<span class="badge {etype}">{etype}</span>'
                f'<span class="title">{h(summ)}</span>'
                f'<span class="meta">{("tentative" if tent else "")}{src}</span>'
                f"</li>"
            )
        rows.append("</ul>")

    # tracked theaters list
    tlist = "".join(
        f'<li><a href="{h(t.get("website",""))}">{h(t["name"])}</a>'
        f'<span class="city">{h(t.get("city",""))}</span></li>'
        for t in data.get("theaters", [])
    )

    # pending (undated) items
    pending = ""
    if skipped:
        items = "".join(
            f'<li>{h(theaters.get(s.get("theater"),{}).get("name", s.get("theater")))}: '
            f'{h(s.get("title",""))} <em>({h(s.get("type","")) })</em></li>'
            for s in skipped
        )
        pending = (f'<details class="pending"><summary>{len(skipped)} announced but '
                   f"undated &mdash; not yet on the calendar</summary><ul>{items}</ul></details>")

    updated = asof.strftime("%B %-d, %Y")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{h(title)}</title>
<style>
  :root {{ --reg:#2563eb; --aud:#9333ea; --show:#16a34a; --ink:#1f2933; --dim:#6b7280; }}
  * {{ box-sizing:border-box; }}
  body {{ font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; color:var(--ink);
         max-width:900px; margin:0 auto; padding:1.5rem 1.25rem 4rem; line-height:1.45; }}
  h1 {{ margin:0 0 .25rem; font-size:1.7rem; }}
  .sub {{ color:var(--dim); margin:0 0 1.25rem; }}
  .actions {{ display:flex; flex-wrap:wrap; gap:.6rem; margin:1rem 0 1.5rem; }}
  .actions a {{ text-decoration:none; border:1px solid #d1d5db; border-radius:8px;
               padding:.5rem .8rem; color:var(--ink); font-weight:600; font-size:.9rem; }}
  .actions a.primary {{ background:var(--ink); color:#fff; border-color:var(--ink); }}
  h2.month {{ font-size:1.05rem; border-bottom:2px solid #eef0f3; padding-bottom:.3rem;
             margin:1.75rem 0 .6rem; }}
  ul.events {{ list-style:none; padding:0; margin:0; }}
  li.evt {{ display:grid; grid-template-columns:8.5rem 6rem 1fr; gap:.5rem; align-items:baseline;
           padding:.4rem 0; border-bottom:1px solid #f3f4f6; }}
  li.evt .date {{ color:var(--dim); font-variant-numeric:tabular-nums; font-size:.9rem; }}
  .badge {{ font-size:.7rem; text-transform:uppercase; letter-spacing:.03em; font-weight:700;
           border-radius:5px; padding:.12rem .4rem; color:#fff; text-align:center; }}
  .badge.registration {{ background:var(--reg); }}
  .badge.audition {{ background:var(--aud); }}
  .badge.show {{ background:var(--show); }}
  li.evt .title {{ font-weight:600; }}
  li.evt .meta {{ grid-column:3; color:var(--dim); font-size:.8rem; }}
  li.evt.tentative .title::after {{ content:" \\2022 tentative"; color:var(--dim); font-weight:400; font-size:.8rem; }}
  li.evt .meta a {{ color:var(--dim); }}
  .legend {{ display:flex; gap:1rem; flex-wrap:wrap; color:var(--dim); font-size:.85rem; margin:.5rem 0 0; }}
  .theaters {{ columns:2; -webkit-columns:2; font-size:.9rem; margin:.5rem 0 0; padding-left:1.1rem; }}
  .theaters li {{ break-inside:avoid; margin-bottom:.2rem; }}
  .theaters .city {{ color:var(--dim); margin-left:.4rem; font-size:.82rem; }}
  details.pending {{ margin:1.5rem 0; }}
  details.pending summary {{ cursor:pointer; font-weight:600; }}
  footer {{ margin-top:2.5rem; color:var(--dim); font-size:.82rem; border-top:1px solid #eef0f3; padding-top:1rem; }}
  @media (max-width:640px) {{
    li.evt {{ grid-template-columns:1fr; gap:.15rem; }}
    li.evt .badge {{ justify-self:start; }}
    li.evt .meta {{ grid-column:1; }}
    .theaters {{ columns:1; }}
  }}
</style>
</head>
<body>
<h1>{h(title)}</h1>
<p class="sub">Registration, audition &amp; show dates for youth-cast theater across metro Phoenix &mdash;
{updated} &rarr; {horizon.strftime('%B %Y')}.</p>

<div class="actions">
  <a class="primary" href="phoenix_youth_theater.ics" download>Download .ics</a>
  <a href="calendar.html">Calendar view</a>
  <a href="https://calendar.google.com/calendar/r/settings/addbyurl">Add to Google Calendar</a>
  <a href="README.md">How it works</a>
</div>
<p class="legend">
  <span><span class="badge registration">reg</span> registration / camps</span>
  <span><span class="badge audition">aud</span> auditions</span>
  <span><span class="badge show">show</span> performances</span>
</p>

{''.join(rows)}

{pending}

<h2 class="month">Theaters tracked ({len(data.get('theaters', []))})</h2>
<ul class="theaters">{tlist}</ul>

<footer>
  {len(vevents)} dated events. Generated by <code>build_calendar.py</code> from <code>data/events.json</code>.
  Dates are a best-effort snapshot &mdash; <strong>verify against each theater's official site (the source link)
  before relying on them</strong>. To subscribe in Google Calendar, choose &ldquo;From URL&rdquo; and paste this
  page's <code>.ics</code> URL.
</footer>
</body>
</html>
"""


def add_months(d: dt.date, months: int) -> dt.date:
    m = d.month - 1 + months
    year = d.year + m // 12
    month = m % 12 + 1
    # clamp day
    day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
                      else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return dt.date(year, month, day)


# --------------------------------------------------------------------------- #
# ICS parsing (for diffing the previous version) — minimal, our-output-shaped
# --------------------------------------------------------------------------- #
def unfold(text: str):
    lines = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw.startswith(" ") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def parse_ics(text: str) -> dict:
    events = {}
    cur = None
    for line in unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT":
            if cur is not None and "uid" in cur:
                events[cur["uid"]] = cur
            cur = None
        elif cur is not None and ":" in line:
            name, _, value = line.partition(":")
            name = name.split(";", 1)[0].upper()
            value = value.replace("\\,", ",").replace("\\;", ";").replace("\\n", "\n").replace("\\\\", "\\")
            if name == "UID":
                cur["uid"] = value
            elif name == "SUMMARY":
                cur["summary"] = value
            elif name == "DTSTART":
                cur["start"] = value
            elif name == "DTEND":
                cur["end"] = value
            elif name == "LOCATION":
                cur["location"] = value
            elif name == "GEO":
                cur["geo"] = value
            elif name == "STATUS":
                cur["status"] = value
    return events


def new_to_compare(vevents: dict) -> dict:
    """Project freshly built events into the same shape as parse_ics output."""
    out = {}
    for uid, ev in vevents.items():
        out[uid] = {
            "uid": uid,
            "summary": ev["summary"],
            "start": fmt_date(ev["start"]),
            "end": fmt_date(ev["end_exclusive"]),
            "location": ev["location"],
            "geo": f"{ev['geo'][0]:.4f};{ev['geo'][1]:.4f}" if ev.get("geo") else "",
            "status": "TENTATIVE" if ev["confidence"] == "tentative" else "CONFIRMED",
        }
    return out


# --------------------------------------------------------------------------- #
# Diff + reporting
# --------------------------------------------------------------------------- #
class C:
    GREEN = "\033[32m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def humandate(yyyymmdd: str) -> str:
    try:
        return dt.datetime.strptime(yyyymmdd, "%Y%m%d").strftime("%a %b %-d, %Y")
    except ValueError:
        return yyyymmdd


def diff_calendars(old: dict, new: dict, use_color: bool) -> int:
    c = C if use_color else type("NoC", (), {k: "" for k in vars(C) if not k.startswith("_")})
    old_ids, new_ids = set(old), set(new)
    added = sorted(new_ids - old_ids, key=lambda u: new[u]["start"])
    removed = sorted(old_ids - new_ids, key=lambda u: old[u]["start"])
    common = new_ids & old_ids
    fields = ["summary", "start", "end", "location", "geo", "status"]
    changed = []
    for uid in common:
        deltas = [(f, old[uid].get(f, ""), new[uid].get(f, "")) for f in fields
                  if old[uid].get(f, "") != new[uid].get(f, "")]
        if deltas:
            changed.append((uid, deltas))
    changed.sort(key=lambda x: new[x[0]]["start"])

    print(f"\n{c.BOLD}Calendar diff vs previous version{c.RESET}")
    print(f"  {c.GREEN}+{len(added)} added{c.RESET}   "
          f"{c.RED}-{len(removed)} removed{c.RESET}   "
          f"{c.YELLOW}~{len(changed)} changed{c.RESET}   "
          f"{c.DIM}({len(new)} events total){c.RESET}\n")

    for uid in added:
        e = new[uid]
        print(f"{c.GREEN}+ {humandate(e['start'])}  {e['summary']}{c.RESET}")
    for uid in removed:
        e = old[uid]
        print(f"{c.RED}- {humandate(e['start'])}  {e.get('summary','(no title)')}{c.RESET}")
    for uid, deltas in changed:
        e = new[uid]
        print(f"{c.YELLOW}~ {humandate(e['start'])}  {e['summary']}{c.RESET}")
        for f, ov, nv in deltas:
            if f in ("start", "end"):
                ov, nv = humandate(ov), humandate(nv)
            print(f"    {c.DIM}{f}:{c.RESET} {c.RED}{ov}{c.RESET} {c.DIM}->{c.RESET} {c.GREEN}{nv}{c.RESET}")

    if not (added or removed or changed):
        print(f"  {c.DIM}No changes.{c.RESET}")
    print()
    return len(added) + len(removed) + len(changed)


# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--window-months", type=int, default=15)
    ap.add_argument("--asof", default=None, help="Pin 'today' as YYYY-MM-DD (default: real today).")
    ap.add_argument("--check", action="store_true", help="Print diff but do not write the .ics.")
    ap.add_argument("--no-html", action="store_true", help="Do not write the index.html page.")
    ap.add_argument("--html", type=Path, default=HERE / "index.html")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args(argv)

    asof = dt.date.fromisoformat(args.asof) if args.asof else dt.date.today()
    use_color = (not args.no_color) and sys.stdout.isatty()

    data = load_data(args.data)
    vevents, skipped = build_events(data, asof, args.window_months)
    new_ics = render_ics(data, vevents, asof)

    old_text = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
    old_cmp = parse_ics(old_text) if old_text else {}
    new_cmp = new_to_compare(vevents)

    horizon = add_months(asof, args.window_months)
    print(f"{C.BOLD if use_color else ''}Metro Phoenix Youth Theater calendar{C.RESET if use_color else ''}")
    print(f"  Window: {asof.isoformat()} -> {horizon.isoformat()} ({args.window_months} months)")
    print(f"  Theaters tracked: {len(data.get('theaters', []))}")
    print(f"  Dated events in window: {len(vevents)}")
    if skipped:
        print(f"  Undated/TBD events held back (not in .ics): {len(skipped)}")

    n = diff_calendars(old_cmp, new_cmp, use_color)

    if args.check:
        print("(--check) Not writing .ics.")
        return 1 if n else 0

    args.out.write_text(new_ics, encoding="utf-8", newline="")
    print(f"Wrote {args.out.relative_to(HERE) if args.out.is_relative_to(HERE) else args.out} "
          f"({len(vevents)} events).")

    if not args.no_html:
        html = render_html(data, vevents, skipped, asof, horizon)
        args.html.write_text(html, encoding="utf-8")
        print(f"Wrote {args.html.relative_to(HERE) if args.html.is_relative_to(HERE) else args.html}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
