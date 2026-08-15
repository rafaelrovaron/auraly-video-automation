# Verification Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic cross-platform fast/full verification harness and independent Linux/Windows GitHub Actions evidence without invoking providers.

**Architecture:** `scripts/verify.py` owns a small typed step registry, strict argparse CLI, fail-fast subprocess runner, and per-generator schema snapshots. Unit tests replace only the subprocess boundary; GitHub Actions invokes the same public CLI used locally.

**Tech Stack:** Python 3.11 standard library, pytest, Ruff, mypy, PyYAML (already locked), GitHub Actions, uv, Node 22, FFmpeg.

## Global Constraints

- Run every subprocess with an explicit argv list, `shell=False`, and repository-root `cwd`.
- Never run ElevenLabs, Google Flow, Playwright generation, HeyGen, or another paid provider.
- Never load `.env`, print a full environment, or expose provider secrets.
- Preserve the documented uv/npm/git command surface and use `MYPYPATH=src` through an environment dictionary.
- Detect drift in `schemas/edit.schema.json` and `schemas/image-generation.schema.json` without reverting user changes.
- Add no Goal 4A production behavior, migrations, UI, renderer, or unrelated refactor.

---

### Task 1: Verification Step Model, Runner, and CLI

**Files:**
- Create: `scripts/verify.py`
- Test: `tests/test_verify_harness.py`

**Interfaces:**
- Produces: `VerificationStep`, `REPOSITORY_ROOT`, `run_steps(...) -> int`, `main(argv: Sequence[str] | None = None) -> int`.
- Consumes: Python standard library only.

- [ ] **Step 1: Write the first failing model/root test**

```python
def test_verification_step_uses_explicit_argv_and_repository_root() -> None:
    verify = load_verify_module()
    step = verify.VerificationStep(name="example", argv=("uv", "run", "pytest"))
    assert step.argv == ("uv", "run", "pytest")
    assert verify.REPOSITORY_ROOT == Path(__file__).resolve().parents[1]
```

- [ ] **Step 2: Run the focused test and confirm it fails because `scripts/verify.py` is absent**

Run: `uv run pytest tests/test_verify_harness.py::test_verification_step_uses_explicit_argv_and_repository_root -q`

- [ ] **Step 3: Implement the frozen step model and robust root**

```python
@dataclass(frozen=True)
class VerificationStep:
    name: str
    argv: tuple[str, ...]
    extra_env: Mapping[str, str] = field(default_factory=dict)
    generated_files: tuple[Path, ...] = ()

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
```

- [ ] **Step 4: Add failing runner tests for list argv, root cwd, `shell=False`, and fail-fast return**

Use a fake callable returning `subprocess.CompletedProcess` and record every invocation. Assert the
second step is never called after the first returns `7`.

- [ ] **Step 5: Implement the minimal runner and strict argparse subcommands**

The runner passes `list(step.argv)`, `cwd=repository_root`, `env=os.environ.copy() | extra_env`,
`check=False`, and `shell=False`. It streams output, stops on failure, and prints a final summary.
The parser has required `fast` and `full` subcommands; only `fast` accepts `--pytest` with
`nargs="+"`.

- [ ] **Step 6: Run focused verification and commit**

Run:

```text
uv run pytest tests/test_verify_harness.py -q
uv run ruff check scripts/verify.py tests/test_verify_harness.py
uv run python -m mypy scripts/verify.py tests/test_verify_harness.py
```

Commit: `chore: add verification harness command runner`

---

### Task 2: Fast Mode

**Files:**
- Modify: `scripts/verify.py`
- Modify: `tests/test_verify_harness.py`

**Interfaces:**
- Produces: `build_fast_steps(pytest_targets: Sequence[str]) -> tuple[VerificationStep, ...]`.

- [ ] **Step 1: Write failing fast-selection tests**

Assert no-target fast mode contains only Ruff (`src tests scripts`) and mypy `src`. Assert supplied
targets appear in one pytest argv exactly and in input order, with no full-suite substitution.

- [ ] **Step 2: Run focused tests and confirm the missing builder/selection failure**

Run: `uv run pytest tests/test_verify_harness.py -k "fast" -q`

- [ ] **Step 3: Implement the minimal fast builder and connect it to `main`**

```python
def build_fast_steps(pytest_targets: Sequence[str]) -> tuple[VerificationStep, ...]:
    steps = [
        VerificationStep("Ruff", ("uv", "run", "ruff", "check", "src", "tests", "scripts")),
        VerificationStep("mypy src", ("uv", "run", "python", "-m", "mypy", "src")),
    ]
    if pytest_targets:
        steps.append(VerificationStep("focused pytest", ("uv", "run", "pytest", *pytest_targets)))
    return tuple(steps)
```

- [ ] **Step 4: Run focused verification and commit**

Commit: `chore: add fast verification mode`

---

### Task 3: Full Deterministic Baseline

**Files:**
- Modify: `scripts/verify.py`
- Modify: `tests/test_verify_harness.py`

**Interfaces:**
- Produces: `build_full_steps(os_name: str = os.name) -> tuple[VerificationStep, ...]` and
  `npm_executable(os_name: str = os.name) -> str`.

- [ ] **Step 1: Write failing full-registry tests**

Assert the ordered registry contains every command from `AGENTS.md`, plus the focused harness Ruff
step. Assert test mypy has `extra_env == {"MYPYPATH": "src"}` and npm is `npm.cmd` for `nt`,
`npm` otherwise.

