from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

from . import __version__
from .rpcli import RPCLI, RPFlowError, ensure_tab_exists, resolve_window
from .state import RPState, load_state, save_state


DEFAULT_TAB = "T1"
DEFAULT_WORKSPACE = "GitHub"


def _split_paths(value: str) -> List[str]:
    return [p.strip() for p in value.split(",") if p.strip()]


def _build_selection_export_cmd(paths: List[str], out: str) -> str:
    chain = ["select clear"]
    for p in paths:
        chain.append(f'select add "{p}"')
    chain.append(f'prompt export "{out}"')
    return " && ".join(chain)


def _build_plan_export_cmd(paths: List[str], task: str, out: str) -> str:
    chain = ["select clear"]
    for p in paths:
        chain.append(f'select add "{p}"')
    chain.append(f'builder "{task}" --type plan')
    chain.append(f'prompt export "{out}"')
    return " && ".join(chain)


def _print_out(res):
    if res.stdout:
        print(res.stdout, end="")
    if res.stderr:
        print(res.stderr, file=sys.stderr, end="")


def _prepare_routing(args, rp: RPCLI, state: RPState):
    windows = rp.list_windows()

    strict = bool(getattr(args, "strict", False))
    if strict:
        if getattr(args, "window", None) is None:
            raise RPFlowError("strict mode requires --window")
        if not getattr(args, "tab", None):
            raise RPFlowError("strict mode requires --tab")
        if not getattr(args, "workspace", None):
            raise RPFlowError("strict mode requires --workspace")

    remembered_window = None if strict else state.last_window
    window = resolve_window(windows, getattr(args, "window", None), remembered_window)

    tab = getattr(args, "tab", None) or (None if strict else state.last_tab) or DEFAULT_TAB
    tabs = rp.list_tabs(window=window)
    ensure_tab_exists(tabs, tab)

    workspace = (
        getattr(args, "workspace", None)
        or (None if strict else state.last_workspace)
        or DEFAULT_WORKSPACE
    )
    return window, tab, workspace


def _maybe_save_state(ok: bool, window: int, tab: str, workspace: str) -> None:
    if ok:
        save_state(RPState(window, tab, workspace))


def _run_smoke_checks(rp: RPCLI, window: int, tab: str, timeout: int):
    checks = []
    tabs = rp.run_exec("tabs", window=window, tab=tab, timeout=timeout)
    checks.append(("tabs", tabs.code == 0, tabs))

    context = rp.run_exec(
        "context --include tokens,selection,prompt --path-display relative",
        window=window,
        tab=tab,
        timeout=timeout,
    )
    checks.append(("context", context.code == 0, context))

    schema = rp.run(["--tools-schema"], timeout=timeout)
    checks.append(("tools-schema", schema.code == 0, schema))
    return checks


def cmd_doctor(args) -> int:
    rp = RPCLI()
    state = load_state()

    windows = rp.list_windows()
    print(f"rpflow {__version__}")
    print(f"windows: {len(windows)}")

    window = None
    try:
        remembered = None if args.strict else state.last_window
        window = resolve_window(windows, args.window, remembered)
        print(f"selected_window: {window}")
    except RPFlowError as e:
        print(f"window_resolution: {e}")

    if window is not None:
        tabs = rp.list_tabs(window=window)
        print(f"tabs_in_window_{window}: {', '.join([t.get('name','?') for t in tabs])}")

    schema = rp.run(["--tools-schema"], timeout=args.timeout)
    if schema.code == 0:
        print("tools_schema: ok")
    else:
        print("tools_schema: failed")
        _print_out(schema)

    return 0


def cmd_exec(args) -> int:
    rp = RPCLI()
    state = load_state()
    window, tab, workspace = _prepare_routing(args, rp, state)

    rp.safe_workspace_switch(workspace, window, tab, timeout=args.timeout)
    res = rp.run_exec(
        args.command,
        window=window,
        tab=tab,
        timeout=args.timeout,
        raw_json=args.raw_json,
    )
    _print_out(res)
    _maybe_save_state(res.code == 0, window, tab, workspace)
    return res.code


