# Goal 4C — Flow Generation, Download & Recovery Design

## Status and boundary

This specification defines the future `Goal 4C — Flow Generation, Download & Recovery`
implementation at Goal 4B closure commit
`9b634a7dc35f0146c427920be0c11a81ed5aae5e`. It does not implement Goal 4C and does not perform a
live Google Flow operation.

Goal 4C connects the durable image domain from Goal 4A to the browser safety boundary from Goal 4B.
It adds one resumable `image.generate` execution path that can upload a required reference image,
verify the persisted prompt, dispatch Generate once, identify two completed candidates, request the
2K form of those candidates, correlate each download to its exact UI action, and ingest both
artifacts without overwrite.

The following remain outside Goal 4C:

- semantic image QC, crops, recommendations, approval, rejection, or replacement changes;
- a live provider canary or any `PROVIDER_VERIFIED` claim;
- downloading every visible candidate;
- Google AI Studio or another image provider;
- API, frontend, or global campaign-progress work;
- headless Flow execution, automatic Google login, or use of the personal Chrome profile; and
- unrelated changes to campaign, copy, voice, HeyGen, rendering, or approval lifecycles.

Goal 4D owns full image QC, review integration hardening, and the explicitly approved real Flow
canary. Goal 4C deterministic verification must not contact Google or consume provider credits.

## Approved product decisions

The P0 contract is intentionally fixed:

- exactly two intentionally selected candidates per Flow generation;
- each selected candidate is requested and ingested as 2K;
- downloading every visible candidate is not required;
- `local_fake` remains the default executor;
- `playwright_python` requires explicit durable operator authorization;
- one `image.generate` Job owns the complete Flow lifecycle; and
- detailed recovery uses durable internal checkpoints rather than separate Jobs per browser step.

The two-candidate decision follows the current PRD default and the existing Goal 4A fake contract.
It is not configurable in Goal 4C. Changing it later requires a new versioned generation contract,
not a silent environment override.

## Existing-code reconciliation

The repository already provides:

- `ImageGeneration` and `ImageCandidate` persistence with immutable generation intent and artifact
  identity;
- an `image.generate` Job linked atomically to its generation;
- a deterministic two-candidate local handler with partial-artifact recovery;
- Job leases, stale recovery, `RECONCILE_BEFORE_RETRY`, and an explicit reconciled-resume seam;
- `ImageExecutor = Literal["local_fake", "playwright_python"]` at the persisted-domain boundary;
- a headed Playwright-managed Chromium runtime, persistent profile, manual authentication,
  semantic locators, a real cross-platform lock, trusted-route validation, and sanitized append-only
  diagnostics;
- legacy request, path, download, format, hash, and non-overwrite helpers in
  `image_generation.py`; and
- deterministic Linux and Windows verification through the common harness.

Goal 4C must preserve those contracts while resolving these current limitations:

- `ImageGenerateRequest` and the public CLI currently permit only `local_fake`;
- the registered image handler is itself the fake implementation and is marked idempotent;
- `ImageGeneration.provider_state` is too coarse to identify browser recovery checkpoints;
- no durable record exists for candidate slots before an `ImageCandidate` artifact is ingested;
- Goal 4B intentionally has no upload, prompt, dispatch, candidate, or download behavior; and
- generic download-directory inventory is weaker than a Playwright download event correlated to an
  exact 2K action.

Goal 4C therefore extends the focused `images/` and `flow/` packages. It reuses compatible legacy
helpers selectively; it does not route the new worker through the old standalone
`image-prepare`/`image-wait-download` CLI sequence and does not perform a big-bang rewrite of
unrelated legacy commands.

## Architecture

```text
auraly image generate --executor playwright-python --confirm-provider-action
        │
        ▼
ImageService
        ├── atomically creates ImageGeneration + image.generate Job
        ├── creates authorized FlowGenerationRun + two candidate slots
        └── persists provider-action authorization audit
                │
                ▼
JobService.worker_once
        │
        ▼
ImageGenerateHandler (executor router)
        ├── local_fake       → existing deterministic local generator
        └── playwright_python
                │
                ▼
            FlowImageGenerationRunner
                ├── Flow runtime config + BrowserRuntimeLock
                ├── headed persistent Chromium + manual authentication
                ├── centralized generation locators/actions
                ├── durable run/slot checkpoints
                ├── sanitized evidence
                └── 2K download validation and immutable ingestion
```

