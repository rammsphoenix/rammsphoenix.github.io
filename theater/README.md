# Metro Phoenix Youth Theater Calendar

A calendar of **registration**, **audition**, and **show** dates for youth‑cast
theater companies across the metro Phoenix area, covering a rolling **15‑month**
window. "Youth‑cast" means the performers are youth (roughly age 18/19 and under);
companies whose mainstage uses adult/professional actors are excluded even when
they perform *for* young audiences.

## What's here

| File | Purpose |
|------|---------|
| `data/events.json` | **Source of truth.** Curated theaters + dated events, each with a `source` URL and a `confidence` flag. This is the file you edit. |
| `build_calendar.py` | Generates the `.ics` and prints a diff vs. the previously committed version. Standard library only — no `pip install`. |
| `phoenix_youth_theater.ics` | Generated calendar. Subscribe to it or import it into Google/Apple/Outlook. |
| `index.html` | Generated agenda/list page (GitHub Pages), with a download/subscribe link. |
| `calendar.html` | Static month-grid calendar UI that reads the `.ics` live in the browser. |
| `README.md` | This file. |

Both `phoenix_youth_theater.ics` and `index.html` are generated — never edit them by
hand; edit `data/events.json` and re-run the build. Use `--no-html` to skip the page.

## Quick start

```bash
cd theater
python3 build_calendar.py          # build the .ics + print the diff
```

First run diffs against nothing, so everything shows as added. Subsequent runs
show only what changed since the last committed `.ics`.

Useful flags:

```bash
python3 build_calendar.py --check          # print the diff but do NOT write the file (preview / CI)
python3 build_calendar.py --window-months 15
python3 build_calendar.py --asof 2026-06-10 # pin "today" for reproducible output
python3 build_calendar.py --no-color
```

## The weekly update process

The calendar is intentionally **data‑driven and human/AI‑auditable** rather than a
fragile scraper. Twenty‑plus theaters publish their schedules on a dozen different
website builders and ticketing platforms (On The Stage, Ludus, Eventbrite, etc.),
and most don't expose a feed. A scraper that silently breaks is worse than no
scraper. So the loop is:

1. **Refresh the data.** For each theater in `data/events.json`, visit its
   `website` (and its season / auditions / classes pages) and update events:
   add newly announced shows/auditions/registration dates, correct any changed
   dates, and remove anything cancelled. Each event carries a `source` URL to
   re‑check. This step is well suited to an AI research assistant — see the prompt
   template in [`PROMPT.md`](PROMPT.md) — but a person can do it too.
2. **Rebuild + review the diff.** Run `python3 build_calendar.py`. The terminal
   diff shows exactly what changed (`+` added, `-` removed, `~` changed, with
   old → new dates). Sanity‑check it against what you found.
3. **Commit.** Commit `data/events.json` and the regenerated
   `phoenix_youth_theater.ics` together with a message like
   `Update theater calendar (week of YYYY-MM-DD)`. The git history then doubles
   as a change log, and the printed diff is the human‑readable summary.

Running weekly is the point: most events 6–15 months out **aren't announced yet**.
The re‑run captures each new announcement as theaters post it.

### Data format

```jsonc
{
  "meta": { "title": "...", "window_months": 15 },
  "theaters": [
    { "id": "vyt", "name": "Valley Youth Theatre", "city": "Phoenix",
      "venue": "525 N 1st St", "website": "https://vyt.com", "age_cap": 18 }
  ],
  "events": [
    { "theater": "vyt",                 // matches a theater id
      "title": "Newsies",
      "type": "show",                   // registration | audition | show
      "start": "2026-09-18",            // YYYY-MM-DD, or "TBD" if announced but undated
      "end": "2026-10-04",              // optional; for multi-day show runs
      "confidence": "confirmed",        // confirmed = date published on official source; tentative = inferred
      "source": "https://vyt.com/...",  // where the date came from
      "notes": "optional free text" }
  ]
}
```

Theater entries carry an `address`, `zip`, and `lat`/`lon` (decimal degrees). Each
event inherits its theater's location: the `.ics` gets a `GEO` and a street-address
`LOCATION`, and `calendar.html` uses those for its distance filter. When adding a new
theater, fill in `lat`/`lon` (a ZIP-centroid is fine — the distance filter is coarse).

- Events with `start: "TBD"` (or missing) are **kept** as placeholders but left out
  of the `.ics`; the build reports how many were held back so nothing is lost.
- Each event gets a **stable UID** derived from `theater + title + type + year`, so a
  date that shifts within a season surfaces as a `~ changed` event (with the old →
  new date) rather than a remove + add. Annually recurring items in different years
  stay distinct.
- Output is **deterministic**: re‑running with unchanged data produces a
  byte‑identical `.ics`, so `git diff` stays quiet until the data actually changes.

### Confirmed vs. tentative

`confidence: tentative` events are emitted with `STATUS:TENTATIVE` in the `.ics`
(calendar apps may render them differently). Use it for dates inferred from a
weekday pattern, a "next season" carousel, or a year that wasn't explicitly
printed. Use `confirmed` only when an official source states the exact date.

## Scope notes

- **Geography:** metro Phoenix (Phoenix, Scottsdale, Glendale, Peoria, Mesa, Tempe,
  Chandler, Gilbert, Queen Creek, Cave Creek, Fountain Hills, Anthem, etc.).
- **Excluded** as not youth‑cast: professional companies performing *for* youth
  (e.g. Childsplay mainstage, Hale Centre Theatre), and adult community theaters
  (e.g. Don Bluth Front Row). Where a mixed‑age company runs distinct youth‑cast
  productions (e.g. Desert Foothills "Jr."/"Kids" titles), only those are included.
- Dates are a best‑effort snapshot from each theater's published information and
  **should be verified against the official source before you rely on them** —
  follow the `source` link on each event.
