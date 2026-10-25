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
investigation layout — those change with the package.

## Defaults

- Resolve ids with `jst api … search` (`--format json`). Never invent ids.
- Mutate the active investigation with `jst query add` and `jst action add` /
  `rm`. Do not edit investigation YAML to change state.
- Do not launch the TUI (`jst` / `jst open`) unless the user asked to operate
  the UI.
- If `jst` is not on PATH, tell the user to `pip install jstdata` (or Poetry)
  and `jst login`.