The expected focused additions are:

```text
src/auraly_pipeline/images/
├── domain.py             # extend request/run/slot contracts
├── db_models.py          # Flow run and slot rows
├── repository.py         # checkpoint and ingestion transactions
├── service.py            # authorization, recovery, manual resolution
├── handler.py            # executor router and retained local fake
└── flow_handler.py       # Job-facing Playwright generation handler

src/auraly_pipeline/flow/
├── generation.py         # lifecycle and recovery orchestration
├── generation_domain.py  # typed browser observations/actions/errors
├── generation_locators.py# only Goal 4C UI locator knowledge
└── artifacts.py          # staging, decoding, 2K facts, exclusive publication
```

Existing Goal 4B modules remain the source of browser config, lock, trust policy, and diagnostic
rules. Shared helpers may be promoted from private to package-internal APIs with regression tests,
but `auraly flow preflight` must retain its exact public JSON/status behavior. Direct Playwright
control remains confined to the `flow/` package; image services and repositories never receive a
`Page`, `Locator`, browser context, cookie, signed URL, or raw provider response.

There is no generic provider framework, event bus, distributed worker, remote browser, or new
configuration file.

## Submission and authorization contract

`ImageGenerateRequest.executor` expands from the Goal 4A request-only literal `local_fake` to the
already persisted `ImageExecutor` union:

```text
local_fake | playwright_python
```

The request also accepts:

```text
provider_action_confirmed: bool
provider_action_approved_by: str | None
```

Validation is executor-specific:

- `local_fake` requires `provider_action_confirmed=false` and no approval actor;
- `playwright_python` requires `provider_action_confirmed=true`, a safe non-empty actor, a reference
  path, and its matching SHA-256;
- prompt, reference path/hash, provider, executor, required candidate count `2`, required resolution
  `2K`, and generation contract version participate in the request fingerprint; and
- authorization actor/time are immutable audit data but not creative intent. Reusing the same
  idempotency key returns the original authorization and never replaces its actor.

The CLI exposes the Playwright path only through:

```text
auraly image generate ... \
  --executor playwright-python \
  --provider-action-approved-by <safe-actor> \
  --confirm-provider-action
```

`image regenerate` uses the same requirements and a new idempotency key. There is no global switch
that changes the default executor, no `--all-candidates`, no `--allow-1k`, and no flag that skips
reference or prompt verification.

For a Playwright request, `ImageService.generate()` atomically creates the generation, Job,
`FlowGenerationRun`, two `FlowCandidateSlot` rows, and an append-only
`job.provider_action_authorized` event. The event contains only actor, timestamp, executor,
candidate count, and resolution. The linked Job uses:

```text
job_type     = image.generate
retry_safety = reconcile_before_retry
max_attempts = 3
```

The local fake keeps its existing idempotent retry policy and behavior. The handler registered for
`image.generate` becomes an executor router. The generic `JobHandler` policy seam is extended with
an optional `accepts_retry_safety(retry_safety: RetrySafety) -> bool` method. `JobService` uses that
method at submission and claim execution when present; handlers without it retain the current exact
`handler.retry_safety == job.retry_safety` rule. The image router accepts only `IDEMPOTENT` and
`RECONCILE_BEFORE_RETRY`, then loads the linked generation and enforces the exact mapping:

```text
local_fake        → idempotent
playwright_python → reconcile_before_retry
```

This makes the router compatible with the existing generic Job gate without weakening any other
handler. A mismatch between executor and persisted Job retry policy is a terminal integrity failure
and never opens the browser.

## Durable Flow run and slot model

Migration `0005_flow_generation_recovery` adds two tables. It does not rewrite existing Goal 4A
rows. Existing `local_fake` generations have no Flow run.

### `flow_generation_runs`

One row exists per `playwright_python` generation:

