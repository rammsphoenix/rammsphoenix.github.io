# Weekly refresh — research prompt template

Use this to drive the weekly data refresh with an AI research assistant (or as a
human checklist). Paste the prompt below, swapping in a batch of theaters from
`data/events.json`. Run a few batches in parallel, then fold the results back into
`data/events.json` and run `python3 build_calendar.py`.

---

> You are refreshing schedule data for a metro‑Phoenix **youth‑cast** theater
> calendar. Today is `<DATE>`. I need events from now through ~15 months out
> (`<DATE+15mo>`).
>
> For each theater below, visit its official website — especially the
> **season / shows**, **auditions**, and **classes / camps / registration**
> pages — and report every event in the window. "Youth‑cast" means performers are
> youth (≈18/19 and under). If a company's mainstage uses adult/professional
> actors, say so and exclude it; if a mixed‑age company has distinct youth‑cast
> productions (e.g. "Jr."/"Kids" titles), include only those.
>
> Theaters: `<paste name + website for each>`
>
> Return a compact list. For each event give: `theater`, `title`,
> `type` (registration | audition | show), `start` (YYYY‑MM‑DD, or "TBD" if
> announced but undated), `end` (YYYY‑MM‑DD for multi‑day show runs), `confidence`
> (confirmed = exact date published on an official source; tentative = inferred),
> and `source` (the URL you found it on).
>
> Rules:
> - **Do not invent dates.** Only report dates you actually find. If a season is
>   announced without dates, report the show with `start: "TBD"`, `confidence:
>   tentative`, and a note on what *is* known.
> - Prefer official theater sites; ticketing pages (On The Stage, Ludus,
>   Eventbrite, TutuTix) are good for show dates.
> - It's expected that much of the second year is unannounced — report what's
>   genuinely published.
> - Also report the company's **cast age cap** and confirm it's youth‑cast.

---

## Folding results back in

- Match each event to a theater `id` in `data/events.json` (add the theater to the
  `theaters` list if it's new).
- Update existing events in place when a date changes — keep the same `title`/`type`
  so the build shows it as a `~ changed` event rather than remove + add.
- Set `start: "TBD"` for announced‑but‑undated items (held out of the `.ics` until
  dated).
- Keep the `source` URL current; it's how next week's refresh re‑verifies.
- Run `python3 build_calendar.py`, eyeball the diff, then commit data + `.ics`.
