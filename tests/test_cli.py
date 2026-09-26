from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from entrypoint_check.cli import EXIT_CLEAN, EXIT_COULD_NOT_ANALYSE, EXIT_FINDINGS, run


def _make_project(tmp_path: Path, *, attr: str) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        f"""\
[project]
name = "demo"
version = "0.1.0"

[project.scripts]
mycli = "demo.cli:{attr}"
""",
        encoding="utf-8",
    )
    src = tmp_path / "src" / "demo"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "cli.py").write_text("def main() -> None:\n    pass\n", encoding="utf-8")
    return tmp_path


def test_cli_clean_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = _make_project(tmp_path, attr="main")
    code = run([str(project)])
    out = capsys.readouterr().out
    assert code == EXIT_CLEAN
    assert "1 ok" in out


def test_cli_findings_exit_code_on_typo(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = _make_project(tmp_path, attr="main_typo")
    code = run([str(project)])
    out = capsys.readouterr().out
    assert code == EXIT_FINDINGS
    assert "main_typo" in out
    assert "1 failing" in out


def test_cli_could_not_analyse_bad_target(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = run([str(tmp_path / "does-not-exist")])
    err = capsys.readouterr().err
    assert code == EXIT_COULD_NOT_ANALYSE
    assert "could not analyse" in err


def test_cli_no_entry_points_is_clean(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    code = run([str(tmp_path)])
    out = capsys.readouterr().out
    assert code == EXIT_CLEAN
    assert "no entry points" in out


def test_cli_json_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = _make_project(tmp_path, attr="main_typo")
    code = run([str(project), "--json"])
    out = capsys.readouterr().out
    assert code == EXIT_FINDINGS
    payload = json.loads(out)
    assert payload["mode"] == "static"
    assert payload["count"] == 1
    assert payload["findings"][0]["status"] == "missing"


def test_cli_import_mode_catches_typo(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = _make_project(tmp_path, attr="main_typo")
    code = run([str(project), "--import"])
    out = capsys.readouterr().out
    assert code == EXIT_FINDINGS
    assert "import" in out.splitlines()[0]


def test_cli_smoke_as_installed_console_script() -> None:
    """Real end-to-end smoke test: invoke the actual installed
    `entrypoint-check` console script (not the Python function) against
    this very package's own installed entry point, as a subprocess -- the
    same way a user would run it from a shell."""

    result = subprocess.run(
        [sys.executable, "-m", "entrypoint_check.cli", "installed:entrypoint-check", "--import"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == EXIT_CLEAN, (result.stdout, result.stderr)
    assert "entrypoint-check" in result.stdout
    assert "1 ok" in result.stdout
