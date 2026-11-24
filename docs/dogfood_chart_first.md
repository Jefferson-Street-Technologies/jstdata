# Chart-first TUI — dogfood checklist

Acceptance targets after the one-pass redesign.

## Navigation

- [ ] Open a project: see header, investigation tabs, sidebar, main pane (no Home hub)
- [ ] `{` / `}` switch tabs; sidebar query list updates
- [ ] `\` toggles sidebar; `Ctrl+h` / `Ctrl+l` move focus
- [ ] `g` returns to research root / preferred query (not five-card Home)

## Query construction

- [ ] `/` search → Enter inspect company → load → Enter metric → chart (draft)
- [ ] `e` add another entity → Update → chart refreshes
- [ ] `s` promotes draft to Saved (appears under Saved in sidebar)
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
| Add company to chart | `e` / Ctrl+Enter from search |
| Change date range | `e` tail/head |
| Review agent findings | `}` tab → sidebar query |
| Inspect evidence | `H` → Enter |
| Bug report | `F` |
