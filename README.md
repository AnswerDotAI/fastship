# fastship

Tiny, local-first release tools for modern Python projects.

`fastship` gives you the same *workflow feel* as the nbdev `nbdev_bump_version`, `release_pypi`, and `release_gh` commands — but for plain (non-notebook) Python projects.

## Install

```bash
pip install fastship
```

## Quick start

Create a new project:

```bash
ship_new my-project
cd my-project
pip install -e .[dev]
```

This creates a complete project with `pyproject.toml`, `__version__`, LICENSE, README, and everything wired for fastship.

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

### `ship_pr`

Create a PR from uncommitted or unpushed work, merge it immediately, and clean up:

```bash
ship_pr "Add new feature"
ship_pr "Fix bug" --label bug
ship_pr "Breaking change" --label breaking
```

This command:
1. Creates a new branch from your current work
2. Commits any uncommitted changes (using the title as commit message)
3. Pushes to origin and creates a PR
4. Adds the specified label (default: `enhancement`)
5. Squash-merges the PR
6. Deletes the remote branch and resets local to updated main

You must be on the default branch (usually `main`) with no unpulled changes.

### `ship_changelog`

Generate or update `CHANGELOG.md` from closed GitHub issues since your last release:

```bash
ship_changelog
```

This is useful when you want to edit the changelog separately (e.g., in an editor or Claude Code) before releasing.

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

If you've already prepared the changelog (e.g., via `ship_changelog`), skip the changelog step:

```bash
ship_release_gh --no-changelog
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

## Existing projects

To add fastship to an existing project:

### 1) Put your version in `__init__.py`

In your package's main `__init__.py`:

```py
__version__ = "0.0.1"
```

### 2) Configure `pyproject.toml`

```toml
[project]
name = "my-project"
dynamic = ["version"]

[tool.setuptools.dynamic]
version = { attr = "my_project.__version__" }
```

Keep `__version__ = "x.y.z"` as a simple literal (don't compute it). `ship_bump` will rewrite this line near the top of the file to keep builds happy.

### 3) Optional: specify branch

Fastship infers your package name from `[project].name` (changing `-` to `_`). To override the release branch:

```toml
[tool.fastship]
branch = "main"  # defaults to current git branch
```
