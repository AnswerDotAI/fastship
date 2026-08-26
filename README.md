# fastship

Tiny, local-first release tools for modern Python projects.

`fastship` gives you the same *workflow feel* as the nbdev `nbdev-bump-version`, `release-pypi`, and `release-gh` commands — but for plain (non-notebook) Python projects.

In an nbdev project (one with `[tool.nbdev]` in `pyproject.toml`), `ship-bump`, `ship-pypi`, `ship-changelog`, and `ship-gh` automatically delegate to their nbdev equivalents, so you can use the same commands in every repo.

## Install

```bash
pip install fastship
```

## Quick start

Create a new project:

```bash
ship-new my-project
cd my-project
pip install -e .[dev]
```

This creates a complete project with `pyproject.toml`, `__version__`, LICENSE, README, and everything wired for fastship. Author metadata comes from Git's `user.name` and `user.email`. New projects also include fastship's Jekyll `_config.yml` and `_layouts/default.html`.

## Commands

### `ship-bump`

Bump a version part (0=major, 1=minor, 2=patch). For Rust projects (a `Cargo.toml` next to `pyproject.toml`) it bumps `[package].version` in `Cargo.toml` and runs `maturin develop`; otherwise it rewrites a static `[project].version` when present, or `__version__` in your package `__init__.py`:

```bash
ship-bump --part 2
ship-bump --part 1
ship-bump --part 0
```

With a post-release version, the default command increments the post component:

```text
0.0.2026082005.post1 → 0.0.2026082005.post2
```

Supplying `--part` explicitly bumps that release component and removes the post component.

Decrement instead:

```bash
ship-bump --part 2 --unbump
```

### `ship-pypi`

Build + upload to PyPI:

```bash
ship-pypi
```

Upload to a named repository in `~/.pypirc` (e.g. `testpypi`):

```bash
ship-pypi --repository testpypi
```

Quiet mode:

```bash
ship-pypi --quiet
```

Build and upload a wheel without creating an sdist, either directly or as part of a plain Python release:

```bash
ship-pypi --wheel-only
ship-release --wheel-only
```

For a permanently wheel-only project:

```toml
[tool.fastship]
wheel-only = true
```

### `ship-pages`

Enable GitHub Pages from the repository's `main` branch and set the repository homepage to the Pages URL:

```bash
ship-pages
```

Run this after creating the GitHub repository and making its first push. The repository is inferred from the local `origin` remote, and authentication uses the same token lookup as `ship-gh`.

### PyO3 / maturin projects

Fastship can also handle the repeated local tooling for PyO3 projects that use maturin and need Rust CLI binaries bundled into wheel scripts.

Create a new PyO3 project:

```bash
ship-rs-new my-project
cd my-project
pip install -e .[dev]
maturin develop
pytest -q
```

Use `Cargo.toml` as the version source:

```toml
[project]
name = "my_project"
dynamic = ["version"]
```

Commands:

```bash
ship-rs-new my-project  # create a new maturin/PyO3 project
ship-rs-init            # configure an existing maturin/PyO3 project
ship-rs-build            # maturin build --release -o dist
ship-bump                # bump Cargo.toml version, then refresh the local editable install
ship-release             # changelog, tag, publish via CI, then bump
```

`ship-rs-init` must be run from an existing maturin project with `Cargo.toml`. It sets `[project].dynamic = ["version"]`, removes `[project].version`, and exposes `__version__` from `CARGO_PKG_VERSION` when it finds the PyO3 module.

Generated CI runs the tests, then builds wheels with `maturin-action` across an OS matrix (`manylinux: auto` on Linux) and publishes to GitHub Releases and PyPI on `v*` tags. Any CLI tools are Python console scripts declared in `[project.scripts]`; there are no native Rust binaries to build.

### Zig-backed projects

Create Python CFFI bindings over a bundled Zig shared library:

```bash
ship-zig-new my-project
cd my-project
pip install -e .[dev]
python build_lib.py
pytest -q
```

Commands:

```bash
ship-zig-new my-project  # create a CFFI/Zig project
ship-zig-build           # build and check the current platform wheel
ship-bump                # bump the Python package version
ship-release             # changelog, tag, publish via CI, then bump
```

