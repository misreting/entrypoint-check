"""Command-line entry point for entrypoint-check."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from entrypoint_check.dynamic_check import DEFAULT_TIMEOUT, check_dynamic
from entrypoint_check.model import CheckMode, Finding, Status
from entrypoint_check.sources import SourceError, load_entry_points
from entrypoint_check.static_check import check_static

EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_COULD_NOT_ANALYSE = 2

_FAILING_STATUSES = {Status.MISSING, Status.NOT_CALLABLE, Status.ERROR}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="entrypoint-check",
        description=(
            "Verify that packaged entry points (console_scripts, "
            "[project.scripts], custom groups) actually resolve to a "
            "real, callable, importable attribute."
        ),
    )
    parser.add_argument(
        "target",
        help=(
            "What to check: a path to a built .whl file, a path to a "
            "pyproject.toml, a path to a setup.cfg, a directory containing "
            "either, or 'installed:<distribution-name>' for an already-"
            "installed package."
        ),
    )
    parser.add_argument(
        "--import",
        dest="use_import",
        action="store_true",
        help=(
            "Actually import each target in an isolated subprocess instead "
            "of the default AST-only static check. Definitive, but runs "
            "third-party top-level code."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Per-entry-point subprocess timeout in seconds for --import (default: {DEFAULT_TIMEOUT}).",
    )
    parser.add_argument(
        "--src",
        action="append",
        default=[],
        metavar="PATH",
        help="Extra module search root, e.g. a src/ directory. Repeatable.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of text.",
    )
    return parser


def _format_text(findings: list[Finding], mode: CheckMode) -> str:
    if not findings:
        return f"entrypoint-check ({mode.value}): no entry points found."

    lines = [f"entrypoint-check ({mode.value}): {len(findings)} entry point(s) checked"]
    for finding in findings:
        ep = finding.entry_point
        symbol = {
            Status.OK: "OK",
            Status.UNVERIFIABLE: "?? ",
            Status.MISSING: "FAIL",
            Status.NOT_CALLABLE: "FAIL",
            Status.ERROR: "ERR",
        }[finding.status]
        lines.append(f"  [{symbol}] {ep.group}: {ep.name} = {ep.spec}")
        lines.append(f"        {finding.message}")
    ok = sum(1 for f in findings if f.status == Status.OK)
    unverifiable = sum(1 for f in findings if f.status == Status.UNVERIFIABLE)
    failing = sum(1 for f in findings if f.status in _FAILING_STATUSES)
    lines.append(f"summary: {ok} ok, {unverifiable} unverifiable, {failing} failing")
    return "\n".join(lines)


def _format_json(findings: list[Finding], mode: CheckMode) -> str:
    payload = {
        "mode": mode.value,
        "count": len(findings),
        "findings": [
            {
                "name": f.entry_point.name,
                "group": f.entry_point.group,
                "spec": f.entry_point.spec,
                "source": f.entry_point.source,
                "status": f.status.value,
                "message": f.message,
            }
            for f in findings
        ],
    }
    return json.dumps(payload, indent=2)


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        entry_points, discovered_roots = load_entry_points(args.target)
    except SourceError as exc:
        print(f"entrypoint-check: could not analyse '{args.target}': {exc}", file=sys.stderr)
        return EXIT_COULD_NOT_ANALYSE

    search_roots = [Path(p) for p in args.src] + discovered_roots

    if not entry_points:
        print(f"entrypoint-check: no entry points declared in '{args.target}'.")
        return EXIT_CLEAN

    mode = CheckMode.IMPORT if args.use_import else CheckMode.STATIC

    if mode is CheckMode.IMPORT:
        findings = check_dynamic(entry_points, search_roots, timeout=args.timeout)
    else:
        if not search_roots:
            print(
                f"entrypoint-check: could not analyse '{args.target}': "
                "no source roots to search (pass --src, or point at a directory "
                "with pyproject.toml/setup.cfg, or use --import against a wheel/installed package).",
                file=sys.stderr,
            )
            return EXIT_COULD_NOT_ANALYSE
        findings = check_static(entry_points, search_roots)

    if args.json:
        print(_format_json(findings, mode))
    else:
        print(_format_text(findings, mode))

    if any(f.status in _FAILING_STATUSES for f in findings):
        return EXIT_FINDINGS
    return EXIT_CLEAN


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
