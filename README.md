# rpflow

Deterministic orchestration CLI for Repo Prompt (`rp-cli`) automation.

`rpflow` is a thin reliability layer over `rp-cli` that standardizes:

- window + tab routing
- safe workspace switching (`Already on workspace` treated as success)
- stateful defaults (last good window/tab/workspace)
- timeout-aware execution
- robust prompt export and plan-export flows
- direct tool-call orchestration (`-c`/`-j`, including `@file` and `@-`)
- machine-readable tools schema passthrough
- smoke tests for end-to-end health
- optional strict mode for deterministic CI-like runs

## Why this exists

Repo Prompt is powerful, but automation can get brittle when window routing, workspace switching, and long context-builder runs vary across versions. `rpflow` provides one stable entrypoint for OpenClaw scripts and CI-like automation.

## Features

- Smart window resolution
  - explicit `--window` wins
  - otherwise reuses last-good window when available
  - otherwise auto-selects if exactly one window is open
- Safe workspace switch
  - treats `Already on workspace "X"` as a no-op success
- JSON-friendly execution
  - pass raw `-e` commands or directly query tools schema
- Export helpers
  - `export`: selection → prompt export
  - `plan-export`: selection → context builder (plan) → prompt export
- Timeout + fallback path
  - optional fallback export when builder times out
- Persistent state
  - `~/.config/rpflow/state.json`

## Install

### Local editable (recommended for development)

```bash
cd repoprompt-rpflow-cli
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Run directly without install

```bash
python3 -m rpflow --help
# or
python3 -m rpflow.cli --help
```

## Prerequisites

- Repo Prompt app running
- Repo Prompt MCP server enabled
- `rp-cli` on PATH

## Quick start

```bash
# Health check
rpflow doctor

# Run command in workspace/tab with safe routing
rpflow exec --workspace GitHub --tab T1 -e 'tabs'

# Export selected context
rpflow export \
  --workspace GitHub \
  --tab T1 \
  --select-set nvidia-dgx-spark/README.md,nvidia-dgx-spark/scripts/ \
  --out /tmp/context.md

# Plan + export (with timeout fallback)
rpflow plan-export \
  --workspace GitHub \
  --tab T1 \
  --select-set nvidia-dgx-spark/scripts/ \
  --task "Draft reliability hardening plan" \
  --out /tmp/plan.md \
  --timeout 90 \
  --fallback-export-on-timeout

# Tools schema passthrough
rpflow tools-schema

# Direct tool call (JSON inline / @file / @-)
rpflow call --workspace GitHub --tab T1 --tool apply_edits --json-arg @examples/edits.json

# Fast smoke run
rpflow smoke --workspace GitHub --tab T1

# Deterministic strict mode (requires explicit routing)
rpflow exec --strict --window 1 --tab T1 --workspace GitHub -e 'tabs'
```

## Command reference

### `rpflow doctor`
Checks connectivity and prints windows/tabs summary.

### `rpflow exec`
Run arbitrary `rp-cli -e` command with safe workspace switching and routing.

Options:
- `--window <id>`
- `--tab <name>` (default: `T1`)
- `--workspace <name>` (default: `GitHub`)
- `--timeout <seconds>` (default: 60)
- `-e '<command>'` (required)
- `--raw-json` (forward raw-json output)
- `--strict` (requires explicit `--window --tab --workspace`)

### `rpflow export`
Selection + prompt export helper.

Options:
- `--select-set <comma-separated-paths>` (required)
- `--out <path>` (required)
- routing/workspace options same as `exec`

### `rpflow plan-export`
Selection + context builder plan + prompt export.

Options:
- `--select-set <paths>` (required)
- `--task <text>` (required)
- `--out <path>` (required)
- `--timeout <seconds>` (default: 120)
- `--fallback-export-on-timeout` (optional)

### `rpflow call`
Run `rp-cli -c/-j` calls with safe routing and workspace handling.

Options:
- `--tool <name>` (required)
- `--json-arg <value>` (optional; inline JSON, `@file`, or `@-`)
- routing/workspace options same as `exec`
- `--strict` available

### `rpflow tools-schema`
Pass-through for Repo Prompt machine-readable schema.

Options:
- `--group <name>` (optional)

### `rpflow smoke`
Run quick end-to-end health checks:
- tabs
- context summary
- tools schema

Options:
- routing/workspace options same as `exec`
- `--strict` available

## State file

`rpflow` stores last-good values in:

`~/.config/rpflow/state.json`

```json
{
  "last_window": 1,
  "last_tab": "T1",
  "last_workspace": "GitHub",
  "updated_at": "2026-02-07T11:00:00-07:00"
}
```

## Design notes

- Keep shell escaping minimal by assembling command chains in Python.
- Favor deterministic failures over hidden retries.
- Keep behavior explicit and script-friendly.

See `docs/design.md` for architecture details.
