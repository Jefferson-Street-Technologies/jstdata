---
name: jst
description: >
  Use when working with Jefferson Street (JST / jstdata): economic and financial
  data, investigations, metrics, entities, series, catalog search, saved
  queries, observations, or the `jst` CLI. Trigger on JST, jstdata, Jefferson
  Street, household debt, GDP, catalog queries, and investigation handoff.
---

# jst

Jefferson Street (`jst`) is a research CLI/TUI for economic and financial data.

## Bootstrap (required)

Before answering or mutating anything, run:

```bash
jst agent-guide
```

That printout is the versioned source of truth (policy, CLI, action catalog,
recipes). Follow it. Do not rely on this skill file for command lists or
project layout — those change with the package.

## Defaults

- Prefer `jst actions …` for audited research. Pass `--project` and
  `--investigation` explicitly when mutating.
- Resolve ids with `jst actions search …` (JSON by default). Never invent ids.
- Run values with `jst actions query run` (optional `--save`); promote with
  `jst actions promote`.
- Workflow: resolve → actions → `jst actions inspect` → promote → hand off.
  Do not edit project/investigation YAML to change state.
- Do not use retired `jst action` / `jst query`, or soft-deprecated `jst api`,
  for the audited research path.
- Do not launch the TUI (`jst` / `jst open`) unless the user asked to operate
  the UI.
- If `jst` is not on PATH, tell the user to `pip install jstdata` (or Poetry)
  and `jst login`.
