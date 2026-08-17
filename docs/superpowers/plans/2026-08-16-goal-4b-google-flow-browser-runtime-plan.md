# Goal 4B — Google Flow Browser Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `auraly flow preflight` as an independent, headed-only, concurrency-one Google Flow browser safety check with manual authentication, a semantic UI contract, sanitized append-only failure evidence, and deterministic local-browser verification.

**Architecture:** Add a focused `auraly_pipeline.flow` package whose public service resolves safe local configuration, acquires one native OS file lock, runs a Playwright-managed persistent Chromium context, and converts all outcomes into one versioned result. Keep Playwright control in `runtime.py`, semantic UI knowledge in `locators.py`, raw-evidence sanitization in `diagnostics.py`, and CLI binding in the existing Typer module; no Goal 4A service, Job, repository, or database participates.

**Tech Stack:** Python 3.11, Pydantic 2, Typer, Playwright Python with managed Chromium, pytest, native `msvcrt`/`fcntl` file locking, stdlib `zipfile`/`json`/`pathlib`, Ruff, mypy, GitHub Actions, Xvfb on Linux CI.

## Global Constraints

- Source of truth: `docs/superpowers/specs/2026-08-16-goal-4b-google-flow-browser-runtime-design.md` at baseline commit `35aebb09628b1363990bb2fe51b05e0598c085d6`.
- The only production command is `auraly flow preflight`; it never creates or reads a Job, Campaign, ImageGeneration, repository, migration, or database engine.
- Production navigation is fixed to `https://labs.google/fx/tools/flow`; no CLI option, environment variable, public service argument, or config file can replace it.
- Chromium is Playwright-managed and always uses `launch_persistent_context(user_data_dir=config.profile_dir, headless=False)` with a dedicated profile; no Chrome channel, executable override, fallback browser, personal profile, `storage_state`, headless mode, or keep-open flag is allowed.
- Configuration precedence is CLI > environment > safe default. Defaults are profile `~/.auraly/browser-profiles/google-flow`, diagnostics `~/.auraly/diagnostics/google-flow`, lock `~/.auraly/locks/google-flow-browser.lock`, login timeout 300 seconds, and navigation timeout 30 seconds.
- Profile, diagnostics, lock, and transient diagnostic staging resolve outside the repository, reject symlink/junction escape, and do not contain one another.
- The native lock is non-blocking, spans browser launch through browser closure, permits concurrency exactly one, and is released on success, failure, exception, and process exit.
- Authentication is observation plus manual operator login/MFA only. Application code never types credentials, clicks login/MFA/consent/captcha, exports session data, or resets the fixed deadline.
- UI interaction is observation-only. No coordinates, XPath, `nth-child`, structural CSS, generated selector, positional click, image matching, or “first similar element” fallback is permitted.
- Goal 4B does not activate Create, type a prompt, upload, click Generate, inspect candidates, download, finalize 2K output, run QC/review, or perform a paid/provider generation.
- Public statuses are exactly `ready`, `authentication_required`, `human_intervention_required`, `runtime_busy`, `browser_launch_failed`, and `ui_contract_failed`.
- `ready` alone exits 0. Every parsed non-ready outcome exits 1 and emits one stable JSON object; invalid Typer syntax remains a usage error.
- All optional public result fields are always serialized and appear as JSON `null` when inapplicable. This plan chooses that stable rule from the design's allowed null/omission choice.
- Failure diagnostics are unique UTC timestamped, append-only, outside the repository, and never cleaned up automatically in Goal 4B. Successful runs persist no diagnostic directory.
- Authentication/challenge failures and pre-trust browser failures persist no screenshot or trace. Authenticated trusted-page UI failures publish screenshot/trace only after sanitization succeeds.
- Diagnostics and stdout/stderr never contain cookies, auth headers, storage state, browser-profile files, email/account identity, credentials, prompt/reference content, HTML/DOM snapshots, source, bodies, query strings/fragments, tokens, arbitrary exceptions, or absolute private paths.
- Deterministic tests use local HTML through a private runtime-only injection seam. The production CLI/service cannot supply a URL or target override, and CI never opens live Google Flow.
- Use TDD for every behavior task: run the focused test red, implement the minimum, rerun green, run the fast harness with that exact test file, review the diff, then commit only that task.
- Goal closure requires a fresh, read-only independent reviewer through `superpowers:requesting-code-review` after full deterministic verification. Implementer self-review is preparatory evidence only; unresolved Critical or High review findings block closure.
- Do not mark `PROVIDER_VERIFIED`. Optional live success may later be recorded only as supplemental `BROWSER_PREFLIGHT_VERIFIED` evidence.

## Repository Map

| Responsibility | Path |
| --- | --- |
| Approved design | `docs/superpowers/specs/2026-08-16-goal-4b-google-flow-browser-runtime-design.md` |
| Public Flow contracts and internal typed failures | `src/auraly_pipeline/flow/domain.py` |
| Safe config/defaults/path validation | `src/auraly_pipeline/flow/config.py` |
| Native cross-platform exclusive lock | `src/auraly_pipeline/flow/lock.py` |
| Central semantic locator contract | `src/auraly_pipeline/flow/locators.py` |
| Allowlisted result/trace diagnostic publication | `src/auraly_pipeline/flow/diagnostics.py` |
| Sole direct Playwright controller | `src/auraly_pipeline/flow/runtime.py` |
| Config/lock/runtime/diagnostic orchestration | `src/auraly_pipeline/flow/service.py` |
| Stable package exports | `src/auraly_pipeline/flow/__init__.py` |
| Typer command group | `src/auraly_pipeline/cli.py` |
| Local semantic pages | `tests/fakes/flow/*.html` |
| Focused tests | `tests/test_flow_domain.py`, `test_flow_config.py`, `test_flow_lock.py`, `test_flow_locators.py`, `test_flow_diagnostics.py`, `test_flow_runtime.py`, `test_flow_service.py`, `test_flow_cli.py`, `test_flow_security.py` |
| CI contract and workflow | `tests/test_verify_harness.py`, `.github/workflows/verify.yml` |
| Closure docs | `README.md`, `docs/GOAL-ROADMAP.md`, `docs/PROJECT-MEMORY.md` |

The extra `diagnostics.py` keeps archive parsing and allowlisted publication out of the Playwright
controller. It does not change the design boundary: `runtime.py` remains the only production module
that imports and controls Playwright, while `diagnostics.py` accepts only bytes, paths, typed scalar
metadata, and a raw trace created by the runtime.

---

### Task 1: Versioned Flow result contract and typed internal failures

**Files:**
- Create: `src/auraly_pipeline/flow/__init__.py`
- Create: `src/auraly_pipeline/flow/domain.py`
- Create: `tests/test_flow_domain.py`

**Interfaces:**
- Consumes: `auraly_pipeline.models.ContractModel` and fixed Flow URL constant.
- Produces: `FLOW_URL`, `FlowPreflightStatus`, `FlowFailedStep`, `FlowLocatorName`, `FlowPreflightResult`, `FlowRuntimeObservation`, `FlowFailureEvidence`, `FlowRuntimeError`, and the six named internal error subclasses.

- [ ] **Step 1: Write the failing contract tests**