| Field | Contract |
| --- | --- |
| `id` | UUID primary key |
| `image_generation_id` | unique non-null FK to `image_generations` |
| `stage` | allowlisted stage below |
| `required_candidate_count` | integer check fixed to `2` |
| `required_resolution` | string check fixed to `2K` |
| `provider_workspace_path` | nullable allowlisted relative Flow route, never origin/query/fragment |
| `provider_workspace_fingerprint` | nullable SHA-256, paired with workspace path |
| `dispatch_attempt_number` | positive integer, initially `1` |
| `dispatch_intent_at` | nullable current-attempt intent timestamp |
| `dispatch_confirmed_at` | nullable current-attempt confirmation timestamp |
| `grid_evidence_path` | nullable workspace-relative screenshot path |
| `grid_evidence_sha256` | nullable SHA-256, paired with evidence path |
| `last_failure_code` | nullable safe allowlisted code |
| `provider_action_approved_by` | immutable safe actor |
| `provider_action_approved_at` | immutable UTC timestamp |
| `created_at`, `updated_at` | UTC audit timestamps |

The stage state machine is:

```text
prepared
  → inputs_verified
  → dispatch_intent_recorded
  → dispatch_confirmed
  → candidates_observed
  → downloading
  → completed
```

`ambiguous`, `blocked`, and `failed` are explicit safe-stop states. A normal forward transition may
enter one of them according to the failure mapping. Recovery may promote `ambiguous` to
`dispatch_confirmed`, `candidates_observed`, or `downloading` only from evidence. Manual
no-dispatch resolution returns to `prepared`, increments `dispatch_attempt_number`, and clears only
the current-attempt intent/confirmation fields after preserving the prior attempt in an append-only
Job event.

`ImageGeneration.provider_state` remains the aggregate state:

```text
queued      before worker execution
generating  any non-terminal active Flow stage
completed   run and two candidate ingestions complete
blocked     reconciliation or operator action required
failed      terminal non-recoverable failure
```

The existing schema literal `created` remains accepted for backward compatibility, but Goal 4C
does not create a Playwright generation in that state.

`ImageGeneration.dispatched_at` is set only after the first confirmed Generate dispatch and is
never cleared. A persisted intent alone does not populate it.

### `flow_candidate_slots`

Exactly two rows, indexes `0` and `1`, are created with the run:

| Field | Contract |
| --- | --- |
| `id` | UUID primary key |
| `flow_generation_run_id` | non-null FK to the run |
| `slot_index` | integer check `0 <= value < 2`, unique per run |
| `provider_slot_fingerprint` | nullable SHA-256 of normalized semantic slot identity |
| `state` | allowlisted state below |
| `download_intent_at` | nullable timestamp persisted before the 2K action |
| `staging_path` | nullable workspace-relative path |
| `staged_sha256` | nullable SHA-256, paired with staging path |
| `image_candidate_id` | nullable unique FK to final `image_candidates` row |
| `created_at`, `updated_at` | UTC audit timestamps |

Slot states are:

```text
pending → observed → download_intent_recorded → downloaded → ingested
```

`blocked` is the only alternate terminal safe-stop. The database enforces paired fields, unique
run/index, unique linked candidate, legal states, and the rule that `ingested` requires a candidate
ID while pre-ingestion states prohibit one.

The slot fingerprint is computed from allowlisted semantic provider attributes in memory. Raw
thumbnail URLs, signed URLs, DOM, prompt text, account data, and provider response bodies are never
stored. On recovery the runtime enumerates the recognized grid, recomputes fingerprints, and
requires an unambiguous match. If the live UI provides no stable safe identity, recovery blocks; it
does not fall back to position-only clicking.

## Browser configuration and lifecycle

The generation path uses the Goal 4B defaults and precedence for profile, diagnostics, login, and
navigation. Because Jobs may execute later in another process, generation-specific operational
timeouts are resolved by the worker from environment or defaults and are not persisted as private
machine configuration:

| Purpose | Environment | Default |
| --- | --- | --- |
| Candidate generation | `AURALY_FLOW_GENERATION_TIMEOUT_SECONDS` | `600` seconds |
| One 2K download | `AURALY_FLOW_DOWNLOAD_TIMEOUT_SECONDS` | `120` seconds |

Both values must be positive bounded integers. These defaults are engineering hypotheses for local
implementation and do not claim live-provider validation. Goal 4D may revise them from canary
evidence through a separate approved change.

The lifecycle is:

```text
validate Job/generation/run/authorization/reference
        ↓
resolve safe runtime config
        ↓
acquire the fixed Goal 4B browser lock without waiting
        ↓
launch headed Playwright-managed persistent Chromium
        ↓
manually authenticate if required within the existing fixed deadline
        ↓
execute or reconcile from the persisted checkpoint
        ↓
close browser resources
        ↓
release lock
        ↓
return a sanitized JobExecutionResult
```

The lock spans every browser action and closure attempt. No second preflight, generation, or
recovery browser may run concurrently. Profile/session persistence is the only authentication
mechanism. The application never types credentials, handles MFA, exports storage state, selects a
Chrome channel, or uses the personal Chrome profile.

The runtime may persist only an allowlisted relative workspace route whose origin is the fixed Flow
origin, whose path matches the approved Flow workspace family, and whose query and fragment are
empty. If Flow requires an unsafe or unstable route token that cannot satisfy this contract, the
same-process run may finish while restart recovery remains blocked. It must never persist a full
signed URL or guess the most recent project.

## Input preparation and verification

Before browser launch, the handler:

1. loads the generation by the claimed Job ID;
2. validates Campaign, SceneVariant, Job, executor, retry policy, run, slots, and authorization;
3. resolves `reference_image_path` under the configured `work_root` without symlink/junction escape;
4. requires a regular supported PNG, JPEG, or WebP file;
5. recomputes its SHA-256 and compares it with the immutable generation snapshot; and
6. validates that the persisted prompt hash still matches the persisted prompt snapshot.

Inside Flow, all interactions use unique semantic locators. The runtime uploads through the exact
recognized file input and verifies a completed upload state plus the expected file identity in
memory. It fills the prompt, reads the value back, hashes it, and requires equality with
`ImageGeneration.prompt_sha256`. Raw prompt content and private source paths never enter logs,
events, diagnostic results, filenames, or public Job output.

The run transitions to `inputs_verified` only after upload and prompt verification both succeed.
No Generate action is available to code before that commit.

## Dispatch safety boundary

Generate is an irreversible or credit-consuming provider action. The exact sequence is:

```text
revalidate trusted Flow route and absence of blocking overlay
        ↓
resolve exactly one enabled Generate control
        ↓
commit run.stage=dispatch_intent_recorded + dispatch_intent_at
        ↓
click Generate exactly once
        ↓
observe a recognized generating state or recognized result transition
        ↓
commit dispatch_confirmed + ImageGeneration.dispatched_at
```

The click cannot occur unless the intent transaction succeeds. A click exception, browser crash,
process exit, route change, or loss of lease after intent but before confirmation is ambiguous. The
handler must not click again in the same or a later attempt.

Confirmation requires positive semantic evidence. Successful Playwright method return, absence of
an exception, disappearance of the button, elapsed time, or an empty grid is not confirmation. A
recognized generating indicator or a result grid attributable to the current verified workspace is
required.

Tracing that could record the prompt-fill action is prohibited. Rich tracing begins only after
input verification, uses no DOM snapshots, sources, screenshots, request/response bodies, or
headers, and is sanitized through the Goal 4B publication boundary. Pre-dispatch input failures
publish only allowlisted result metadata unless a separately sanitized screenshot can mask account,
prompt, reference preview, and file identity regions.

## Candidate observation and grid evidence

After confirmed dispatch, the runtime waits up to the generation timeout for a recognized grid with
at least two completed and enabled candidate slots. It rejects loading, failed, duplicate,
disabled, structurally ambiguous, or unknown slots.

The two selected candidates are the first two slots in the provider's validated semantic slot
order, but selection is never implemented as a blind positional click. The runtime must first:

1. resolve one recognized grid;
2. enumerate all completed semantic slot containers;
3. establish a stable safe fingerprint for every slot;
4. prove fingerprints are unique;
5. bind slot indexes `0` and `1` to the first two validated identities; and
6. commit those fingerprints before opening any slot action.

If the UI cannot expose a stable identity under deterministic local fixtures and the production
locator contract, the run blocks. Image matching, screen coordinates, CSS position, generated
class names, and “closest-looking” heuristics are prohibited.

