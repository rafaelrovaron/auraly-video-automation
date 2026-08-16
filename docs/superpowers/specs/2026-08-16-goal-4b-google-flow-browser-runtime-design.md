# Goal 4B — Google Flow Browser Runtime Design

## Status and boundary

This specification defines the future `Goal 4B — Google Flow Browser Runtime`
implementation. It does not implement Goal 4B. Goal 4B remains neither `IMPLEMENTED` nor
`LOCAL_VERIFIED`, and no real provider generation occurs here.

Goal 4B adds one independent command:

```text
auraly flow preflight
```

The command proves only that a dedicated Playwright-managed Chromium can launch, reach the
allowlisted Google Flow route, preserve a manually authenticated session, and recognize the
minimum UI contract required to continue safely. It does not create or read a `Job`, Campaign,
`ImageGeneration`, or database row.

The following are explicitly outside Goal 4B:

- prompt submission or typing;
- reference-image upload;
- clicking Generate or starting a paid/provider operation;
- candidate discovery, selection, or download;
- 1K/2K finalization or download correlation;
- technical or semantic image QC;
- image approval, rejection, or replacement;
- integration with `image.generate`, workers, Jobs, or SQLite;
- headless execution, Chrome channels, or the operator's personal Chrome profile;
- Google AI Studio or any provider other than Google Flow; and
- a provider canary or `PROVIDER_VERIFIED` claim.

Those responsibilities remain in Goals 4C and 4D. Goal 4B must stop after the preflight result and
must not begin an implementation plan until the user reviews this written specification.

## Existing-code reconciliation

The repository already provides:

- a Typer CLI with focused command groups;
- Playwright as a direct Python dependency;
- Goal 4A image-domain persistence and the deterministic local `image.generate` handler;
- compatible request preparation, trusted-root validation, download correlation, finalization,
  manifests, and sanitized image diagnostics in `image_generation.py`;
- metadata-security helpers that reject private absolute paths and unsafe diagnostic details; and
- a deterministic verification harness with Linux full and Windows-focused jobs.

It does not have a reusable browser runtime, Flow locator package, or cross-platform runtime lock.
Goal 4B therefore adds a focused `flow/` package rather than extending
`image_generation.py` or `images/handler.py`. Existing Goal 4A behavior remains unchanged. The new
preflight command does not instantiate `ImageService`, `JobService`, a repository, or a database
engine.

## Architecture

```text
auraly flow preflight
        │
        ▼
FlowPreflightService
        │
        ├── FlowRuntimeConfig
        │     ├── persistent profile directory
        │     ├── diagnostics directory
        │     ├── login timeout
        │     └── navigation timeout
        │
        ├── BrowserRuntimeLock
        │
        └── GoogleFlowRuntime
               ├── Playwright-managed Chromium
               ├── persistent headed context
               ├── fixed Flow route and redirect policy
               ├── manual authentication observation
               ├── semantic locator contract
               └── sanitized failure diagnostics
```

The expected package boundary is:

```text
src/auraly_pipeline/flow/
├── __init__.py
├── domain.py
├── config.py
├── lock.py
├── locators.py
├── runtime.py
└── service.py
```

Responsibilities are intentionally narrow:

- `domain.py` owns the typed status and result contracts plus internal typed errors.
- `config.py` resolves and validates operator configuration without opening a browser.
- `lock.py` owns the exclusive OS-level lock and no browser behavior.
- `locators.py` is the only source of Flow UI locator knowledge.
- `runtime.py` is the only module that imports and directly controls Playwright.
- `service.py` owns the acquire/run/close/release lifecycle and maps internal failures to the
  public sanitized result.
- `cli.py` only binds typed options, calls the service, emits JSON, and selects the exit code.

There is no generic provider framework, browser abstraction hierarchy, database model, migration,
event bus, or Job integration in this Goal.

## Configuration contract

Configuration precedence is:

```text
CLI option > environment variable > safe local default
```

The public options and environment variables are:

| Purpose | CLI | Environment | Default |
| --- | --- | --- | --- |
| Persistent profile | `--profile-dir` | `AURALY_FLOW_PROFILE_DIR` | `~/.auraly/browser-profiles/google-flow` |
| Failure diagnostics | `--diagnostics-dir` | `AURALY_FLOW_DIAGNOSTICS_DIR` | `~/.auraly/diagnostics/google-flow` |
| Manual login timeout | `--login-timeout` | `AURALY_FLOW_LOGIN_TIMEOUT_SECONDS` | `300` seconds |
| Navigation timeout | `--navigation-timeout` | `AURALY_FLOW_NAVIGATION_TIMEOUT_SECONDS` | `30` seconds |