```python
def test_ready_result_requires_authenticated_ui_ready_and_no_failure_fields() -> None:
    result = FlowPreflightResult.ready(timestamp=datetime(2026, 8, 16, tzinfo=UTC))
    payload = result.model_dump(by_alias=True, mode="json", exclude_none=False)
    assert payload["status"] == "ready"
    assert payload["success"] is True
    assert payload["authenticated"] is True
    assert payload["uiReady"] is True
    assert payload["failedStep"] is None
    assert payload["screenshot"] is None


def test_non_ready_result_rejects_success_true_and_private_artifact_paths() -> None:
    with pytest.raises(ValidationError):
        FlowPreflightResult(
            success=True,
            status="ui_contract_failed",
            flow_url=FLOW_URL,
            authenticated=True,
            ui_ready=False,
            screenshot=r"C:\Users\private\screenshot.png",
            timestamp=datetime.now(UTC),
        )
```

Also parameterize all six statuses and all eight failed steps; reject unknown values and enforce
that artifact fields are only `screenshot.png`/`trace.zip` with a `diagnosticRunId`.

- [ ] **Step 2: Run the red tests**

Run: `uv run pytest tests/test_flow_domain.py -q`
Expected: FAIL at collection because `auraly_pipeline.flow.domain` does not exist.

- [ ] **Step 3: Implement the exact public types and invariants**

```python
FLOW_URL = "https://labs.google/fx/tools/flow"

FlowPreflightStatus = Literal[
    "ready",
    "authentication_required",
    "human_intervention_required",
    "runtime_busy",
    "browser_launch_failed",
    "ui_contract_failed",
]
FlowFailedStep = Literal[
    "validate_config",
    "acquire_runtime_lock",
    "launch_browser",
    "navigate_flow",
    "await_manual_authentication",
    "verify_flow_ui",
    "sanitize_diagnostics",
    "close_browser",
]
FlowLocatorName = Literal[
    "FLOW_WORKSPACE", "CREATE_ENTRY_POINT", "PROMPT_INPUT", "ACCOUNT_IDENTITY"
]

class FlowPreflightResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    success: bool
    status: FlowPreflightStatus
    flow_url: Literal["https://labs.google/fx/tools/flow"] = FLOW_URL
    authenticated: bool
    ui_ready: bool
    failed_step: FlowFailedStep | None = None
    failed_locator: FlowLocatorName | None = None
    diagnostic_run_id: str | None = None
    screenshot: Literal["screenshot.png"] | None = None
    trace: Literal["trace.zip"] | None = None
    timestamp: datetime
```

Add a model validator for the ready/non-ready invariants, UTC timestamps, safe run IDs, and
artifact/run linkage. Define immutable internal dataclasses `FlowRuntimeObservation` and
`FlowFailureEvidence`; the latter holds only transient `screenshot_png`, `raw_trace_path`, and
test-supplied `deny_values`. Define `FlowRuntimeError` with sanitized scalar fields and subclasses
`FlowRuntimeBusyError`, `FlowBrowserLaunchError`, `FlowAuthenticationTimeoutError`,
`FlowUnexpectedStateError`, `FlowUiContractError`, and `FlowDiagnosticSanitizationError`.

```python
@dataclass(frozen=True)
class FlowRuntimeObservation:
    status: Literal["ready"] = "ready"
    authenticated: Literal[True] = True
    ui_ready: Literal[True] = True


@dataclass(frozen=True)
class FlowFailureEvidence:
    screenshot_png: bytes | None = None
    raw_trace_path: Path | None = None
    deny_values: tuple[str, ...] = ()


class FlowRuntimeError(RuntimeError):
    status: FlowPreflightStatus
    failed_step: FlowFailedStep
    authenticated: bool
    ui_ready: bool
    failed_locator: FlowLocatorName | None
    trusted_page: bool
    evidence: FlowFailureEvidence
```

Subclass defaults are fixed and require no caller-supplied public message:

| Internal error | Public status | Default failed step |
| --- | --- | --- |
| `FlowRuntimeBusyError` | `runtime_busy` | `acquire_runtime_lock` |
| `FlowBrowserLaunchError` | `browser_launch_failed` | `launch_browser` (or explicit `validate_config`/`navigate_flow`) |
| `FlowAuthenticationTimeoutError` | `authentication_required` | `await_manual_authentication` |
| `FlowUnexpectedStateError` | `human_intervention_required` | caller-supplied allowlisted phase |
| `FlowUiContractError` | `ui_contract_failed` | `verify_flow_ui` |
| `FlowDiagnosticSanitizationError` | `human_intervention_required` | `sanitize_diagnostics` |

- [ ] **Step 4: Run focused verification**

Run: `uv run pytest tests/test_flow_domain.py -q`
Run: `uv run python scripts/verify.py fast --pytest tests/test_flow_domain.py`
Expected: tests pass; Ruff and source mypy pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add src/auraly_pipeline/flow/__init__.py src/auraly_pipeline/flow/domain.py tests/test_flow_domain.py
git commit -m "feat: add Flow preflight contracts"
```

### Task 2: Safe runtime configuration and fixed production destination

**Files:**
- Create: `src/auraly_pipeline/flow/config.py`
- Create: `tests/test_flow_config.py`
- Modify: `src/auraly_pipeline/flow/__init__.py`

**Interfaces:**
- Consumes: `FLOW_URL` and `FlowBrowserLaunchError` from Task 1.
- Produces: `FlowRuntimeConfig` and `resolve_flow_runtime_config`.

- [ ] **Step 1: Write failing precedence/default/path tests**

```python
def test_config_precedence_is_cli_then_env_then_exact_defaults(tmp_path: Path) -> None:
    env = {
        "AURALY_FLOW_PROFILE_DIR": str(tmp_path / "env-profile"),
        "AURALY_FLOW_LOGIN_TIMEOUT_SECONDS": "120",
    }
    config = resolve_flow_runtime_config(
        profile_dir=tmp_path / "cli-profile",
        diagnostics_dir=tmp_path / "cli-diagnostics",
        login_timeout_seconds=45,
        environment=env,
        repository_root=REPOSITORY_ROOT,
    )
    assert config.profile_dir == (tmp_path / "cli-profile").resolve()
    assert config.login_timeout_seconds == 45
    assert config.navigation_timeout_seconds == 30
    assert config.flow_url == FLOW_URL


@pytest.mark.parametrize("unsafe_name", ["profile", "diagnostics", "lock"])
def test_config_rejects_any_runtime_path_inside_repository(
    unsafe_name: str, tmp_path: Path
) -> None:
    options: dict[str, object] = {
        "profile_dir": tmp_path / "profile",
        "diagnostics_dir": tmp_path / "diagnostics",
        "_local_state_root": tmp_path / "state",
        "repository_root": REPOSITORY_ROOT,
        "environment": {},
    }
    if unsafe_name == "profile":
        options["profile_dir"] = REPOSITORY_ROOT / "unsafe-profile"
    elif unsafe_name == "diagnostics":
        options["diagnostics_dir"] = REPOSITORY_ROOT / "unsafe-diagnostics"
    else:
        options["_local_state_root"] = REPOSITORY_ROOT / "unsafe-state"
    with pytest.raises(FlowBrowserLaunchError):
        resolve_flow_runtime_config(**options)
```

Add tests for invalid/zero/negative/non-integer env timeouts, profile/diagnostics containment,
canonical symlink/junction escape where supported, unusable paths, fixed lock path, and absence of
any `AURALY_FLOW_URL` behavior.

- [ ] **Step 2: Run the red tests**

Run: `uv run pytest tests/test_flow_config.py -q`
Expected: FAIL because `flow.config` does not exist.

- [ ] **Step 3: Implement immutable config resolution**

```python
@dataclass(frozen=True)
class FlowRuntimeConfig:
    profile_dir: Path
    diagnostics_dir: Path
    lock_path: Path
    staging_root: Path
    login_timeout_seconds: int
    navigation_timeout_seconds: int
    flow_url: Literal["https://labs.google/fx/tools/flow"] = FLOW_URL