The grid screenshot is stored under the generation's trusted workspace inspection directory using
exclusive creation. Account identity, prompt input, reference preview, filenames, and any other
input-bearing region are masked. The screenshot must show the two generated candidate regions and
is stored with a workspace-relative path and SHA-256. Sanitization failure blocks the run and never
publishes the raw screenshot.

## 2K download correlation and artifact ingestion

For each persisted slot, independently:

1. revalidate route, grid identity, and exact slot fingerprint;
2. resolve the exact slot's download/upscale menu and unique enabled `2K` action;
3. commit `download_intent_recorded` before clicking;
4. wrap that exact click in Playwright's download expectation;
5. require exactly one completed download event from that action;
6. save to a unique staging file inside the generation directory;
7. decode and inspect the actual bytes;
8. require PNG, JPEG, or WebP, positive dimensions/size, and `max(width, height) >= 2048`;
9. compute SHA-256, persist the downloaded checkpoint, and publish to the canonical final path;
10. insert one immutable `ImageCandidate` and link the slot in one immediate transaction.

The maximum-axis rule rejects 1K while allowing portrait and landscape 2K aspect ratios. It is a
minimum ingest invariant, not the full technical or semantic QC owned by Goal 4D.

Canonical final paths remain compatible with Goal 4A:

```text
work/campaigns/<campaign-id>/images/<scene-variant-id>/generation-0001/candidate-0000.<ext>
work/campaigns/<campaign-id>/images/<scene-variant-id>/generation-0001/candidate-0001.<ext>
```

Publication uses a same-filesystem validated staging file and an atomic exclusive hard-link publish
to the absent final path, followed by directory/file sync where supported and staging unlink. If
the platform/filesystem cannot provide exclusive publication, the operation fails closed; it never
falls back to overwrite. An existing final path is accepted only through recovery after its bytes
match the expected staged hash and any persisted candidate facts exactly. Otherwise the run blocks
with an artifact conflict.

The legacy global-download inventory is not used by the new worker. `~/Downloads`, suggested
provider filenames, and simultaneous unrelated browser downloads cannot determine candidate
identity.

After both slots are `ingested`, the handler commits the run and generation as completed and returns
success with only generation ID, candidate IDs/count, resolution, and a recovery flag. Job
completion occurs through the existing claim-finishing transaction. A crash between domain
completion and Job completion is reconciled by returning success without browser launch on the next
approved attempt.

## Recovery contract

Stale `RECONCILE_BEFORE_RETRY` Jobs continue to become blocked through the existing Job recovery
policy. Goal 4C adds:

```text
auraly image generation recover <image-generation-id> --reconciled-by <safe-actor>

auraly image generation resolve-no-dispatch <image-generation-id> \
  --resolved-by <safe-actor> \
  --reason <sanitized-reason>
```

`recover` is evidence-driven. It may inspect local files and, when required, open Flow under the
same lock for observation and download continuation. It never clicks Generate. Successful recovery
calls an extended `resume_reconciled_job(job_id, reason=...)` with one exact allowlisted reason:

```text
no_dispatch_proven
existing_dispatch_reconciled
staged_artifact_reconciled
completed_generation_reconciled
```

The generic Job layer records the supplied reason but does not interpret Flow state. ImageService
owns all domain-level reconciliation before requesting the resume.

The recovery matrix is:

| Last durable evidence | Required behavior |
| --- | --- |
| Before dispatch intent | Resume without browser; the code boundary proves no click was allowed |
| Intent recorded, no confirmation | Observe the exact workspace; promote only from recognized generating/results evidence, otherwise remain ambiguous |
| Dispatch confirmed | Never click Generate; reopen the safe persisted workspace and continue observation/download |
| Slots observed | Recompute and uniquely match both slot fingerprints before download |
| Download intent recorded | Reconcile staging/final first; redownload only the same unambiguously matched slot when no artifact exists |
| Downloaded staging exists | Validate bytes/hash/2K and ingest without provider interaction |
| Candidate ingested | Validate immutable DB facts against final artifact and preserve it |
| Both candidates ingested | Complete run/generation or return reconciled success without browser interaction |