def cmd_call(args) -> int:
    rp = RPCLI()
    state = load_state()
    window, tab, workspace = _prepare_routing(args, rp, state)

    rp.safe_workspace_switch(workspace, window, tab, timeout=args.timeout)
    res = rp.run_call(
        args.tool,
        json_arg=args.json_arg,
        window=window,
        tab=tab,
        timeout=args.timeout,
    )
    _print_out(res)
    _maybe_save_state(res.code == 0, window, tab, workspace)
    return res.code


def cmd_tools_schema(args) -> int:
    rp = RPCLI()
    if args.group:
        res = rp.run(["--tools-schema=" + args.group], timeout=args.timeout)
    else:
        res = rp.run(["--tools-schema"], timeout=args.timeout)
    _print_out(res)
    return res.code


def cmd_export(args) -> int:
    rp = RPCLI()
    state = load_state()
    window, tab, workspace = _prepare_routing(args, rp, state)

    rp.safe_workspace_switch(workspace, window, tab, timeout=args.timeout)
    out = Path(args.out)
    if out.exists():
        out.unlink()

    cmd = _build_selection_export_cmd(_split_paths(args.select_set), str(out))
    res = rp.run_exec(cmd, window=window, tab=tab, timeout=args.timeout)
    _print_out(res)
    _maybe_save_state(res.code == 0, window, tab, workspace)
    return res.code


def cmd_plan_export(args) -> int:
    rp = RPCLI()
    state = load_state()
    window, tab, workspace = _prepare_routing(args, rp, state)

    rp.safe_workspace_switch(workspace, window, tab, timeout=args.timeout)
    out = Path(args.out)
    if out.exists():
        out.unlink()

    cmd = _build_plan_export_cmd(_split_paths(args.select_set), args.task, str(out))
    res = rp.run_exec(cmd, window=window, tab=tab, timeout=args.timeout)
    if res.code == 124 and args.fallback_export_on_timeout:
        fallback = _build_selection_export_cmd(_split_paths(args.select_set), str(out))
        fb = rp.run_exec(fallback, window=window, tab=tab, timeout=args.timeout)
        _print_out(fb)
        _maybe_save_state(fb.code == 0, window, tab, workspace)
        return fb.code

    _print_out(res)
    _maybe_save_state(res.code == 0, window, tab, workspace)
    return res.code


def cmd_smoke(args) -> int:
    rp = RPCLI()
    state = load_state()
    window, tab, workspace = _prepare_routing(args, rp, state)

    rp.safe_workspace_switch(workspace, window, tab, timeout=args.timeout)

    checks = _run_smoke_checks(rp, window, tab, args.timeout)
    failed = [name for name, ok, _ in checks if not ok]
    for name, ok, _ in checks:
        print(f"{name}: {'ok' if ok else 'fail'}")

    if failed:
        for name, ok, res in checks:
            if not ok:
                print(f"\n--- {name} output ---", file=sys.stderr)
                _print_out(res)
        return 1

    _maybe_save_state(True, window, tab, workspace)
    return 0


