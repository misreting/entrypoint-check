"""Collect entry point declarations from the places Python packaging puts them.

Four sources are supported:

- A built wheel's ``*.dist-info/entry_points.txt`` (the ground truth: what
  will actually be installed).
- A source tree's ``pyproject.toml`` (``[project.scripts]``,
  ``[project.gui-scripts]``, ``[project.entry-points.<group>]``).
- A source tree's ``setup.cfg`` (``[options.entry_points]``).
- An already-installed distribution, via :mod:`importlib.metadata`.
"""

from __future__ import annotations

import configparser
import re
import tempfile
import tomllib
import zipfile
from importlib import metadata as importlib_metadata
from pathlib import Path

from entrypoint_check.model import EntryPoint

_EXTRAS_RE = re.compile(
    r"^(?P<spec>[^\[\]\s]+)\s*(?:\[(?P<extras>[^\]]*)\])?\s*$"
)


class SourceError(Exception):
    """Raised when a source cannot be located or parsed at all.

    This is distinct from an individual entry point being broken -- it means
    "there is nothing here to check", which the CLI maps to exit code 2.
    """


def _split_spec(raw: str) -> tuple[str, str, tuple[str, ...]]:
    """Split ``module:attr [extra1,extra2]`` into (module, attr, extras)."""

    match = _EXTRAS_RE.match(raw.strip())
    if not match:
        raise SourceError(f"cannot parse entry point value: {raw!r}")
    spec = match.group("spec")
    extras_raw = match.group("extras") or ""
    extras = tuple(e.strip() for e in extras_raw.split(",") if e.strip())
    if ":" in spec:
        module, attr = spec.split(":", 1)
    else:
        module, attr = spec, ""
    if not module:
        raise SourceError(f"entry point value has no module: {raw!r}")
    return module, attr, extras


def parse_entry_points_txt(text: str, source: str = "entry_points.txt") -> list[EntryPoint]:
    """Parse the INI-like ``entry_points.txt`` format used inside wheels."""

    # entry_points.txt values must NOT be interpolated (they can contain '%'),
    # and option (entry point) names are case-sensitive.
    parser = configparser.ConfigParser(delimiters=("=",), strict=False, interpolation=None)
    parser.optionxform = str  # type: ignore[method-assign,assignment]
    try:
        parser.read_string(text, source=source)
    except configparser.Error as exc:
        raise SourceError(f"could not parse {source}: {exc}") from exc

    entries: list[EntryPoint] = []
    for group in parser.sections():
        for name, raw_value in parser.items(group):
            if raw_value is None:
                continue
            module, attr, extras = _split_spec(raw_value)
            entries.append(
                EntryPoint(
                    name=name,
                    group=group,
                    module=module,
                    attr=attr,
                    extras=extras,
                    source=source,
                )
            )
    return entries


def load_entry_points_from_wheel(wheel_path: Path) -> list[EntryPoint]:
    """Read ``entry_points.txt`` out of a built ``.whl`` file."""

    if not wheel_path.is_file():
        raise SourceError(f"wheel not found: {wheel_path}")
    try:
        with zipfile.ZipFile(wheel_path) as zf:
            candidates = [
                n
                for n in zf.namelist()
                if n.endswith(".dist-info/entry_points.txt")
            ]
            if not candidates:
                return []
            if len(candidates) > 1:
                raise SourceError(
                    f"wheel {wheel_path} contains multiple dist-info directories: {candidates}"
                )
            text = zf.read(candidates[0]).decode("utf-8")
    except zipfile.BadZipFile as exc:
        raise SourceError(f"{wheel_path} is not a valid wheel (zip): {exc}") from exc
    return parse_entry_points_txt(text, source=f"{wheel_path.name}!{candidates[0]}")


def extract_wheel(wheel_path: Path) -> Path:
    """Unzip a wheel into a fresh temporary directory and return its path.

    Used so static (AST) mode can search a wheel's shipped ``.py`` source
    the same way it searches a ``src/`` layout on disk -- most pure-Python
    wheels ship source, not bytecode. The directory is not cleaned up
    automatically; callers running as a short-lived CLI process can let the
    OS reclaim it.
    """

    if not wheel_path.is_file():
        raise SourceError(f"wheel not found: {wheel_path}")
    dest = Path(tempfile.mkdtemp(prefix="entrypoint-check-wheel-"))
    try:
        with zipfile.ZipFile(wheel_path) as zf:
            zf.extractall(dest)
    except zipfile.BadZipFile as exc:
        raise SourceError(f"{wheel_path} is not a valid wheel (zip): {exc}") from exc
    return dest


