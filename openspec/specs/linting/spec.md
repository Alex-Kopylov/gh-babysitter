# linting Specification

## Purpose

This repository separates linting into two groups: **fast lint** and **full
lint**. The split exists because some linters are useful final gates but poor
feedback during active code generation. An AI agent or developer can
temporarily create unused dependencies, incomplete imports, duplicate code, or
inconsistent type flows while a change is still being assembled. Reporting
those findings too early can create false alarms and distract the workflow
before the code reaches a coherent state.

## Requirements

### Requirement: Two lint groups with an authoritative alias

The repository SHALL provide `mise run lint-fast` for active editing and agent
feedback loops, and `mise run lint-full` as the final lint gate.
`mise run lint` SHALL be the conventional alias for `lint-full`, and CI SHALL
use this command so the default lint command stays the authoritative final
gate. `mise run install-hooks` SHALL install the commit hooks.

#### Scenario: CI runs the final gate

- **WHEN** CI lints the repository
- **THEN** it runs `mise run lint`, which is `lint-full`

### Requirement: Fast lint is safe on incomplete work

Fast lint MUST be safe to run while code is still being generated or edited.
It SHALL prefer checks that are local, deterministic, and unlikely to depend
on unfinished work elsewhere in the repository: formatting checks, syntax and
style checks, file-local Python linting, configuration syntax checks;
Markdown, YAML, JSON, TOML, Dockerfile, and configured automation validation;
spelling checks when the project accepts that spelling lint may flag prose.

#### Scenario: Agent inner loop

- **WHEN** an agent or editor integration needs quick feedback before a change
  is complete
- **THEN** fast lint runs without false alarms caused by half-built work

### Requirement: Full lint is the final quality gate

Full lint SHALL include everything from fast lint plus slower and broader
checks meant for coherent changes, not half-built work: type checking,
unused-code checks, dependency declaration checks such as `deptry`,
duplicate-code checks, secret scans, dependency vulnerability audits, and
automation security scans when that automation is enabled.

#### Scenario: deptry placement

- **WHEN** deciding where dependency declaration checks run
- **THEN** `deptry` belongs in full lint and the commit hook, not fast lint:
  the checks are valuable before code is committed, but they can produce false
  alarms while dependencies and imports are still being edited

### Requirement: No hidden hook checks

Commit hooks MUST NOT contain hidden checks that CI and manual commands skip.
If a check can block a commit, it MUST also be represented in
`mise run lint-full` so developers can run the same class of check explicitly
before they commit. Hook-only exceptions SHALL be rare and documented with why
they cannot run in CI or `lint-full`.

#### Scenario: A check blocks a commit

- **WHEN** a commit hook check blocks a commit
- **THEN** the developer can reproduce the same class of check with
  `mise run lint-full`

### Requirement: Placement rules for new linters

When adding a new linter, it SHALL go to fast lint when it gives reliable
feedback on incomplete work, and to full lint when it needs the project to be
in a coherent final state. Missing configured targets MUST NOT be hidden
behind "skip if missing" logic: if a task names `src`, `tests`, `Dockerfile`,
or an automation file, that target is part of the repository contract —
restore the target or update the task configuration.

#### Scenario: A configured target disappears

- **WHEN** a lint task's configured target is missing from the repository
- **THEN** the task fails loudly instead of silently skipping; the target is
  restored or the task configuration is updated
