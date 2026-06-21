# Release notes

<!-- do not remove -->

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


