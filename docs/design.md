# rpflow Design

## Objectives

1. Deterministic automation for Repo Prompt in OpenClaw workflows
2. Preserve full `rp-cli` power while reducing orchestration friction
3. Keep implementation simple, auditable, and dependency-light

## Core architecture

- `rpflow.rpcli`: subprocess wrapper around `rp-cli`
- `rpflow.state`: persistent last-good routing state
- `rpflow.cli`: argparse command surface

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

- `exec`: hard timeout
- `plan-export`: hard timeout on builder chain
- optional fallback: export selection-only prompt when builder times out

## Safety

- never mutates files directly (except target export path)
- no destructive git behavior
- explicit failures on ambiguous routing

## Future extensions

- non-interactive strict mode profile
- builder partial-result capture
- structured metrics + telemetry output
- policy files per workspace