There is no new YAML, TOML, or JSON configuration file. There is no `--url`, browser-channel,
headless, personal-profile, keep-open, or automatic-login option.

All directories are expanded, canonicalized, and validated before lock acquisition. The profile,
diagnostics, and lock directories must resolve outside the repository, must not resolve through a
symlink or junction back into it, and must not contain one another. The runtime creates only the
required directories with conservative local permissions where the OS supports them. Invalid
timeouts, unusable directories, or unsafe paths fail before Playwright starts.

The lock location is fixed at:

```text
~/.auraly/locks/google-flow-browser.lock
```

It is deliberately not configurable: all local Flow preflights are mutually exclusive even if an
operator overrides the profile path.

The only production destination is the fixed public Flow URL:

```text
https://labs.google/fx/tools/flow
```

The CLI and environment cannot replace it. The runtime may accept recognized Google authentication
redirects while login is in progress, but a successful preflight must end on the exact approved
Flow origin and route. Query strings and fragments are ignored for comparison and never emitted in
results or diagnostics. An unexpected origin or route is not followed heuristically and returns
`human_intervention_required`.

Tests may inject a local page or URL through a private constructor seam. That seam is unavailable
from CLI, environment, installed configuration, and the public service API.

## Browser lifecycle and concurrency

The runtime uses only:

```python
playwright.chromium.launch_persistent_context(
    user_data_dir=validated_profile_dir,
    headless=False,
    ...,
)
```

It does not use `channel="chrome"`, an executable-path override, a fallback browser, exported
`storage_state`, or a personal browser profile. Playwright's managed Chromium version is the sole
supported browser. The persistent profile is the only mechanism that preserves the authenticated
session between invocations.

The complete lifecycle is:

```text
resolve and validate config
        ↓
acquire exclusive runtime lock, without waiting
        ↓
launch persistent headed Chromium
        ↓
navigate to fixed Flow URL
        ↓
observe authentication state
        ↓
verify minimum semantic UI contract
        ↓
return structured result
        ↓
close page/context/browser resources
        ↓
release lock
```

The browser closes after both success and failure. There is no `--keep-open`. The only deliberate
interactive interval is a recognized manual login flow, bounded by the login timeout.

`BrowserRuntimeLock` holds a non-blocking exclusive file lock for the entire interval from before
browser launch until after browser closure. It uses native OS primitives:

- `msvcrt` on Windows; and
- `fcntl` on Linux and macOS.

The file can remain on disk after release; ownership is defined by the kernel lock, not by file
existence. A crash releases the OS lock. A second process fails immediately as `runtime_busy` and
does not launch or share a profile. Effective Google Flow browser concurrency is always one.

All cleanup lives in `finally` paths. Failure to close one browser resource does not skip attempts
to close the remaining resources or release the lock. Cleanup details are never exposed as raw
exceptions.

## Authentication contract

The runtime observes authentication; it never supplies identity or credentials.

```text
navigate to Flow
        ↓
detect state
   ├── authenticated Flow session → verify UI
   └── recognized Google login state
             ↓
       keep headed window open
             ↓
       operator completes login/MFA manually
             ↓
       poll and revalidate until deadline
          ├── authenticated Flow session → verify UI
          └── deadline reached → authentication_required
```

The runtime never:

- types an email, password, recovery value, or one-time code;
- clicks through MFA, consent, captcha, or a security challenge;
- reads, logs, or stores credentials;
- exports cookies, headers, local storage, IndexedDB, or `storage_state`; or
- copies the profile or any part of it into diagnostics.

The operator may manually complete ordinary recognized login and MFA screens. A captcha,
unrecognized challenge, unexpected consent, or redirect outside the approved authentication flow
returns `human_intervention_required`; the runtime captures only diagnostics permitted for that
status, closes the browser, and releases the lock. A recognized login state that simply remains
unfinished until the deadline returns `authentication_required`.

The authentication poll performs observation only. It does not guess which control should be
clicked and does not reset the deadline after redirects or page activity.

## Semantic UI contract

The governing rule is:

> The browser may observe and validate the UI; it may not invent the next action.

All Flow UI knowledge is centralized in `flow/locators.py`. Required elements have logical names,
including at minimum:

```text
FLOW_WORKSPACE
CREATE_ENTRY_POINT
PROMPT_INPUT
```

Each logical locator has a short ordered list of explicitly approved semantic strategies:

1. accessible role and name;
2. associated label;
3. stable placeholder;
4. stable visible text; and
5. an explicitly reviewed stable attribute only when the preceding strategies cannot represent
   the element.

