# rpflow

Deterministic orchestration CLI for Repo Prompt (`rp-cli`) automation.

`rpflow` is a thin reliability layer over `rp-cli` that standardizes:

- window + tab routing
- safe workspace switching (`Already on workspace` treated as success)
- stateful defaults (last good window/tab/workspace)
- profile-driven timeout-aware execution (`--profile fast|normal|deep`)
- robust prompt export and plan-export flows
- direct tool-call orchestration (`-c`/`-j`, including `@file` and `@-`)
- machine-readable tools schema passthrough
- smoke tests for end-to-end health
- optional strict mode for deterministic CI-like runs
- structured JSON run reports per command (`--report-json`)
- clearer timeout/stall classification (builder timeout vs generic timeout vs killed)
- optional single retry on builder timeout/SIGKILL (`--retry-on-timeout`)
- optional resume path from an existing export (`--resume-from-export`)

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
  - `autopilot`: preflight smoke + plan-export in one command
- Timeout profiles
  - `--profile fast|normal|deep` with sensible per-command defaults
- Timeout + fallback path
  - optional fallback export when builder times out
  - optional one-shot retry on timeout/SIGKILL before fallback
  - optional resume from an existing export if plan/fallback still fails
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
- Python 3.9+

Tested baseline:
- OpenClaw 2026.2.x
- Repo Prompt app + MCP enabled on macOS

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
  --profile fast \
  --fallback-export-on-timeout \
  --retry-on-timeout

# One-shot preflight + plan-export
rpflow autopilot \
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

# Structured report for automation logs
rpflow autopilot --workspace GitHub --tab T1 --select-set repoprompt-rpflow-cli/src/ --task "Draft plan" --out /tmp/plan.md --fallback-export-on-timeout --report-json /tmp/rpflow-run.json

# Resume from prior export if plan path fails
rpflow plan-export --workspace GitHub --tab T1 --select-set repoprompt-rpflow-cli/src/ --task "Draft plan" --out /tmp/plan.md --resume-from-export /tmp/last-known-good.md

# Deterministic strict mode (requires explicit routing)
rpflow exec --strict --window 1 --tab T1 --workspace GitHub -e 'tabs'
```

## 2-minute quickstart (community)

```bash
# 1) Clone + install
cd /Users/<you>/github
git clone <your-private-or-public-rpflow-repo-url> repoprompt-rpflow-cli
cd repoprompt-rpflow-cli
python3 -m venv .venv && source .venv/bin/activate && pip install -e .

# 2) Verify Repo Prompt wiring
rpflow smoke --workspace GitHub --tab T1 --profile fast
# expected lines: tabs: ok / context: ok / tools-schema: ok

# 3) Run one end-to-end plan/export
rpflow autopilot --workspace GitHub --tab T1 --select-set <repo-or-path> --task "draft plan" --out /tmp/rpflow-plan.md --retry-on-timeout --fallback-export-on-timeout
# expected: preflight:*: ok and Prompt Export to /tmp/rpflow-plan.md
```

## Command reference

Common options (all subcommands):
- `--report-json <path>` write a structured run report JSON for audit/logging
- `--profile fast|normal|deep` choose timeout defaults (default: `normal`)

### `rpflow doctor`
Checks connectivity and prints windows/tabs summary.

### `rpflow exec`
Run arbitrary `rp-cli -e` command with safe workspace switching and routing.

Options:
- `--window <id>`
- `--tab <name>` (default: `T1`)
- `--workspace <name>` (default: `GitHub`)
- `--timeout <seconds>` (optional; otherwise from selected profile)
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
- `--timeout <seconds>` (optional; otherwise from selected profile)
- `--fallback-export-on-timeout` (optional)
- `--retry-on-timeout` (optional)
- `--retry-timeout <seconds>` (optional)
- `--retry-timeout-scale <float>` (optional, default 1.5)
- `--resume-from-export <path>` (optional)

### `rpflow autopilot`
One-shot `preflight + plan-export` orchestration.

Behavior:
- runs smoke-style preflight checks (tabs/context/tools-schema)
- then runs plan-export flow
- optional fallback export on timeout

Options:
- `--select-set <paths>` (required)
- `--task <text>` (required)
- `--out <path>` (required)
- `--timeout <seconds>` (optional; otherwise from selected profile)
- `--preflight-timeout <seconds>` (optional; otherwise from selected profile)
- `--fallback-export-on-timeout` (optional)
- `--retry-on-timeout` (optional)
- `--retry-timeout <seconds>` (optional)
- `--retry-timeout-scale <float>` (optional, default 1.5)
- `--resume-from-export <path>` (optional)
- routing/workspace options same as `exec`

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

## Troubleshooting

- `rpflow error: rp-cli not found in PATH`
  - Install rp-cli from Repo Prompt settings (MCP Server page).
- `tab '<name>' not found`
  - Run `rpflow exec -e 'tabs'` and use a valid tab name.
- `multiple windows detected`
  - Pass `--window <id>` or close extra Repo Prompt windows.
- Builder timeout / SIGKILL
  - Use `--retry-on-timeout --fallback-export-on-timeout`, optionally `--resume-from-export`.

## Security and privacy

- `rpflow` does not store API keys or secrets.
- `--report-json` files can contain command/output tails; treat them as local diagnostics.
- Keep runtime state local where appropriate; commit docs/config, not volatile state snapshots.

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
