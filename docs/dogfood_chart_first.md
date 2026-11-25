# Chart-first TUI — dogfood checklist

Acceptance targets after the one-pass redesign.

## Navigation

- [ ] Open a project: see header, investigation tabs, sidebar, main pane (no Home hub)
- [ ] `{` / `}` switch tabs; sidebar query list updates
- [ ] `\` toggles sidebar; `Ctrl+h` / `Ctrl+l` move focus
- [ ] `g` returns to research root / preferred query (not five-card Home)

## Query construction

- [ ] `/` search → Enter inspect company → load → Enter metric → chart (draft)
- [ ] `+` add another entity (scoped to first metric) → chart refreshes
- [ ] `m` add another metric (scoped to first entity) → chart refreshes
- [ ] `e` Edit view → add entity/metric the same way → Update
- [ ] `s` saves the single draft into the query list (new empty draft appears)
- [ ] Workspace collect (Space) → `n` builds draft from workspace

## Progressive disclosure

- [ ] Default is chart + annotations
- [ ] `t` table · `d` definition · `v` history/raw
- [ ] `a` annotate commentary / notes_on

## Agent collaboration

- [ ] Agents writing via `jst actions` show in `H` history without stealing viewport
- [ ] Tab `●` / `!` marks appear with activity
- [ ] Enter inspect = historical artifact; `r` in history = rerun current data

## Feedback / publish

- [ ] `F` from anywhere queues feedback with diagnostics
- [ ] `jst preview` shows only Saved (`in_report`) queries
- [ ] Legacy single-tab publish/clone still works

## Targets from the design brief

| Task | Path |
|------|------|
| Query a known series | `/` → select → chart |
| Company metric | `/` company → metric → chart |
| Add company to chart | `+` (entities on first metric) |
| Add metric to chart | `m` (metrics on first entity) |
| Change date range | `e` → Observations |
| Review agent findings | `}` tab → sidebar query |
| Inspect evidence | `H` → Enter |
| Bug report | `F` |
