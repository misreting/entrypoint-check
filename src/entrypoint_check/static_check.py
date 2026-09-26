"""AST-based entry point checking: never imports anything.

This is the default mode. It locates the target module's source file on
disk by translating its dotted name into a path, then parses that single
file with :mod:`ast` and looks for the target attribute among its top-level
(or, for a dotted attribute, class-level) bindings. Nothing is executed --
not the target module, and not any package it belongs to.

What this catches: the exact bug this tool exists for -- a typo'd or
renamed function/class referenced by an entry point (``main_typo`` when the
real name is ``main``), and a module path that does not exist at all.

What this cannot catch, by design (no execution):

- Attributes created dynamically (``setattr``, ``exec``, decorators that
  register a *different* name, metaclasses that inject attributes).
- Module-level ``__getattr__`` (PEP 562) providing the name lazily.
- Names introduced by ``from x import *`` -- these are reported as
  ``UNVERIFIABLE``, not ``OK`` or ``MISSING``, because a static reader
  cannot know what a star import actually brings in.
- Whether an attribute that *is* present is truly callable at runtime
  (e.g. it might be reassigned to a non-callable elsewhere at import time).
  A conservative best-effort judgement is made from the AST shape.
- Conditional definitions (``if sys.platform == ...: def main(): ...`` in
  one branch only) -- if *any* branch defines the name, it counts as found.

Use :mod:`entrypoint_check.dynamic_check` for a definitive answer at the
cost of actually importing the code.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from entrypoint_check.model import CheckMode, EntryPoint, Finding, Status

_CALLABLE_DEF_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


@dataclass
class _ModuleNames:
    """What a single module file statically appears to define at top level."""

    callable_defs: set[str]
    """Names bound by ``def``, ``async def``, or ``class`` -- confidently callable."""

    other_bindings: set[str]
    """Names bound by assignment, import, or ``as`` alias -- present, but
    callability can't be judged from the AST alone."""

    classes: dict[str, ast.ClassDef]
    """Class bodies, for resolving dotted attributes like ``Class.method``."""

    has_star_import: bool
    """True if the module does ``from x import *`` anywhere at module level."""

    has_dynamic_getattr: bool
    """True if the module defines a module-level ``__getattr__`` (PEP 562),
    which can hand back arbitrary names at import time."""


