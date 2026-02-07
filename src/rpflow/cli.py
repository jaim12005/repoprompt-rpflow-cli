from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from . import __version__
from .rpcli import RPCLI, RPFlowError, RunResult, ensure_tab_exists, resolve_window
from .state import RPState, load_state, save_state


DEFAULT_TAB = "T1"
DEFAULT_WORKSPACE = "GitHub"

PROFILE_TIMEOUTS = {
    "fast": {
        "doctor": 20,
        "exec": 45,
        "call": 45,
        "tools_schema": 30,
        "export": 60,
        "plan_export": 90,
        "autopilot": 90,
        "preflight": 25,
        "smoke": 25,
    },
    "normal": {
        "doctor": 30,
        "exec": 60,
        "call": 60,
        "tools_schema": 60,
        "export": 90,
        "plan_export": 120,
        "autopilot": 120,
        "preflight": 45,
        "smoke": 45,
    },
    "deep": {
        "doctor": 45,
        "exec": 90,
        "call": 90,
        "tools_schema": 90,
        "export": 150,
        "plan_export": 240,
        "autopilot": 240,
        "preflight": 90,
        "smoke": 90,
    },
}


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


def _print_out(res: RunResult):
    if res.stdout:
        print(res.stdout, end="")
    if res.stderr:
        print(res.stderr, file=sys.stderr, end="")


def _tail(text: str, limit: int = 600) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[-limit:]


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _effective_timeout(args, key: str, fallback: int) -> int:
    explicit = getattr(args, "timeout", None)
    if explicit is not None and int(explicit) > 0:
        return int(explicit)
    profile = getattr(args, "profile", "normal")
    return int(PROFILE_TIMEOUTS.get(profile, PROFILE_TIMEOUTS["normal"]).get(key, fallback))


def _effective_preflight_timeout(args, fallback: int) -> int:
    explicit = getattr(args, "preflight_timeout", None)
    if explicit is not None and int(explicit) > 0:
        return int(explicit)
    profile = getattr(args, "profile", "normal")
    return int(PROFILE_TIMEOUTS.get(profile, PROFILE_TIMEOUTS["normal"]).get("preflight", fallback))


def _retry_timeout(args, base_timeout: int) -> int:
    explicit = getattr(args, "retry_timeout", None)
    if explicit is not None and int(explicit) > 0:
        return int(explicit)
    scale = float(getattr(args, "retry_timeout_scale", 1.5) or 1.5)
    scale = max(1.0, scale)
    return max(base_timeout + 1, int(base_timeout * scale))


def _should_retry_builder(args, res: RunResult) -> bool:
    if not bool(getattr(args, "retry_on_timeout", False)):
        return False
    return bool(res.timed_out or res.signal == 9)


def _stage_classification(res: RunResult, builder_related: bool = False) -> str:
    if res.code == 0:
        if res.classification == "already_on_workspace":
            return "workspace_already_selected"
        return "ok"
    if res.timed_out:
        return "builder_timeout" if builder_related else "timeout"
    if res.signal is not None:
        if res.signal == 9:
            return "killed_sigkill"
        return f"killed_sig{res.signal}"
    return res.classification or "error"


def _emit_failure_hint(stage: str, res: RunResult, builder_related: bool = False) -> None:
    if res.code == 0:
        return
    cls = _stage_classification(res, builder_related=builder_related)
    print(f"rpflow stage {stage}: {cls} (code={res.code})", file=sys.stderr)


def _start_report(command: str) -> Dict[str, Any]:
    return {
        "report_version": 1,
        "command": command,
        "started_at": _now_iso_utc(),
        "stages": [],
        "_t0": time.time(),
    }


def _add_stage(
    report: Dict[str, Any],
    name: str,
    res: RunResult,
    *,
    builder_related: bool = False,
    extra: Dict[str, Any] | None = None,
) -> None:
    stage = {
        "name": name,
        "classification": _stage_classification(res, builder_related=builder_related),
        "code": res.code,
        "timed_out": bool(res.timed_out),
        "signal": res.signal,
        "stdout_chars": len(res.stdout or ""),
        "stderr_chars": len(res.stderr or ""),
        "stdout_tail": _tail(res.stdout or ""),
        "stderr_tail": _tail(res.stderr or ""),
    }
    if extra:
        stage.update(extra)
    report["stages"].append(stage)


