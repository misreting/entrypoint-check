from __future__ import annotations

from pathlib import Path

import pytest

from entrypoint_check.model import EntryPoint
from entrypoint_check.sources import (
    SourceError,
    load_entry_points,
    load_entry_points_from_pyproject,
    load_entry_points_from_setup_cfg,
    load_entry_points_from_wheel,
    parse_entry_points_txt,
)


def test_parse_entry_points_txt_basic() -> None:
    text = """\
[console_scripts]
mycli = pkg.cli:main

[gui_scripts]
mygui = pkg.gui:run

[pkg.plugins]
plugin_a = pkg.plugins.a:register
"""
    entries = parse_entry_points_txt(text)
    assert len(entries) == 3
    by_name = {e.name: e for e in entries}
    assert by_name["mycli"].module == "pkg.cli"
    assert by_name["mycli"].attr == "main"
    assert by_name["mycli"].group == "console_scripts"
    assert by_name["plugin_a"].group == "pkg.plugins"


def test_parse_entry_points_txt_extras_and_dotted_attr() -> None:
    text = """\
[console_scripts]
mycli = pkg.cli:App.run [extra1,extra2]
"""
    entries = parse_entry_points_txt(text)
    assert len(entries) == 1
    ep = entries[0]
    assert ep.module == "pkg.cli"
    assert ep.attr == "App.run"
    assert ep.extras == ("extra1", "extra2")
    assert ep.spec == "pkg.cli:App.run"


def test_parse_entry_points_txt_module_only_no_attr() -> None:
    text = """\
[some_group]
whole_module = pkg.plugin_module
"""
    entries = parse_entry_points_txt(text)
    assert entries[0].module == "pkg.plugin_module"
    assert entries[0].attr == ""
    assert entries[0].spec == "pkg.plugin_module"


def test_parse_entry_points_txt_malformed_raises() -> None:
    with pytest.raises(SourceError):
        parse_entry_points_txt("not-a-valid-ini-line-without-brackets")


def test_load_from_pyproject_scripts_and_custom_groups(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """\
[project]
name = "demo"
version = "0.1.0"

[project.scripts]
mycli = "demo.cli:main"

[project.gui-scripts]
mygui = "demo.gui:main"

[project.entry-points."demo.plugins"]
plugin_a = "demo.plugins:register"
""",
        encoding="utf-8",
    )
    entries = load_entry_points_from_pyproject(pyproject)
    groups = {e.name: e.group for e in entries}
    assert groups["mycli"] == "console_scripts"
    assert groups["mygui"] == "gui_scripts"
    assert groups["plugin_a"] == "demo.plugins"


def test_load_from_pyproject_missing_file(tmp_path: Path) -> None:
    with pytest.raises(SourceError):
        load_entry_points_from_pyproject(tmp_path / "nope.toml")


def test_load_from_pyproject_non_string_value_raises(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """\
[project]
name = "demo"
version = "0.1.0"

[project.scripts]
mycli = 123
""",
        encoding="utf-8",
    )
    with pytest.raises(SourceError):
        load_entry_points_from_pyproject(pyproject)


def test_load_from_setup_cfg(tmp_path: Path) -> None:
    setup_cfg = tmp_path / "setup.cfg"
    setup_cfg.write_text(
        """\
[options.entry_points]
console_scripts =
    mycli = demo.cli:main
    othercli = demo.cli:other
""",
        encoding="utf-8",
    )
    entries = load_entry_points_from_setup_cfg(setup_cfg)
    assert len(entries) == 2
    by_name = {e.name: e for e in entries}
    assert by_name["mycli"].module == "demo.cli"
    assert by_name["mycli"].attr == "main"
    assert by_name["othercli"].attr == "other"


def test_load_from_setup_cfg_no_section(tmp_path: Path) -> None:
    setup_cfg = tmp_path / "setup.cfg"
    setup_cfg.write_text("[metadata]\nname = demo\n", encoding="utf-8")
    assert load_entry_points_from_setup_cfg(setup_cfg) == []


def test_load_from_wheel(tmp_path: Path) -> None:
    import zipfile

    wheel_path = tmp_path / "demo-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel_path, "w") as zf:
        zf.writestr(
            "demo-0.1.0.dist-info/entry_points.txt",
            "[console_scripts]\nmycli = demo.cli:main\n",
        )
        zf.writestr("demo-0.1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: demo\n")
        zf.writestr("demo/__init__.py", "")
        zf.writestr("demo/cli.py", "def main() -> None:\n    pass\n")

    entries = load_entry_points_from_wheel(wheel_path)
    assert len(entries) == 1
    assert entries[0].name == "mycli"
    assert entries[0].module == "demo.cli"
    assert entries[0].attr == "main"


def test_load_from_wheel_no_entry_points_file(tmp_path: Path) -> None:
    import zipfile

    wheel_path = tmp_path / "demo-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel_path, "w") as zf:
        zf.writestr("demo-0.1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: demo\n")
    assert load_entry_points_from_wheel(wheel_path) == []


def test_load_from_wheel_missing_file(tmp_path: Path) -> None:
    with pytest.raises(SourceError):
        load_entry_points_from_wheel(tmp_path / "nope.whl")


def test_load_from_wheel_not_a_zip(tmp_path: Path) -> None:
    bad = tmp_path / "bad.whl"
    bad.write_text("not a zip file", encoding="utf-8")
    with pytest.raises(SourceError):
        load_entry_points_from_wheel(bad)


def test_load_entry_points_dispatch_directory(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """\
[project]
name = "demo"
version = "0.1.0"

[project.scripts]
mycli = "demo.cli:main"
""",
        encoding="utf-8",
    )
    src = tmp_path / "src"
    src.mkdir()
    entries, roots = load_entry_points(str(tmp_path))
    assert len(entries) == 1
    assert tmp_path in roots
    assert src in roots


def test_load_entry_points_dispatch_directory_with_nothing_raises(tmp_path: Path) -> None:
    with pytest.raises(SourceError):
        load_entry_points(str(tmp_path))


def test_load_entry_points_dispatch_unknown_target(tmp_path: Path) -> None:
    unknown = tmp_path / "weird.xyz"
    unknown.write_text("", encoding="utf-8")
    with pytest.raises(SourceError):
        load_entry_points(str(unknown))


def test_load_entry_points_installed_self() -> None:
    # entrypoint-check installs itself in editable mode for the test run,
    # so its own console script is a real, always-available fixture.
    entries, roots = load_entry_points("installed:entrypoint-check")
    names = {e.name: e for e in entries}
    assert "entrypoint-check" in names
    ep = names["entrypoint-check"]
    assert ep.module == "entrypoint_check.cli"
    assert ep.attr == "main"
    assert roots  # some site/src directory was found
