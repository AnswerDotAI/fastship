# Release notes

<!-- do not remove -->

## 0.1.4

### New Features

- ship_pr takes a path ([#39](https://github.com/AnswerDotAI/fastship/pull/39)), thanks to [@jph00](https://github.com/jph00)
- Generate separate local and distribution Rust profiles ([#38](https://github.com/AnswerDotAI/fastship/pull/38)), thanks to [@jph00](https://github.com/jph00)
- Generate efficient Rust build cache configuration ([#37](https://github.com/AnswerDotAI/fastship/pull/37)), thanks to [@jph00](https://github.com/jph00)

### Bugs Squashed

- Raise `CliError` instead of `SystemExit` from release steps ([#40](https://github.com/AnswerDotAI/fastship/pull/40)), thanks to [@jph00](https://github.com/jph00)


## 0.1.3

### New Features

- Add tag-driven releases for Python platform wheels ([#34](https://github.com/AnswerDotAI/fastship/pull/34)), thanks to [@jph00](https://github.com/jph00)
- Refactor Cargo version bumping into shared _cargo_bump helper; skip nbdev release flow for nbdev-docs-over-maturin projects ([#33](https://github.com/AnswerDotAI/fastship/issues/33))
- Add pure-Rust crate support: ship-crate-new and Cargo.toml tag releases ([#31](https://github.com/AnswerDotAI/fastship/pull/31)), thanks to [@jph00](https://github.com/jph00)
- verbose flag ([#30](https://github.com/AnswerDotAI/fastship/issues/30))

### Bugs Squashed

- ship_pr accepts a long single-line body ([#36](https://github.com/AnswerDotAI/fastship/pull/36)), thanks to [@jph00](https://github.com/jph00)
- Update CLI flags for fastcore.script hyphenation ([#35](https://github.com/AnswerDotAI/fastship/pull/35)), thanks to [@jph00](https://github.com/jph00)
- Correct first-release instructions in crate README template ([#32](https://github.com/AnswerDotAI/fastship/pull/32)), thanks to [@jph00](https://github.com/jph00)


## 0.1.2

### New Features

- ship-release dispatches npm and Rust/Zig tag-push flows flag-free, with dirty-tree guards, replacing ship-rs-release ([#29](https://github.com/AnswerDotAI/fastship/pull/29)), thanks to [@jph00](https://github.com/jph00)


## 0.1.1

### New Features

- Add Zig project scaffolding, GitHub Pages setup, wheel-only builds, git-derived authors, and unified release workflow across project types ([#28](https://github.com/AnswerDotAI/fastship/issues/28))
- Add expanduser() to `_prepare_new_root` for ~-relative path support ([#27](https://github.com/AnswerDotAI/fastship/issues/27))
- Remove silent error suppression in `ship_pr`: enable Git exceptions, require label success, support stdin body, and delete merged branch ([#26](https://github.com/AnswerDotAI/fastship/issues/26))
- Remove ship-rs-test command in favor of maturin develop && pytest -q ([#25](https://github.com/AnswerDotAI/fastship/issues/25))
- Add --`no_editor` and --yes flags to skip editor/confirmation in release workflow ([#24](https://github.com/AnswerDotAI/fastship/issues/24))
- ship-release now generates changelog itself; changelog insertion is idempotent via nbdev `update_changelog` ([#23](https://github.com/AnswerDotAI/fastship/issues/23))
- Add nbdev delegation to ship-bump, ship-pypi, ship-changelog, and ship-gh ([#22](https://github.com/AnswerDotAI/fastship/issues/22))


## 0.1.0

### New Features

- ghapi v2 compat ([#21](https://github.com/AnswerDotAI/fastship/issues/21))
- Consolidate Rust CLI wrappers into their base functions ([#20](https://github.com/AnswerDotAI/fastship/issues/20))
- Unify ship-rs-bump into ship-bump with automatic Cargo.toml detection ([#19](https://github.com/AnswerDotAI/fastship/issues/19))
- After bumping Cargo.toml version, refresh the local editable install via maturin develop ([#18](https://github.com/AnswerDotAI/fastship/issues/18))


## 0.0.15

### New Features

- Remove native Rust binary bundling and ship-rs-prep; simplify Rust workflow to maturin-only builds ([#17](https://github.com/AnswerDotAI/fastship/issues/17))


## 0.0.14

### New Features

- Split `ship_rs_`* functions into callable core + CLI wrappers; replace maturin develop with wheel build/install in `ship_rs_test` ([#16](https://github.com/AnswerDotAI/fastship/issues/16))


## 0.0.13

### New Features

- Split Rust CI into platform-specific jobs and refactor tests to use structured TOML assertions ([#15](https://github.com/AnswerDotAI/fastship/issues/15))
- Add explicit pyo3 feature mapping to dep:pyo3 in Cargo template ([#14](https://github.com/AnswerDotAI/fastship/issues/14))


## 0.0.12

### New Features

- Use project optional-dependencies extras for CI installs instead of separate pip install commands ([#13](https://github.com/AnswerDotAI/fastship/issues/13))


## 0.0.11

### New Features

- Add ship-rs-new and ship-rs-init commands for scaffolding and configuring maturin/PyO3 projects, using Cargo.toml as the version source ([#12](https://github.com/AnswerDotAI/fastship/issues/12))


## 0.0.10

### New Features

- Add PyO3/maturin support with ship-rs-* commands for Rust CLI bin bundling ([#11](https://github.com/AnswerDotAI/fastship/issues/11))
- Add package find config to project template and simplify fastship package discovery ([#10](https://github.com/AnswerDotAI/fastship/issues/10))


## 0.0.9

### New Features

- Add `CHANGELOG_MARKER` constant and validate its presence before inserting release notes ([#9](https://github.com/AnswerDotAI/fastship/issues/9))


## 0.0.8

### New Features

- Allow changelog review even with --no-changelog and skip commit when worktree is clean ([#8](https://github.com/AnswerDotAI/fastship/issues/8))


## 0.0.7

### New Features

- Replace underscore with hyphen ([#7](https://github.com/AnswerDotAI/fastship/issues/7))


## 0.0.6

### New Features

- Add ship_release for full release workflow ([#6](https://github.com/AnswerDotAI/fastship/pull/6)), thanks to [@jph00](https://github.com/jph00)


## 0.0.5

### New Features

- Add ship_changelog and improve Release API ([#5](https://github.com/AnswerDotAI/fastship/pull/5)), thanks to [@jph00](https://github.com/jph00)


## 0.0.4

### New Features

- project.urls in `ship_new` template with `gh_org` param ([#4](https://github.com/AnswerDotAI/fastship/pull/4)), thanks to [@jph00](https://github.com/jph00)


## 0.0.3

### New Features

- `ship_release_gh`: only include issue/PR titles in changelog ([#3](https://github.com/AnswerDotAI/fastship/pull/3)), thanks to [@jph00](https://github.com/jph00)


## 0.0.2

### New Features

- Add `ship_pr` command for quick PR workflow ([#1](https://github.com/AnswerDotAI/fastship/pull/1)), thanks to [@jph00](https://github.com/jph00)

### Bugs Squashed

- Fix body path check for multiline strings ([#2](https://github.com/AnswerDotAI/fastship/pull/2)), thanks to [@jph00](https://github.com/jph00)


## 0.0.1

- init commit