def resolve_flow_runtime_config(
    *,
    profile_dir: Path | None = None,
    diagnostics_dir: Path | None = None,
    login_timeout_seconds: int | None = None,
    navigation_timeout_seconds: int | None = None,
    environment: Mapping[str, str] | None = None,
    repository_root: Path | None = None,
    _local_state_root: Path | None = None,
) -> FlowRuntimeConfig:
    """Resolve approved options and validate every runtime path before creating directories."""
```

Use `Path.home() / ".auraly"` for all defaults, derive the non-configurable lock and staging roots,
resolve all candidates before creating any directory, reject equality/containment in either
direction and repository containment after canonical resolution, then create only validated parent
directories with mode `0o700` where honored. Map every validation or filesystem error to
`FlowBrowserLaunchError(failed_step="validate_config")` without including the rejected path.

- [ ] **Step 4: Run focused verification**

Run: `uv run pytest tests/test_flow_config.py -q`
Run: `uv run python scripts/verify.py fast --pytest tests/test_flow_config.py`
Expected: all config/path tests, Ruff, and source mypy pass on the current platform.

- [ ] **Step 5: Commit Task 2**

```bash
git add src/auraly_pipeline/flow/config.py src/auraly_pipeline/flow/__init__.py tests/test_flow_config.py
git commit -m "feat: add safe Flow runtime config"
```

### Task 3: Native OS-level exclusive browser lock

**Files:**
- Create: `src/auraly_pipeline/flow/lock.py`
- Create: `tests/test_flow_lock.py`
- Create: `tests/flow_lock_holder.py`
- Modify: `src/auraly_pipeline/flow/__init__.py`

**Interfaces:**
- Consumes: validated `FlowRuntimeConfig.lock_path` and `FlowRuntimeBusyError`.
- Produces: `BrowserRuntimeLock.acquire()`, `release()`, and context-manager behavior.

- [ ] **Step 1: Write the failing real-process lock tests**

```python
def test_second_process_fails_immediately_while_first_owns_lock(tmp_path: Path) -> None:
    holder = subprocess.Popen(
        [sys.executable, str(LOCK_HOLDER), str(tmp_path / "flow.lock")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None and holder.stdout.readline().strip() == "locked"
    with pytest.raises(FlowRuntimeBusyError):
        BrowserRuntimeLock(tmp_path / "flow.lock").acquire()
    assert holder.stdin is not None
    holder.stdin.write("release\n")
    holder.stdin.flush()
    assert holder.wait(timeout=5) == 0


def test_process_exit_releases_kernel_lock_even_when_file_remains(tmp_path: Path) -> None:
    lock_path = tmp_path / "flow.lock"
    holder = start_lock_holder(lock_path)
    holder.kill()
    holder.wait(timeout=5)
    with BrowserRuntimeLock(lock_path):
        assert lock_path.is_file()
```

The helper acquires the real lock, prints `locked`, waits for stdin/termination, and never deletes
the file. Add same-process release/idempotent-finally tests.

- [ ] **Step 2: Run the red tests**

Run: `uv run pytest tests/test_flow_lock.py -q`
Expected: FAIL because `BrowserRuntimeLock` does not exist.

- [ ] **Step 3: Implement the non-blocking cross-platform lock**

```python
class BrowserRuntimeLock:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle: BinaryIO | None = None

    def acquire(self) -> None:
        """Acquire one byte non-blockingly or raise FlowRuntimeBusyError."""

    def release(self) -> None:
        """Unlock and close the owned handle without deleting the lock file."""

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()
```

Open the fixed file in binary append/update mode, ensure at least one byte exists, and use
`msvcrt.locking(handle.fileno(), LK_NBLCK, 1)` on Windows or
`fcntl.flock(handle.fileno(), LOCK_EX | LOCK_NB)` on POSIX.
Translate only contention to `FlowRuntimeBusyError`; close the handle on every failed acquisition.
Unlock and close in `release()` without deleting the file.

- [ ] **Step 4: Run focused verification**

Run: `uv run pytest tests/test_flow_lock.py -q`
Run: `uv run python scripts/verify.py fast --pytest tests/test_flow_lock.py`
Expected: real subprocess contention and crash-release tests pass.

- [ ] **Step 5: Commit Task 3**

```bash
git add src/auraly_pipeline/flow/lock.py src/auraly_pipeline/flow/__init__.py tests/test_flow_lock.py tests/flow_lock_holder.py
git commit -m "feat: lock Flow browser runtime"
```

### Task 4: Central semantic locator contract and local Flow pages

**Files:**
- Create: `src/auraly_pipeline/flow/locators.py`
- Create: `tests/test_flow_locators.py`
- Create: `tests/flow_browser_support.py`
- Create: `tests/fakes/flow/ready.html`
- Create: `tests/fakes/flow/login-required.html`
- Create: `tests/fakes/flow/login-completes.html`
- Create: `tests/fakes/flow/ambiguous-ui.html`
- Create: `tests/fakes/flow/missing-prompt.html`
- Create: `tests/fakes/flow/blocking-modal.html`

**Interfaces:**
- Consumes: `FlowLocatorName` and `FlowUiContractError`.
- Produces: `LocatorStrategy`, `RequiredLocator`, `REQUIRED_FLOW_LOCATORS`, `resolve_required_locator(page, name)`, `resolve_account_identity_masks(page)`, and `blocking_overlay_present(page)` using local Protocols rather than runtime Playwright imports.

- [ ] **Step 1: Install managed Chromium and write failing locator tests**

Run once for the development machine: `uv run playwright install chromium`

```python
def test_ready_page_resolves_exactly_one_required_semantic_element(flow_page: Page) -> None:
    flow_page.goto(fake_flow_url("ready.html"))
    for name in ("FLOW_WORKSPACE", "CREATE_ENTRY_POINT", "PROMPT_INPUT"):
        assert resolve_required_locator(flow_page, name).count() == 1


@pytest.mark.parametrize("fixture", ["missing-prompt.html", "ambiguous-ui.html"])
def test_zero_or_multiple_prompt_matches_fail_closed(flow_page: Page, fixture: str) -> None:
    flow_page.goto(fake_flow_url(fixture))
    with pytest.raises(FlowUiContractError) as caught:
        resolve_required_locator(flow_page, "PROMPT_INPUT")
    assert caught.value.failed_locator == "PROMPT_INPUT"
```

Add a blocking-dialog test and a source scan asserting locator code contains no XPath,
`nth-child`, coordinates, `.first`, positional indexing, or structural CSS selectors.

- [ ] **Step 2: Run the red tests under headed Chromium**

Windows run: `uv run pytest tests/test_flow_locators.py -q`
Linux run: `xvfb-run -a uv run pytest tests/test_flow_locators.py -q`
Expected: FAIL because `flow.locators` and local pages do not exist.

- [ ] **Step 3: Implement the exact initial semantic contract and fixtures**

```python
REQUIRED_FLOW_LOCATORS = {
    "FLOW_WORKSPACE": RequiredLocator(
        name="FLOW_WORKSPACE",
        strategies=(LocatorStrategy("role", role="main", value="Flow workspace"),),
    ),
    "CREATE_ENTRY_POINT": RequiredLocator(
        name="CREATE_ENTRY_POINT",
        strategies=(LocatorStrategy("role", role="button", value="Create"),),
    ),
    "PROMPT_INPUT": RequiredLocator(
        name="PROMPT_INPUT",
        strategies=(
            LocatorStrategy("label", value="Prompt"),
            LocatorStrategy("placeholder", value="Describe your image"),
        ),
    ),
}
```

Each strategy is tried in order, but only a strategy resolving exactly one visible and applicable
enabled element succeeds. Zero across all strategies and 2+ in any otherwise viable strategy fail
closed; never choose one match. `ACCOUNT_IDENTITY` is mask-only and resolves a semantic button
named `Google Account`. `blocking_overlay_present()` checks visible accessible dialogs and alert
dialogs without clicking. The fixture pages use exactly these roles/labels and contain no network
resource.

- [ ] **Step 4: Run focused verification**

Windows run: `uv run python scripts/verify.py fast --pytest tests/test_flow_locators.py`
Linux run: `xvfb-run -a uv run python scripts/verify.py fast --pytest tests/test_flow_locators.py`
Expected: locator uniqueness, ambiguity, overlay, and forbidden-selector tests pass.

- [ ] **Step 5: Commit Task 4**

```bash
git add src/auraly_pipeline/flow/locators.py tests/test_flow_locators.py tests/flow_browser_support.py tests/fakes/flow
git commit -m "feat: define Flow semantic UI contract"
```

### Task 5: Append-only sanitized diagnostic publication

**Files:**
- Create: `src/auraly_pipeline/flow/diagnostics.py`
- Create: `tests/test_flow_diagnostics.py`
- Modify: `src/auraly_pipeline/flow/__init__.py`

**Interfaces:**
- Consumes: `FlowPreflightResult`, `FlowFailureEvidence`, `FlowDiagnosticSanitizationError`, and validated diagnostics/staging roots.
- Produces: `sanitize_trace_archive` and `FlowDiagnosticWriter.write_failure() -> FlowPreflightResult`.

- [ ] **Step 1: Write failing append-only and denylist tests**

```python
def test_trace_sanitizer_drops_network_resources_sources_and_url_secrets(tmp_path: Path) -> None:
    raw = build_synthetic_trace(
        tmp_path,
        trace_url="https://labs.google/fx/tools/flow?token=SECRET#fragment",
        network_headers={"cookie": "session=SECRET"},
        resource_body=b"PRIVATE_PROMPT",
    )
    sanitize_trace_archive(raw, tmp_path / "safe.zip", deny_values=("SECRET", "PRIVATE_PROMPT"))
    expanded = expanded_zip_bytes(tmp_path / "safe.zip")
    assert b"trace.network" not in expanded
    assert b"resources/" not in expanded
    assert b"?token=" not in expanded
    assert b"SECRET" not in expanded


def test_writer_creates_unique_runs_without_overwrite_or_cleanup(tmp_path: Path) -> None:
    first = writer.write_failure(failure_result, evidence=FlowFailureEvidence())
    second = writer.write_failure(failure_result, evidence=FlowFailureEvidence())
    assert first.diagnostic_run_id != second.diagnostic_run_id
    assert set(path.name for path in tmp_path.iterdir()) == {
        first.diagnostic_run_id, second.diagnostic_run_id
    }
```

Add the complete evidence matrix: result-only for auth/busy/pre-trust launch failures; result plus
sanitized screenshot/trace for authenticated trusted UI failures; no success diagnostics; JSON
contains only allowlisted fields and relative artifact names. Seed email, cookie, token, query,
prompt, and Windows/POSIX private paths in synthetic inputs and expanded archives.

- [ ] **Step 2: Run the red tests**

Run: `uv run pytest tests/test_flow_diagnostics.py -q`
Expected: FAIL because `flow.diagnostics` does not exist.

- [ ] **Step 3: Implement staged sanitization and exclusive publication**

```python
def sanitize_trace_archive(
    raw_path: Path,
    output_path: Path,
    *,
    deny_values: Sequence[str] = (),
) -> None:
    """Publish a query-free, resource-free allowlisted trace archive or raise."""


class FlowDiagnosticWriter:
    def __init__(self, diagnostics_dir: Path, staging_root: Path) -> None:
        self._diagnostics_dir = diagnostics_dir
        self._staging_root = staging_root

    def write_failure(
        self,
        result: FlowPreflightResult,
        *,
        evidence: FlowFailureEvidence,
    ) -> FlowPreflightResult:
        """Sanitize in staging, publish one exclusive run, and return artifact references."""
```

The trace sanitizer reads only `trace.trace`, parses it as JSON lines, recursively removes
`headers`, `request`, `response`, `body`, `postData`, `snapshot`, `html`, `source`, and resource
references, and canonicalizes every HTTP(S) URL to scheme + host + path. It never copies
`trace.network`, resources, sources, DOM snapshots, or unknown archive members. Validate the staged
archive and screenshot bytes against fixed deny patterns plus `deny_values` before publication.

Prepare all safe artifacts in a unique staging directory, then create the final run directory with
`mkdir(exist_ok=False)`, use exclusive file creation, write artifacts first and `result.json` last,
and remove raw/staged material in `finally`. On sanitizer failure, publish no raw artifact and raise
`FlowDiagnosticSanitizationError`; the service in Task 7 will emit a result-only sanitized
`human_intervention_required` run with `failedStep="sanitize_diagnostics"`.

- [ ] **Step 4: Run focused verification**

Run: `uv run pytest tests/test_flow_diagnostics.py -q`
Run: `uv run python scripts/verify.py fast --pytest tests/test_flow_diagnostics.py`
Expected: archive expansion, append-only behavior, failure matrix, and denylist tests pass.

- [ ] **Step 5: Commit Task 5**

```bash
git add src/auraly_pipeline/flow/diagnostics.py src/auraly_pipeline/flow/__init__.py tests/test_flow_diagnostics.py
git commit -m "feat: sanitize Flow failure diagnostics"
```

### Task 6: Headed persistent Chromium runtime, manual auth, and safe closure

**Files:**
- Create: `src/auraly_pipeline/flow/runtime.py`
- Create: `tests/test_flow_runtime.py`
- Modify: `tests/fakes/flow/login-required.html`
- Modify: `tests/fakes/flow/login-completes.html`
- Modify: `src/auraly_pipeline/flow/__init__.py`

**Interfaces:**
- Consumes: `FlowRuntimeConfig`, locator functions, observations/evidence/errors, and Playwright's synchronous API.
- Produces: `GoogleFlowRuntime.run() -> FlowRuntimeObservation`; private `_FlowRuntimeTarget` and `_local_test_target` exist only for tests inside this module and are not exported.

- [ ] **Step 1: Write failing real-browser and lifecycle tests**

```python
def test_ready_uses_managed_persistent_headed_context_without_actions(tmp_path: Path) -> None:
    runtime = GoogleFlowRuntime(config(tmp_path), _target=local_target("ready.html"))
    observation = runtime.run()
    assert observation.authenticated is True
    assert observation.ui_ready is True
    assert observation.status == "ready"
    assert no_recorded_click_fill_upload_generate_or_download(tmp_path)


def test_login_timeout_has_no_screenshot_or_trace(tmp_path: Path) -> None:
    runtime = GoogleFlowRuntime(
        config(tmp_path, login_timeout_seconds=1),
        _target=local_target("login-required.html"),
    )
    with pytest.raises(FlowAuthenticationTimeoutError) as caught:
        runtime.run()
    assert caught.value.evidence == FlowFailureEvidence()


@pytest.mark.parametrize("fixture", ["missing-prompt.html", "ambiguous-ui.html"])
def test_ui_failure_captures_masked_screenshot_and_raw_trace_before_close(
    tmp_path: Path, fixture: str
) -> None:
    runtime = GoogleFlowRuntime(config(tmp_path), _target=local_target(fixture))
    with pytest.raises(FlowUiContractError) as caught:
        runtime.run()
    evidence = caught.value.evidence
    assert evidence.screenshot_png is not None
    assert evidence.raw_trace_path is not None and evidence.raw_trace_path.is_file()
```

Add tests for login page transitioning itself to the ready page, blocking modal, unexpected route,
launch exception before trust, exception after trust, query/fragment stripping in classification,
fixed deadline not reset by page activity, and fake-context closure failure converting a would-be
ready result into `human_intervention_required`/`close_browser`.

- [ ] **Step 2: Run the red tests under a headed display**

Windows run: `uv run pytest tests/test_flow_runtime.py -q`
Linux run: `xvfb-run -a uv run pytest tests/test_flow_runtime.py -q`
Expected: FAIL because `GoogleFlowRuntime` does not exist.

- [ ] **Step 3: Implement the sole Playwright controller**

```python
class GoogleFlowRuntime:
    def __init__(
        self,
        config: FlowRuntimeConfig,
        *,
        _target: _FlowRuntimeTarget | None = None,
        _playwright_factory: Callable[[], AbstractContextManager[Playwright]] = sync_playwright,
        _monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._target = PRODUCTION_TARGET if _target is None else _target
        self._playwright_factory = _playwright_factory
        self._monotonic = _monotonic

    def run(self) -> FlowRuntimeObservation:
        """Run observation-only preflight, close all browser resources, or raise a typed failure."""
```

Production always substitutes the module-private immutable target containing the fixed Flow URL,
trusted origin/path, and exact recognized auth origin `https://accounts.google.com`. Tests alone
construct a local `file:` target with explicit Flow/login paths; neither service nor CLI accepts it.

Launch only `playwright.chromium.launch_persistent_context(user_data_dir=self._config.profile_dir, headless=False)` with
the configured navigation timeout. Navigate to the target, classify auth/route without query or
fragment, poll every 500 ms until the original monotonic deadline, and start tracing only after the
trusted authenticated Flow page exists using
`tracing.start(screenshots=False, snapshots=False, sources=False)`.

Resolve all required locators and blocking overlays without clicking or typing. On eligible failure,
capture `page.screenshot(mask=resolve_account_identity_masks(page), mask_color="#000000")`, stop
the trace to a unique raw staging path, and attach both only to transient `FlowFailureEvidence`.
On success, stop tracing without a path. Always close the persistent context and Playwright in
`finally`; never leave the browser open. Wrap only sanitized status/step/locator metadata in typed
errors and discard raw Playwright exception text.

- [ ] **Step 4: Run focused verification**

Windows run: `uv run python scripts/verify.py fast --pytest tests/test_flow_runtime.py`
Linux run: `xvfb-run -a uv run python scripts/verify.py fast --pytest tests/test_flow_runtime.py`
Expected: real local headed browser, manual-auth simulation, UI safe-stop, evidence eligibility, and
closure tests pass.

- [ ] **Step 5: Commit Task 6**

```bash
git add src/auraly_pipeline/flow/runtime.py src/auraly_pipeline/flow/__init__.py tests/test_flow_runtime.py tests/fakes/flow/login-required.html tests/fakes/flow/login-completes.html
git commit -m "feat: add headed Flow browser runtime"
```

### Task 7: Preflight service orchestration and public error mapping

**Files:**
- Create: `src/auraly_pipeline/flow/service.py`
- Create: `tests/test_flow_service.py`
- Modify: `src/auraly_pipeline/flow/__init__.py`

**Interfaces:**
- Consumes: config resolver, native lock, runtime, diagnostic writer, and all typed errors.
- Produces: `FlowPreflightService.preflight() -> FlowPreflightResult` as the only public application entry point.

Define the injectable types in `flow/service.py` exactly as follows. `ConfigResolver` is a keyword
Protocol because the public config resolver has keyword-only options; the three factories are exact
constructor callables from Tasks 3, 5, and 6.

```python
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, TypeAlias


class ConfigResolver(Protocol):
    def __call__(
        self,
        *,
        profile_dir: Path | None = None,
        diagnostics_dir: Path | None = None,
        login_timeout_seconds: int | None = None,
        navigation_timeout_seconds: int | None = None,
    ) -> FlowRuntimeConfig: ...


LockFactory: TypeAlias = Callable[[Path], BrowserRuntimeLock]
RuntimeFactory: TypeAlias = Callable[[FlowRuntimeConfig], GoogleFlowRuntime]
DiagnosticWriterFactory: TypeAlias = Callable[[Path, Path], FlowDiagnosticWriter]


def utc_now() -> datetime:
    return datetime.now(UTC)
```

`FlowPreflightService.preflight()` calls `_config_resolver` with the four named keyword arguments
above, constructs `_lock_factory(config.lock_path)`, `_runtime_factory(config)`, and
`_diagnostic_writer_factory(config.diagnostics_dir, config.staging_root)`. Test fakes must implement
the same signatures rather than relying on untyped `lambda *args, **kwargs` behavior.

- [ ] **Step 1: Write failing orchestration tests with injected fakes**

```python
def test_service_holds_lock_until_runtime_has_closed_and_returns_ready() -> None:
    events: list[str] = []
    service = FlowPreflightService(
        _lock_factory=lambda _: RecordingLock(events),
        _runtime_factory=lambda _: RecordingRuntime(events, status="ready"),
    )
    result = service.preflight()
    assert result.status == "ready"
    assert events == ["lock.acquire", "runtime.run", "runtime.close", "lock.release"]


@pytest.mark.parametrize(
    ("error_type", "status"),
    [
        (FlowRuntimeBusyError, "runtime_busy"),
        (FlowBrowserLaunchError, "browser_launch_failed"),
        (FlowAuthenticationTimeoutError, "authentication_required"),
        (FlowUnexpectedStateError, "human_intervention_required"),
        (FlowUiContractError, "ui_contract_failed"),
    ],
)
def test_service_maps_internal_errors_to_exact_public_statuses(error_type: type[Exception], status: str) -> None:
    service = service_with_runtime_error(error_type)
    result = service.preflight()
    assert result.status == status
    assert result.success is False
```

Add tests that config failure occurs before lock/browser, busy never launches, every non-ready result
writes `result.json` when diagnostics are available, ready writes none, diagnostic sanitizer failure
becomes result-only `human_intervention_required`, unknown exceptions map according to trusted-page
state, and raw exception text/path never reaches the result.

- [ ] **Step 2: Run the red tests**

Run: `uv run pytest tests/test_flow_service.py -q`
Expected: FAIL because `flow.service` does not exist.

- [ ] **Step 3: Implement the public service and dependency seams**

```python
class FlowPreflightService:
    def __init__(
        self,
        *,
        _config_resolver: ConfigResolver = resolve_flow_runtime_config,
        _lock_factory: LockFactory = BrowserRuntimeLock,
        _runtime_factory: RuntimeFactory = GoogleFlowRuntime,
        _diagnostic_writer_factory: DiagnosticWriterFactory = FlowDiagnosticWriter,
        _now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._config_resolver = _config_resolver
        self._lock_factory = _lock_factory
        self._runtime_factory = _runtime_factory
        self._diagnostic_writer_factory = _diagnostic_writer_factory
        self._now = _now

    def preflight(
        self,
        *,
        profile_dir: Path | None = None,
        diagnostics_dir: Path | None = None,
        login_timeout_seconds: int | None = None,
        navigation_timeout_seconds: int | None = None,
    ) -> FlowPreflightResult:
        """Resolve config, hold the exclusive lock, run preflight, and map sanitized results."""
```

Resolve config, acquire the lock without waiting, run the browser, verify closure is complete, then
release the lock in `finally`. Build public results only through allowlisted constructors. For every
failure with a validated diagnostics root, call the writer; if writer sanitization fails, discard
unsafe evidence and write a fresh result-only `human_intervention_required` result with
`sanitize_diagnostics`. Do not expose the private runtime target or accept a URL.

- [ ] **Step 4: Run focused verification**

Run: `uv run pytest tests/test_flow_service.py -q`
Run: `uv run python scripts/verify.py fast --pytest tests/test_flow_service.py`
Expected: mapping, ordering, diagnostics, and finally-path tests pass.

- [ ] **Step 5: Commit Task 7**

```bash
git add src/auraly_pipeline/flow/service.py src/auraly_pipeline/flow/__init__.py tests/test_flow_service.py
git commit -m "feat: orchestrate Flow browser preflight"
```

### Task 8: `auraly flow preflight` JSON CLI

**Files:**
- Modify: `src/auraly_pipeline/cli.py`
- Create: `tests/test_flow_cli.py`

**Interfaces:**
- Consumes: `FlowPreflightService.preflight()` and `FlowPreflightResult`.
- Produces: `flow_app` and `flow_preflight_command` with the four approved options and stable JSON/null behavior.

- [ ] **Step 1: Write failing CLI contract tests**

```python
@pytest.mark.parametrize(
    ("status", "exit_code"),
    [
        ("ready", 0),
        ("authentication_required", 1),
        ("human_intervention_required", 1),
        ("runtime_busy", 1),
        ("browser_launch_failed", 1),
        ("ui_contract_failed", 1),
    ],
)
def test_flow_preflight_emits_one_json_object_and_exact_exit_code(status: str, exit_code: int, monkeypatch: pytest.MonkeyPatch) -> None:
    result_value = result_for_status(status)
    monkeypatch.setattr(FlowPreflightService, "preflight", lambda self, **kwargs: result_value)
    invocation = runner.invoke(app, ["flow", "preflight"])
    assert invocation.exit_code == exit_code
    assert json.loads(invocation.stdout)["status"] == status
    assert invocation.stdout.count("{\n") == 1


def test_flow_help_exposes_only_approved_options() -> None:
    result = runner.invoke(app, ["flow", "preflight", "--help"])
    assert {"--profile-dir", "--diagnostics-dir", "--login-timeout", "--navigation-timeout"} <= set(result.stdout.split())
    for forbidden in ("--url", "--headless", "--channel", "--keep-open", "--storage-state"):
        assert forbidden not in result.stdout
```

Also assert all null keys are present, stdout parses as exactly one JSON document, stderr/stdout have
no traceback/private path/raw exception, and no database/work-root/Job option is exposed.

- [ ] **Step 2: Run the red tests**

Run: `uv run pytest tests/test_flow_cli.py -q`
Expected: FAIL because the `flow` command group does not exist.

- [ ] **Step 3: Add the thin Typer binding**

```python
flow_app = typer.Typer(help="Safely inspect the local Google Flow browser runtime.", no_args_is_help=True)
app.add_typer(flow_app, name="flow")

@flow_app.command("preflight")
def flow_preflight_command(
    profile_dir: Annotated[Path | None, typer.Option("--profile-dir")] = None,
    diagnostics_dir: Annotated[Path | None, typer.Option("--diagnostics-dir")] = None,
    login_timeout: Annotated[int | None, typer.Option("--login-timeout", min=1)] = None,
    navigation_timeout: Annotated[int | None, typer.Option("--navigation-timeout", min=1)] = None,
) -> None:
    result = FlowPreflightService().preflight(
        profile_dir=profile_dir,
        diagnostics_dir=diagnostics_dir,
        login_timeout_seconds=login_timeout,
        navigation_timeout_seconds=navigation_timeout,
    )
    _json_echo(result.model_dump(by_alias=True, mode="json", exclude_none=False))
    if result.status != "ready":
        raise typer.Exit(code=1)
```

Catch only unexpected CLI-boundary failures, converting them to the fixed
`browser_launch_failed`/`validate_config` JSON without exception text. Do not add logging that can
interleave with stdout.

- [ ] **Step 4: Run focused verification**

Run: `uv run pytest tests/test_flow_cli.py -q`
Run: `uv run python scripts/verify.py fast --pytest tests/test_flow_cli.py`
Expected: all six statuses, option surface, null fields, JSON, and sanitization tests pass.

- [ ] **Step 5: Commit Task 8**

```bash
git add src/auraly_pipeline/cli.py tests/test_flow_cli.py
git commit -m "feat: expose Flow preflight CLI"
```

### Task 9: End-to-end local security and lifecycle regression

**Files:**
- Create: `tests/test_flow_security.py`
- Modify only Goal 4B files required by failing regressions.

**Interfaces:**
- Consumes: complete Tasks 1–8 production path and local HTML target seam.
- Produces: security regression proof for forbidden actions/imports, redaction, browser cleanup, lock release, and no Job/DB integration.

- [ ] **Step 1: Write the integrated failing/security tests**

```python
def test_authenticated_ui_failure_publishes_only_sanitized_evidence(tmp_path: Path) -> None:
    secret_values = ("person@example.com", "COOKIE_SECRET", "PRIVATE_PROMPT", r"C:\Users\private")
    result = local_preflight(
        fixture="missing-prompt.html?token=QUERY_SECRET#fragment",
        deny_values=(*secret_values, "QUERY_SECRET"),
        tmp_path=tmp_path,
    )
    assert result.status == "ui_contract_failed"
    run_dir = diagnostics_dir(tmp_path) / str(result.diagnostic_run_id)
    published = b"\n".join(path.read_bytes() for path in run_dir.rglob("*") if path.is_file())
    expanded_trace = expanded_zip_bytes(run_dir / "trace.zip")
    for forbidden in (*secret_values, "QUERY_SECRET", "?token=", "#fragment"):
        assert forbidden.encode() not in published + expanded_trace


def test_flow_package_has_no_job_database_provider_actions_or_unsafe_browser_controls() -> None:
    source = read_flow_package_source()
    for forbidden in (
        "auraly_pipeline.jobs", "auraly_pipeline.images", "auraly_pipeline.campaigns",
        "sqlalchemy", ".click(", ".fill(", "set_input_files", "expect_download",
        "headless=True", 'channel="chrome"', "storage_state",
    ):
        assert forbidden not in source
```

Add a full local service run that proves context process closure and immediate reacquisition of the
real lock after `ready`, UI failure, auth timeout, and injected exception. Verify the persistent
profile remains outside the repository while diagnostics never copy it. Verify account masking by
asserting the runtime calls `page.screenshot()` with the exact semantic account locator in `mask`
and opaque black `mask_color`, rather than relying on binary PNG text search alone.

- [ ] **Step 2: Run the regression tests and observe any genuine failures**

Windows run: `uv run pytest tests/test_flow_security.py -q`
Linux run: `xvfb-run -a uv run pytest tests/test_flow_security.py -q`
Expected: any unmet cross-component safety invariant fails with a focused assertion; do not weaken
the test to accommodate production behavior.

- [ ] **Step 3: Make only regression-driven Goal 4B fixes**

```text
For each failing invariant:
1. keep the failing assertion;
2. change only the smallest Goal 4B module owning that behavior;
3. rerun the single failing test;
4. rerun the entire security file.
```

Do not introduce Generate/upload/download/QC, a generic browser framework, or changes to Goal 4A.

- [ ] **Step 4: Run the integrated focused set**

Windows run:

```bash
uv run python scripts/verify.py fast --pytest \
  tests/test_flow_domain.py \
  tests/test_flow_config.py \
  tests/test_flow_lock.py \
  tests/test_flow_locators.py \
  tests/test_flow_diagnostics.py \
  tests/test_flow_runtime.py \
  tests/test_flow_service.py \
  tests/test_flow_cli.py \
  tests/test_flow_security.py
```

On Linux, prefix the same command with `xvfb-run -a`. Expected: all Goal 4B focused tests, Ruff,
and source mypy pass.

- [ ] **Step 5: Commit Task 9**

```bash
git add tests/test_flow_security.py src/auraly_pipeline/flow src/auraly_pipeline/cli.py
git commit -m "test: harden Flow runtime safety"
```

Before committing, inspect `git diff --name-only` and exclude `cli.py` or any flow module that did
not require a regression fix; never stage unrelated user changes.

### Task 10: Managed Chromium installation and deterministic CI coverage

**Files:**
- Modify: `.github/workflows/verify.yml`
- Modify: `tests/test_verify_harness.py`

**Interfaces:**
- Consumes: complete deterministic Goal 4B test set and existing Linux full/Windows-focused jobs.
- Produces: managed Chromium availability, headed Linux display, and focused Windows protection without live Google access.

- [ ] **Step 1: Write failing workflow-contract tests**

```python
def test_linux_ci_installs_managed_chromium_and_runs_full_under_xvfb() -> None:
    commands = workflow_commands("linux-full")
    assert "uv sync --locked --all-groups" in commands
    assert "uv run playwright install --with-deps chromium" in commands
    assert "xvfb-run -a uv run python scripts/verify.py full" in commands


def test_windows_ci_installs_chromium_and_includes_goal_4b_targets() -> None:
    commands = workflow_commands("windows-focused")
    assert "uv run playwright install chromium" in commands
    focused = next(command for command in commands if "scripts/verify.py fast" in command)
    for target in FLOW_TEST_TARGETS:
        assert target in focused
    assert "labs.google" not in "\n".join(commands)
```

Keep existing FFmpeg, Node, image, job, permissions, and no-secret assertions intact.

- [ ] **Step 2: Run the red workflow tests**

Run: `uv run pytest tests/test_verify_harness.py -q`
Expected: FAIL because CI does not install Chromium, use Xvfb for the full suite, or select Flow tests.

- [ ] **Step 3: Update CI minimally**

Linux steps, in order after Python/uv setup and before full verification:

```yaml
- name: Synchronize locked dependencies
  run: uv sync --locked --all-groups
- name: Install Playwright-managed Chromium and system dependencies
  run: uv run playwright install --with-deps chromium
- name: Run full deterministic verification
  run: xvfb-run -a uv run python scripts/verify.py full
```

Preserve application FFmpeg installation and Node setup. Windows keeps its existing sync, adds:

```yaml
- name: Install Playwright-managed Chromium
  run: uv run playwright install chromium
```

Append all nine `tests/test_flow_*.py` files to the existing focused command. Do not add a live
preflight, Google account, secrets, browser profile cache, or provider call to CI.

- [ ] **Step 4: Run workflow and focused verification**

Run: `uv run pytest tests/test_verify_harness.py -q`
Run: `uv run playwright install --dry-run chromium`
Run: `uv run python scripts/verify.py fast --pytest tests/test_verify_harness.py tests/test_flow_lock.py tests/test_flow_runtime.py tests/test_flow_security.py`
Expected: workflow contract passes; dry-run reports managed Chromium; focused suite passes locally.

- [ ] **Step 5: Commit Task 10**

```bash
git add .github/workflows/verify.yml tests/test_verify_harness.py
git commit -m "ci: verify headed Flow browser runtime"
```

### Task 11: Goal 4B closure, independent review, documentation, and final evidence

**Files:**
- Modify: `README.md`
- Modify: `docs/GOAL-ROADMAP.md`
- Modify: `docs/PROJECT-MEMORY.md`
- Modify only files required by accepted reviewer findings and their regression tests.

**Interfaces:**
- Consumes: Tasks 1–10, the common deterministic harness, and both GitHub Actions jobs.
- Produces: evidence-backed `IMPLEMENTED`/`LOCAL_VERIFIED` documentation while leaving `PROVIDER_VERIFIED` unclaimed.

- [ ] **Step 1: Run the complete local deterministic gate**

```bash
uv run playwright install chromium
uv run playwright install --dry-run chromium
uv run python scripts/verify.py full
git diff --check
```

On Linux, run the full harness as `xvfb-run -a uv run python scripts/verify.py full`. Expected: all
harness steps pass, all Flow tests use local pages, and no live provider request occurs.

- [ ] **Step 2: Perform the local pre-review; it is not the independent review**

```bash
git status --short
git diff --stat 35aebb09628b1363990bb2fe51b05e0598c085d6..HEAD
git diff --name-only 35aebb09628b1363990bb2fe51b05e0598c085d6..HEAD
rg -n "Generate|set_input_files|expect_download|headless=True|channel=|storage_state|sqlalchemy|JobService|ImageService" src/auraly_pipeline/flow tests/test_flow_*.py
```

Review every match in context. Accept only negative assertions, status text, or the required
`headless=False` launch. Confirm no prompt, upload, download, candidate, QC, Job/DB integration,
personal profile, arbitrary URL, raw trace, secret, or private path entered production output.

This is an implementer self-check only. It must never be reported as, substituted for, or used to
waive the independent review required in Step 3.

- [ ] **Step 3: Request a fresh independent reviewer with `superpowers:requesting-code-review`**

After Step 1 passes, obtain the implementation range:

```bash
git rev-parse 35aebb09628b1363990bb2fe51b05e0598c085d6
git rev-parse HEAD
git diff --stat 35aebb09628b1363990bb2fe51b05e0598c085d6..HEAD
git diff 35aebb09628b1363990bb2fe51b05e0598c085d6..HEAD
```

Invoke `superpowers:requesting-code-review` to dispatch a fresh reviewer/subagent who did not
implement Goal 4B and is read-only on the checkout. Supply this complete reviewer brief, replacing
`<IMPLEMENTATION_HEAD>` with the SHA returned by `git rev-parse HEAD`:

```text
Review Goal 4B — Google Flow Browser Runtime independently and read-only.

Approved design spec:
docs/superpowers/specs/2026-08-16-goal-4b-google-flow-browser-runtime-design.md

Approved implementation plan:
docs/superpowers/plans/2026-08-16-goal-4b-google-flow-browser-runtime-plan.md

Baseline commit:
35aebb09628b1363990bb2fe51b05e0598c085d6

Implementation HEAD:
<IMPLEMENTATION_HEAD>

Inspect:
git diff --stat 35aebb09628b1363990bb2fe51b05e0598c085d6..<IMPLEMENTATION_HEAD>
git diff 35aebb09628b1363990bb2fe51b05e0598c085d6..<IMPLEMENTATION_HEAD>

Verify all of the following:
- compliance with the approved design spec and this plan;
- scope creep or accidental Goal 4C/4D behavior;
- browser safety, headed-only Playwright-managed Chromium, and no unsafe interaction;
- secret/private-data leakage through results, logs, screenshots, traces, paths, or profiles;
- browser/context cleanup and closure on every path;
- native lock correctness and concurrency-one behavior;
- manual authentication boundary and timeout handling;
- semantic locator uniqueness and fail-closed behavior;
- status-specific diagnostics sanitization and append-only publication; and
- test coverage gaps, especially real local-browser, OS-lock, CLI, and CI coverage.

Classify findings exactly as Critical, High, or Minor. Give every finding a file:line reference,
impact, and specific remediation. Critical and High findings block Goal 4B closure. State whether
the reviewed implementation is ready for closure only after considering the exact range above.
```

The reviewer returns strengths, findings, and an explicit assessment. The implementer/coordinator
may clarify a finding with evidence, but cannot self-approve closure or downgrade a valid Critical
or High finding without a documented technical reason and a new reviewer response.

- [ ] **Step 4: Fix accepted Critical or High findings with red-green commits**

Critical and High findings block closure. For each accepted finding—every Critical/High item and any
Minor item the user explicitly elects to fix—first add one focused regression test that fails on the
reviewed implementation, prove the red result, make the minimum Goal 4B fix, rerun the focused test
and the relevant fast harness, then commit the regression and fix separately from closure
documentation. Preserve unaccepted Minor findings in the review report. Do not bundle reviewer
fixes into docs.

- [ ] **Step 5: Re-run focused and full verification after reviewer fixes**

```bash
uv run pytest tests/test_flow_domain.py tests/test_flow_config.py tests/test_flow_lock.py tests/test_flow_locators.py tests/test_flow_diagnostics.py tests/test_flow_runtime.py tests/test_flow_service.py tests/test_flow_cli.py tests/test_flow_security.py -q
uv run python scripts/verify.py fast --pytest tests/test_flow_domain.py tests/test_flow_config.py tests/test_flow_lock.py tests/test_flow_locators.py tests/test_flow_diagnostics.py tests/test_flow_runtime.py tests/test_flow_service.py tests/test_flow_cli.py tests/test_flow_security.py
uv run playwright install --dry-run chromium
uv run python scripts/verify.py full
git diff --check
```

On Linux, use `xvfb-run -a` for browser-bearing pytest/full commands. If no Critical or High
findings were accepted, run the full command and whitespace check anyway. Do not proceed to closure
documentation until this post-review full verification passes.

- [ ] **Step 6: Update truthful closure documentation**

Record only proven durable behavior:

```text
Goal 4B — Google Flow Browser Runtime
IMPLEMENTED       YES
LOCAL_VERIFIED    YES
PROVIDER_VERIFIED NOT ESTABLISHED BY GOAL 4B
BROWSER_PREFLIGHT_VERIFIED not run unless separately approved and evidenced
```

README summarizes the independent preflight and exact non-generation boundary. Roadmap marks Goal
4B implemented/local only after Steps 1–5 pass. PROJECT-MEMORY records the persistent external
profile, manual headed authentication, semantic fail-closed locator contract, OS lock, sanitized
append-only diagnostics, closure behavior, and that Goals 4C/4D remain deferred. Do not claim a
live Flow run or provider verification.

- [ ] **Step 7: Re-run the closure gate after documentation and commit**

```bash
uv run python scripts/verify.py full
git diff --check
git add README.md docs/GOAL-ROADMAP.md docs/PROJECT-MEMORY.md
git commit -m "docs: close Goal 4B implementation"
```

On Linux, use the Xvfb wrapper for the full harness. Expected: full deterministic gate passes on
the exact documentation commit; commit contains only closure docs.

- [ ] **Step 8: Require final remote CI evidence**

Push the final branch/commit through the user's selected Git workflow and require both:

```text
Linux full verification       SUCCESS
Windows focused verification  SUCCESS
```

If CI finds a real issue, add a focused failing regression test, fix only that issue, rerun focused
and local full verification, commit separately, push, and require both jobs on the new final SHA.
Do not mark `LOCAL_VERIFIED` on a SHA whose required CI is failing or pending. Closure is prohibited
when the Step 3 reviewer left any Critical or High finding unresolved.

## Optional operator-approved live preflight

This is not part of deterministic CI, not required for `LOCAL_VERIFIED`, and must not run without
the operator present for manual login/MFA. After Task 11 and explicit approval, run only:

```bash
uv run auraly flow preflight
```

Accept only a structured `ready` result followed by confirmed Chromium closure and lock release.
Do not type a prompt, upload, click Create/Generate, inspect candidates, download, or run QC. A
successful run may be recorded in a separate documentation-only commit as
`BROWSER_PREFLIGHT_VERIFIED` with timestamp and commit SHA. Any non-ready status is diagnostic
evidence, not permission to add heuristic selectors or widen the Goal. Never translate this
preflight into `PROVIDER_VERIFIED`; real generation/download verification remains Goal 4D.

## Plan Self-Review

| Approved requirement | Implementing task(s) |
| --- | --- |
| Independent `auraly flow preflight`, no Jobs/DB | 7, 8, 9 |
| Versioned six-status JSON and exit contract | 1, 7, 8 |
| CLI > env > exact safe defaults | 2, 7, 8 |
| Fixed allowlisted production Flow URL/private local seam | 2, 6, 8, 9 |
| Persistent external profile, Playwright Chromium, headed-only | 2, 6, 9, 10 |
| Manual login/MFA with fixed timeout and no credential automation | 4, 6, 9 |
| Semantic-first unique locators and no heuristic/coordinate fallback | 4, 6, 9 |
| Minimum workspace/create/prompt/overlay contract, observation-only | 4, 6, 9 |
| OS-level non-blocking lock and concurrency one | 3, 7, 9, 10 |
| Browser closes and lock releases on every outcome | 3, 6, 7, 9 |
| Append-only timestamped diagnostics outside repo | 2, 5, 7, 9 |
| Authentication-safe evidence matrix | 5, 6, 7, 9 |
| Trace/screenshot/result sanitization | 5, 6, 7, 9 |
| Deterministic local headed browser tests | 4, 6, 9, 10 |
| Windows-focused and Linux/Xvfb CI | 3, 10, 11 |
| Fresh independent review; Critical/High regression-fix gate | 11 Steps 3–5 |
| Optional manual live preflight, not provider verification | 11 and optional section |
| No Generate/upload/download/candidates/QC | Global Constraints, 6, 9, 11 |

Type consistency check:

```text
FlowRuntimeConfig
  → BrowserRuntimeLock(config.lock_path)
  → GoogleFlowRuntime(config).run() returns FlowRuntimeObservation or raises FlowRuntimeError
  → FlowPreflightService.preflight() maps to FlowPreflightResult
  → FlowDiagnosticWriter.write_failure(result, evidence) returns updated FlowPreflightResult
  → CLI serializes FlowPreflightResult with aliases and exclude_none=False
```

Every referenced production module is created before a later task consumes it. Every browser test
has a managed-Chromium installation step and a headed display path. No task adds a database model,
migration, Job handler, image generation integration, provider operation, arbitrary URL, headless
fallback, personal profile, Generate/upload/download behavior, QC, or provider-verification claim.
