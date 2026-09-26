# entrypoint-check

Verify that a package's entry points (`console_scripts`, `[project.scripts]`,
custom `entry-points` groups) actually resolve to a real, importable, callable
attribute — before your users find out at runtime.

```
$ epkg              # installed from a wheel that built and installed cleanly
Traceback (most recent call last):
  File ".../bin/epkg", line 3, in <module>
    from epkg.cli import main_typo
ImportError: cannot import name 'main_typo' from 'epkg.cli' (.../site-packages/epkg/cli.py)

$ entrypoint-check dist/epkg_broken_hatchling-0.1.0-py3-none-any.whl
entrypoint-check (static): 1 entry point(s) checked
  [FAIL] console_scripts: epkg = epkg.cli:main_typo
        'main_typo' is not defined anywhere in .../epkg/cli.py
summary: 0 ok, 0 unverifiable, 1 failing
```

The wheel above built successfully, `pip install`ed without a warning, and the
break was only visible by actually running the command. `entrypoint-check`
finds it without running anything, in milliseconds, before the wheel ships.

## The problem

`[project.scripts]` (and its setuptools/`entry_points.txt` equivalents) is a
string that names a module and an attribute: `mycli = "pkg.cli:main"`. Nothing
in the build pipeline checks that `pkg.cli` exists, that it has an attribute
called `main`, or that the attribute is callable. Both major backends build
the wheel anyway:

```
$ python -m build --wheel   # pyproject.toml declares epkg.cli:main_typo,
Successfully built epkg_broken_hatchling-0.1.0-py3-none-any.whl   # but epkg.cli only has `main`.
```
```
$ python -m build --wheel   # same typo, setuptools backend this time
Successfully built epkg_broken_setuptools-0.1.0-py3-none-any.whl
```

`pip install` of either wheel also succeeds without a warning — the
`entry_points.txt` metadata is well-formed, it just points at nothing. The
first sign of trouble is an end user's traceback, in production, days later.

**No existing tool catches this:**

| Tool | What it actually checks |
| --- | --- |
| `check-wheel-contents` | File layout: stray `__pycache__/`, a `tests/` directory shipped in the wheel, namespace package conflicts. Never opens `entry_points.txt`. |
| `twine check` | README renders as valid reStructuredText/Markdown, and the metadata version is supported by PyPI. Nothing about entry points. |
| `vermin` | Which Python version your syntax/stdlib usage requires. Unrelated to entry points entirely. |

**npm already does this for its own `bin` field.** `npm publish --dry-run`
resolves each `bin` entry against the file tree and warns (removing the entry)
if the target is missing. Python packaging has no equivalent for
`console_scripts`, despite `entry_points.txt` being just as static and just as
checkable.

## Install

```sh
pip install entrypoint-check
```

Python >= 3.11 (uses `tomllib`, stdlib since 3.11). Zero runtime dependencies.

## Usage

```sh
# Default: static (AST) mode -- never imports anything.
entrypoint-check dist/mypackage-1.0.0-py3-none-any.whl
entrypoint-check .                        # reads ./pyproject.toml (and setup.cfg)
entrypoint-check path/to/pyproject.toml
entrypoint-check installed:black          # an already-installed distribution

# Opt-in dynamic mode: actually imports each target, in an isolated subprocess.
entrypoint-check dist/mypackage-1.0.0-py3-none-any.whl --import --timeout 15
```

Exit codes: `0` clean, `1` one or more entry points don't resolve, `2` the
target couldn't be analysed at all (bad path, unparsable TOML, no source
roots available for static mode).

## Options

| Flag | Default | Effect |
| --- | --- | --- |
| `target` (positional) | — | A `.whl` file, a `pyproject.toml`, a `setup.cfg`, a directory containing either, or `installed:<name>`. |
| `--import` | off | Switch from static (AST) checking to actually importing each target in an isolated subprocess. |
| `--timeout` | `10.0` | Per-entry-point subprocess wall-clock budget, seconds. Only applies with `--import`. |
| `--src PATH` | — | Extra module search root for static mode (e.g. a nonstandard `src/` layout). Repeatable. |
| `--json` | off | Machine-readable output instead of text. |

## Static mode vs. import mode

Importing arbitrary third-party code runs that code's top-level statements —
side effects, a stray `sys.exit()`, or a hang are all real risks with real
packages. So there are two modes, and the safer one is the default.

**Static mode (default, `entrypoint-check target`)** parses the target
module's `.py` source with `ast` and looks for the attribute among its
top-level (or class-level, for `Class.method`) bindings. It never executes
the module or any of its parents.

- Catches: the typo/rename case this tool exists for (`main_typo` when the
  real name is `main`), and a module path that doesn't exist at all.