The contract does not permit coordinates, XPath, `nth-child`, generated selectors, DOM-position
clicks, structural CSS fallbacks, image matching, or “first similar button” heuristics. Locators
do not appear inline in the service or runtime.

Every required logical element must resolve to exactly one visible element and, when appropriate,
one enabled/actionable element. Zero matches mean the UI contract is missing. Two or more matches
mean the state is ambiguous. Both return `ui_contract_failed`; the runtime does not choose one.

To return `ready`, the preflight must prove all of the following without mutating provider or
project state:

1. the current origin and route are the approved Flow destination;
2. the session is authenticated;
3. the main Flow workspace is uniquely identifiable;
4. the creation entry point is uniquely identifiable;
5. the prompt input is uniquely identifiable; and
6. no known or unknown overlay blocks safe interaction.

Goal 4B does not activate the creation entry point, type into the prompt input, click Generate, or
otherwise advance the generation lifecycle. If the live Flow UI cannot expose this minimum
contract without such an action, the real preflight must stop with `ui_contract_failed`; changing
that boundary requires a reviewed design amendment rather than a heuristic click.

An authenticated expected route with a missing or ambiguous required locator returns
`ui_contract_failed`. An unknown modal, consent, challenge, blocking overlay, or state where the
route/authentication classification itself is uncertain returns `human_intervention_required`.

## Public result and CLI contract

`FlowPreflightResult` is a versioned typed JSON contract. Its public statuses are exactly:

```text
ready
authentication_required
human_intervention_required
runtime_busy
browser_launch_failed
ui_contract_failed
```

Their meanings are:

| Status | Meaning |
| --- | --- |
| `ready` | Chromium launched; the fixed Flow route, authenticated session, minimum UI contract, and absence of a blocking overlay were confirmed. |
| `authentication_required` | A recognized login state was observed, but the operator did not finish authentication before the fixed deadline. |
| `human_intervention_required` | An unexpected redirect, consent, challenge, captcha, modal, overlay, or otherwise uncertain state made automatic continuation unsafe. |
| `runtime_busy` | Another process owns the exclusive Flow runtime lock. |
| `browser_launch_failed` | The runtime could not be initialized safely, including invalid resolved runtime paths, unavailable/corrupt Playwright Chromium, an unusable profile, or Playwright failure before a trustworthy page existed. |
| `ui_contract_failed` | The runtime is authenticated on the expected Flow route, but a required semantic locator is missing or ambiguous. |

Invalid CLI syntax remains Typer's command-usage error. After typed option parsing, every config,
lock, browser, navigation, authentication, and UI outcome is mapped to one of the statuses above;
raw exceptions never cross the CLI boundary. Config validation failures use
`browser_launch_failed` with `failedStep="validate_config"` because the approved public status set
has no separate configuration state and the browser runtime could not safely initialize.

The stable payload contains only allowlisted fields:

```json
{
  "schemaVersion": "1.0",
  "success": false,
  "status": "ui_contract_failed",
  "flowUrl": "https://labs.google/fx/tools/flow",
  "authenticated": true,
  "uiReady": false,
  "failedStep": "verify_flow_ui",
  "failedLocator": "PROMPT_INPUT",
  "diagnosticRunId": "20260816T204100Z-7f3a2c1d",
  "screenshot": "screenshot.png",
  "trace": "trace.zip",
  "timestamp": "2026-08-16T20:41:00Z"
}
```

Fields that do not apply are `null` or omitted according to one rule selected in the implementation
plan and then kept stable. Artifact names are relative to the diagnostic run directory. The CLI
does not emit absolute profile, diagnostics, repository, home-directory, or temporary paths.

`ready` returns exit code `0`. Every other status returns the same non-zero operational exit code;
the JSON status is the source of detailed machine-readable meaning. The CLI writes one JSON object
and no traceback. Human-readable logs go to stderr only when they are sanitized and do not corrupt
stdout JSON.

## Safe-stop and diagnostic policy

The safe-stop sequence is:

```text
uncertain or failed state
        ↓
do not click or type
        ↓
capture only status-permitted evidence
        ↓
sanitize and publish diagnostics append-only
        ↓
close browser resources
        ↓
release lock
        ↓
return structured JSON
```

Every non-`ready` runtime result for which the diagnostics root was validated receives a unique UTC
timestamp plus random-suffix run ID under:

```text
<diagnostics-dir>/<run-id>/
```

`result.json` is written by exclusive creation and is mandatory whenever the validated diagnostics
directory is available. Diagnostic runs are append-only: they never overwrite a prior run, and
Goal 4B performs no automatic retention, rotation, or cleanup.