def _collect_module_names(tree: ast.Module) -> _ModuleNames:
    callable_defs: set[str] = set()
    other_bindings: set[str] = set()
    classes: dict[str, ast.ClassDef] = {}
    has_star_import = False
    has_dynamic_getattr = False

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            callable_defs.add(node.name)
            if node.name == "__getattr__":
                has_dynamic_getattr = True
        elif isinstance(node, ast.ClassDef):
            callable_defs.add(node.name)
            classes[node.name] = node
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    other_bindings.add(target.id)
                elif isinstance(target, (ast.Tuple, ast.List)):
                    for elt in target.elts:
                        if isinstance(elt, ast.Name):
                            other_bindings.add(elt.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                other_bindings.add(node.target.id)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                other_bindings.add(bound)
        elif isinstance(node, ast.ImportFrom):
            if any(a.name == "*" for a in node.names):
                has_star_import = True
            for alias in node.names:
                if alias.name == "*":
                    continue
                bound = alias.asname or alias.name
                other_bindings.add(bound)
        elif isinstance(node, ast.If):
            # Best-effort: treat names defined in either branch of a
            # module-level `if` as present (e.g. platform/version guards).
            nested = _collect_module_names(ast.Module(body=node.body, type_ignores=[]))
            nested_else = _collect_module_names(ast.Module(body=node.orelse, type_ignores=[]))
            callable_defs |= nested.callable_defs | nested_else.callable_defs
            other_bindings |= nested.other_bindings | nested_else.other_bindings
            classes |= nested.classes | nested_else.classes
            has_star_import = has_star_import or nested.has_star_import or nested_else.has_star_import
            has_dynamic_getattr = (
                has_dynamic_getattr
                or nested.has_dynamic_getattr
                or nested_else.has_dynamic_getattr
            )
        elif isinstance(node, ast.Try):
            for block in (node.body, *(h.body for h in node.handlers), node.orelse, node.finalbody):
                nested = _collect_module_names(ast.Module(body=block, type_ignores=[]))
                callable_defs |= nested.callable_defs
                other_bindings |= nested.other_bindings
                classes |= nested.classes
                has_star_import = has_star_import or nested.has_star_import
                has_dynamic_getattr = has_dynamic_getattr or nested.has_dynamic_getattr

    return _ModuleNames(
        callable_defs=callable_defs,
        other_bindings=other_bindings,
        classes=classes,
        has_star_import=has_star_import,
        has_dynamic_getattr=has_dynamic_getattr,
    )


def _class_member_names(cls: ast.ClassDef) -> tuple[set[str], set[str]]:
    """Return (callable_members, other_members) for a class body."""

    callable_members: set[str] = set()
    other_members: set[str] = set()
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            callable_members.add(node.name)
        elif isinstance(node, ast.ClassDef):
            callable_members.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    other_members.add(target.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                other_members.add(node.target.id)
    return callable_members, other_members


def find_module_file(module: str, search_roots: list[Path]) -> Path | None:
    """Locate ``module``'s source file under one of ``search_roots``, without importing it."""

    parts = module.split(".")
    for root in search_roots:
        pkg_dir = root.joinpath(*parts)
        init_file = pkg_dir / "__init__.py"
        if init_file.is_file():
            return init_file
        module_file = root.joinpath(*parts[:-1], parts[-1] + ".py") if parts else None
        if module_file is not None and module_file.is_file():
            return module_file
    return None


def check_entry_point_static(
    entry_point: EntryPoint, search_roots: list[Path]
) -> Finding:
    """Check a single entry point using only :mod:`ast`, no imports."""

    module_file = find_module_file(entry_point.module, search_roots)
    if module_file is None:
        return Finding(
            entry_point=entry_point,
            status=Status.MISSING,
            message=(
                f"module '{entry_point.module}' not found under "
                f"{[str(r) for r in search_roots]}"
            ),
            mode=CheckMode.STATIC,
        )

    try:
        source = module_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(module_file))
    except (SyntaxError, UnicodeDecodeError) as exc:
        return Finding(
            entry_point=entry_point,
            status=Status.ERROR,
            message=f"could not parse {module_file}: {exc}",
            mode=CheckMode.STATIC,
        )

    if not entry_point.attr:
        # The entry point references the module itself, e.g. `name = pkg.mod`.
        return Finding(
            entry_point=entry_point,
            status=Status.OK,
            message=f"module '{entry_point.module}' found at {module_file}",
            mode=CheckMode.STATIC,
        )

    names = _collect_module_names(tree)
    attr_parts = entry_point.attr.split(".")
    first, rest = attr_parts[0], attr_parts[1:]

    if not rest:
        if first in names.callable_defs:
            return Finding(
                entry_point=entry_point,
                status=Status.OK,
                message=f"'{entry_point.attr}' is a def/class in {module_file}",
                mode=CheckMode.STATIC,
            )
        if first in names.other_bindings:
            return Finding(
                entry_point=entry_point,
                status=Status.UNVERIFIABLE,
                message=(
                    f"'{entry_point.attr}' is bound in {module_file} via assignment/import; "
                    "static mode cannot confirm it is callable -- use --import to be sure"
                ),
                mode=CheckMode.STATIC,
            )
        if names.has_dynamic_getattr or names.has_star_import:
            reason = "a module-level __getattr__" if names.has_dynamic_getattr else "a star import"
            return Finding(
                entry_point=entry_point,
                status=Status.UNVERIFIABLE,
                message=(
                    f"'{entry_point.attr}' not found among static bindings in {module_file}, "
                    f"but the module has {reason} that could supply it -- use --import to be sure"
                ),
                mode=CheckMode.STATIC,
            )
        return Finding(
            entry_point=entry_point,
            status=Status.MISSING,
            message=f"'{entry_point.attr}' is not defined anywhere in {module_file}",
            mode=CheckMode.STATIC,
        )

    # Dotted attribute, e.g. "Class.method".
    if first not in names.classes:
        if names.has_dynamic_getattr or names.has_star_import:
            return Finding(
                entry_point=entry_point,
                status=Status.UNVERIFIABLE,
                message=(
                    f"class '{first}' not found statically in {module_file}, "
                    "but the module has dynamic name resolution -- use --import to be sure"
                ),
                mode=CheckMode.STATIC,
            )
        return Finding(
            entry_point=entry_point,
            status=Status.MISSING,
            message=f"class '{first}' is not defined in {module_file}",
            mode=CheckMode.STATIC,
        )

    cls = names.classes[first]
    if len(rest) != 1:
        return Finding(
            entry_point=entry_point,
            status=Status.UNVERIFIABLE,
            message=(
                f"attribute path '{entry_point.attr}' is nested more than one level deep; "
                "static mode only resolves Class.member -- use --import to be sure"
            ),
            mode=CheckMode.STATIC,
        )
    member = rest[0]
    callable_members, other_members = _class_member_names(cls)
    if member in callable_members:
        return Finding(
            entry_point=entry_point,
            status=Status.OK,
            message=f"'{entry_point.attr}' is a method/class on '{first}' in {module_file}",
            mode=CheckMode.STATIC,
        )
    if member in other_members:
        return Finding(
            entry_point=entry_point,
            status=Status.UNVERIFIABLE,
            message=(
                f"'{entry_point.attr}' is a class attribute assigned in {module_file}; "
                "static mode cannot confirm it is callable -- use --import to be sure"
            ),
            mode=CheckMode.STATIC,
        )
    return Finding(
        entry_point=entry_point,
        status=Status.MISSING,
        message=f"'{member}' is not a member of class '{first}' in {module_file}",
        mode=CheckMode.STATIC,
    )


def check_static(
    entry_points: list[EntryPoint], search_roots: list[Path]
) -> list[Finding]:
    """Check every entry point statically. See module docstring for what this catches."""

    return [check_entry_point_static(ep, search_roots) for ep in entry_points]