An ambiguous intent never becomes “no dispatch” from an empty grid, elapsed timeout, ready button,
or lack of an error. The separate `resolve-no-dispatch` operation requires a blocked ambiguous run,
safe actor, and sanitized non-empty reason. It records an append-only
`job.flow_dispatch_resolved` event containing the previous dispatch-attempt number and timestamps,
increments the run's attempt number, resets the current attempt to `prepared`, and only then uses
the reconciled resume seam. It never deletes prior evidence or changes a confirmed dispatch.

Recovery fails closed when:

- the workspace route is unavailable, unsafe, or no longer identifies the same workspace;
- prompt/reference verification cannot be re-established in memory;
- candidate fingerprints are absent, duplicated, or changed;
- staged, final, and database artifact evidence conflict;
- a required diagnostic cannot be sanitized;
- the browser cannot close cleanly; or
- Job/generation/run/slot ownership or state is inconsistent.

In these cases the run and aggregate generation remain blocked unless the inconsistency is a
terminal integrity or security failure, in which case both fail without another provider action.

## Failure mapping and diagnostics

Goal 4C uses typed internal errors and allowlisted public Job codes. At minimum the mapping covers:

```text
flow_runtime_busy
flow_authentication_required
flow_ui_contract_failed
flow_input_verification_failed
flow_dispatch_ambiguous
flow_candidate_grid_ambiguous
flow_download_failed
flow_artifact_invalid
flow_artifact_conflict
flow_recovery_blocked
flow_diagnostic_sanitization_failed
flow_browser_close_failed
image_job_integrity_failed
```

Raw exceptions, Playwright messages, DOM text, provider URLs, filenames, prompt/reference content,
and private absolute paths never cross the handler, Job event, or CLI boundary. Unexpected failures
map conservatively according to whether provider mutation was possible: before durable dispatch
intent they are safe pre-dispatch failures; at or after intent they are ambiguous or blocked.

Goal 4B's evidence rules remain the minimum security boundary. Goal 4C additionally masks prompt,
reference preview, file identity, and input-bearing UI. Failure evidence is append-only and status
appropriate. Result metadata contains only safe IDs, stage/code, timestamps, relative artifact
names, and hashes where required. It never persists cookies, authorization headers, storage state,
HTML/DOM, response bodies, signed URLs, query strings, fragments, or account identity.

## CLI and service behavior

The existing generation and candidate get/list/review commands remain compatible. Their JSON gains
Flow run/slot information only through new explicit inspection commands or additive nested fields
whose keys are covered by contract tests; existing keys do not change meaning.

New commands are:

```text
auraly image generation recover
auraly image generation resolve-no-dispatch
```

They emit one sanitized JSON object and exit zero only when the requested reconciliation/resolution
is durably complete. Validation, conflict, blocked, and unexpected outcomes use stable non-zero
behavior with no traceback. Invalid Typer syntax remains a usage error.

There is no `auraly flow generate` command. `auraly flow preflight` remains independent of Jobs and
the database. Provider execution occurs only through an authorized `image.generate` Job and the
ordinary worker boundary.

## Deterministic test strategy

Default tests use no internet, Google account, live Flow route, or paid action. The Goal 4B private
local target seam is extended only inside the `flow/` package for deterministic pages and synthetic
Playwright downloads.

Required fixtures include recognized upload/prompt readiness, upload completion, generating state,
two-candidate grid, more-than-two grid, loading/failed slots, ambiguous grid, missing 2K action,
route change, blocking overlay, and safe restart workspace. Fixtures make no HTTP request and seed
sensitive prompt, reference, identity, URL-token, and private-path values for negative leak scans.

Required coverage includes:

- migration upgrade, direct-insert checks, relationships, unique constraints, and legacy Goal 4A
  row compatibility;
- run and slot state-machine validation, paired fields, immutable authorization, and exactly two
  slots;
- executor-specific request validation and fingerprint stability;
- atomic generation + Job + run + slots + authorization creation;
- fake executor unchanged, idempotent, and still the default;
- Playwright Job policy fixed to `RECONCILE_BEFORE_RETRY`;
- missing/mismatched authorization or reference rejected before browser launch;
- reference path containment, symlink/junction escape, actual SHA, and format checks;
- upload completion and prompt readback/hash verification;
- intent commit observed before exactly one Generate click;
- crash injection immediately before intent, after intent, during click, after recognized dispatch,
  after grid evidence, before/after each download event, after staging, after final publication,
  after each candidate transaction, and after domain completion;
