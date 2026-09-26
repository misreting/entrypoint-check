"""Shared data model for entrypoint-check."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class Status(enum.Enum):
    """Outcome of checking a single entry point."""

    OK = "ok"
    """The target resolves (module found, attribute exists, looks callable)."""

    MISSING = "missing"
    """The module could not be found, or the attribute does not exist on it."""

    NOT_CALLABLE = "not-callable"
    """The attribute exists but is not callable (a plain value, a module, ...)."""

    UNVERIFIABLE = "unverifiable"
    """Static analysis could not reach a definite answer (e.g. a star import,
    a dynamic ``__getattr__`` hook, or module-level metaprogramming). Not a
    finding by itself -- it means "use --import for a real answer"."""

    ERROR = "error"
    """Something went wrong while checking (timeout, crash, subprocess
    failure unrelated to the entry point itself)."""


class CheckMode(enum.Enum):
    STATIC = "static"
    IMPORT = "import"


@dataclass(frozen=True)
class EntryPoint:
    """One `name = module:attr [extras]` declaration."""

    name: str
    """The console/gui script name, or the key inside a custom group."""

    group: str
    """``console_scripts``, ``gui_scripts``, or a custom
    ``project.entry-points.<group>`` name."""

    module: str
    """Dotted module path, e.g. ``pkg.cli``."""

    attr: str
    """Dotted attribute path within the module, e.g. ``main`` or
    ``Class.method``. Empty string means the entry point references the
    module itself."""

    extras: tuple[str, ...] = field(default_factory=tuple)
    """Extras qualifier, e.g. ``name = module:attr [extra1,extra2]``. Rarely
    used outside legacy setuptools ``setup()`` calls."""

    source: str = ""
    """Where this entry point declaration came from, for reporting."""

    @property
    def spec(self) -> str:
        if self.attr:
            return f"{self.module}:{self.attr}"
        return self.module

    @property
    def qualified_name(self) -> str:
        return f"{self.group}/{self.name}"


@dataclass(frozen=True)
class Finding:
    """The result of checking one entry point."""

    entry_point: EntryPoint
    status: Status
    message: str
    mode: CheckMode

    @property
    def is_clean(self) -> bool:
        return self.status in (Status.OK, Status.UNVERIFIABLE)
