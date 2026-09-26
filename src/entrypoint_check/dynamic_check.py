"""Subprocess-isolated entry point checking: actually imports the target.

This is the definitive check -- it imports the target module for real and
calls ``callable()`` on the resolved attribute, which is the only way to be
certain an entry point works. It is opt-in (``--import``) because importing
arbitrary third-party code runs that code's top-level statements, which can:

- have side effects (open sockets, write files, spawn threads)
- call ``sys.exit()`` (argparse-style CLIs sometimes do this at import time
  if misconfigured)
- hang forever (a broken blocking call at module scope)

To contain all three, each entry point is checked in its own fresh
``python -c`` subprocess with a hard wall-clock timeout. A subprocess that
hangs is killed; one that calls ``sys.exit`` only takes itself down; one
with side effects only pollutes its own throwaway process. Nothing runs in
the process that is doing the checking.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from entrypoint_check.model import CheckMode, EntryPoint, Finding, Status

DEFAULT_TIMEOUT = 10.0

_CHECK_SCRIPT = """\
import importlib
import sys

module_name = sys.argv[1]
attr_path = sys.argv[2]

try:
    mod = importlib.import_module(module_name)
except BaseException as exc:  # noqa: BLE001 - we want to report ANY failure
    print(f"IMPORT_ERROR:{type(exc).__name__}:{exc}", file=sys.stderr)
    sys.exit(3)

if not attr_path:
    print("OK: module import succeeded")
    sys.exit(0)

target = mod
try:
    for part in attr_path.split("."):
        target = getattr(target, part)
except AttributeError as exc:
    print(f"ATTR_ERROR:{exc}", file=sys.stderr)
    sys.exit(4)

if not callable(target):
    print(f"NOT_CALLABLE:{type(target).__name__}", file=sys.stderr)
    sys.exit(5)

print(f"OK: {module_name}:{attr_path} resolves to {target!r} and is callable")
sys.exit(0)
"""


def check_entry_point_dynamic(
    entry_point: EntryPoint,
    search_roots: list[Path],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    python_executable: str | None = None,
) -> Finding:
    """Check one entry point by importing it in an isolated subprocess."""

    python = python_executable or sys.executable
    env = None
    if search_roots:
        import os

        env = os.environ.copy()
        existing = env.get("PYTHONPATH", "")
        joined = ":".join(str(r) for r in search_roots)
        env["PYTHONPATH"] = f"{joined}:{existing}" if existing else joined

    try:
        # Note: deliberately NOT python's `-I` (isolated) mode -- that also
        # strips PYTHONPATH, which is how `search_roots` (e.g. a package's
        # `src/` layout) gets into the subprocess in the first place.
        # Isolation here means "separate OS process with a timeout", not
        # "no environment inheritance".
        proc = subprocess.run(
            [python, "-c", _CHECK_SCRIPT, entry_point.module, entry_point.attr],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return Finding(
            entry_point=entry_point,
            status=Status.ERROR,
            message=(
                f"importing '{entry_point.module}' did not finish within {timeout}s "
                "(the module may block at import time)"
            ),
            mode=CheckMode.IMPORT,
        )
    except OSError as exc:
        return Finding(
            entry_point=entry_point,
            status=Status.ERROR,
            message=f"could not launch subprocess: {exc}",
            mode=CheckMode.IMPORT,
        )

    stderr = proc.stderr.strip()
    stdout = proc.stdout.strip()

    if proc.returncode == 0:
        return Finding(
            entry_point=entry_point,
            status=Status.OK,
            message=stdout or "OK",
            mode=CheckMode.IMPORT,
        )
    if proc.returncode == 3:
        return Finding(
            entry_point=entry_point,
            status=Status.MISSING,
            message=f"module '{entry_point.module}' failed to import: {stderr}",
            mode=CheckMode.IMPORT,
        )
    if proc.returncode == 4:
        return Finding(
            entry_point=entry_point,
            status=Status.MISSING,
            message=f"attribute '{entry_point.attr}' not found on '{entry_point.module}': {stderr}",
            mode=CheckMode.IMPORT,
        )
    if proc.returncode == 5:
        return Finding(
            entry_point=entry_point,
            status=Status.NOT_CALLABLE,
            message=f"'{entry_point.spec}' resolved but is not callable: {stderr}",
            mode=CheckMode.IMPORT,
        )
    # Any other exit (e.g. the module called sys.exit(1) itself, or segfaulted).
    return Finding(
        entry_point=entry_point,
        status=Status.ERROR,
        message=(
            f"subprocess for '{entry_point.spec}' exited {proc.returncode} "
            f"unexpectedly; stderr: {stderr or '(empty)'}"
        ),
        mode=CheckMode.IMPORT,
    )


def check_dynamic(
    entry_points: list[EntryPoint],
    search_roots: list[Path],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    python_executable: str | None = None,
) -> list[Finding]:
    """Check every entry point dynamically, each in its own subprocess."""

    return [
        check_entry_point_dynamic(
            ep, search_roots, timeout=timeout, python_executable=python_executable
        )
        for ep in entry_points
    ]
