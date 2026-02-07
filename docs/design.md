# rpflow Design

## Objectives

1. Deterministic automation for Repo Prompt in OpenClaw workflows
2. Preserve full `rp-cli` power while reducing orchestration friction
3. Keep implementation simple, auditable, and dependency-light

## Core architecture

- `rpflow.rpcli`: subprocess wrapper around `rp-cli`
- `rpflow.state`: persistent last-good routing state
- `rpflow.cli`: argparse command surface (exec/call/export/plan-export/autopilot/tools-schema/smoke)
- structured command reports via `--report-json`

## Routing policy

Window resolution:
1) explicit `--window`
2) last-good window if still valid
3) single open window auto-selection
4) otherwise fail with actionable message

Tab handling:
- defaults to `T1`
- validates tab exists in targeted window

Workspace switch:
- executed before flow commands
- if output includes `Already on workspace`, continue

## Timeout policy

- profile-driven defaults (`--profile fast|normal|deep`) set baseline timeouts per command
- `exec`: hard timeout
- `call`: hard timeout
- `plan-export`: hard timeout on builder chain
- `autopilot`: preflight timeout + plan/export timeout
- optional retry: one builder re-run on timeout/SIGKILL (`--retry-on-timeout`)
- optional fallback: export selection-only prompt when builder times out or gets SIGKILL
- optional resume path: reuse a prior known-good export (`--resume-from-export`)
- timeout returns code `124` (not an uncaught exception)
- stage classification distinguishes `builder_timeout` vs generic `timeout` vs signal kill

## Safety

- never mutates files directly (except target export path)
- no destructive git behavior
- explicit failures on ambiguous routing

## Future extensions

- non-interactive strict mode profile
- builder partial-result capture
- structured metrics + telemetry output
- policy files per workspace