The generated workflow builds one Python-ABI-independent wheel for Linux x86_64, Linux ARM64, macOS Intel, and macOS Apple Silicon. Tagged builds create the GitHub release and publish to PyPI with trusted publishing.

### `ship-pr`

Create a PR from uncommitted or unpushed work, merge it immediately, and clean up:

```bash
ship-pr "Add new feature"
ship-pr "Fix bug" --label bug
ship-pr "Breaking change" --label breaking
```

This command:
1. Creates a new branch from your current work
2. Commits any uncommitted changes (using the title as commit message)
3. Pushes to origin and creates a PR
4. Adds the specified label (default: `enhancement`)
5. Squash-merges the PR
6. Deletes the remote branch and resets local to updated main

You must be on the default branch (usually `main`) with no unpulled changes.

### `ship-changelog`

Generate or update `CHANGELOG.md` from closed GitHub issues since your last release:

```bash
ship-changelog
```

This is useful when you want to edit the changelog separately (e.g., in an editor or Claude Code) before releasing.
If you already have a `CHANGELOG.md`, it must include `<!-- do not remove -->` near the top so fastship knows where to insert the next release notes.

### `ship-gh`

This is an interactive helper:

1. Creates/updates `CHANGELOG.md` from closed GitHub issues since your last GitHub release
2. Opens your `$EDITOR` (defaults to `nano`) so you can edit the changelog
3. Prompts you to confirm
4. Runs `git commit -am release`, `git push`
5. Creates a GitHub release tagged with your current `__version__`

```bash
ship-gh
```

If you've reviewed a changelog created by `ship-changelog`, release it without regenerating or reopening it:

```bash
ship-gh --no-changelog --no-editor
```

The confirmation prompt remains. Add `--yes` only when confirmation has already happened elsewhere.

#### GitHub token setup

`ship-gh` looks for a token in this order:

1. `FASTSHIP_TOKEN`
2. a `./token` file in your repo root
3. `GITHUB_TOKEN`

The token must have permission to create releases (typically `repo` scope for classic PATs, or appropriate fine-grained permissions).

### `ship-release`

Full release workflow:

```bash
ship-release        # generate changelog, release, bump version, push
```

`ship-release` selects the project type automatically:

- Plain Python projects build and check the distribution before creating the GitHub release, upload it to PyPI, then bump.
- Maturin and fastship Zig projects push one annotated version tag for their trusted-publishing workflow, then bump - no changelog step, no prompts, no token needed.
- npm projects (a `package.json` with no `pyproject.toml` anywhere nearer) refuse a dirty tree, push one annotated version tag for their trusted-publishing workflow, then bump `package.json`. Maturin and Zig releases refuse a dirty tree the same way.

For another project whose artifacts must be built and published by CI, select the same tag-driven flow explicitly:

```toml
[tool.fastship]
release = "tag"
```

`ship-release` then pushes `v<version>`, increments the version, and leaves publication to the repository's workflow.

If a native build duplicates the package version, list those synchronized copies:

```toml
[tool.fastship]
release = "tag"
version-files = ["bazel/versions.bzl"]
```

Each file must contain the old version exactly once; fastship validates every file before changing any of them.

The final `Released` message is printed only after the full local publishing workflow succeeds. Tag-driven native releases print `Release started` because CI owns publication.

To generate and review the changelog separately, then run the rest of the workflow without another editor or prompt:

```bash
ship-changelog
# review CHANGELOG.md
ship-release --no-changelog --no-editor --yes
```

Changelog generation is idempotent: regenerating replaces the pending section for the current version rather than duplicating it. Regeneration also discards manual edits to that pending section, so do not regenerate after the final review.

## Notes

- `ship-pypi` does *not* bump your version for you — keep it explicit and boring.
- `ship-gh` requires that your project has a git `origin` remote pointing at GitHub (or use `--repo OWNER/REPO`).

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

Keep `__version__ = "x.y.z"` as a simple literal (don't compute it). `ship-bump` will rewrite this line near the top of the file to keep builds happy.

### 3) Optional: specify branch

Fastship infers your package name from `[project].name` (changing `-` to `_`). To override the release branch:

```toml
[tool.fastship]
branch = "main"  # defaults to current git branch
```