def load_entry_points_from_pyproject(pyproject_path: Path) -> list[EntryPoint]:
    """Read entry points declared under ``[project]`` in a ``pyproject.toml``."""

    if not pyproject_path.is_file():
        raise SourceError(f"pyproject.toml not found: {pyproject_path}")
    try:
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise SourceError(f"could not parse {pyproject_path}: {exc}") from exc

    project = data.get("project", {})
    if not isinstance(project, dict):
        raise SourceError(f"{pyproject_path}: [project] table is malformed")

    entries: list[EntryPoint] = []
    source = str(pyproject_path)

    def _add(group: str, table: object) -> None:
        if not isinstance(table, dict):
            return
        for name, raw_value in table.items():
            if not isinstance(raw_value, str):
                raise SourceError(
                    f"{pyproject_path}: entry point {group}.{name} is not a string"
                )
            module, attr, extras = _split_spec(raw_value)
            entries.append(
                EntryPoint(
                    name=name,
                    group=group,
                    module=module,
                    attr=attr,
                    extras=extras,
                    source=source,
                )
            )

    _add("console_scripts", project.get("scripts"))
    _add("gui_scripts", project.get("gui-scripts"))

    entry_points_table = project.get("entry-points", {})
    if isinstance(entry_points_table, dict):
        for group_name, table in entry_points_table.items():
            _add(group_name, table)

    return entries


def load_entry_points_from_setup_cfg(setup_cfg_path: Path) -> list[EntryPoint]:
    """Read ``[options.entry_points]`` from a ``setup.cfg``."""

    if not setup_cfg_path.is_file():
        raise SourceError(f"setup.cfg not found: {setup_cfg_path}")
    parser = configparser.ConfigParser(delimiters=("=",), strict=False)
    parser.optionxform = str  # type: ignore[method-assign,assignment]
    try:
        parser.read(setup_cfg_path, encoding="utf-8")
    except configparser.Error as exc:
        raise SourceError(f"could not parse {setup_cfg_path}: {exc}") from exc

    if not parser.has_section("options.entry_points"):
        return []

    entries: list[EntryPoint] = []
    source = str(setup_cfg_path)
    for group, block in parser.items("options.entry_points"):
        for line in block.splitlines():
            line = line.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if "=" not in line:
                raise SourceError(
                    f"{setup_cfg_path}: malformed entry point line in [{group}]: {line!r}"
                )
            name, raw_value = line.split("=", 1)
            module, attr, extras = _split_spec(raw_value)
            entries.append(
                EntryPoint(
                    name=name.strip(),
                    group=group.strip(),
                    module=module,
                    attr=attr,
                    extras=extras,
                    source=source,
                )
            )
    return entries


def load_entry_points_from_installed(distribution_name: str) -> list[EntryPoint]:
    """Read entry points from an already-installed distribution's metadata."""

    try:
        dist = importlib_metadata.distribution(distribution_name)
    except importlib_metadata.PackageNotFoundError as exc:
        raise SourceError(f"distribution not installed: {distribution_name}") from exc

    entries: list[EntryPoint] = []
    source = f"installed:{distribution_name}"
    for ep in dist.entry_points:
        # importlib.metadata.EntryPoint already parses "module:attr [extras]"
        # for us -- .module, .attr and .extras are properties on it.
        entries.append(
            EntryPoint(
                name=ep.name,
                group=ep.group,
                module=ep.module,
                attr=ep.attr or "",
                extras=tuple(ep.extras or ()),
                source=source,
            )
        )
    return entries


def source_roots_for_installed(distribution_name: str) -> list[Path]:
    """Best-effort list of directories to search for an installed distribution's modules."""

    try:
        dist = importlib_metadata.distribution(distribution_name)
    except importlib_metadata.PackageNotFoundError as exc:
        raise SourceError(f"distribution not installed: {distribution_name}") from exc
    locate = dist.locate_file("")
    return [Path(str(locate))]


def load_entry_points(target: str) -> tuple[list[EntryPoint], list[Path]]:
    """Dispatch on the shape of ``target`` and return (entry_points, search_roots).

    ``target`` may be:

    - a path to a ``.whl`` file
    - a path to a ``pyproject.toml`` file
    - a path to a ``setup.cfg`` file
    - a directory containing a ``pyproject.toml`` and/or ``setup.cfg``
    - ``installed:<distribution-name>`` for an already-installed package
    """

    if target.startswith("installed:"):
        name = target[len("installed:") :]
        return load_entry_points_from_installed(name), source_roots_for_installed(name)

    path = Path(target)
    if path.is_dir():
        entries: list[EntryPoint] = []
        pyproject = path / "pyproject.toml"
        setup_cfg = path / "setup.cfg"
        found_any_file = False
        if pyproject.is_file():
            entries.extend(load_entry_points_from_pyproject(pyproject))
            found_any_file = True
        if setup_cfg.is_file():
            entries.extend(load_entry_points_from_setup_cfg(setup_cfg))
            found_any_file = True
        if not found_any_file:
            raise SourceError(
                f"{path}: no pyproject.toml or setup.cfg found"
            )
        roots = [path, path / "src"]
        return entries, [r for r in roots if r.is_dir()]

    if path.suffix == ".whl":
        entries = load_entry_points_from_wheel(path)
        extracted = extract_wheel(path)
        return entries, [extracted]

    if path.name == "pyproject.toml":
        entries = load_entry_points_from_pyproject(path)
        parent = path.parent
        roots = [parent, parent / "src"]
        return entries, [r for r in roots if r.is_dir()]

    if path.name == "setup.cfg":
        entries = load_entry_points_from_setup_cfg(path)
        parent = path.parent
        roots = [parent, parent / "src"]
        return entries, [r for r in roots if r.is_dir()]

    raise SourceError(
        f"don't know how to load entry points from {target!r} "
        "(expected a .whl file, a pyproject.toml, a setup.cfg, a directory, "
        "or installed:<name>)"
    )
