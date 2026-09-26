"""entrypoint-check: verify that packaged entry points actually resolve.

A wheel builds successfully even when ``[project.scripts]`` points at a
function that does not exist. Both hatchling and setuptools happily emit
``entry_points.txt`` with a dangling target; ``pip install`` succeeds; the
failure only appears when an end user runs the console script and gets an
``ImportError``. This package finds that class of bug before it ships.

Public API:

- :func:`entrypoint_check.sources.load_entry_points` -- collect entry points
  from a wheel, a pyproject.toml, a setup.cfg, or an installed distribution.
- :func:`entrypoint_check.static_check.check_static` -- AST-based check that
  never imports the target package.
- :func:`entrypoint_check.dynamic_check.check_dynamic` -- subprocess-isolated
  check that actually imports and calls ``callable()`` on the target.
"""

from entrypoint_check.model import (
    CheckMode,
    EntryPoint,
    Finding,
    Status,
)

__all__ = [
    "CheckMode",
    "EntryPoint",
    "Finding",
    "Status",
]

__version__ = "0.1.0"