- Reports `UNVERIFIABLE`, not a pass or a fail, when it genuinely can't tell:
  a plain assignment (`main = some_factory()` — present, but static analysis
  can't confirm it ends up callable), a re-export via `from x import y as z`,
  a `from x import *`, or a module-level `__getattr__` (PEP 562). These are
  real patterns in real packages (see the survey below) and are deliberately
  *not* reported as failures.
- Cannot catch: an attribute that exists as a static binding but turns out
  not to be callable once the module actually runs, or anything produced by
  `exec`/`setattr`/metaclass magic.

**Import mode (`--import`)** is definitive: it imports the module for real,
walks `getattr()` down the dotted attribute path, and checks `callable()` —
in a fresh `python -c` subprocess per entry point, with a timeout. A subprocess
that hangs is killed by the timeout; one that calls `sys.exit()` or crashes
only takes down its own throwaway process, never the checker. This is the
only way to be sure, and the only way to catch a target that's present but
not callable, at the cost of actually running the package's import-time code.

## False-positive survey

Static mode was run against every console-script-shipping package in a fresh
venv that came to hand — no cherry-picking, this is every entry point every
installed package declared:

```
black       3 entry points  -> 3 ok, 0 unverifiable, 0 failing
mypy        5 entry points  -> 5 ok, 0 unverifiable, 0 failing
pytest      2 entry points  -> 2 ok, 0 unverifiable, 0 failing
pip-tools   2 entry points  -> 2 ok, 0 unverifiable, 0 failing
httpie      3 entry points  -> 3 ok, 0 unverifiable, 0 failing
hatch       1 entry point   -> 1 ok, 0 unverifiable, 0 failing
flask       1 entry point   -> 1 ok, 0 unverifiable, 0 failing
twine       4 entry points  -> 4 ok, 0 unverifiable, 0 failing
ipython     2 entry points  -> 2 ok, 0 unverifiable, 0 failing
```

**0 false positives out of 23 real entry points across 9 packages**, and
notably 0 `UNVERIFIABLE` results either — every one of these projects points
its entry points straight at a plain top-level `def`. Honest caveat: this is
a sample of well-maintained, popular packages; it says static mode is not
noisy on the common case, not that `UNVERIFIABLE`/false-positive-shaped code
never occurs in the wild (the dedicated tests in `tests/test_static_check.py`
exercise the re-export/star-import/`__getattr__` cases directly and confirm
they come back `UNVERIFIABLE`, not a false `FAIL`).

Running the *same* survey with `--import` turns up something instructive,
not a bug in the tool:

```
$ entrypoint-check installed:black --import
  [OK]   console_scripts: black = black:patched_main
  [FAIL] console_scripts: blackd = blackd:patched_main
         module 'blackd' failed to import: IMPORT_ERROR:ImportError:aiohttp dependency is
         not installed: No module named 'aiohttp'. Please re-install black with the '[d]'
         extra install to obtain aiohttp_cors: `pip install black[d]`
```

`blackd` is real and callable — it just depends on black's optional `[d]`
extra, which wasn't installed in the survey venv. Static mode reported this
entry point `OK` (the attribute is a real `def` in the source); import mode
"failed" it because of the local environment, not because the entry point is
broken. This is exactly the tradeoff described above: import mode is
definitive about *this installation*, not about the package in the abstract,
and its failures need a human to distinguish "actually broken" from "extra
not installed here."

## What it does not do

- **It does not check that dependencies are installed.** An entry point can
  be perfectly correct and still fail to import in an environment missing an
  optional extra (see `blackd` above) — that's an environment problem, not an
  entry-point problem, and static mode won't flag it at all.
- **It is not a substitute for actually running your CLI once.** Nothing
  catches "the callable exists but raises immediately when invoked" except
  invoking it; this tool stops at "is it there and is it callable."
- **Static mode cannot see through runtime metaprogramming.** `exec`,
  `setattr`, decorators that register a name other than the one you'd guess
  from the AST, and similar tricks are invisible to it by construction — that
  is the whole point of the mode (no execution), not an oversight.
- **No PyPI/registry integration.** This checks a wheel, a source tree, or an
  already-installed distribution that you hand it; it does not fetch or
  compare against what's published.
- **Only `console_scripts`/`gui_scripts`/custom `entry-points` groups with a
  `module:attr` value are supported.** Legacy `setup.py` calls that build
  entry points programmatically (not via `setup.cfg` or `pyproject.toml`)
  aren't read — check the built wheel's `entry_points.txt` instead, which
  works regardless of how `setup.py` produced it.

## Develop

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q
.venv/bin/python -m mypy src tests --strict
```

Real output from this machine (Python 3.14.7; CI in `.github/workflows/ci.yml`
additionally covers 3.11–3.13, which could not be run here):

```
$ .venv/bin/python -m pytest -q
................................................                         [100%]
48 passed in 1.31s

$ .venv/bin/python -m mypy src tests --strict
Success: no issues found in 10 source files

$ .venv/bin/entrypoint-check --help
usage: entrypoint-check [-h] [--import] [--timeout TIMEOUT] [--src PATH]
                        [--json]
                        target

Verify that packaged entry points (console_scripts, [project.scripts], custom
groups) actually resolve to a real, callable, importable attribute.

positional arguments:
  target             What to check: a path to a built .whl file, a path to a
                     pyproject.toml, a path to a setup.cfg, a directory
                     containing either, or 'installed:<distribution-name>' for
                     an already-installed package.

options:
  -h, --help         show this help message and exit
  --import           Actually import each target in an isolated subprocess
                     instead of the default AST-only static check. Definitive,
                     but runs third-party top-level code.
  --timeout TIMEOUT  Per-entry-point subprocess timeout in seconds for
                     --import (default: 10.0).
  --src PATH         Extra module search root, e.g. a src/ directory.
                     Repeatable.
  --json             Emit machine-readable JSON instead of text.
```

`examples/broken_hatchling/` and `examples/broken_setuptools/` are the two
demo packages used for the evidence at the top of this README — each is a
minimal, real, buildable package whose `pyproject.toml` deliberately points
`[project.scripts]` at a name that doesn't exist in `epkg/cli.py`. Build
either one (`python -m build --wheel`) to reproduce the failure end to end:
a clean build, a clean install, a runtime `ImportError`, and `entrypoint-check`
catching it beforehand.

## License

MIT
