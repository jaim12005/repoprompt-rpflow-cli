from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


class RPFlowError(RuntimeError):
    pass


@dataclass
class RunResult:
    code: int
    stdout: str
    stderr: str


class RPCLI:
    def __init__(self, binary: str = "rp-cli") -> None:
        self.binary = binary
        if not shutil.which(self.binary):
            raise RPFlowError("rp-cli not found in PATH")

    def run(self, args: List[str], timeout: int = 60) -> RunResult:
        try:
            proc = subprocess.run(
                [self.binary] + args,
                text=True,
                capture_output=True,
                timeout=timeout,
            )
            return RunResult(proc.returncode, proc.stdout, proc.stderr)
        except subprocess.TimeoutExpired as e:
            stdout = e.stdout or ""
            stderr = (e.stderr or "")
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            if stderr and not stderr.endswith("\n"):
                stderr += "\n"
            stderr += f"TIMEOUT: rp-cli exceeded {timeout}s"
            return RunResult(124, stdout, stderr)

    def run_exec(
        self,
        command: str,
        window: Optional[int] = None,
        tab: Optional[str] = None,
        timeout: int = 60,
        raw_json: bool = False,
    ) -> RunResult:
        args: List[str] = []
        if raw_json:
            args.append("--raw-json")
        if window is not None:
            args += ["-w", str(window)]
        if tab:
            args += ["-t", tab]
        args += ["-e", command]
        return self.run(args, timeout=timeout)

    def run_call(
        self,
        tool: str,
        json_arg: str = "",
        window: Optional[int] = None,
        tab: Optional[str] = None,
        timeout: int = 60,
    ) -> RunResult:
        args: List[str] = []
        if window is not None:
            args += ["-w", str(window)]
        if tab:
            args += ["-t", tab]
        args += ["-c", tool]
        if json_arg:
            args += ["-j", json_arg]
        return self.run(args, timeout=timeout)

    def list_windows(self, timeout: int = 30) -> List[Dict[str, Any]]:
        res = self.run(["--raw-json", "-e", "windows"], timeout=timeout)
        if res.code != 0:
            raise RPFlowError((res.stderr or res.stdout).strip() or "failed to list windows")
        try:
            payload = json.loads(res.stdout)
            if isinstance(payload, list):
                return payload
            raise RPFlowError("unexpected windows payload")
        except json.JSONDecodeError as e:
            raise RPFlowError(f"failed to parse windows json: {e}") from e

    def list_tabs(self, window: Optional[int] = None, timeout: int = 30) -> List[Dict[str, Any]]:
        args: List[str] = ["--raw-json"]
        if window is not None:
            args += ["-w", str(window)]
        args += ["-e", "tabs"]
        res = self.run(args, timeout=timeout)
        if res.code != 0:
            raise RPFlowError((res.stderr or res.stdout).strip() or "failed to list tabs")
        try:
            payload = json.loads(res.stdout)
            tabs = payload.get("tabs") if isinstance(payload, dict) else None
            if isinstance(tabs, list):
                return tabs
            raise RPFlowError("unexpected tabs payload")
        except json.JSONDecodeError as e:
            raise RPFlowError(f"failed to parse tabs json: {e}") from e

    def safe_workspace_switch(
        self,
        workspace: str,
        window: Optional[int],
        tab: Optional[str],
        timeout: int = 60,
    ) -> None:
        res = self.run_exec(
            f'workspace switch "{workspace}"',
            window=window,
            tab=tab,
            timeout=timeout,
            raw_json=False,
        )
        merged = (res.stdout or "") + "\n" + (res.stderr or "")
        if res.code == 0:
            return
        if "already on workspace" in merged.lower():
            return
        raise RPFlowError(merged.strip() or f"workspace switch failed ({res.code})")


def resolve_window(
    windows: List[Dict[str, Any]],
    requested: Optional[int],
    remembered: Optional[int],
) -> Optional[int]:
    ids = [int(w.get("windowID")) for w in windows if w.get("windowID") is not None]
    if requested is not None:
        if requested not in ids:
            raise RPFlowError(f"requested window {requested} not found; available: {ids}")
        return requested
    if remembered is not None and remembered in ids:
        return remembered
    if len(ids) == 1:
        return ids[0]
    if len(ids) == 0:
        raise RPFlowError("no Repo Prompt windows are open")
    raise RPFlowError(f"multiple windows detected ({ids}); pass --window")


def ensure_tab_exists(tabs: List[Dict[str, Any]], tab: str) -> None:
    names = [str(t.get("name")) for t in tabs if t.get("name")]
    if tab not in names:
        raise RPFlowError(f"tab '{tab}' not found; available: {names}")
