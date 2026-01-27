# fastship

Tiny, local-first release tools for modern Python projects.

`fastship` gives you the same *workflow feel* as the nbdev `nbdev_bump_version`, `release_pypi`, and `release_gh` commands — but for plain (non-notebook) Python projects.

You get three CLI tools:

- `ship_bump` — bump `__version__` in your package `__init__.py`
- `ship_pypi` — build + upload to PyPI using `python -m build` and `twine`
- `ship_release_gh` — generate/update `CHANGELOG.md` from closed GitHub issues, let you edit it, then push and create a GitHub release via `ghapi`

## Install

```bash
pip install fastship
```

## One-time project setup

### 1) Put your version in `__init__.py`

In your package's main `__init__.py`:

```py
__version__ = "0.0.1"
```

`fastship` will read and update this line.

### 2) Make `pyproject.toml` read the version from `__init__.py` (setuptools)

In your project `pyproject.toml`:

```toml
[project]
name = "my-project"
dynamic = ["version"]

[tool.setuptools.dynamic]
version = { attr = "my_project.__version__" }
```

(You can import things in `__init__.py`, **as long as `__version__` is a literal string assignment**. Setuptools tries to read `attr` values from the module’s AST first; it only falls back to importing the module if it can’t statically evaluate the value, which can be error-prone during builds.)

Practical rule:
- Keep `__version__ = "x.y.z"` as a simple literal (don’t compute it), and you’re free to import whatever you like.
- `ship_bump` will (re)write the `__version__` line near the top of the file to keep builds happy.


### 3) Optional: specify branch

Fastship reads your package name from `[project].name` in `pyproject.toml` (changing `-` to `_` if needed). If you need to override the default branch for releases:

```toml
[tool.fastship]
branch = "main"  # defaults to current git branch
```

## Commands

### `ship_bump`

Bump a version part (0=major, 1=minor, 2=patch):

```bash
ship_bump --part 2
ship_bump --part 1
ship_bump --part 0
```

Decrement instead:

```bash
ship_bump --part 2 --unbump
```

### `ship_pypi`

Build + upload to PyPI:

```bash
ship_pypi
```

Upload to a named repository in `~/.pypirc` (e.g. `testpypi`):

```bash
ship_pypi --repository testpypi
```

Quiet mode:

```bash
ship_pypi --quiet
```

### `ship_release_gh`

This is an interactive helper:

1. Creates/updates `CHANGELOG.md` from closed GitHub issues since your last GitHub release
2. Opens your `$EDITOR` (defaults to `nano`) so you can edit the changelog
3. Prompts you to confirm
4. Runs `git commit -am release`, `git push`
5. Creates a GitHub release tagged with your current `__version__`

```bash
ship_release_gh
```

#### GitHub token setup

`ship_release_gh` looks for a token in this order:

1. `FASTSHIP_TOKEN`
2. a `./token` file in your repo root
3. `GITHUB_TOKEN`

The token must have permission to create releases (typically `repo` scope for classic PATs, or appropriate fine-grained permissions).

## Notes

- `ship_pypi` does *not* bump your version for you — keep it explicit and boring.
- `ship_release_gh` requires that your project has a git `origin` remote pointing at GitHub (or use `--repo OWNER/REPO`).

## ship_new

Scaffold a modern setuptools project wired for fastship:

```bash
ship_new my-project
```

Options:

```bash
ship_new my-project --package my_project --description "My lib"
```