Evidence is status-specific to prevent diagnostics from becoming a credential leak:

| Status | `result.json` | Screenshot | Sanitized trace |
| --- | --- | --- | --- |
| `ui_contract_failed` | required | required after successful sanitization | required after successful sanitization |
| authenticated `human_intervention_required` on a trusted Flow page | required | required after successful sanitization | required after successful sanitization |
| `authentication_required` or an auth/challenge page | required | prohibited | prohibited |
| `runtime_busy` | required when diagnostics root is available | not applicable | not applicable |
| `browser_launch_failed` before a trusted page exists | required when diagnostics root is available | prohibited | prohibited |

This matrix resolves the conflict between useful browser evidence and the stricter rule that login
identity, cookies, tokens, and authentication pages must never be persisted. A failure must never
capture richer evidence merely because a page object exists.

Tracing starts only after authentication has completed and the page has returned to the trusted
Flow origin/route. It uses no source capture and no DOM snapshots. The raw trace is first written to
a unique temporary staging directory outside the repository, never directly to the append-only
diagnostics directory. Before publication, the diagnostic writer removes resource bodies and
headers, strips URL query strings and fragments, and validates the archive against the diagnostic
denylist. The sanitized archive is then published as `trace.zip`; the raw staging file is removed
in `finally`. If a valid sanitized trace cannot be produced, the runtime must not publish the raw
trace or claim a normal diagnostic completion; it returns a sanitized
`human_intervention_required` result whose failed step identifies diagnostic sanitization.

Screenshots are captured only after authentication on the trusted Flow route. Known account/avatar
identity regions are masked before publication, and the screenshot is rejected if the sanitizer
cannot establish the required masks. As with a rejected trace, the raw screenshot is never
published and the result becomes sanitized `human_intervention_required` with
`failedStep="sanitize_diagnostics"`; successful artifact capture never takes precedence over the
sensitive-data boundary. The preflight never enters a prompt, so prompt content cannot appear.
Diagnostic filenames and run IDs never include email, prompt, URL, locator text, or raw exception
content.

Diagnostics must not contain:

- cookies, authorization headers, storage state, or browser-profile files;
- email addresses, account names, credentials, MFA values, or recovery information;
- prompt or reference-image content;
- HTML/DOM snapshots or full page source;
- request/response bodies, source files, or arbitrary exception dumps;
- URL query strings, fragments, signed URLs, or tokens; or
- absolute private paths.

The implementation must apply allowlisted serialization rather than serializing exception objects
and then trying to redact them. Tests seed representative cookies, email, prompt text, query values,
tokens, and private paths and assert that none appear in `result.json`, `screenshot.png`, the
expanded `trace.zip`, stdout, or stderr.

No diagnostics are persisted for `ready`; the structured stdout result is sufficient.

## Internal failures and mapping

Internal typed errors may include:

```text
FlowRuntimeBusyError
FlowBrowserLaunchError
FlowAuthenticationTimeoutError
FlowUnexpectedStateError
FlowUiContractError
FlowDiagnosticSanitizationError
```

They are implementation details. `FlowPreflightService` catches them, performs safe cleanup, and
maps them to the approved public statuses. Unknown exceptions are caught at the service boundary,
mapped conservatively to `browser_launch_failed` before a trusted page or
`human_intervention_required` after one exists, and represented only with an allowlisted public
failed step. Exception class names, messages, stacks, locals, and filesystem paths are not public
data.

The public failed-step allowlist includes only stable logical phases such as:

```text
validate_config
acquire_runtime_lock
launch_browser
navigate_flow
await_manual_authentication
verify_flow_ui
sanitize_diagnostics
close_browser
```

Cleanup failures do not turn `ready` into success: if browser closure cannot be confirmed, the
result is `human_intervention_required`, diagnostics are sanitized, and the lock is still released
only after all close attempts finish. The process must not intentionally leave a browser or context
running after returning any result.

## Deterministic test strategy

The default suite must not depend on internet access, Google authentication, the live Flow UI, or a
real provider operation. `GoogleFlowRuntime` receives a private test seam that points only tests at
local deterministic pages, for example:

```text
tests/fakes/flow/
├── ready.html
├── login-required.html
├── ambiguous-ui.html
├── missing-prompt.html
└── blocking-modal.html
```

The seam exercises real browser/locator behavior against local content while keeping the public
Flow URL fixed for production. Unit fakes may cover failures that must occur before a page can
exist, but the core locator and lifecycle tests use Playwright-managed Chromium and the local pages.

