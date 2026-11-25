# Chart-first TUI — dogfood checklist

Acceptance targets after the interaction-design one-shot (composition header, E/M/S, auto-promote).

## Navigation

- [ ] Open a project: see header, investigation tabs, sidebar, main pane
- [ ] `{` / `}` switch tabs; sidebar query list updates
- [ ] Click a query / workspace resource to open it (no j/k scrolling)
- [ ] `1` then digit jumps to a query; `2` then digit jumps to a workspace item
- [ ] Workspace is a flat list with faint Entities / Metrics / Series headers
- [ ] `\` toggles sidebar; `g` returns to research home / preferred query

## Query construction

- [ ] `/` search → Enter inspect company → load → Enter metric → chart
- [ ] First executable plot auto-runs and auto-promotes (Saved); new empty draft appears
- [ ] `E` add another entity (scoped to first metric) → refresh banner → `r`
- [ ] `M` add another metric (scoped to first entity) → refresh banner → `r`
- [ ] `S` add a direct series the same way
- [ ] Composition chips / frequency / obs selectors are clickable; no Edit view
- [ ] Workspace collect (Space) → `n` builds draft from workspace

## Progressive disclosure

- [ ] Default is chart + composition header; Chart/Table tabs sit above the plot
- [ ] `c` chart · `t` table (no Sources view; no YoY headline metrics)
- [ ] Table shows Release column; Enter opens Release page (or series|value|units)
- [ ] Research notes at bottom are prominent; `a` / Annotate edits them

## Series / home

- [ ] Research home: search / create query / resume CTAs with shortcuts
- [ ] Series screen: metadata + Plot CTA → draft chart

## Agent collaboration

- [ ] Agents writing via `jst actions` show in `H` history without stealing viewport
- [ ] Tab `●` / `!` marks appear with activity
- [ ] Enter inspect = historical artifact; `r` in history = rerun current data

## Feedback / publish

- [ ] `F` from anywhere queues feedback with diagnostics
- [ ] `jst preview` shows only Saved (`in_report`) queries
- [ ] `?` reminds that shortcuts live beside controls (no help dump)

## Targets from the design brief

| Task | Path |
|------|------|
| Query a known series | `/` → select → chart, or Series → Plot |
| Company metric | `/` company → metric → chart |
| Add company to chart | `E` (scoped) → `r` |
| Add metric to chart | `M` (scoped) → `r` |
| Change frequency / obs | composition header selectors → `r` |
| Review agent findings | `}` tab → sidebar query |
| Inspect evidence | `H` → Enter |
| Bug report | `F` |