def _write_report(
    args,
    report: Dict[str, Any],
    *,
    exit_code: int,
    routing: Dict[str, Any] | None = None,
    extra: Dict[str, Any] | None = None,
) -> None:
    path = getattr(args, "report_json", "")
    if not path:
        return

    payload = dict(report)
    t0 = float(payload.pop("_t0", time.time()))
    payload["finished_at"] = _now_iso_utc()
    payload["duration_ms"] = int((time.time() - t0) * 1000)
    payload["exit_code"] = int(exit_code)
    payload["ok"] = bool(exit_code == 0)
    if routing:
        payload["routing"] = routing
    if extra:
        payload.update(extra)

    out = Path(path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_exception_report(args, command: str, err: Exception) -> None:
    path = getattr(args, "report_json", "")
    if not path:
        return
    payload = {
        "report_version": 1,
        "command": command,
        "started_at": _now_iso_utc(),
        "finished_at": _now_iso_utc(),
        "duration_ms": 0,
        "exit_code": 2,
        "ok": False,
        "error": str(err),
        "classification": "rpflow_error",
        "stages": [],
    }
    out = Path(path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


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


def _attempt_resume_from_export(
    resume_path_raw: str,
    out_path: Path,
    report: Dict[str, Any],
    reason: str,
) -> bool:
    if not resume_path_raw:
        return False

    resume_path = Path(resume_path_raw).expanduser()
    if not resume_path.exists() or not resume_path.is_file():
        report["resume"] = {
            "attempted": True,
            "used": False,
            "reason": reason,
            "source": str(resume_path),
            "source_exists": False,
        }
        return False

    out_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(resume_path, out_path)
    report["resume"] = {
        "attempted": True,
        "used": True,
        "reason": reason,
        "source": str(resume_path),
        "destination": str(out_path),
        "bytes": out_path.stat().st_size,
    }
    print(f"rpflow resume: reused export from {resume_path}", file=sys.stderr)
    return True


def cmd_doctor(args) -> int:
    report = _start_report("doctor")
    rp = RPCLI()
    state = load_state()
    timeout = _effective_timeout(args, "doctor", 30)

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

    schema = rp.run(["--tools-schema"], timeout=timeout)
    _add_stage(report, "tools-schema", schema)
    if schema.code == 0:
        print("tools_schema: ok")
    else:
        print("tools_schema: failed")
        _emit_failure_hint("tools-schema", schema)
        _print_out(schema)

    _write_report(
        args,
        report,
        exit_code=0,
        extra={
            "windows_count": len(windows),
            "selected_window": window,
            "timeout_seconds": timeout,
            "profile": getattr(args, "profile", "normal"),
        },
    )
    return 0


def cmd_exec(args) -> int:
    report = _start_report("exec")
    rp = RPCLI()
    state = load_state()
    window, tab, workspace = _prepare_routing(args, rp, state)
    timeout = _effective_timeout(args, "exec", 60)

    ws = rp.safe_workspace_switch(workspace, window, tab, timeout=timeout)
    _add_stage(report, "workspace_switch", ws)

    res = rp.run_exec(
        args.command,
        window=window,
        tab=tab,
        timeout=timeout,
        raw_json=args.raw_json,
    )
    _add_stage(report, "exec", res, builder_related=("builder " in args.command))
    if res.code != 0:
        _emit_failure_hint("exec", res, builder_related=("builder " in args.command))

    _print_out(res)
    _maybe_save_state(res.code == 0, window, tab, workspace)
    _write_report(
        args,
        report,
        exit_code=res.code,
        routing={"window": window, "tab": tab, "workspace": workspace},
        extra={
            "command_text": args.command,
            "timeout_seconds": timeout,
            "profile": getattr(args, "profile", "normal"),
        },
    )
    return res.code


def cmd_call(args) -> int:
    report = _start_report("call")
    rp = RPCLI()
    state = load_state()
    window, tab, workspace = _prepare_routing(args, rp, state)
    timeout = _effective_timeout(args, "call", 60)

    ws = rp.safe_workspace_switch(workspace, window, tab, timeout=timeout)
    _add_stage(report, "workspace_switch", ws)

    res = rp.run_call(
        args.tool,
        json_arg=args.json_arg,
        window=window,
        tab=tab,
        timeout=timeout,
    )
    _add_stage(report, "call", res)
    if res.code != 0:
        _emit_failure_hint("call", res)

    _print_out(res)
    _maybe_save_state(res.code == 0, window, tab, workspace)
    _write_report(
        args,
        report,
        exit_code=res.code,
        routing={"window": window, "tab": tab, "workspace": workspace},
        extra={
            "tool": args.tool,
            "timeout_seconds": timeout,
            "profile": getattr(args, "profile", "normal"),
        },
    )
    return res.code


def cmd_tools_schema(args) -> int:
    report = _start_report("tools-schema")
    rp = RPCLI()
    timeout = _effective_timeout(args, "tools_schema", 60)
    if args.group:
        res = rp.run(["--tools-schema=" + args.group], timeout=timeout)
    else:
        res = rp.run(["--tools-schema"], timeout=timeout)
    _add_stage(report, "tools-schema", res)
    if res.code != 0:
        _emit_failure_hint("tools-schema", res)
    _print_out(res)
    _write_report(
        args,
        report,
        exit_code=res.code,
        extra={
            "group": args.group,
            "timeout_seconds": timeout,
            "profile": getattr(args, "profile", "normal"),
        },
    )
    return res.code


def cmd_export(args) -> int:
    report = _start_report("export")
    rp = RPCLI()
    state = load_state()
    window, tab, workspace = _prepare_routing(args, rp, state)
    timeout = _effective_timeout(args, "export", 90)

    ws = rp.safe_workspace_switch(workspace, window, tab, timeout=timeout)
    _add_stage(report, "workspace_switch", ws)

    out = Path(args.out)
    if out.exists():
        out.unlink()

    cmd = _build_selection_export_cmd(_split_paths(args.select_set), str(out))
    res = rp.run_exec(cmd, window=window, tab=tab, timeout=timeout)
    _add_stage(report, "export", res)
    if res.code != 0:
        _emit_failure_hint("export", res)

    _print_out(res)
    _maybe_save_state(res.code == 0, window, tab, workspace)
    _write_report(
        args,
        report,
        exit_code=res.code,
        routing={"window": window, "tab": tab, "workspace": workspace},
        extra={
            "out_path": str(out),
            "out_exists": out.exists(),
            "out_bytes": out.stat().st_size if out.exists() else 0,
            "timeout_seconds": timeout,
            "profile": getattr(args, "profile", "normal"),
        },
    )
    return res.code


def cmd_plan_export(args) -> int:
    report = _start_report("plan-export")
    rp = RPCLI()
    state = load_state()
    window, tab, workspace = _prepare_routing(args, rp, state)
    timeout = _effective_timeout(args, "plan_export", 120)

    ws = rp.safe_workspace_switch(workspace, window, tab, timeout=timeout)
    _add_stage(report, "workspace_switch", ws)

    out = Path(args.out)
    if out.exists():
        out.unlink()

    cmd = _build_plan_export_cmd(_split_paths(args.select_set), args.task, str(out))
    res = rp.run_exec(cmd, window=window, tab=tab, timeout=timeout)
    _add_stage(report, "plan_export", res, builder_related=True)

    retry_used = False
    retry_timeout = None
    if _should_retry_builder(args, res):
        retry_used = True
        retry_timeout = _retry_timeout(args, timeout)
        retry_res = rp.run_exec(cmd, window=window, tab=tab, timeout=retry_timeout)
        _add_stage(
            report,
            "plan_export_retry",
            retry_res,
            builder_related=True,
            extra={"retry_timeout_seconds": retry_timeout},
        )
        if retry_res.code != 0:
            _emit_failure_hint("plan_export_retry", retry_res, builder_related=True)
        res = retry_res

    fallback_used = False
    resume_used = False

    if (res.code == 124 or res.signal == 9) and args.fallback_export_on_timeout:
        fallback_used = True
        fallback = _build_selection_export_cmd(_split_paths(args.select_set), str(out))
        fb = rp.run_exec(fallback, window=window, tab=tab, timeout=timeout)
        _add_stage(report, "fallback_export", fb)
        if fb.code != 0:
            _emit_failure_hint("fallback_export", fb)
            resume_used = _attempt_resume_from_export(
                args.resume_from_export,
                out,
                report,
                reason="fallback_export_failed_after_timeout",
            )
            code = 0 if resume_used else fb.code
        else:
            _print_out(fb)
            code = fb.code
    elif res.code != 0:
        _emit_failure_hint("plan_export", res, builder_related=True)
        if res.timed_out:
            resume_used = _attempt_resume_from_export(
                args.resume_from_export,
                out,
                report,
                reason="plan_export_timeout_without_fallback",
            )
        else:
            resume_used = _attempt_resume_from_export(
                args.resume_from_export,
                out,
                report,
                reason="plan_export_failed",
            )
        code = 0 if resume_used else res.code
    else:
        _print_out(res)
        code = res.code

    _maybe_save_state(code == 0, window, tab, workspace)
    _write_report(
        args,
        report,
        exit_code=code,
        routing={"window": window, "tab": tab, "workspace": workspace},
        extra={
            "fallback_used": fallback_used,
            "resume_used": resume_used,
            "retry_used": retry_used,
            "retry_timeout_seconds": retry_timeout,
            "timeout_seconds": timeout,
            "profile": getattr(args, "profile", "normal"),
            "out_path": str(out),
            "out_exists": out.exists(),
            "out_bytes": out.stat().st_size if out.exists() else 0,
        },
    )
    return code


def cmd_smoke(args) -> int:
    report = _start_report("smoke")
    rp = RPCLI()
    state = load_state()
    window, tab, workspace = _prepare_routing(args, rp, state)
    timeout = _effective_timeout(args, "smoke", 45)

    ws = rp.safe_workspace_switch(workspace, window, tab, timeout=timeout)
    _add_stage(report, "workspace_switch", ws)

    checks = _run_smoke_checks(rp, window, tab, timeout)
    failed = [name for name, ok, _ in checks if not ok]
    for name, ok, res in checks:
        print(f"{name}: {'ok' if ok else 'fail'}")
        _add_stage(report, f"smoke_{name}", res)

    if failed:
        for name, ok, res in checks:
            if not ok:
                print(f"\n--- {name} output ---", file=sys.stderr)
                _emit_failure_hint(name, res)
                _print_out(res)
        _write_report(
            args,
            report,
            exit_code=1,
            routing={"window": window, "tab": tab, "workspace": workspace},
            extra={
                "failed_checks": failed,
                "timeout_seconds": timeout,
                "profile": getattr(args, "profile", "normal"),
            },
        )
        return 1

    _maybe_save_state(True, window, tab, workspace)
    _write_report(
        args,
        report,
        exit_code=0,
        routing={"window": window, "tab": tab, "workspace": workspace},
        extra={
            "timeout_seconds": timeout,
            "profile": getattr(args, "profile", "normal"),
        },
    )
    return 0


def cmd_autopilot(args) -> int:
    report = _start_report("autopilot")
    rp = RPCLI()
    state = load_state()
    window, tab, workspace = _prepare_routing(args, rp, state)
    timeout = _effective_timeout(args, "autopilot", 120)
    preflight_timeout = _effective_preflight_timeout(args, 45)

    ws = rp.safe_workspace_switch(workspace, window, tab, timeout=timeout)
    _add_stage(report, "workspace_switch", ws)

    checks = _run_smoke_checks(rp, window, tab, preflight_timeout)
    failed = [name for name, ok, _ in checks if not ok]
    for name, ok, res in checks:
        print(f"preflight:{name}: {'ok' if ok else 'fail'}")
        _add_stage(report, f"preflight_{name}", res)

    out = Path(args.out)

    if failed:
        for name, ok, res in checks:
            if not ok:
                print(f"\n--- preflight {name} output ---", file=sys.stderr)
                _emit_failure_hint(f"preflight_{name}", res)
                _print_out(res)

        resumed = _attempt_resume_from_export(
            args.resume_from_export,
            out,
            report,
            reason="preflight_failed",
        )
        code = 0 if resumed else 1
        _write_report(
            args,
            report,
            exit_code=code,
            routing={"window": window, "tab": tab, "workspace": workspace},
            extra={
                "failed_checks": failed,
                "resume_used": resumed,
                "timeout_seconds": timeout,
                "preflight_timeout_seconds": preflight_timeout,
                "profile": getattr(args, "profile", "normal"),
            },
        )
        return code

    if out.exists():
        out.unlink()

    cmd = _build_plan_export_cmd(_split_paths(args.select_set), args.task, str(out))
    res = rp.run_exec(cmd, window=window, tab=tab, timeout=timeout)
    _add_stage(report, "autopilot_plan_export", res, builder_related=True)

    retry_used = False
    retry_timeout = None
    if _should_retry_builder(args, res):
        retry_used = True
        retry_timeout = _retry_timeout(args, timeout)
        retry_res = rp.run_exec(cmd, window=window, tab=tab, timeout=retry_timeout)
        _add_stage(
            report,
            "autopilot_plan_retry",
            retry_res,
            builder_related=True,
            extra={"retry_timeout_seconds": retry_timeout},
        )
        if retry_res.code != 0:
            _emit_failure_hint("autopilot_plan_retry", retry_res, builder_related=True)
        res = retry_res

    fallback_used = False
    resume_used = False

    if (res.code == 124 or res.signal == 9) and args.fallback_export_on_timeout:
        fallback_used = True
        fallback = _build_selection_export_cmd(_split_paths(args.select_set), str(out))
        fb = rp.run_exec(fallback, window=window, tab=tab, timeout=timeout)
        _add_stage(report, "fallback_export", fb)
        if fb.code != 0:
            _emit_failure_hint("fallback_export", fb)
            resume_used = _attempt_resume_from_export(
                args.resume_from_export,
                out,
                report,
                reason="fallback_export_failed_after_timeout",
            )
            code = 0 if resume_used else fb.code
        else:
            _print_out(fb)
            code = fb.code
    elif res.code != 0:
        _emit_failure_hint("autopilot_plan_export", res, builder_related=True)
        if res.timed_out:
            resume_used = _attempt_resume_from_export(
                args.resume_from_export,
                out,
                report,
                reason="autopilot_timeout_without_fallback",
            )
        else:
            resume_used = _attempt_resume_from_export(
                args.resume_from_export,
                out,
                report,
                reason="autopilot_plan_failed",
            )
        code = 0 if resume_used else res.code
    else:
        _print_out(res)
        code = res.code

    _maybe_save_state(code == 0, window, tab, workspace)
    _write_report(
        args,
        report,
        exit_code=code,
        routing={"window": window, "tab": tab, "workspace": workspace},
        extra={
            "fallback_used": fallback_used,
            "resume_used": resume_used,
            "retry_used": retry_used,
            "retry_timeout_seconds": retry_timeout,
            "timeout_seconds": timeout,
            "preflight_timeout_seconds": preflight_timeout,
            "profile": getattr(args, "profile", "normal"),
            "out_path": str(out),
            "out_exists": out.exists(),
            "out_bytes": out.stat().st_size if out.exists() else 0,
        },
    )
    return code


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rpflow", description="Reliable Repo Prompt automation wrapper")
    p.add_argument("--version", action="version", version=f"rpflow {__version__}")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--report-json", default="", help="write structured run report JSON to path")
    common.add_argument(
        "--profile",
        choices=["fast", "normal", "deep"],
        default="normal",
        help="timeout/reliability profile (default: normal)",
    )

    sub = p.add_subparsers(dest="cmd", required=True)

    pd = sub.add_parser("doctor", parents=[common], help="check rp-cli connectivity and routing")
    pd.add_argument("--window", type=int)
    pd.add_argument("--timeout", type=int, default=None)
    pd.add_argument("--strict", action="store_true", help="require explicit routing args")
    pd.set_defaults(func=cmd_doctor)

    pe = sub.add_parser("exec", parents=[common], help="run rp-cli exec command with safe routing")
    pe.add_argument("-e", "--command", required=True)
    pe.add_argument("--window", type=int)
    pe.add_argument("--tab")
    pe.add_argument("--workspace")
    pe.add_argument("--timeout", type=int, default=None)
    pe.add_argument("--raw-json", action="store_true")
    pe.add_argument("--strict", action="store_true", help="require explicit --window/--tab/--workspace")
    pe.set_defaults(func=cmd_exec)

    pc = sub.add_parser("call", parents=[common], help="run rp-cli tool call (-c/-j) with safe routing")
    pc.add_argument("--tool", required=True, help="tool name for -c")
    pc.add_argument("--json-arg", default="", help="arg for -j (inline JSON, @file, or @-)")
    pc.add_argument("--window", type=int)
    pc.add_argument("--tab")
    pc.add_argument("--workspace")
    pc.add_argument("--timeout", type=int, default=None)
    pc.add_argument("--strict", action="store_true", help="require explicit --window/--tab/--workspace")
    pc.set_defaults(func=cmd_call)

    ps = sub.add_parser("tools-schema", parents=[common], help="print Repo Prompt tools schema")
    ps.add_argument("--group", default="")
    ps.add_argument("--timeout", type=int, default=None)
    ps.set_defaults(func=cmd_tools_schema)

    px = sub.add_parser("export", parents=[common], help="selection -> prompt export")
    px.add_argument("--select-set", required=True)
    px.add_argument("--out", required=True)
    px.add_argument("--window", type=int)
    px.add_argument("--tab")
    px.add_argument("--workspace")
    px.add_argument("--timeout", type=int, default=None)
    px.add_argument("--strict", action="store_true", help="require explicit --window/--tab/--workspace")
    px.set_defaults(func=cmd_export)

    pp = sub.add_parser("plan-export", parents=[common], help="selection -> builder plan -> prompt export")
    pp.add_argument("--select-set", required=True)
    pp.add_argument("--task", required=True)
    pp.add_argument("--out", required=True)
    pp.add_argument("--window", type=int)
    pp.add_argument("--tab")
    pp.add_argument("--workspace")
    pp.add_argument("--timeout", type=int, default=None)
    pp.add_argument("--fallback-export-on-timeout", action="store_true")
    pp.add_argument("--retry-on-timeout", action="store_true", help="retry one builder run on timeout/SIGKILL")
    pp.add_argument(
        "--retry-timeout",
        type=int,
        default=None,
        help="retry timeout seconds (defaults to scaled base timeout)",
    )
    pp.add_argument(
        "--retry-timeout-scale",
        type=float,
        default=1.5,
        help="retry timeout multiplier when --retry-timeout is not set (default: 1.5)",
    )
    pp.add_argument(
        "--resume-from-export",
        default="",
        help="optional existing export path to reuse if plan/fallback fails",
    )
    pp.add_argument("--strict", action="store_true", help="require explicit --window/--tab/--workspace")
    pp.set_defaults(func=cmd_plan_export)

    pa = sub.add_parser("autopilot", parents=[common], help="preflight + plan-export in one command")
    pa.add_argument("--select-set", required=True)
    pa.add_argument("--task", required=True)
    pa.add_argument("--out", required=True)
    pa.add_argument("--window", type=int)
    pa.add_argument("--tab")
    pa.add_argument("--workspace")
    pa.add_argument("--timeout", type=int, default=None, help="plan/export timeout")
    pa.add_argument("--preflight-timeout", type=int, default=None)
    pa.add_argument("--fallback-export-on-timeout", action="store_true")
    pa.add_argument("--retry-on-timeout", action="store_true", help="retry one builder run on timeout/SIGKILL")
    pa.add_argument(
        "--retry-timeout",
        type=int,
        default=None,
        help="retry timeout seconds (defaults to scaled base timeout)",
    )
    pa.add_argument(
        "--retry-timeout-scale",
        type=float,
        default=1.5,
        help="retry timeout multiplier when --retry-timeout is not set (default: 1.5)",
    )
    pa.add_argument(
        "--resume-from-export",
        default="",
        help="optional existing export path to reuse if preflight/plan/fallback fails",
    )
    pa.add_argument("--strict", action="store_true", help="require explicit --window/--tab/--workspace")
    pa.set_defaults(func=cmd_autopilot)

    pm = sub.add_parser("smoke", parents=[common], help="run quick end-to-end health checks")
    pm.add_argument("--window", type=int)
    pm.add_argument("--tab")
    pm.add_argument("--workspace")
    pm.add_argument("--timeout", type=int, default=None)
    pm.add_argument("--strict", action="store_true", help="require explicit --window/--tab/--workspace")
    pm.set_defaults(func=cmd_smoke)

    return p


def main(argv: List[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except RPFlowError as e:
        _write_exception_report(args, getattr(args, "cmd", "unknown"), e)
        print(f"rpflow error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
