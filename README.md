# Jefferson Street Data (jstdata)

A Python interface and Investigation IDE for the Jefferson Street financial and
economic data API.

## Design Philosophy: From Filing Cabinet to Knowledge Graph

`jstdata` is built to minimize the friction between human thought and actionable data.

- **Series-First**: The individual data series is the primary resource, carrying its own metadata (frequency, units, source).
- **Entity Graph**: Entities are nodes in a relational graph — walk from a company to its SIC, a security ticker, or a geography.
- **Taxonomies**: Named populations (`country`, `sec-central-index-key`, …) scope search, query, and ranking.
- **Intent over IDs**: CLI and Python favor fuzzy search over memorizing slugs; always resolve before querying.
- **Investigations**: Durable research artifacts — one workspace (staged ids), saved queries (cartesian metric × entity), and a tree of actions (analytical TUIs).
- **Reproducible Discovery**: High-speed discovery in the TUI transitions into immutable Python / CLI snippets.

---

## Installation

```bash
# Using poetry
poetry install

# Manual install
pip install .
```

## Authentication & Configuration

### Quick Start (CLI)

```bash
jst login
```

Credentials are saved to `~/.jstdata/config.json` with secure permissions.

### Environment Variables

These take precedence over the config file:

```bash
export JSTDATA_API_KEY="your-api-key-here"
# Optional: export JSTDATA_BASE_URL="https://api.jeffersonst.io"
```

### Managing Configuration

```bash
jst config show
jst config show --verbose
jst config set api_key XXX
```

## Investigations

An **investigation** is the durable research unit: YAML under
`~/.jstdata/investigations/` with one **workspace** (staged metrics / entities /
series), saved **queries** (cartesian metric × entity against `GET /query`), and
a tree of **actions** (reusable TUI screens).

```bash
jst new --id housing-slowdown
jst open housing-slowdown
jst ls
jst                 # reopen the active investigation (Untitled if none)

# Saved queries (cartesian metric × entity)
jst query add --metric gdp --entity france --entity usa --name "FR vs US"
jst query inspect 1
jst query run 1 --format json

# Add actions (children of the active action unless --parent is set)
jst action add console --args '{"taxonomy":"country","resource_type":"entity"}'
jst action add discover --args '{"mode":"union"}' --activate
jst action add rank --args '{"taxonomy":"country"}'
jst action rm 2 --force
```

Built-in actions:

| Action | Role |
|--------|------|
| `console` | Search catalog; stage into the workspace |
| `discover` | Find metrics for workspace entities; preview coverage |
| `rank` | Leaderboard entities for a workspace metric |

```bash
jst action ls
jst action show console
jst action show rank --json
```

Inside the TUI: `g` returns Home (Recent / Search / Actions / Workspace /
Queries). `esc` goes back one level. `:` opens the command palette (fuzzy
destinations and saved queries, plus `:workspace`, `:search metric gdp`,
`:query`, `:rename`, `:add`, `:rm`, `:tutorial`) · `n`/`p` (↑/↓) siblings ·
`N`/`P` (→/←) depth · space collects into the workspace · `t` tutorials ·
`?` help · `q` quit.

Active context lives in `~/.jstdata/.active.yaml`.

### Tutorials

The first time you open the TUI, a tutorial menu appears. Pick one, or press
`esc` to explore on your own; `t` (or `:tutorial`) brings the menu back any
time. A coach bar docked at the top explains each step and names the exact
keys to press, and advances as you do them. Each tutorial runs in its own
investigation (`tutorial-<id>`); leaving it reopens yours.

| Tutorial | Level | What you do |
|----------|-------|-------------|
| `big-tech-gross-profit` | Beginner · guided | Compare Apple, Microsoft, Amazon: Home → Search → collect → metric view → Workspace → Query → chart |
| `gulf-oil-exports` | Intermediate · assisted | Metric-first: collect the oil metrics, open one to see which countries report it, collect the Gulf, chart the query |
| `gulf-fiscal-breakeven` | Advanced · open | Research question on fiscal breakeven oil prices; the coach only checks progress |

Progress (menu seen, tutorials completed) lives in `~/.jstdata/onboarding.json`.
First `jst` / `jst new` / `jst open` also bootstraps the bundled `tutorial`
(welcome) and `plain-bagel` demo investigations when none exist.

### Agents

```bash
jst agent-guide
jst agent install          # Codex, Claude Code, Cursor (~/.agents, ~/.claude, ~/.cursor)
jst agent install cursor
```

`jst agent-guide` is the versioned operating manual (hard rules, modes, recipes,
and the live CLI/action reference). Prefer it over guessing command shapes or
inventing ids. `jst agent install` copies a tiny pointer skill (`run jst
agent-guide`) into user-level skill directories. Discovery is not guaranteed —
if the agent does not pick it up, say: *Use the jst skill. Then run `jst
agent-guide` and follow it.*

## The Scriptable API CLI (`jst api`)

Pipe-friendly resource commands for automation and quick extraction. Nested
under `jst api` so they stay distinct from investigation commands.

```bash
# Fuzzy / set search (omit QUERY to list the matching set)
jst api metric search "defense spending" --taxonomy country --limit 20 --format json
jst api entity search --relation classified_as:sic:3674 --limit 50 --format json
jst api entity search --metric gdp --metric cpi --mode intersect --format json

# Taxonomies
jst api taxonomy ls --format json
jst api taxonomy entities country --limit 20 --format json

# Bounded cross-sectional query (head/tail per series)
jst api query --metric inflation --entity "United States" --frequency Monthly --tail 20
jst api query --metric gdp --taxonomy country --tail 1 --sort-by value --limit 50 --format json

# Deep history for one known series
jst api series observations ABC123 --start-date 2000-01-01 --limit 1000

# Entity graph
jst api entity relations cik:1045810 --format pretty
```

## The Python API

```python
from jstdata import JSTDataClient
from jstdata.investigation import Workspace

client = JSTDataClient()

# Cross-sectional observations (bounded per series)
df = client.query_df(
    metric="gross-domestic-product",
    entity=["usa", "gbr"],
    tail=20,
)

# Rank a taxonomy population by latest value
top = client.query(
    metric="gross-domestic-product",
    taxonomy="country",
    frequency="Annual",
    tail=1,
    sort_by="value",
    limit=50,
)

# Relation-scoped entity search (OR when multiple)
entities = client.search_entities(
    relation=["classified_as:sic:7372", "classified_as:sic:5961"],
    taxonomy="sec-central-index-key",
    limit=50,
)

# Deep history for one series
obs = client.get_series_observations("ABC123", start_date="2000-01-01")

# Workspace helpers project the same selectors as jst api query
ws = Workspace(
    metric=["gross-domestic-product"],
    taxonomy="country",
    tail=1,
    sort_by="value",
)
print(ws.to_cli())
df = ws.execute_df(client)
```

## Extending the product

See [AGENTS.md](AGENTS.md) for how investigations, workspaces, and actions are
structured when adding new interactive surface area.