def cmd_autopilot(args) -> int:
    rp = RPCLI()
    state = load_state()
    window, tab, workspace = _prepare_routing(args, rp, state)

    rp.safe_workspace_switch(workspace, window, tab, timeout=args.timeout)

    checks = _run_smoke_checks(rp, window, tab, args.preflight_timeout)
    failed = [name for name, ok, _ in checks if not ok]
    for name, ok, _ in checks:
        print(f"preflight:{name}: {'ok' if ok else 'fail'}")

    if failed:
        for name, ok, res in checks:
            if not ok:
                print(f"\n--- preflight {name} output ---", file=sys.stderr)
                _print_out(res)
        return 1

    out = Path(args.out)
    if out.exists():
        out.unlink()

    cmd = _build_plan_export_cmd(_split_paths(args.select_set), args.task, str(out))
    res = rp.run_exec(cmd, window=window, tab=tab, timeout=args.timeout)

    if res.code == 124 and args.fallback_export_on_timeout:
        fallback = _build_selection_export_cmd(_split_paths(args.select_set), str(out))
        fb = rp.run_exec(fallback, window=window, tab=tab, timeout=args.timeout)
        _print_out(fb)
        _maybe_save_state(fb.code == 0, window, tab, workspace)
        return fb.code

    _print_out(res)
    _maybe_save_state(res.code == 0, window, tab, workspace)
    return res.code


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rpflow", description="Reliable Repo Prompt automation wrapper")
    p.add_argument("--version", action="version", version=f"rpflow {__version__}")

    sub = p.add_subparsers(dest="cmd", required=True)

    pd = sub.add_parser("doctor", help="check rp-cli connectivity and routing")
    pd.add_argument("--window", type=int)
    pd.add_argument("--timeout", type=int, default=30)
    pd.add_argument("--strict", action="store_true", help="require explicit routing args")
    pd.set_defaults(func=cmd_doctor)

    pe = sub.add_parser("exec", help="run rp-cli exec command with safe routing")
    pe.add_argument("-e", "--command", required=True)
    pe.add_argument("--window", type=int)
    pe.add_argument("--tab")
    pe.add_argument("--workspace")
    pe.add_argument("--timeout", type=int, default=60)
    pe.add_argument("--raw-json", action="store_true")
    pe.add_argument("--strict", action="store_true", help="require explicit --window/--tab/--workspace")
    pe.set_defaults(func=cmd_exec)

    pc = sub.add_parser("call", help="run rp-cli tool call (-c/-j) with safe routing")
    pc.add_argument("--tool", required=True, help="tool name for -c")
    pc.add_argument("--json-arg", default="", help="arg for -j (inline JSON, @file, or @-)")
    pc.add_argument("--window", type=int)
    pc.add_argument("--tab")
    pc.add_argument("--workspace")
    pc.add_argument("--timeout", type=int, default=60)
    pc.add_argument("--strict", action="store_true", help="require explicit --window/--tab/--workspace")
    pc.set_defaults(func=cmd_call)

    ps = sub.add_parser("tools-schema", help="print Repo Prompt tools schema")
    ps.add_argument("--group", default="")
    ps.add_argument("--timeout", type=int, default=60)
    ps.set_defaults(func=cmd_tools_schema)

    px = sub.add_parser("export", help="selection -> prompt export")
    px.add_argument("--select-set", required=True)
    px.add_argument("--out", required=True)
    px.add_argument("--window", type=int)
    px.add_argument("--tab")
    px.add_argument("--workspace")
    px.add_argument("--timeout", type=int, default=90)
    px.add_argument("--strict", action="store_true", help="require explicit --window/--tab/--workspace")
    px.set_defaults(func=cmd_export)

    pp = sub.add_parser("plan-export", help="selection -> builder plan -> prompt export")
    pp.add_argument("--select-set", required=True)
    pp.add_argument("--task", required=True)
    pp.add_argument("--out", required=True)
    pp.add_argument("--window", type=int)
    pp.add_argument("--tab")
    pp.add_argument("--workspace")
    pp.add_argument("--timeout", type=int, default=120)
    pp.add_argument("--fallback-export-on-timeout", action="store_true")
    pp.add_argument("--strict", action="store_true", help="require explicit --window/--tab/--workspace")
    pp.set_defaults(func=cmd_plan_export)

    pa = sub.add_parser("autopilot", help="preflight + plan-export in one command")
    pa.add_argument("--select-set", required=True)
    pa.add_argument("--task", required=True)
    pa.add_argument("--out", required=True)
    pa.add_argument("--window", type=int)
    pa.add_argument("--tab")
    pa.add_argument("--workspace")
    pa.add_argument("--timeout", type=int, default=120, help="plan/export timeout")
    pa.add_argument("--preflight-timeout", type=int, default=45)
    pa.add_argument("--fallback-export-on-timeout", action="store_true")
    pa.add_argument("--strict", action="store_true", help="require explicit --window/--tab/--workspace")
    pa.set_defaults(func=cmd_autopilot)

    pm = sub.add_parser("smoke", help="run quick end-to-end health checks")
    pm.add_argument("--window", type=int)
    pm.add_argument("--tab")
    pm.add_argument("--workspace")
    pm.add_argument("--timeout", type=int, default=45)
    pm.add_argument("--strict", action="store_true", help="require explicit --window/--tab/--workspace")
    pm.set_defaults(func=cmd_smoke)

    return p


def main(argv: List[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except RPFlowError as e:
        print(f"rpflow error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
