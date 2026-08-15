# Verification Harness Design

**Status:** approved by the implementation brief
**Date:** 2026-08-15

## Objective

Provide one deterministic, cross-platform command surface for local verification and GitHub CI:

```text
uv run python scripts/verify.py fast
uv run python scripts/verify.py fast --pytest <target...>
uv run python scripts/verify.py full
```

The harness produces deterministic `LOCAL_VERIFIED` evidence only. It never runs provider
canaries and cannot establish `PROVIDER_VERIFIED`.

## Architecture

Implement a single standard-library Python script. A small frozen `VerificationStep` model owns a
human-readable name, explicit argv tuple, optional environment overrides, and any generated schema
files whose pre/post state must be compared. Mode builders return ordered step tuples. A runner
executes each step with `subprocess.run`, an explicit argument list, `shell=False`, repository-root
`cwd`, and an environment dictionary.

This keeps command selection independently testable without creating a package framework, plugin
system, YAML registry, Docker dependency, or task-runner dependency.

## Command selection

`fast` always runs Ruff for source, tests, and the harness, followed by mypy for source. It runs
pytest only when one or more targets follow `--pytest`, and forwards those targets exactly. With no
targets, it does not run the full test suite.

`full` runs the deterministic `AGENTS.md` baseline in order:

1. locked uv sync;
2. full pytest;
3. Ruff for source and tests;
4. a focused Ruff check for the harness itself;
5. mypy source;
6. mypy tests with `MYPYPATH=src` supplied through the child environment;
7. edit-schema generation;
8. image-generation-schema export;
9. uv dependency compatibility check;
10. locked npm install;
11. HyperFrames doctor;
12. production npm audit at high severity;
13. Git whitespace check.

The npm executable is `npm.cmd` on Windows and `npm` on POSIX. All other commands retain the
documented uv/git argv. The repository root is derived from `scripts/verify.py`, never from the
caller's current directory.

## Execution and failure behavior

Before each step, print its ordinal, name, and diagnostic-safe command. Let subprocess stdout and
stderr stream directly so failures remain diagnosable. Never print the complete child environment.

Stop on the first non-zero subprocess status and return that status. Print a concise failure
summary identifying the stopped step. When all steps pass, print a final passed/total summary.
Invalid modes and invalid `--pytest` usage fail through `argparse` with exit code 2.

## Generated-schema drift

The schema-producing steps declare their tracked outputs:

- `schemas/edit.schema.json` for `python -m auraly_pipeline.schema`;
- `schemas/image-generation.schema.json` for the image schema CLI export.

Capture each declared file's existence and SHA-256 before its generator runs. Compare it after the
successful command. If state changed, stop with exit code 1 and explain that generated schema drift
must be reviewed and committed. Never overwrite, restore, or otherwise mutate user work beyond the
generator's normal behavior.

Because comparison is limited to the declared generated file and its own pre-run state, unrelated
pre-existing dirty files do not cause false drift failures. A pre-existing edit to a schema also
passes when regeneration leaves those exact bytes unchanged.

## Security

The step registry contains no provider command. It does not load `.env`, inspect provider secrets,
dump the process environment, or print environment values. CI does not receive provider
credentials. Normal locked dependency installation/audit remains the only expected network use.

## Tests

`tests/test_verify_harness.py` imports the harness and replaces only the subprocess boundary. It
verifies mode selection, exact pytest forwarding, the complete full baseline, cross-platform
`MYPYPATH`, explicit argv lists and `shell=False`, repository-root `cwd`, fail-fast behavior,
secret-safe output, schema drift detection, unrelated dirty-file tolerance, and invalid CLI input.
Tests do not run the real full baseline, providers, browsers, package installers, or network calls.

## GitHub Actions

Create `.github/workflows/verify.yml` for pushes to `main` and pull requests, with concurrency
cancellation for superseded runs.

The Ubuntu job installs Python 3.11, uv, Node 22, and FFmpeg, then runs
`uv run python scripts/verify.py full`.

The Windows job installs Python 3.11 and uv, performs a locked sync, then invokes `fast` with the
harness tests plus existing high-value Windows/path/security/migration/orchestration tests. It
does not duplicate Node checks or run providers.

## Documentation and status

Update `AGENTS.md` and `README.md` only enough to make the harness the documented command surface.
The final report distinguishes:

- `IMPLEMENTED`: harness/tests/workflow exist;
- `LOCAL_VERIFIED`: fresh local `full` execution exits successfully;
- `CI VERIFIED`: only after GitHub actually runs successfully;
- `PROVIDER_VERIFIED`: not applicable.

## Explicit exclusions

No Goal 4A domain or persistence work, database migration, browser runtime, selector, provider
canary, HeyGen integration, renderer, API/UI, CopyMaster lifecycle, production-behavior expansion,
or unrelated refactor is part of this design.