- [ ] **Step 2: Confirm RED**

Run: `uv run pytest tests/test_verify_harness.py -k "full or mypy or npm" -q`

- [ ] **Step 3: Implement the explicit full registry and connect `full` to `main`**

Use literal argv tuples for all required commands; do not execute shell command strings.

- [ ] **Step 4: Run focused verification and commit**

Commit: `chore: add full deterministic verification mode`

---

### Task 4: Generated-Schema Drift and Secret-Safe Diagnostics

**Files:**
- Modify: `scripts/verify.py`
- Modify: `tests/test_verify_harness.py`

**Interfaces:**
- Produces: immutable file-state snapshots around any `VerificationStep.generated_files`.

- [ ] **Step 1: Write failing schema-drift tests**

Use a temporary repository root and a fake runner that changes a declared schema. Assert exit `1`,
fail-fast behavior, a clear drift message, and no automatic restore. Add a passing case where an
unrelated dirty file exists but the declared schema bytes remain unchanged.

- [ ] **Step 2: Write the failing secret-output test**

Place `ELEVENLABS_API_KEY=do-not-print` in the test environment, run a successful fake step, and
assert the value never appears in captured output.

- [ ] **Step 3: Confirm RED**

Run: `uv run pytest tests/test_verify_harness.py -k "schema or secret" -q`

- [ ] **Step 4: Implement SHA-256 pre/post snapshots**

Snapshot only declared generated files, compare after a successful subprocess, return `1` on
change, and never inspect or restore unrelated files. Attach `schemas/edit.schema.json` and
`schemas/image-generation.schema.json` to their respective full steps.

- [ ] **Step 5: Run focused verification and commit**

Commit: `chore: detect generated schema drift`

---

### Task 5: CLI Error and Harness Regression Coverage

**Files:**
- Modify: `tests/test_verify_harness.py`
- Modify: `scripts/verify.py` only if a failing regression requires it.

**Interfaces:**
- Verifies: invalid mode/empty `--pytest`, progress output, summary output, exact fail status, and
  no post-failure calls.

- [ ] **Step 1: Add each missing regression as a failing focused test**

Use `pytest.raises(SystemExit)` and `capsys` for argparse failures. Name the production behavior
that would make each test fail before adding it.

- [ ] **Step 2: Make only required minimal corrections**

- [ ] **Step 3: Run all harness tests, Ruff, and mypy**

- [ ] **Step 4: Commit**

Commit: `test: harden verification harness behavior`

---

### Task 6: Linux GitHub Actions Job

**Files:**
- Create: `.github/workflows/verify.yml`
- Modify: `tests/test_verify_harness.py`

**Interfaces:**
- Produces: push-to-main and pull-request deterministic Ubuntu full job.

- [ ] **Step 1: Add a failing YAML structure test**

Load the workflow with `yaml.safe_load`; assert triggers, concurrency cancellation, Ubuntu runner,
Python `3.11`, Node `22`, FFmpeg setup, and exact full harness invocation.

- [ ] **Step 2: Confirm RED because the workflow is absent**

- [ ] **Step 3: Add the minimal Ubuntu workflow using locked dependencies and no secrets**

Use checkout, setup-python, setup-uv, setup-node with npm cache, FFmpeg install, and one full-harness
step.

- [ ] **Step 4: Run focused test/YAML parse and commit**

Commit: `ci: add Linux deterministic verification`

---

### Task 7: Windows GitHub Actions Job

**Files:**
- Modify: `.github/workflows/verify.yml`
- Modify: `tests/test_verify_harness.py`

**Interfaces:**
- Produces: focused Windows job using the public fast CLI after locked dependency sync.

- [ ] **Step 1: Add a failing Windows-job structure test**

Assert Windows runner, Python 3.11, uv sync, and exact existing test targets covering harness,
configuration paths, model path security, image path/download security, migrations, migration
locking, and orchestration concurrency.

- [ ] **Step 2: Confirm RED, then add the Windows job**

Do not add provider secrets, Node setup, a matrix, or provider calls.

- [ ] **Step 3: Run focused tests/YAML parse and commit**

Commit: `ci: add focused Windows verification`

---

### Task 8: Documentation and Final Verification

**Files:**
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `docs/GOAL-ROADMAP.md`

**Interfaces:**
- Documents: fast, focused fast, full, status terminology, and independent CI limitation.

- [ ] **Step 1: Update the command guidance minimally**

Make `scripts/verify.py full` the `LOCAL_VERIFIED` gate while retaining the expanded baseline for
auditability. Mark the roadmap harness task implemented only after final verification.

- [ ] **Step 2: Run the required focused harness command**

```text
uv run python scripts/verify.py fast --pytest tests/test_verify_harness.py
```

- [ ] **Step 3: Run the required full command**

```text
uv run python scripts/verify.py full
```

- [ ] **Step 4: Parse and inspect workflow YAML**

```text
uv run python -c "from pathlib import Path; import yaml; yaml.safe_load(Path('.github/workflows/verify.yml').read_text(encoding='utf-8'))"
```

- [ ] **Step 5: Review status/diff/scope and run `git diff --check`**

- [ ] **Step 6: Request independent review, fix important findings, and commit**

Commit: `docs: document deterministic verification workflow`
