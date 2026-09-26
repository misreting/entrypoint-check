from __future__ import annotations

from pathlib import Path

from entrypoint_check.model import CheckMode, EntryPoint, Status
from entrypoint_check.static_check import check_entry_point_static, find_module_file


def _write_module(tmp_path: Path, relpath: str, source: str) -> Path:
    file_path = tmp_path.joinpath(*relpath.split("/"))
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(source, encoding="utf-8")
    return file_path


def _ep(module: str, attr: str, group: str = "console_scripts", name: str = "mycli") -> EntryPoint:
    return EntryPoint(name=name, group=group, module=module, attr=attr, source="test")


def test_ok_function(tmp_path: Path) -> None:
    _write_module(tmp_path, "pkg/__init__.py", "")
    _write_module(tmp_path, "pkg/cli.py", "def main() -> None:\n    pass\n")
    finding = check_entry_point_static(_ep("pkg.cli", "main"), [tmp_path])
    assert finding.status == Status.OK
    assert finding.mode == CheckMode.STATIC


def test_missing_attribute_is_the_headline_bug(tmp_path: Path) -> None:
    """This is THE bug entrypoint-check exists to catch: a typo'd entry point
    attribute that both hatchling and setuptools happily build into a wheel."""

    _write_module(tmp_path, "pkg/__init__.py", "")
    _write_module(tmp_path, "pkg/cli.py", "def main() -> None:\n    pass\n")
    finding = check_entry_point_static(_ep("pkg.cli", "main_typo"), [tmp_path])
    assert finding.status == Status.MISSING
    assert "main_typo" in finding.message


def test_missing_module(tmp_path: Path) -> None:
    finding = check_entry_point_static(_ep("pkg.does_not_exist", "main"), [tmp_path])
    assert finding.status == Status.MISSING


def test_module_only_entry_point_ok(tmp_path: Path) -> None:
    _write_module(tmp_path, "pkg/__init__.py", "")
    _write_module(tmp_path, "pkg/plugin.py", "x = 1\n")
    finding = check_entry_point_static(_ep("pkg.plugin", ""), [tmp_path])
    assert finding.status == Status.OK


def test_class_method_dotted_attr_ok(tmp_path: Path) -> None:
    _write_module(tmp_path, "pkg/__init__.py", "")
    _write_module(
        tmp_path,
        "pkg/cli.py",
        "class App:\n    def run(self) -> None:\n        pass\n",
    )
    finding = check_entry_point_static(_ep("pkg.cli", "App.run"), [tmp_path])
    assert finding.status == Status.OK


def test_class_method_dotted_attr_missing(tmp_path: Path) -> None:
    _write_module(tmp_path, "pkg/__init__.py", "")
    _write_module(
        tmp_path,
        "pkg/cli.py",
        "class App:\n    def run(self) -> None:\n        pass\n",
    )
    finding = check_entry_point_static(_ep("pkg.cli", "App.run_typo"), [tmp_path])
    assert finding.status == Status.MISSING


def test_class_missing(tmp_path: Path) -> None:
    _write_module(tmp_path, "pkg/__init__.py", "")
    _write_module(tmp_path, "pkg/cli.py", "class App:\n    pass\n")
    finding = check_entry_point_static(_ep("pkg.cli", "OtherApp.run"), [tmp_path])
    assert finding.status == Status.MISSING


def test_plain_assignment_is_unverifiable_not_ok(tmp_path: Path) -> None:
    _write_module(tmp_path, "pkg/__init__.py", "")
    _write_module(tmp_path, "pkg/cli.py", "main = some_factory()\n")
    finding = check_entry_point_static(_ep("pkg.cli", "main"), [tmp_path])
    assert finding.status == Status.UNVERIFIABLE


def test_reexport_via_plain_import_is_found(tmp_path: Path) -> None:
    _write_module(tmp_path, "pkg/__init__.py", "")
    _write_module(tmp_path, "pkg/impl.py", "def real_main() -> None:\n    pass\n")
    _write_module(tmp_path, "pkg/cli.py", "from pkg.impl import real_main as main\n")
    finding = check_entry_point_static(_ep("pkg.cli", "main"), [tmp_path])
    # A re-exported name is a real, present binding -- static mode can't
    # prove it's callable without following the import, so it is
    # UNVERIFIABLE rather than a false OK or a false MISSING.
    assert finding.status == Status.UNVERIFIABLE


def test_star_import_makes_missing_name_unverifiable(tmp_path: Path) -> None:
    _write_module(tmp_path, "pkg/__init__.py", "")
    _write_module(tmp_path, "pkg/impl.py", "def main() -> None:\n    pass\n")
    _write_module(tmp_path, "pkg/cli.py", "from pkg.impl import *\n")
    finding = check_entry_point_static(_ep("pkg.cli", "main"), [tmp_path])
    assert finding.status == Status.UNVERIFIABLE
    assert "star import" in finding.message


def test_dynamic_getattr_makes_missing_name_unverifiable(tmp_path: Path) -> None:
    _write_module(
        tmp_path,
        "pkg/cli.py",
        "def __getattr__(name):\n    return lambda: None\n",
    )
    finding = check_entry_point_static(_ep("pkg.cli", "main"), [tmp_path])
    assert finding.status == Status.UNVERIFIABLE
    assert "__getattr__" in finding.message


def test_conditional_definition_in_if_branch_is_found(tmp_path: Path) -> None:
    _write_module(
        tmp_path,
        "pkg/cli.py",
        "import sys\nif sys.platform == 'win32':\n    def main() -> None:\n        pass\nelse:\n    def main() -> None:\n        pass\n",
    )
    finding = check_entry_point_static(_ep("pkg.cli", "main"), [tmp_path])
    assert finding.status == Status.OK


def test_syntax_error_reported_as_error(tmp_path: Path) -> None:
    _write_module(tmp_path, "pkg/cli.py", "def main(:\n")
    finding = check_entry_point_static(_ep("pkg.cli", "main"), [tmp_path])
    assert finding.status == Status.ERROR


def test_find_module_file_init_py(tmp_path: Path) -> None:
    _write_module(tmp_path, "pkg/__init__.py", "")
    found = find_module_file("pkg", [tmp_path])
    assert found == tmp_path / "pkg" / "__init__.py"


def test_find_module_file_submodule(tmp_path: Path) -> None:
    _write_module(tmp_path, "pkg/__init__.py", "")
    _write_module(tmp_path, "pkg/sub.py", "")
    found = find_module_file("pkg.sub", [tmp_path])
    assert found == tmp_path / "pkg" / "sub.py"


def test_find_module_file_not_found(tmp_path: Path) -> None:
    assert find_module_file("pkg.nope", [tmp_path]) is None