- zero/multiple Generate controls and zero/duplicate/unknown candidate identities fail closed;
- deterministic selection of two from a larger recognized grid without blind positional clicking;
- grid evidence masking, exclusive publication, and hash verification;
- exact Playwright download-event correlation to each slot's unique 2K action;
- rejection of 1K, empty, partial, malformed, unsupported, polyglot, oversized-metadata, and
  conflicting artifacts;
- distinct final paths and candidate records with no overwrite;
- all recovery-matrix rows, including offline completion and browser-required reconciliation;
- ambiguous dispatch remains blocked until evidence or audited manual no-dispatch resolution;
- manual resolution cannot alter a confirmed dispatch or erase prior audit evidence;
- browser/context closure and lock release on every success, failure, exception, and recovery path;
- stdout, stderr, Job inputs/outputs/events, SQLite text fields, screenshots, and expanded traces
  contain none of the seeded sensitive values; and
- complete Goal 4A image and Goal 4B preflight regression coverage.

Browser behavior tests use headed Playwright Chromium locally, Xvfb on Linux CI, and the existing
Windows-focused workflow. Unit fakes cover only pre-browser persistence failures and precisely
injected crash seams; they do not replace the local-page browser lifecycle tests.

## Verification and closure classification

Implementation must run focused tests after each task and the repository's common deterministic
baseline at closure, including:

```text
uv run python scripts/verify.py fast
uv run python scripts/verify.py full
uv run playwright install --dry-run chromium
```

The final Goal 4C gate requires:

1. fresh full deterministic verification;
2. implementation self-review;
3. a fresh independent reviewer over the complete Goal 4C range;
4. regression-driven fixes for every accepted Critical or High finding;
5. fresh post-review verification;
6. truthful closure updates to README, roadmap, and project memory;
7. a documentation-only closure commit that passes the complete local gate; and
8. Linux full and Windows focused GitHub Actions on the exact final SHA.

No live Flow generation or preflight is part of Goal 4C verification. After a future implementation,
the maximum truthful classification is:

```text
Goal 4C — Flow Generation, Download & Recovery

IMPLEMENTED       YES
LOCAL_VERIFIED    YES
PROVIDER_VERIFIED NOT ESTABLISHED
```

`BROWSER_PREFLIGHT_VERIFIED` also remains not run/not established unless a separately approved
operator-attended preflight occurs. Only the Goal 4D canary may establish provider verification for
the image lifecycle.

## Acceptance criteria

Goal 4C is ready for implementation planning only when this design is user-approved. A later
implementation may be classified `IMPLEMENTED` and `LOCAL_VERIFIED` only when all of the following
are true:

- `image.generate` supports an explicitly authorized `playwright_python` executor while retaining
  `local_fake` as the default and preserving its behavior;
- each Playwright generation atomically owns one Job, one durable Flow run, and exactly two durable
  slots;
- the reference is contained, hashed, required, uploaded, and verified, and the persisted prompt is
  entered and verified by hash before dispatch;
- Generate intent is committed before exactly one click and confirmation requires positive
  semantic evidence;
- no crash or retry after dispatch intent can authorize a blind second generation;
- two stable semantic candidate identities are persisted with sanitized grid evidence;
- each exact slot's 2K action correlates to exactly one Playwright download event;
- 1K or invalid files never become `ImageCandidate` rows;
- staging, final publication, hashing, and candidate ingestion are restart-safe, exclusive, and
  non-overwriting;
- every recovery-matrix path either continues from evidence or stops for intervention without
  losing prior generation/download evidence;
- manual no-dispatch resolution is explicit, actor/reason audited, and unable to rewrite confirmed
  history;
- browser resources close and the global Flow lock releases on every path;
- diagnostics and public metadata satisfy the expanded sensitive-data denylist;
- deterministic local browser, persistence, recovery, security, Linux, Windows, and full baseline
  tests pass; and
- the diff contains no semantic approval/QC, live provider canary, alternate provider, API/UI, or
  unrelated lifecycle implementation.

The next step after this specification commit is user review of the written file. Do not create the
Goal 4C implementation plan or implement Goal 4C until that review is complete.