Required deterministic coverage includes:

- each of the six public statuses;
- exactly one match for every required logical locator in `ready`;
- zero-match and multiple-match `ui_contract_failed` results;
- unknown overlay and unexpected-route safe-stop;
- recognized login observation, successful manual-auth transition simulation, and fixed timeout;
- no automated typing, credential handling, Generate click, upload, or download;
- production URL not configurable through CLI or environment;
- profile, diagnostics, and lock paths outside the repository after canonicalization;
- symlink/junction containment rejection where the platform can create the fixture;
- CLI > environment > default precedence and exact timeout defaults;
- headed-only launch using Playwright-managed Chromium;
- browser/context closure after success, every failure, and an injected exception;
- lock release after success, failure, exception, and process exit;
- diagnostic exclusive creation, unique timestamped run IDs, and no cleanup/overwrite;
- the status-specific screenshot/trace matrix;
- sanitization of result, screenshot, expanded trace, stdout, and stderr;
- stable JSON keys and `ready`/non-ready exit behavior; and
- no imports or calls into Jobs, Campaign repositories, ImageService, or database setup.

### Real OS lock test

The concurrency test uses separate processes and the real OS primitive:

```text
process A acquires lock
        ↓
process B attempts lock
        ↓
runtime_busy, no browser launch
        ↓
process A releases or exits
        ↓
process B can acquire
```

Mocking the lock implementation is not sufficient for this test. It must run in the
Windows-focused suite and the applicable POSIX suite.

### CLI tests

The CLI tests invoke the command through the deterministic seam or dependency injection and prove:

```text
ready                       → one JSON object + exit 0
authentication_required     → one JSON object + non-zero
human_intervention_required → one JSON object + non-zero
runtime_busy                → one JSON object + non-zero
browser_launch_failed       → one JSON object + non-zero
ui_contract_failed          → one JSON object + non-zero
```

They also prove that stdout/stderr contain no traceback, raw exception, secret, or private absolute
path.

## Real browser preflight and verification classification

Live Google Flow verification is optional, manual, and separate from deterministic CI. It performs
only:

```text
launch Playwright Chromium
→ open fixed Flow route
→ allow manual login/MFA if required
→ verify minimum UI contract
→ return ready
→ close Chromium and release lock
```

It performs no upload, prompt submission, Generate click, candidate interaction, download, or QC.
CI never opens live Google Flow and never requires a Google account.

The implementation task must run focused tests plus the repository's applicable deterministic
baseline. CI keeps Linux full deterministic verification and adds the smallest Windows-focused set
covering config/path behavior, the real OS lock, locator contract, local browser runtime, CLI, and
diagnostic sanitization. Browser installation availability is checked with:

```bash
uv run playwright install --dry-run chromium
```

After production code and required tests exist, the normal milestone terms remain independent:

```text
IMPLEMENTED       production code and tests exist
LOCAL_VERIFIED    required deterministic/local baseline passed
PROVIDER_VERIFIED not established by Goal 4B
```

If the operator separately completes the optional live preflight, it may be recorded as
`BROWSER_PREFLIGHT_VERIFIED` supplemental evidence. That label means only launch/auth/UI preflight
succeeded; it is not a repository-wide milestone term and must never be reported as
`PROVIDER_VERIFIED`. Provider verification remains reserved for the explicitly approved Goal 4D
generation/download canary.

## Acceptance criteria

Goal 4B is ready for implementation planning only when this design is user-approved. A later
implementation may be classified `IMPLEMENTED` and `LOCAL_VERIFIED` only when all of the following
are true:

- `auraly flow preflight` exists independently of Jobs and the database;
- only Playwright-managed Chromium is launched, always headed, with a dedicated persistent profile
  outside the repository;
- authentication is manual, bounded by the configured deadline, and no credentials or storage
  state are handled by application code;
- the fixed Flow URL and redirect policy cannot be overridden publicly;
- the minimum semantic UI contract is centralized, unique, and free of coordinate/structural
  fallbacks;
- effective browser concurrency is one under a real cross-platform OS lock;
- browser resources close and the lock releases on every success and failure path;
- the six approved statuses and JSON/exit-code contract are stable and sanitized;
- failure diagnostics are append-only, timestamped, status-appropriate, and contain none of the
  prohibited sensitive material;
- deterministic local browser tests and the applicable verification baseline pass; and
- the diff contains no Generate, upload, download, candidate, QC, Job/DB integration, headless
  behavior, or real provider operation.

The next step after this commit is user review of the specification. Do not create the Goal 4B
implementation plan or start implementation until that review is complete.
