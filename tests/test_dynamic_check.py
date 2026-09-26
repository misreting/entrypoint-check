from __future__ import annotations

from pathlib import Path

from entrypoint_check.model import CheckMode, EntryPoint, Status
from entrypoint_check.dynamic_check import check_entry_point_dynamic


def _write_module(tmp_path: Path, relpath: str, source: str) -> Path:
    file_path = tmp_path.joinpath(*relpath.split("/"))
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(source, encoding="utf-8")
    return file_path


def _ep(module: str, attr: str) -> EntryPoint:
    return EntryPoint(name="mycli", group="console_scripts", module=module, attr=attr, source="test")


def test_ok(tmp_path: Path) -> None:
    _write_module(tmp_path, "pkg/__init__.py", "")
    _write_module(tmp_path, "pkg/cli.py", "def main():\n    return 0\n")
    finding = check_entry_point_dynamic(_ep("pkg.cli", "main"), [tmp_path])
    assert finding.status == Status.OK
    assert finding.mode == CheckMode.IMPORT


def test_missing_attribute_real_import_error(tmp_path: Path) -> None:
    """The headline scenario, but for real: the module imports fine, the
    attribute genuinely does not exist, and we get an ImportError from a
    real subprocess -- exactly what an end user sees when they run the
    broken console script."""

    _write_module(tmp_path, "pkg/__init__.py", "")
    _write_module(tmp_path, "pkg/cli.py", "def main():\n    return 0\n")
    finding = check_entry_point_dynamic(_ep("pkg.cli", "main_typo"), [tmp_path])
    assert finding.status == Status.MISSING
    assert "main_typo" in finding.message


def test_module_not_found(tmp_path: Path) -> None:
    finding = check_entry_point_dynamic(_ep("pkg.nope", "main"), [tmp_path])
    assert finding.status == Status.MISSING


def test_not_callable(tmp_path: Path) -> None:
    _write_module(tmp_path, "pkg/__init__.py", "")
    _write_module(tmp_path, "pkg/cli.py", "main = 42\n")
    finding = check_entry_point_dynamic(_ep("pkg.cli", "main"), [tmp_path])
    assert finding.status == Status.NOT_CALLABLE


def test_module_level_sys_exit_is_contained(tmp_path: Path) -> None:
    """A module that calls sys.exit() at import time (e.g. a misconfigured
    argparse script) must not take down the checking process -- only its own
    throwaway subprocess."""

    _write_module(tmp_path, "pkg/__init__.py", "")
    _write_module(tmp_path, "pkg/cli.py", "import sys\nsys.exit(7)\n\ndef main():\n    pass\n")
    finding = check_entry_point_dynamic(_ep("pkg.cli", "main"), [tmp_path])
    # sys.exit() raises SystemExit, which our checker script catches as an
    # import failure (the module never finished importing) -- reported as
    # MISSING, not a crash of the checking process itself.
    assert finding.status == Status.MISSING
    assert "SystemExit" in finding.message


def test_side_effect_at_import_time_is_contained(tmp_path: Path) -> None:
    """A module with a real side effect (writing a file) at import time runs,
    but only inside its own subprocess -- proving isolation, not just that we
    catch an exception."""

    marker = tmp_path / "side_effect_marker.txt"
    _write_module(tmp_path, "pkg/__init__.py", "")
    _write_module(
        tmp_path,
        "pkg/cli.py",
        f"open({str(marker)!r}, 'w').write('ran')\n\ndef main():\n    pass\n",
    )
    finding = check_entry_point_dynamic(_ep("pkg.cli", "main"), [tmp_path])
    assert finding.status == Status.OK
    assert marker.read_text() == "ran"


def test_hang_at_import_time_hits_timeout(tmp_path: Path) -> None:
    _write_module(tmp_path, "pkg/__init__.py", "")
    _write_module(
        tmp_path,
        "pkg/cli.py",
        "import time\ntime.sleep(30)\n\ndef main():\n    pass\n",
    )
    finding = check_entry_point_dynamic(_ep("pkg.cli", "main"), [tmp_path], timeout=1.0)
    assert finding.status == Status.ERROR
    assert "did not finish within" in finding.message


def test_dotted_attribute_ok(tmp_path: Path) -> None:
    _write_module(tmp_path, "pkg/__init__.py", "")
    _write_module(
        tmp_path,
        "pkg/cli.py",
        "class App:\n    @staticmethod\n    def run():\n        return 0\n",
    )
    finding = check_entry_point_dynamic(_ep("pkg.cli", "App.run"), [tmp_path])
    assert finding.status == Status.OK
