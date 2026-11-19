# Annotations

Annotations are the researcher's commentary on an investigation: what it is
asking, what the data shows, and what to check next. They live in the
investigation's YAML, travel with `jst publish` and `jst clone`, and are shown
on the published page. This document is the agreed format (JEF-156, from the
Publish & Clone PRD, section 5.6).

## What ships today

Two places take an annotation, both optional:

```yaml
id: hyperscaler-debt
title: Hyperscaler Debt
notes: Debt, cash and capex for the four largest cloud providers.   # short description
annotations: |                                                       # investigation-level
  ## Question
  Are hyperscalers funding the AI build-out with debt?

  ## What the data shows
  - Long-term debt rose at **Amazon** and **Meta** after 2022.
  - Capex grew faster than cash at all four.
queries:
- id: 1
  name: Latest debt
  annotations: Latest reported value per company, largest first.     # query-level
  metric: [long-term-debt]
  entity: [cik:1018724, cik:1326801]
  tail: 1
```

- **`notes`** is the investigation's one-line description. It's shown under
  the title.
- **`annotations`** (investigation) is the main write-up, shown above the
  queries.
- **`queries[].annotations`** explains one query, shown above its charts.
  The older key `notes` on a query is still read as its annotations.

Annotations are **Markdown**, a small, safe subset: `##`/`###` headings,
paragraphs, `-` and `1.` lists, `> quotes`, `**bold**`, `*italic*`,
`` `code` ``, fenced code blocks and `[links](https://…)`. Anything else shows
as plain text, so annotations written before Markdown still read correctly.
The page builds this into page elements, never raw HTML, so an annotation
can't inject markup or scripts; links may only be `http(s)`, `mailto` or a
path on the site.

Writing them:

- In the TUI: `:annotate` (investigation) or `:annotate <query-id>`, or `a`
  on the Queries list.
- From the CLI: `jst query add --annotations "…"`.
- Or edit the YAML under `~/.jstdata/investigations/` directly.

Whitespace around an annotation is trimmed and an empty one is removed. A
missing annotation never stops a publish.

## Proposed next step: series and time-slice notes

The PRD's longer-term model also annotates **one series** ("this filing was
restated") or **a period** ("2020: pandemic distortion"). Series and dates
aren't objects in the investigation (queries select them through metrics and
entities), so these notes point at them with a **selector** instead of
changing how queries are defined:

```yaml
queries:
- id: 2
  name: Long-term debt, last 12 quarters
  annotations: Quarterly history from 10-Q and 10-K filings.
  metric: [long-term-debt]
  entity: [cik:1652044, cik:789019]
  tail: 12
  notes_on:                       # proposed; not read yet
  - series: edgar:0000789019:long-term-debt:usd:annual
    text: Microsoft reports on a June fiscal year end.
  - from: 2020-03-01
    to: 2020-12-31
    text: Pandemic-era bond issuance.
  - series: edgar:0001652044:long-term-debt:usd:quarterly
    from: 2024-01-01
    text: Includes the 2024 bond offering.
```

- A note has `text` plus at least one of `series` (a series id from the
  query's results) and `from`/`to` (ISO dates, both inclusive; either may be
  left open).
- `series` only: shown next to that series in the chart legend and table.
- `from`/`to` only: shaded on the query's time charts.
- Both: marks that series over that period.
- A selector that matches nothing (e.g. a series that left the results after
  a refresh) is shown in a "notes" list under the query rather than dropped,
  so the author's comment is never lost silently.

This needs no change to `Query` execution or to the publication data
contract beyond carrying `notes_on` through (the website currently keeps only
known query fields, so it would drop the key). Older jstdata versions ignore
the key when reading, but would lose it if they re-save the file, so the
format should ship together with a jstdata release that preserves it. It
isn't built yet: it should follow feedback from the first publications.
