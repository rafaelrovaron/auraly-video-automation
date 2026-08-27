# Goal 4C — Flow Generation, Download & Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect the durable image domain to the headed Google Flow browser runtime so one explicitly authorized Job can safely produce, correlate, ingest, and recover exactly two 2K image candidates without blind redispatch or overwrite.

**Architecture:** Keep `image.generate` as the single Job type and route execution by the persisted `ImageGeneration.executor`. Preserve the existing local fake, while the Playwright path uses one authorized `FlowGenerationRun`, two durable `FlowCandidateSlot` checkpoints, the Goal 4B lock/runtime/security boundary, positive dispatch evidence, exact Playwright download-event correlation, and an evidence-driven recovery service.

**Tech Stack:** Python 3.11.15, Pydantic 2, Typer, SQLAlchemy 2, Alembic, SQLite, Playwright Python with managed headed Chromium, Pillow as a direct bounded image-decoding dependency, pytest, stdlib hashing/filesystem primitives, Ruff, mypy, GitHub Actions, Xvfb on Linux.

**Spec:** `docs/superpowers/specs/2026-08-27-goal-4c-flow-generation-download-recovery-design.md` at commit `1772957`.

## Global Constraints

- Implementation baseline is Goal 4B closure commit `9b634a7dc35f0146c427920be0c11a81ed5aae5e`; the approved Goal 4C design commit is `1772957`.
- `local_fake` remains the public default, retains exactly two deterministic PNG candidates, and remains `RetrySafety.IDEMPOTENT`.
- `playwright_python` requires a reference path/hash, `provider_action_confirmed=true`, a safe approval actor, exactly two candidates, resolution `2K`, and `RetrySafety.RECONCILE_BEFORE_RETRY`.
- No environment variable or CLI default may silently select Playwright, alter candidate count, allow 1K, skip reference/prompt verification, or bypass provider-action authorization.
- One `image.generate` Job owns the complete browser lifecycle. Do not create per-step upload/dispatch/download Jobs or an `auraly flow generate` command.
- Generate intent is committed before the click. After that commit, no exception, restart, timeout, or retry may click Generate again without positive reconciliation or explicit audited no-dispatch resolution.
- Dispatch confirmation requires a recognized generating indicator or attributable result transition. A successful click call, missing error, empty grid, ready button, or elapsed timeout is not confirmation.
- Exactly two semantic slot identities are persisted. A slot is never selected by coordinate, XPath, generated class, blind `nth`, raw position-only click, screenshot matching, or heuristic similarity.
- Each 2K download is captured from the Playwright event associated with the exact selected slot action; generic `~/Downloads` inventory is not used by the new worker.
- Accepted artifacts are PNG, JPEG, or WebP with positive facts and `max(width, height) >= 2048`. This is an ingest invariant, not Goal 4D semantic/full technical QC.
- Staging and final paths remain inside the configured work root after canonicalization, reject symlink/junction escape, publish without overwrite, and preserve every intentionally downloaded candidate.
- Browser profile, diagnostic root, and lock remain the Goal 4B safe external defaults. Browser execution is headed, uses Playwright-managed Chromium, permits manual authentication only, and holds the one global Flow lock through closure.
- Prompt, reference content/path, account identity, cookies, auth headers, storage state, signed URLs, query strings/fragments, DOM/HTML, response bodies, raw exceptions, and private absolute paths never enter public JSON, Job metadata, SQLite audit text, screenshots, or published traces.
- Production Flow origin remains fixed. Only an allowlisted relative workspace path with no query/fragment may be persisted; inability to establish a stable safe identity blocks recovery.
- Deterministic tests use private local Flow targets and synthetic downloads. CI and local closure verification never contact Google or consume provider credits.
- Goal 4C must not implement semantic approval/QC, a provider canary, alternate image provider, API/UI, or unrelated campaign/voice/video lifecycle changes.
- Use TDD for each behavior task: focused red test, minimum implementation, focused green test, relevant regression group, diff review, then one task commit.
- Goal closure requires fresh full verification, implementer self-review, a fresh independent reviewer over the complete Goal 4C range, regression-driven fixes for accepted Critical/High findings, a documentation-only closure commit, and Linux/Windows CI on the exact final SHA.
- Never mark `PROVIDER_VERIFIED` or `BROWSER_PREFLIGHT_VERIFIED` in Goal 4C.

## Repository Map

| Responsibility | Path |
| --- | --- |
| Approved Goal 4C contract | `docs/superpowers/specs/2026-08-27-goal-4c-flow-generation-download-recovery-design.md` |
| Job retry-policy protocol and execution gate | `src/auraly_pipeline/jobs/handlers.py`, `src/auraly_pipeline/jobs/service.py` |
| Image request/generation/candidate/run/slot contracts | `src/auraly_pipeline/images/domain.py` |
| Image and Flow checkpoint ORM rows | `src/auraly_pipeline/images/db_models.py` |
| Goal 4C schema migration | `src/auraly_pipeline/campaigns/migrations/versions/0005_flow_generation_recovery.py` |
| Image persistence and checkpoint transactions | `src/auraly_pipeline/images/repository.py` |
| Submission, authorization, inspection, and recovery | `src/auraly_pipeline/images/service.py` |
| Executor router and retained fake | `src/auraly_pipeline/images/handler.py` |
| Job-facing Flow executor | `src/auraly_pipeline/images/flow_handler.py` |
| Goal 4C browser contracts | `src/auraly_pipeline/flow/generation_domain.py` |
| Goal 4C semantic UI contract | `src/auraly_pipeline/flow/generation_locators.py` |
| Download staging, facts, and exclusive publication | `src/auraly_pipeline/flow/artifacts.py` |
| Generation and reconciliation browser lifecycle | `src/auraly_pipeline/flow/generation.py` |
| Shared Goal 4B runtime/config/diagnostics | `src/auraly_pipeline/flow/runtime.py`, `config.py`, `diagnostics.py`, `lock.py` |
| Public package exports | `src/auraly_pipeline/images/__init__.py`, `src/auraly_pipeline/flow/__init__.py` |
| CLI | `src/auraly_pipeline/cli.py` |
| Local Flow pages/download assets | `tests/fakes/flow-generation/*` |
| Focused tests | `tests/test_job_service.py`, `test_image_domain.py`, `test_image_migrations.py`, `test_image_repository.py`, `test_image_service.py`, `test_image_cli.py`, `test_flow_generation_domain.py`, `test_flow_generation_locators.py`, `test_flow_artifacts.py`, `test_flow_generation.py`, `test_flow_image_handler.py`, `test_flow_recovery.py`, `test_flow_generation_security.py` |
| Verification and CI | `tests/test_verify_harness.py`, `.github/workflows/verify.yml` |
| Closure truth | `README.md`, `docs/GOAL-ROADMAP.md`, `docs/PROJECT-MEMORY.md` |

---

### Task 1: Support executor-dependent retry safety without weakening existing handlers

**Files:**
- Modify: `src/auraly_pipeline/jobs/handlers.py`
- Modify: `src/auraly_pipeline/jobs/service.py`
- Modify: `tests/test_job_service.py`

**Interfaces:**
- Consumes: existing `JobHandler.retry_safety`, `RetrySafety`, and JobService submission/claim gates.
- Produces: `handler_accepts_retry_safety(handler: JobHandler, retry_safety: RetrySafety) -> bool` and optional handler method `accepts_retry_safety(retry_safety: RetrySafety) -> bool`.

- [ ] **Step 1: Add failing tests for the optional policy seam**

```python
class DualPolicyHandler:
    retry_safety = RetrySafety.IDEMPOTENT

    @staticmethod
    def accepts_retry_safety(retry_safety: RetrySafety) -> bool:
        return retry_safety in {
            RetrySafety.IDEMPOTENT,
            RetrySafety.RECONCILE_BEFORE_RETRY,
        }

    def execute(self, context: JobExecutionContext) -> JobExecutionResult:
        return JobExecutionResult(outcome=JobExecutionOutcome.SUCCESS)


def test_submit_uses_optional_retry_policy_acceptance(job_service) -> None:
    service = job_service(handlers={"image.generate": DualPolicyHandler()})
    submitted = service.submit_job(
        JobSubmit(
            job_type="image.generate",
            idempotency_key="flow-policy",
            retry_safety=RetrySafety.RECONCILE_BEFORE_RETRY,
        )
    )
    assert submitted.retry_safety is RetrySafety.RECONCILE_BEFORE_RETRY


def test_existing_fixed_policy_handler_still_rejects_mismatch(job_service) -> None:
    service = job_service(handlers={"fixed": SuccessHandler()})
    with pytest.raises(JobRetrySafetyError):
        service.submit_job(
            JobSubmit(
                job_type="fixed",
                idempotency_key="fixed-mismatch",
                retry_safety=RetrySafety.RECONCILE_BEFORE_RETRY,
            )
        )
```

Add a worker test that persists an accepted dual-policy Job, swaps in a handler that rejects its policy, and asserts the existing safe `handler_retry_safety_mismatch` failure without executing the handler.

- [ ] **Step 2: Run the focused tests and verify red**

Run: `uv run pytest tests/test_job_service.py -k "optional_retry_policy or fixed_policy or handler_retry_safety_mismatch" -q`

Expected: FAIL because JobService still compares only the fixed `retry_safety` attribute.

- [ ] **Step 3: Implement one compatibility helper**

```python
def handler_accepts_retry_safety(
    handler: JobHandler,
    retry_safety: RetrySafety,
) -> bool:
    accepts = getattr(handler, "accepts_retry_safety", None)
    if callable(accepts):
        return bool(accepts(retry_safety))
    return getattr(handler, "retry_safety", None) == retry_safety
```

Use this helper in `submit_job`, `submit_linked_job`, and the pre-execution check in `worker_once`. Do not alter retry scheduling, stale recovery, or state-machine behavior.

- [ ] **Step 4: Run focused and full Job regressions**

Run: `uv run pytest tests/test_job_service.py tests/test_job_handlers.py tests/test_job_concurrency.py tests/test_jobs.py -q`

Expected: PASS.

- [ ] **Step 5: Run the fast gate and review the diff**

Run: `uv run python scripts/verify.py fast`

Expected: Ruff and mypy pass. Confirm only the compatibility helper and its three JobService call sites changed.

- [ ] **Step 6: Commit Task 1**

```bash
git add src/auraly_pipeline/jobs/handlers.py src/auraly_pipeline/jobs/service.py tests/test_job_service.py
git commit -m "feat: support dynamic job retry policies"
```

---

### Task 2: Add versioned Flow request, run, slot, and transition contracts

**Files:**
- Modify: `src/auraly_pipeline/images/domain.py`
- Modify: `src/auraly_pipeline/images/__init__.py`
- Modify: `tests/test_image_domain.py`

**Interfaces:**
- Consumes: existing `ImageGenerateRequest`, `ImageGeneration`, `ImageCandidate`, and safe identifier/path validators.
- Produces: `FlowGenerationStage`, `FlowCandidateSlotState`, `FlowReconciliationReason`, `FlowGenerationRun`, `FlowCandidateSlot`, `ensure_flow_run_transition()`, `ensure_flow_slot_transition()`, and executor-aware request validation.

- [ ] **Step 1: Write failing executor/request tests**

```python
def test_local_fake_request_keeps_v1_defaults_and_fingerprint() -> None:
    request = ImageGenerateRequest(
        campaign_id="campaign-1",
        scene_variant_id=SCENE_ID,
        idempotency_key="fake-1",
        prompt_snapshot="safe prompt",
    )
    assert request.executor == "local_fake"
    assert request.generation_contract_version == "image-generation-v1"
    assert request.provider_action_confirmed is False
    assert request.provider_action_approved_by is None


def test_playwright_request_requires_reference_and_explicit_authorization() -> None:
    with pytest.raises(ValidationError):
        ImageGenerateRequest(
            campaign_id="campaign-1",
            scene_variant_id=SCENE_ID,
            idempotency_key="flow-1",
            prompt_snapshot="safe prompt",
            executor="playwright_python",
            generation_contract_version="flow-generation-v1",
        )


def test_playwright_request_accepts_exact_fixed_contract() -> None:
    request = _playwright_request()
    assert request.required_candidate_count == 2
    assert request.required_output_resolution == "2K"
    assert re.fullmatch(r"[0-9a-f]{64}", generation_request_fingerprint(request))
```

Keep a golden assertion for the existing local-fake v1 fingerprint so adding Flow fields does not invalidate previously persisted fake idempotency records. Reject Playwright requests with a missing reference pair, false confirmation, unsafe actor, wrong contract version, candidate count other than two, or resolution other than 2K.

- [ ] **Step 2: Write failing run/slot invariant tests**

```python
@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("prepared", "inputs_verified"),
        ("inputs_verified", "dispatch_intent_recorded"),
        ("dispatch_intent_recorded", "dispatch_confirmed"),
        ("dispatch_confirmed", "candidates_observed"),
        ("candidates_observed", "downloading"),
        ("downloading", "completed"),
        ("ambiguous", "dispatch_confirmed"),
        ("ambiguous", "candidates_observed"),
        ("ambiguous", "downloading"),
        ("ambiguous", "prepared"),
    ],
)
def test_allowed_flow_run_transitions(current, target) -> None:
    ensure_flow_run_transition(current, target)


def test_slot_ingested_requires_candidate_and_prior_download() -> None:
    with pytest.raises(ValidationError):
        _flow_slot(state="ingested", image_candidate_id=None)
```

Also reject illegal backwards transitions, slot indexes outside `0..1`, absolute/private artifact paths, unpaired path/hash fields, confirmation without intent, grid evidence before candidates observation, and unknown reconciliation reasons.

- [ ] **Step 3: Run the domain tests and verify red**

Run: `uv run pytest tests/test_image_domain.py -q`

Expected: FAIL because Flow contracts and Playwright request fields do not exist.

- [ ] **Step 4: Implement exact aliases and state machines**

```python
FlowGenerationStage = Literal[
    "prepared",
    "inputs_verified",
    "dispatch_intent_recorded",
    "dispatch_confirmed",
    "candidates_observed",
    "downloading",
    "completed",
    "ambiguous",
    "blocked",
    "failed",
]
FlowCandidateSlotState = Literal[
    "pending",
    "observed",
    "download_intent_recorded",
    "downloaded",
    "ingested",
    "blocked",
]
FlowReconciliationReason = Literal[
    "no_dispatch_proven",
    "existing_dispatch_reconciled",
    "staged_artifact_reconciled",
    "completed_generation_reconciled",
]
```

Add `FlowGenerationRun` and `FlowCandidateSlot` Pydantic models with the exact fields and paired-field validators from the spec. Extend `ImageGenerateRequest` with executor-aware validation.

Preserve the old local-fake canonical fingerprint byte-for-byte. Use a separate `flow-generation-v1` canonical payload for Playwright that includes executor, prompt/reference hashes, candidate count `2`, and resolution `2K` but excludes approval actor/time.

- [ ] **Step 5: Export only stable domain contracts**

Expose the aliases/models/transition functions from `images/__init__.py`. Do not export ORM rows, repository helpers, Playwright types, or internal transition maps.

- [ ] **Step 6: Run domain, service, and fake-handler regressions**

Run: `uv run pytest tests/test_image_domain.py tests/test_image_service.py tests/test_image_handler.py -q`

Expected: PASS, including the unchanged fake fingerprint and two-candidate behavior.

- [ ] **Step 7: Commit Task 2**

```bash
git add src/auraly_pipeline/images/domain.py src/auraly_pipeline/images/__init__.py tests/test_image_domain.py
git commit -m "feat: define Flow generation checkpoints"
```

---


### Task 3: Persist Flow runs and two candidate slots

**Files:**
- Create: `src/auraly_pipeline/campaigns/migrations/versions/0005_flow_generation_recovery.py`
- Modify: `src/auraly_pipeline/images/db_models.py`
- Modify: `tests/test_image_migrations.py`
- Create: `tests/test_image_migrations_direct_insert.py`

**Interfaces:**
- Consumes: `ImageGenerationRow`, `ImageCandidateRow`, Alembic revision `0004_image_domain`.
- Produces: `FlowGenerationRunRow`, `FlowCandidateSlotRow`, revision `0005_flow_generation_recovery`.

- [ ] **Step 1: Add a failing migration-head and legacy-upgrade test**

```python
def test_flow_generation_recovery_is_schema_head(tmp_path: Path) -> None:
    database = tmp_path / "auraly.db"
    migrate_database(database)
    with sqlite3.connect(database) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert revision == ("0005_flow_generation_recovery",)
    assert {"flow_generation_runs", "flow_candidate_slots"} <= tables
```

Build a database at `0004_image_domain`, insert one valid local-fake generation/candidate, upgrade to head, and assert the old rows are unchanged and no Flow run was synthesized.

- [ ] **Step 2: Add failing direct-insert constraint tests**

Use parameterized raw SQLite inserts for these exact invalid cases:

```python
INVALID_RUN_CASES = (
    {"required_candidate_count": 1},
    {"required_resolution": "1K"},
    {"provider_workspace_path": "workspace/abc", "provider_workspace_fingerprint": None},
    {"grid_evidence_path": "inspection/grid.png", "grid_evidence_sha256": None},
    {"dispatch_intent_at": None, "dispatch_confirmed_at": NOW},
)
INVALID_SLOT_CASES = (
    {"slot_index": -1},
    {"slot_index": 2},
    {"state": "ingested", "image_candidate_id": None},
    {"state": "pending", "image_candidate_id": CANDIDATE_ID},
)
```

Also assert rejection of duplicate run per generation, duplicate slot index per run, duplicate candidate link, and cross-generation candidate ownership.

- [ ] **Step 3: Run migration tests and verify red**

Run: `uv run pytest tests/test_image_migrations.py tests/test_image_migrations_direct_insert.py -q`

Expected: FAIL because revision 0005 and the ORM rows do not exist.

- [ ] **Step 4: Implement the migration with named checks and indexes**

Create `flow_generation_runs` with the exact spec fields. Use named constraints including:

```python
sa.CheckConstraint("required_candidate_count = 2", name="flow_run_candidate_count"),
sa.CheckConstraint("required_resolution = '2K'", name="flow_run_resolution"),
sa.CheckConstraint(
    "stage IN ('prepared','inputs_verified','dispatch_intent_recorded',"
    "'dispatch_confirmed','candidates_observed','downloading','completed',"
    "'ambiguous','blocked','failed')",
    name="flow_run_stage",
),
```

Create `flow_candidate_slots` with slot/state checks, paired staging/hash fields, unique `(flow_generation_run_id, slot_index)`, unique nullable `image_candidate_id`, and indexes by run/state. Add insert/update triggers that enforce the candidate linked by an ingested slot belongs to the same `ImageGeneration` as the slot's run.

- [ ] **Step 5: Add matching SQLAlchemy rows and relationships**

Define `FlowGenerationRunRow` and `FlowCandidateSlotRow` with column lengths matching the migration. Add narrow relationships from run to slots only; do not add cascading delete because generation and candidate history is immutable/restricted.

- [ ] **Step 6: Run migration and model regressions**

Run: `uv run pytest tests/test_image_migrations.py tests/test_image_migrations_direct_insert.py tests/test_migrations.py -q`

Expected: PASS on a fresh database and a 0004-to-0005 upgrade.

- [ ] **Step 7: Run the fast gate and commit Task 3**

Run: `uv run python scripts/verify.py fast`

Expected: Ruff and mypy pass.

```bash
git add src/auraly_pipeline/campaigns/migrations/versions/0005_flow_generation_recovery.py src/auraly_pipeline/images/db_models.py tests/test_image_migrations.py tests/test_image_migrations_direct_insert.py
git commit -m "feat: persist Flow generation recovery state"
```

---

### Task 4: Add transactional Flow checkpoint repository operations

**Files:**
- Modify: `src/auraly_pipeline/images/repository.py`
- Modify: `tests/test_image_repository.py`
- Modify: `tests/test_image_concurrency.py`

**Interfaces:**
- Consumes: `FlowGenerationRun`, `FlowCandidateSlot`, corresponding ORM rows, and `ImageCandidate`.
- Produces: `create_flow_run_in_session()`, `get_flow_run()`, `list_flow_slots()`, `transition_flow_run()`, `transition_flow_slot()`, and `ingest_flow_candidate()`.

- [ ] **Step 1: Write failing atomic creation tests**

```python
def test_create_flow_run_creates_exactly_two_pending_slots(session_factory) -> None:
    with session_factory() as session:
        run = ImageRepository.create_flow_run_in_session(
            session,
            _flow_run(),
            [_flow_slot(index=0), _flow_slot(index=1)],
        )
        session.commit()
    persisted = repository.get_flow_run(run.id)
    slots = repository.list_flow_slots(run.id)
    assert persisted.stage == "prepared"
    assert [(slot.slot_index, slot.state) for slot in slots] == [
        (0, "pending"),
        (1, "pending"),
    ]
```

Add rollback tests for one missing slot, a third slot, duplicate index, and generation ownership failure. The transaction must leave zero run/slot rows after every failure.

- [ ] **Step 2: Write failing transition and ingestion tests**

```python
def test_ingest_flow_candidate_links_slot_and_candidate_atomically(repository) -> None:
    candidate = _candidate(index=0)
    persisted = repository.ingest_flow_candidate(
        run_id=RUN_ID,
        slot_index=0,
        expected_slot_state="downloaded",
        candidate=candidate,
        now=NOW,
    )
    assert persisted.state == "ingested"
    assert persisted.image_candidate_id == candidate.image_candidate_id
    assert repository.get_candidate(candidate.image_candidate_id) is not None
```

Assert stale expected-state compare-and-set failure, illegal transition rollback, duplicate candidate rollback, cross-generation candidate rejection, and idempotent reload when the same slot/candidate facts already match exactly.

- [ ] **Step 3: Run repository tests and verify red**

Run: `uv run pytest tests/test_image_repository.py tests/test_image_concurrency.py -q`

Expected: FAIL because the checkpoint repository API is absent.

- [ ] **Step 4: Implement immediate compare-and-set transactions**

Use exact signatures:

```python
def transition_flow_run(
    self,
    run_id: str,
    *,
    expected_stage: FlowGenerationStage,
    target_stage: FlowGenerationStage,
    now: datetime,
    updates: Mapping[str, object] | None = None,
) -> FlowGenerationRunRow: ...

def transition_flow_slot(
    self,
    run_id: str,
    slot_index: int,
    *,
    expected_state: FlowCandidateSlotState,
    target_state: FlowCandidateSlotState,
    now: datetime,
    updates: Mapping[str, object] | None = None,
) -> FlowCandidateSlotRow: ...
```

Every mutator opens `BEGIN IMMEDIATE`, reloads current state, calls the domain transition guard, allowlists update fields, updates UTC timestamps, flushes constraints, and commits. It never returns a partially updated row after rollback.

- [ ] **Step 5: Implement atomic candidate ingestion**

`ingest_flow_candidate()` rechecks run/slot/generation ownership inside one immediate transaction, inserts the immutable candidate, links the slot, and changes only `downloaded -> ingested`. If an exact linked candidate already exists, return it without mutation; any mismatch raises an artifact conflict.

- [ ] **Step 6: Run repository, concurrency, and migration regressions**

Run: `uv run pytest tests/test_image_repository.py tests/test_image_concurrency.py tests/test_image_migrations.py -q`

Expected: PASS, including concurrent stale-state rejection.

- [ ] **Step 7: Commit Task 4**

```bash
git add src/auraly_pipeline/images/repository.py tests/test_image_repository.py tests/test_image_concurrency.py
git commit -m "feat: add Flow checkpoint transactions"
```

---

### Task 5: Create authorized Playwright submissions without changing the fake default

**Files:**
- Modify: `src/auraly_pipeline/images/service.py`
- Modify: `src/auraly_pipeline/cli.py`
- Modify: `tests/test_image_service.py`
- Modify: `tests/test_image_cli.py`

**Interfaces:**
- Consumes: Task 1 retry seam, Task 2 contracts, Task 3 rows, Task 4 atomic run creation.
- Produces: executor-aware `ImageService.generate()/regenerate()`, atomic provider authorization, and CLI options `--executor`, `--provider-action-approved-by`, `--confirm-provider-action`.

- [ ] **Step 1: Write a failing atomic submission test**

```python
def test_playwright_submission_atomically_creates_job_generation_run_slots_and_event(
    image_service,
) -> None:
    submission = image_service.generate(_playwright_request())
    assert submission.generation.executor == "playwright_python"
    assert submission.job.retry_safety is RetrySafety.RECONCILE_BEFORE_RETRY
    run = image_service.get_flow_run(submission.generation.image_generation_id)
    assert run.provider_action_approved_by == "operator-1"
    assert [slot.slot_index for slot in image_service.list_flow_slots(run.flow_generation_run_id)] == [
        0,
        1,
    ]
    event = next(
        item
        for item in submission.job.events
        if item.event_type == "job.provider_action_authorized"
    )
    assert event.metadata == {
        "approvedBy": "operator-1",
        "candidateCount": 2,
        "executor": "playwright_python",
        "resolution": "2K",
    }
```

Inject failure after Job creation, after generation insertion, after run insertion, and after the first slot; assert all entity/event groups roll back together.

- [ ] **Step 2: Write failing idempotency and fake-compatibility tests**

Assert these exact outcomes:

```text
same Playwright key + same intent → original submission reused
same key + different prompt/reference/executor → image_idempotency_conflict
same key + different approval actor → original authorization retained, not overwritten
local fake with no new flags → existing JSON keys and idempotent Job
local fake with confirmation/actor → image_invalid
Playwright without confirmation/reference/actor → image_invalid
```

- [ ] **Step 3: Write failing CLI contract tests**

```python
def test_image_generate_playwright_requires_explicit_confirmation(runner) -> None:
    result = runner.invoke(
        app,
        [
            "image", "generate", "campaign-1",
            "--scene-variant-id", SCENE_ID,
            "--idempotency-key", "flow-1",
            "--prompt-snapshot", "safe prompt",
            "--reference-image-path", "refs/avatar.png",
            "--reference-image-sha256", SHA256,
            "--executor", "playwright-python",
            "--provider-action-approved-by", "operator-1",
        ],
    )
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "image_invalid"
```

Inspect Typer's registered options and assert there is no candidate-count, resolution, provider URL, headless, personal-profile, or authorization-bypass option.

- [ ] **Step 4: Run service/CLI tests and verify red**

Run: `uv run pytest tests/test_image_service.py tests/test_image_cli.py -q`

Expected: FAIL because the Playwright submission and options do not exist.

- [ ] **Step 5: Implement atomic submission**

In the linked-job callback, create `ImageGeneration` first, then for Playwright create the authorized run, two pending slots, and append the authorization event through `job.events.append(JobEventRow(...))`. Use the same transaction already owned by `submit_linked_job`.

Add `ImageService.get_flow_run(image_generation_id)` and `ImageService.list_flow_slots(flow_generation_run_id)` as read-only domain-model mappings for submission, CLI, recovery, and review tests. They expose no ORM row or private path.

Construct the Job request exactly as:

```python
retry_safety = (
    RetrySafety.IDEMPOTENT
    if request.executor == "local_fake"
    else RetrySafety.RECONCILE_BEFORE_RETRY
)
job_request = JobSubmit(
    job_type="image.generate",
    campaign_id=request.campaign_id,
    scene_variant_id=request.scene_variant_id,
    idempotency_key=request.idempotency_key,
    input={"imageRequestFingerprint": request_fingerprint},
    max_attempts=3,
    retry_safety=retry_safety,
)
```

- [ ] **Step 6: Add explicit CLI options with safe defaults**

Typer accepts `Literal["local-fake", "playwright-python"]` and converts to domain underscore values. The confirmation flag defaults false; actor defaults null. Build `generation_contract_version` internally from executor so users cannot claim another version.

Update help text from “deterministic local-fake” to executor-aware language while keeping fake as default.

- [ ] **Step 7: Run focused and existing image CLI regressions**

Run: `uv run pytest tests/test_image_service.py tests/test_image_cli.py tests/test_cli.py -q`

Expected: PASS with one sanitized JSON object on every parsed path.

- [ ] **Step 8: Commit Task 5**

```bash
git add src/auraly_pipeline/images/service.py src/auraly_pipeline/cli.py tests/test_image_service.py tests/test_image_cli.py
git commit -m "feat: authorize Flow image submissions"
```

---


### Task 6: Define the Goal 4C semantic UI contract and deterministic pages

**Files:**
- Create: `src/auraly_pipeline/flow/generation_domain.py`
- Create: `src/auraly_pipeline/flow/generation_locators.py`
- Modify: `src/auraly_pipeline/flow/__init__.py`
- Create: `tests/fakes/flow-generation/ready.html`
- Create: `tests/fakes/flow-generation/upload-complete.html`
- Create: `tests/fakes/flow-generation/generating.html`
- Create: `tests/fakes/flow-generation/grid-two.html`
- Create: `tests/fakes/flow-generation/grid-three.html`
- Create: `tests/fakes/flow-generation/ambiguous-grid.html`
- Create: `tests/fakes/flow-generation/missing-2k.html`
- Create: `tests/test_flow_generation_domain.py`
- Create: `tests/test_flow_generation_locators.py`

**Interfaces:**
- Consumes: Goal 4B locator protocols, trusted-route failures, and local-browser support.
- Produces: `FlowGenerationLocatorName`, `FlowGenerationFailedStep`, `FlowWorkspaceIdentity`, `FlowCandidateObservation`, `FlowGenerationObservation`, typed UI/dispatch/download errors, and exact semantic resolver functions.

- [ ] **Step 1: Write failing contract tests**

```python
def test_candidate_observation_contains_only_safe_identity() -> None:
    observation = FlowCandidateObservation(
        fingerprint="a" * 64,
        semantic_order=0,
        completed=True,
    )
    assert observation.model_dump() == {
        "fingerprint": "a" * 64,
        "semantic_order": 0,
        "completed": True,
    }


def test_generation_contract_rejects_raw_url_or_prompt_fields() -> None:
    fields = set(FlowCandidateObservation.model_fields)
    assert {"url", "thumbnail_url", "prompt", "dom", "html"}.isdisjoint(fields)
```

Define only allowlisted failed steps: `open_workspace`, `upload_reference`, `verify_reference`, `fill_prompt`, `verify_prompt`, `record_dispatch_intent`, `dispatch_generate`, `confirm_dispatch`, `observe_candidates`, `capture_grid_evidence`, `request_2k`, `capture_download`, and `close_browser`.

- [ ] **Step 2: Write failing real-browser locator tests**

```python
def test_ready_page_resolves_exact_generation_controls(flow_generation_page: Page) -> None:
    flow_generation_page.goto(fake_generation_url("ready.html"))
    assert resolve_reference_input(flow_generation_page).count() == 1
    assert resolve_generation_prompt(flow_generation_page).count() == 1
    assert resolve_generate_control(flow_generation_page).count() == 1


def test_grid_three_returns_unique_semantic_identities_in_validated_order(
    flow_generation_page: Page,
) -> None:
    flow_generation_page.goto(fake_generation_url("grid-three.html"))
    observations = observe_completed_candidate_slots(flow_generation_page)
    assert [item.semantic_order for item in observations] == [0, 1, 2]
    assert len({item.fingerprint for item in observations}) == 3
```

For each locator, add zero-match, multiple-match, hidden, disabled, blocking-overlay, and unexpected-route cases. `ambiguous-grid.html` contains duplicated safe slot identity and must fail closed. `missing-2k.html` exposes a slot but no unique enabled 2K action.

- [ ] **Step 3: Run new tests and verify red**

Run: `uv run pytest tests/test_flow_generation_domain.py tests/test_flow_generation_locators.py -q`

Expected: FAIL at collection because the new modules do not exist.

- [ ] **Step 4: Implement typed semantic resolvers**

Use role, label, placeholder, exact visible text, and explicitly allowlisted stable attributes only. Define:

```python
def resolve_reference_input(page: PageProtocol[_LocatorT]) -> _LocatorT: ...
def resolve_upload_complete(page: PageProtocol[_LocatorT]) -> _LocatorT: ...
def resolve_generation_prompt(page: PageProtocol[_LocatorT]) -> _LocatorT: ...
def resolve_generate_control(page: PageProtocol[_LocatorT]) -> _LocatorT: ...
def resolve_generating_indicator(page: PageProtocol[_LocatorT]) -> _LocatorT: ...
def observe_completed_candidate_slots(
    page: PageProtocol[_LocatorT],
) -> tuple[FlowCandidateObservation, ...]: ...
def resolve_candidate_2k_action(
    page: PageProtocol[_LocatorT],
    fingerprint: str,
) -> _LocatorT: ...
```

A candidate fingerprint is SHA-256 over canonical JSON containing only the safe semantic slot key and normalized completion role. Do not hash raw URL/token values and do not persist accessible text that can contain prompt/account data.

- [ ] **Step 5: Add deterministic fixtures with no network**

Each fixture includes explicit accessible roles/labels and stable `data-flow-candidate-id` values used only by the private local seam. Add the existing request-listener assertion over every new page and click path; the list must contain only local `file:` requests until a synthetic Playwright download begins.

- [ ] **Step 6: Prohibit unsafe selector syntax structurally**

Extend the locator AST/source scan to reject `xpath=`, `nth-child`, `.nth(`, coordinate APIs, generated-class selectors, and image matching in both Goal 4B and Goal 4C locator/runtime modules.

- [ ] **Step 7: Run locator and Goal 4B regressions**

Run: `uv run pytest tests/test_flow_generation_domain.py tests/test_flow_generation_locators.py tests/test_flow_locators.py tests/test_flow_runtime.py -q`

Expected: PASS with the existing preflight locator contract unchanged.

- [ ] **Step 8: Commit Task 6**

```bash
git add src/auraly_pipeline/flow/generation_domain.py src/auraly_pipeline/flow/generation_locators.py src/auraly_pipeline/flow/__init__.py tests/fakes/flow-generation tests/test_flow_generation_domain.py tests/test_flow_generation_locators.py
git commit -m "feat: define Flow generation UI contract"
```

---

### Task 7: Validate, stage, and exclusively publish downloaded image artifacts

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `src/auraly_pipeline/flow/artifacts.py`
- Create: `tests/test_flow_artifacts.py`
- Modify: `src/auraly_pipeline/flow/__init__.py`

**Interfaces:**
- Consumes: trusted `work_root`, generation identity, candidate index, downloaded staging path.
- Produces: `FlowArtifactFacts`, `FlowArtifactInvalidError`, `FlowArtifactConflictError`, `allocate_flow_staging_path()`, `resolve_flow_final_path()`, `inspect_flow_artifact()`, and `publish_flow_artifact_exclusive()`.

- [ ] **Step 1: Write failing path and non-overwrite tests**

```python
def test_candidate_staging_and_final_paths_are_canonical_and_distinct(tmp_path: Path) -> None:
    staging = allocate_flow_staging_path(
        work_root=tmp_path,
        campaign_id="campaign-1",
        scene_variant_id=SCENE_ID,
        generation_number=1,
        candidate_index=0,
    )
    final = resolve_flow_final_path(
        work_root=tmp_path,
        campaign_id="campaign-1",
        scene_variant_id=SCENE_ID,
        generation_number=1,
        candidate_index=0,
        image_format="png",
    )
    assert final.relative_to(tmp_path).as_posix().endswith(
        "generation-0001/candidate-0000.png"
    )
    assert staging.parent == final.parent / ".staging"
    assert staging.suffix == ".part"
    assert staging != final


def test_exclusive_publish_never_overwrites_existing_final(tmp_path: Path) -> None:
    staging, final = _valid_staging_and_final(tmp_path)
    final.write_bytes(b"existing")
    with pytest.raises(FlowArtifactConflictError):
        publish_flow_artifact_exclusive(staging, final, trusted_root=tmp_path)
    assert final.read_bytes() == b"existing"
```

Add canonicalization tests for traversal, absolute input, symlink/junction escape, candidate index outside `0..1`, and staging/final containment.

- [ ] **Step 2: Write failing format and 2K tests**

Create small deterministic Pillow-generated fixtures for valid 2048-axis PNG/JPEG/WebP and invalid 1024-axis equivalents:

```python
@pytest.mark.parametrize("fixture", ["2k.png", "2k.jpg", "2k.webp"])
def test_inspect_accepts_supported_decodable_2k_artifact(fixture_path: Path) -> None:
    facts = inspect_flow_artifact(fixture_path)
    assert facts.format in {"png", "jpeg", "webp"}
    assert max(facts.width, facts.height) >= 2048
    assert facts.size_bytes == fixture_path.stat().st_size
    assert facts.sha256 == hashlib.sha256(fixture_path.read_bytes()).hexdigest()


@pytest.mark.parametrize("fixture", ["1k.png", "1k.jpg", "1k.webp"])
def test_inspect_rejects_1k_artifact(fixture_path: Path) -> None:
    with pytest.raises(FlowArtifactInvalidError):
        inspect_flow_artifact(fixture_path)
```

Also reject empty, partial extension, truncated headers, mismatched extension/signature, malformed JPEG segment length, malformed RIFF size, unsupported GIF/BMP, polyglot/trailing executable marker, and files above the explicit size cap `100_000_000` bytes without reading them unboundedly.

- [ ] **Step 3: Run artifact tests and verify red**

Run: `uv run pytest tests/test_flow_artifacts.py -q`

Expected: FAIL because `flow.artifacts` does not exist.

- [ ] **Step 4: Add and lock a direct Pillow dependency**

Add `Pillow>=11,<12` to project dependencies and refresh only the lockfile dependency resolution:

```bash
uv lock
uv run python -c "from PIL import Image; print(Image.__version__)"
```

Expected: the installed version satisfies `>=11,<12` and no unrelated direct dependency changes.

- [ ] **Step 5: Implement bounded full decode and container validation**

Reject files above `100_000_000` bytes before opening. Convert `DecompressionBombWarning` to an error, set an explicit maximum pixel count of `100_000_000`, call `Image.verify()`, reopen, call `load()`, and require Pillow format in `PNG`, `JPEG`, or `WEBP`. Validate that PNG ends at its IEND chunk, JPEG at EOI, and WebP RIFF declared length equals file length so trailing polyglot payloads fail. Normalize Pillow's `JPEG` to domain `jpeg`; compute SHA-256 by fixed-size chunks only after successful decode/container validation.

- [ ] **Step 6: Implement exclusive same-filesystem publication**

Allocate a unique neutral `.part` staging file before the download without consulting the provider's suggested filename. After inspection determines normalized `png`, `jpeg`, or `webp`, derive the canonical final suffix from those bytes alone. Publish with `os.link(staging, final)`, which atomically fails if the final exists. Flush the linked file and parent directory where supported, then unlink staging. Map unsupported hard links, permission errors, or hash changes to a typed safe failure; never use `os.replace`, overwrite mode, or a copy fallback.

- [ ] **Step 7: Test crash residues and exact-match recovery**

Inject failure after staging write, after link creation, and before staging unlink. Prove that a matching final/staging pair is recoverable by hash, a mismatched pair blocks, and a retry never changes final bytes.

- [ ] **Step 8: Run artifacts and Goal 4A artifact regressions**

Run: `uv run pytest tests/test_flow_artifacts.py tests/test_image_handler.py tests/test_image_recovery.py tests/test_image_generation.py -q`

Expected: PASS.

- [ ] **Step 9: Commit Task 7**

```bash
git add pyproject.toml uv.lock src/auraly_pipeline/flow/artifacts.py src/auraly_pipeline/flow/__init__.py tests/test_flow_artifacts.py
git commit -m "feat: validate and publish Flow artifacts"
```

---

### Task 8: Build the authenticated input and single-dispatch browser lifecycle

**Files:**
- Modify: `src/auraly_pipeline/flow/config.py`
- Modify: `src/auraly_pipeline/flow/runtime.py`
- Create: `src/auraly_pipeline/flow/generation.py`
- Create: `tests/test_flow_generation.py`
- Modify: `tests/test_flow_config.py`
- Modify: `tests/test_flow_runtime.py`
- Modify: `tests/test_flow_security.py`

**Interfaces:**
- Consumes: Goal 4B persistent context/auth/trust/lock/diagnostics, Task 6 locators.
- Produces: `FlowGenerationConfig`, `FlowGenerationCheckpointSink`, `FlowGenerationRuntime.prepare_and_dispatch()`, and package-internal authenticated session reuse.

- [ ] **Step 1: Write failing generation-config tests**

```python
def test_generation_timeout_defaults_and_environment(monkeypatch) -> None:
    config = resolve_flow_generation_config()
    assert config.generation_timeout_seconds == 600
    assert config.download_timeout_seconds == 120
    monkeypatch.setenv("AURALY_FLOW_GENERATION_TIMEOUT_SECONDS", "900")
    monkeypatch.setenv("AURALY_FLOW_DOWNLOAD_TIMEOUT_SECONDS", "180")
    overridden = resolve_flow_generation_config()
    assert overridden.generation_timeout_seconds == 900
    assert overridden.download_timeout_seconds == 180
```

Reject zero, negative, non-integer, excessive values above `3600`, and any public URL/headless/channel option. Keep `FlowPreflightService.preflight()` signature unchanged.

- [ ] **Step 2: Write failing input-verification browser tests**

```python
def test_prepare_uploads_reference_and_verifies_prompt_hash(
    flow_generation_page: Page,
    reference_png: Path,
) -> None:
    runtime = _runtime_for_fixture("ready.html", flow_generation_page)
    observed = runtime.prepare_inputs(
        reference_path=reference_png,
        reference_sha256=sha256(reference_png),
        prompt_snapshot="private prompt",
        prompt_sha256=sha256_text("private prompt"),
    )
    assert observed.reference_verified is True
    assert observed.prompt_verified is True
    assert observed.persisted_fields == {}
```

Assert failure before dispatch for wrong reference hash, missing/ambiguous file input, absent upload-complete state, prompt readback mismatch, route change, blocking overlay, and injected file-input error. Seed prompt/reference/private path values and prove they are absent from exception text and evidence metadata.

- [ ] **Step 3: Write the dispatch ordering and crash tests**

```python
def test_dispatch_commits_intent_before_exactly_one_click(runtime, checkpoint_sink) -> None:
    runtime.prepare_and_dispatch(_prepared_request(), checkpoint_sink)
    assert checkpoint_sink.events == [
        "inputs_verified",
        "dispatch_intent_recorded",
        "dispatch_confirmed",
    ]
    assert runtime.generate_click_count == 1
    assert checkpoint_sink.call_index("dispatch_intent_recorded") < runtime.generate_click_index


@pytest.mark.parametrize("crash_point", ["after_intent", "during_click", "before_confirmation"])
def test_post_intent_failure_is_ambiguous_and_never_clicks_twice(
    runtime,
    checkpoint_sink,
    crash_point,
) -> None:
    runtime.inject_crash(crash_point)
    with pytest.raises(FlowDispatchAmbiguousError):
        runtime.prepare_and_dispatch(_prepared_request(), checkpoint_sink)
    assert runtime.generate_click_count <= 1
    runtime.reconcile(_run_at_intent(), checkpoint_sink)
    assert runtime.generate_click_count <= 1
```

Add positive confirmation cases for recognized generating indicator and attributable existing result transition. Reject successful click return, empty grid, ready Generate button, and timeout as confirmation.

- [ ] **Step 4: Run focused tests and verify red**

Run: `uv run pytest tests/test_flow_config.py tests/test_flow_generation.py -k "config or prepare or dispatch" -q`

Expected: FAIL because generation config/runtime do not exist.

- [ ] **Step 5: Extract a package-internal authenticated session seam**

Refactor Goal 4B `runtime.py` only enough to share launch, fixed-route authentication, current-route validation, evidence capture, and closure through a package-internal `FlowBrowserSession`. Keep `GoogleFlowRuntime.run()` public behavior and every preflight status/result unchanged.

The generation module may call the session seam; it must not access cookies, storage state, browser profile contents, or authentication form controls.

- [ ] **Step 6: Implement input verification without persistence leakage**

`prepare_inputs()` resolves the exact file input, calls `set_input_files(reference_path)`, observes the exact upload-complete contract, fills the prompt, reads it back, hashes it in memory, and returns booleans/safe hashes only. It never returns raw prompt, filename, path, DOM, or locator.

- [ ] **Step 7: Implement the checkpoint-before-click protocol**

```python
class FlowGenerationCheckpointSink(Protocol):
    def record_inputs_verified(self, observation: FlowGenerationObservation) -> None: ...
    def record_dispatch_intent(self, workspace: FlowWorkspaceIdentity) -> None: ...
    def record_dispatch_confirmed(self, observation: FlowGenerationObservation) -> None: ...
```

`prepare_and_dispatch()` must call `record_dispatch_intent()` synchronously and receive success before invoking the unique Generate locator's `click()`. Any exception after the callback begins maps to `FlowDispatchAmbiguousError`. Reconciliation code has no Generate locator/click path.

- [ ] **Step 8: Prove Goal 4B behavioral identity**

Run: `uv run pytest tests/test_flow_runtime.py tests/test_flow_service.py tests/test_flow_cli.py tests/test_flow_security.py -q`

Expected: PASS with the exact six preflight statuses, JSON keys, evidence matrix, and close semantics unchanged.

- [ ] **Step 9: Run focused generation tests and commit Task 8**

Run: `uv run pytest tests/test_flow_config.py tests/test_flow_generation.py -k "config or prepare or dispatch" -q`

Expected: PASS.

```bash
git add src/auraly_pipeline/flow/config.py src/auraly_pipeline/flow/runtime.py src/auraly_pipeline/flow/generation.py tests/test_flow_config.py tests/test_flow_runtime.py tests/test_flow_generation.py tests/test_flow_security.py
git commit -m "feat: add safe Flow generation dispatch"
```

---


### Task 9: Observe two candidates, sanitize grid evidence, and correlate exact 2K downloads

**Files:**
- Modify: `src/auraly_pipeline/flow/generation.py`
- Modify: `src/auraly_pipeline/flow/diagnostics.py`
- Modify: `tests/test_flow_generation.py`
- Modify: `tests/test_flow_diagnostics.py`
- Modify: `tests/fakes/flow-generation/grid-two.html`
- Modify: `tests/fakes/flow-generation/grid-three.html`
- Create: `tests/fakes/flow-generation/download-2k.png`

**Interfaces:**
- Consumes: Task 6 candidate observations/2K actions, Task 7 artifact functions, Task 8 authenticated session/checkpoint sink.
- Produces: `FlowGenerationRuntime.observe_and_download()`, grid-evidence publication, and slot download callbacks.

- [ ] **Step 1: Write failing candidate binding and evidence tests**

```python
def test_observe_binds_first_two_validated_semantic_slots(runtime, checkpoint_sink) -> None:
    observations = runtime.observe_candidates(checkpoint_sink)
    assert [item.semantic_order for item in observations] == [0, 1]
    assert len({item.fingerprint for item in observations}) == 2
    assert checkpoint_sink.bound_slots == {
        0: observations[0].fingerprint,
        1: observations[1].fingerprint,
    }


def test_grid_evidence_masks_input_and_identity_regions(runtime, evidence_root) -> None:
    published = runtime.capture_grid_evidence()
    payload = (evidence_root / published.relative_path).read_bytes()
    assert published.sha256 == hashlib.sha256(payload).hexdigest()
    assert b"PRIVATE PROMPT" not in payload
    assert b"person@example.com" not in payload
    assert b"reference-secret.png" not in payload
```

Assert zero/one completed slot timeout, duplicate fingerprints, changed fingerprints between observation and evidence, sanitizer failure, route change, and a third visible slot not selected or downloaded.

- [ ] **Step 2: Write failing exact-download ordering tests**

```python
@pytest.mark.parametrize("slot_index", [0, 1])
def test_download_records_intent_before_exact_2k_action(
    runtime,
    checkpoint_sink,
    slot_index,
) -> None:
    artifact = runtime.download_slot(slot_index, checkpoint_sink)
    assert checkpoint_sink.slot_events(slot_index) == [
        "download_intent_recorded",
        "downloaded",
    ]
    assert runtime.download_actions == [(slot_index, "2K")]
    assert max(artifact.width, artifact.height) >= 2048


def test_unrelated_download_event_cannot_satisfy_slot(runtime, checkpoint_sink) -> None:
    runtime.inject_unrelated_download_before_slot()
    with pytest.raises(FlowDownloadCorrelationError):
        runtime.download_slot(0, checkpoint_sink)
    assert checkpoint_sink.slot_state(0) == "download_intent_recorded"
```

Also inject no event, two events, canceled/failing download, partial file, 1K bytes, changed slot fingerprint, and crash after event/save/checkpoint/publication. Every case preserves the last durable slot state and never selects another slot.

- [ ] **Step 3: Run candidate/download tests and verify red**

Run: `uv run pytest tests/test_flow_generation.py tests/test_flow_diagnostics.py -k "candidate or grid or download" -q`

Expected: FAIL because observation/download lifecycle and grid evidence are incomplete.

- [ ] **Step 4: Add generation-specific masked evidence publication**

Reuse Goal 4B PNG structural validation and exclusive diagnostic publication. Add a function that accepts screenshot bytes only after Playwright masks are resolved for account identity, prompt field, reference preview, and upload filename. It returns only:

```python
@dataclass(frozen=True)
class FlowGridEvidence:
    relative_path: str
    sha256: str
```

Publish under the generation's trusted inspection root with exclusive creation. Never publish a raw pre-mask screenshot, DOM snapshot, source, trace body, or full path.

- [ ] **Step 5: Implement stable candidate observation**

Poll with the injected monotonic clock until at least two completed unique semantic observations remain unchanged across two consecutive reads. Bind exactly indexes 0 and 1 in validated semantic order through the checkpoint sink, capture/persist masked evidence, then move the run to `candidates_observed`.

A third or later validated slot is ignored without interaction. Loading, disabled, failed, duplicate, or unknown slot state cannot count toward two.

- [ ] **Step 6: Implement one-event-per-action 2K download**

For each persisted slot, re-enumerate and match its exact fingerprint, resolve its unique 2K action, call the slot intent checkpoint, then wrap only that action:

```python
with page.expect_download(timeout=config.download_timeout_seconds * 1000) as pending:
    action.click()
download = pending.value
staging_path = allocate_flow_staging_path(
    work_root=work_root,
    campaign_id=generation.campaign_id,
    scene_variant_id=generation.scene_variant_id,
    generation_number=generation.generation_number,
    candidate_index=slot.slot_index,
)
download.save_as(staging_path)
```

Inspect staging through Task 7, call the downloaded checkpoint with relative path/hash, exclusively publish, and return facts. Do not use suggested filename, global download inventory, raw download URL, or another page event as identity.

- [ ] **Step 7: Run candidate/download, diagnostics, and locator regressions**

Run: `uv run pytest tests/test_flow_generation.py tests/test_flow_diagnostics.py tests/test_flow_generation_locators.py tests/test_flow_security.py -q`

Expected: PASS.

- [ ] **Step 8: Commit Task 9**

```bash
git add src/auraly_pipeline/flow/generation.py src/auraly_pipeline/flow/diagnostics.py tests/test_flow_generation.py tests/test_flow_diagnostics.py tests/fakes/flow-generation
git commit -m "feat: correlate Flow 2K candidate downloads"
```

---

### Task 10: Route image Jobs into the Flow runtime and ingest two candidates

**Files:**
- Modify: `src/auraly_pipeline/images/handler.py`
- Create: `src/auraly_pipeline/images/flow_handler.py`
- Modify: `src/auraly_pipeline/jobs/service.py`
- Modify: `src/auraly_pipeline/images/service.py`
- Create: `tests/test_flow_image_handler.py`
- Modify: `tests/test_image_handler.py`
- Modify: `tests/test_job_service.py`

**Interfaces:**
- Consumes: executor-aware Job, Flow run/slots, repository checkpoints, Flow generation runtime/artifacts.
- Produces: `LocalFakeImageGenerateHandler`, executor-routing `ImageGenerateHandler`, `FlowImageGenerateHandler`, and default worker registration.

- [ ] **Step 1: Freeze the current fake handler behavior before refactoring**

Add/retain an explicit golden test:

```python
def test_default_image_job_still_uses_local_fake_without_flow_import_or_launch(
    image_service,
) -> None:
    submission = image_service.generate(_fake_request())
    completed = image_service.worker_once("worker-1")
    assert completed is not None
    assert completed.status is JobStatus.COMPLETED
    assert completed.output == {
        "candidateCount": 2,
        "imageGenerationId": submission.generation.image_generation_id,
    }
    assert len(image_service.list_candidates(submission.generation.image_generation_id)) == 2
```

Patch the Flow runtime factory to raise if called. The test must still pass.

- [ ] **Step 2: Write a failing local-page Flow Job integration test**

```python
def test_playwright_image_job_completes_two_2k_candidates(
    flow_image_service,
    reference_2k_png,
) -> None:
    submission = flow_image_service.generate(_playwright_request(reference_2k_png))
    completed = flow_image_service.worker_once("flow-worker", lease_seconds=30)
    assert completed is not None
    assert completed.status is JobStatus.COMPLETED
    assert completed.output["candidateCount"] == 2
    assert completed.output["resolution"] == "2K"
    candidates = flow_image_service.list_candidates(
        submission.generation.image_generation_id
    )
    assert [item.candidate_index for item in candidates] == [0, 1]
    assert all(max(item.width, item.height) >= 2048 for item in candidates)
    assert all(item.review_status == "pending_review" for item in candidates)
```

Use the private local runtime target and synthetic Playwright download; do not monkeypatch away the browser, locator, or download event.

- [ ] **Step 3: Write failing integrity and pre-browser tests**

Parameterize corrupted/missing Job fingerprint, Campaign/Scene ownership, executor/policy mismatch, authorization event mismatch, reference path escape, reference SHA mismatch, run count/resolution mismatch, missing/extra slots, and completed generation with missing artifact. Assert safe terminal/blocked codes and zero browser-factory calls.

- [ ] **Step 4: Run handler tests and verify red**

Run: `uv run pytest tests/test_flow_image_handler.py tests/test_image_handler.py -q`

Expected: FAIL because the router and Flow handler are absent.

- [ ] **Step 5: Extract the existing fake implementation without behavior changes**

Rename the current concrete fake to `LocalFakeImageGenerateHandler`. Keep `deterministic_png_bytes`, paths, retry safety, results, failure codes, and recovery semantics unchanged.

Create an executor router:

```python
class ImageGenerateHandler:
    retry_safety = RetrySafety.IDEMPOTENT

    def accepts_retry_safety(self, retry_safety: RetrySafety) -> bool:
        return retry_safety in {
            RetrySafety.IDEMPOTENT,
            RetrySafety.RECONCILE_BEFORE_RETRY,
        }

    def execute(self, context: JobExecutionContext) -> JobExecutionResult:
        executor = self._executor_for_claim(context)
        if executor == "local_fake":
            return self._local_fake.execute(context)
        if executor == "playwright_python":
            return self._flow.execute(context)
        return self._terminal_integrity_failure()
```

The lookup validates linked generation ownership and exact executor/retry mapping before delegation.

- [ ] **Step 6: Implement the repository-backed checkpoint sink**

In `flow_handler.py`, implement `FlowGenerationCheckpointSink` by calling Task 4 compare-and-set methods. Each callback commits one safety boundary and reloads to verify it. It stores only safe hashes, relative paths, timestamps, and allowlisted state.

- [ ] **Step 7: Implement Flow handler execution and completion**

The Flow handler:

```text
validates all preconditions
marks aggregate provider_state=generating
reconciles current checkpoint before any new browser action
prepares/dispatches only when stage < dispatch_intent_recorded
observes/binds slots only after confirmed dispatch
downloads/ingests only missing slots
validates already-ingested candidates by final bytes/facts
commits run + generation completed after both slots ingested
returns safe success
```

Map typed failures before intent to blocked/recoverable safe codes and failures at/after intent to ambiguous/blocked. Never catch `KeyboardInterrupt` or `SystemExit`; browser cleanup still runs in `finally`.

- [ ] **Step 8: Register the router by default**

`JobService.for_database()` constructs the local fake, Flow handler, and router. Add private constructor/factory injection for tests only; no public CLI target URL or browser override.

- [ ] **Step 9: Run handler, worker, fake, and browser regressions**

Run: `uv run pytest tests/test_flow_image_handler.py tests/test_image_handler.py tests/test_image_recovery.py tests/test_job_service.py tests/test_flow_generation.py -q`

Expected: PASS.

- [ ] **Step 10: Commit Task 10**

```bash
git add src/auraly_pipeline/images/handler.py src/auraly_pipeline/images/flow_handler.py src/auraly_pipeline/jobs/service.py src/auraly_pipeline/images/service.py tests/test_flow_image_handler.py tests/test_image_handler.py tests/test_job_service.py
git commit -m "feat: execute authorized Flow image jobs"
```

---

### Task 11: Add evidence-driven recovery and audited no-dispatch resolution

**Files:**
- Modify: `src/auraly_pipeline/jobs/repository.py`
- Modify: `src/auraly_pipeline/jobs/service.py`
- Modify: `src/auraly_pipeline/images/service.py`
- Modify: `src/auraly_pipeline/cli.py`
- Create: `tests/test_flow_recovery.py`
- Modify: `tests/test_job_service.py`
- Modify: `tests/test_image_cli.py`

**Interfaces:**
- Consumes: blocked reconcile-before-retry Job, Flow run/slots/artifacts, read-only browser reconciliation.
- Produces: reason-aware `resume_reconciled_job()`, `ImageService.recover_generation()`, `ImageService.resolve_no_dispatch()`, and two CLI commands.

- [ ] **Step 1: Write failing reason-aware Job resume tests**

```python
@pytest.mark.parametrize(
    "reason",
    [
        "no_dispatch_proven",
        "existing_dispatch_reconciled",
        "staged_artifact_reconciled",
        "completed_generation_reconciled",
    ],
)
def test_resume_reconciled_records_exact_allowlisted_reason(job_service, reason) -> None:
    blocked = _blocked_reconcile_job(job_service)
    resumed = job_service.resume_reconciled_job(blocked.job_id, reason=reason)
    assert resumed.status is JobStatus.QUEUED
    event = next(item for item in resumed.events if item.event_type == "job.reconciled")
    assert event.metadata["reason"] == reason
```

Reject arbitrary reasons, non-blocked Jobs, idempotent/manual-only Jobs, and exhausted attempts. Preserve all existing transition guards.

- [ ] **Step 2: Write the complete failing recovery matrix**

```python
@pytest.mark.parametrize(
    ("checkpoint", "expected_reason", "browser_opened", "generate_clicks"),
    [
        ("prepared", "no_dispatch_proven", False, 0),
        ("dispatch_intent_with_generating_ui", "existing_dispatch_reconciled", True, 0),
        ("dispatch_confirmed", "existing_dispatch_reconciled", True, 0),
        ("downloaded_staging", "staged_artifact_reconciled", False, 0),
        ("two_ingested", "completed_generation_reconciled", False, 0),
    ],
)
def test_recover_generation_uses_evidence_without_redispatch(
    recovery_service,
    checkpoint,
    expected_reason,
    browser_opened,
    generate_clicks,
) -> None:
    result = recovery_service.recover_generation(
        GENERATION_ID,
        reconciled_by="operator-1",
    )
    assert result.reason == expected_reason
    assert recovery_service.browser_opened is browser_opened
    assert recovery_service.generate_click_count == generate_clicks
```

Add blocked cases for intent plus empty grid, changed workspace, unsafe route, prompt/reference mismatch, duplicate slot fingerprint, staging/final/DB conflict, missing completed artifact, sanitizer failure, and browser close failure.

- [ ] **Step 3: Write failing manual resolution audit tests**

```python
def test_resolve_no_dispatch_preserves_attempt_audit_and_requeues(service) -> None:
    resolved = service.resolve_no_dispatch(
        GENERATION_ID,
        resolved_by="operator-1",
        reason="Operator inspected Flow history and confirmed no generation.",
    )
    assert resolved.stage == "prepared"
    assert resolved.dispatch_attempt_number == 2
    assert resolved.dispatch_intent_at is None
    event = next(
        item
        for item in resolved.job.events
        if item.event_type == "job.flow_dispatch_resolved"
    )
    assert event.metadata["previousDispatchAttemptNumber"] == 1
    assert event.metadata["resolvedBy"] == "operator-1"
```

Reject resolution of confirmed dispatch, non-ambiguous run, active/running Job, unsafe actor/reason, exhausted Job, and a second call after the run has already advanced.

- [ ] **Step 4: Write failing CLI JSON/security tests**

Invoke:

```text
auraly image generation recover 00000000-0000-4000-8000-000000000001 --reconciled-by operator-1
auraly image generation resolve-no-dispatch 00000000-0000-4000-8000-000000000001 --resolved-by operator-1 --reason "Operator confirmed no generation."
```

Assert exact success keys `success`, `generation`, `flowRun`, `slots`, `job`, `reconciliationReason`; exact non-zero sanitized failures; no traceback; and no profile/work-root/provider URL/prompt/reference/raw exception leakage.

- [ ] **Step 5: Run recovery tests and verify red**

Run: `uv run pytest tests/test_flow_recovery.py tests/test_job_service.py tests/test_image_cli.py -q`

Expected: FAIL because reason-aware resume and recovery APIs do not exist.

- [ ] **Step 6: Extend the generic reconciled-resume seam narrowly**

Change signatures to:

```python
def resume_reconciled_job(
    self,
    job_id: str,
    *,
    reason: FlowReconciliationReason,
) -> Job: ...
```

Keep the generic layer domain-neutral by typing the accepted values as a local safe literal/validated identifier if importing image types would create a dependency cycle. ImageService supplies the exact allowlist and owns all Flow evidence checks.

- [ ] **Step 7: Implement offline-first recovery**

`recover_generation()` acquires an image-level transaction, validates the blocked Job/run, and checks in order:

```text
both candidates/finals exact → completed_generation_reconciled
downloaded staging/final exact → ingest → staged_artifact_reconciled
pre-intent durable state → no_dispatch_proven
otherwise → browser observation under Flow lock
```

Browser recovery exposes no Generate method. It may promote intent to confirmed from positive generating/result evidence, rebind already persisted exact slots, or continue exact-slot downloads. Empty/unknown evidence remains blocked.

- [ ] **Step 8: Implement audited manual no-dispatch resolution**

In one immediate transaction, require blocked Job plus `run.stage == ambiguous` and no confirmed dispatch, append `job.flow_dispatch_resolved` with prior attempt/timestamps/actor/reason, increment attempt number, reset current intent/confirmation, return stage to `prepared`, and commit. Only after that commit call reason-aware reconciled resume. If resume fails, the next invocation recognizes the already-resolved blocked state and finishes idempotently.

- [ ] **Step 9: Add CLI boundaries and run the recovery regression group**

Run: `uv run pytest tests/test_flow_recovery.py tests/test_job_service.py tests/test_image_cli.py tests/test_image_recovery.py -q`

Expected: PASS.

- [ ] **Step 10: Commit Task 11**

```bash
git add src/auraly_pipeline/jobs/repository.py src/auraly_pipeline/jobs/service.py src/auraly_pipeline/images/service.py src/auraly_pipeline/cli.py tests/test_flow_recovery.py tests/test_job_service.py tests/test_image_cli.py
git commit -m "feat: recover ambiguous Flow generations"
```

---


### Task 12: Complete crash-boundary and sensitive-data hardening

**Files:**
- Create: `tests/test_flow_generation_security.py`
- Modify: `tests/test_flow_generation.py`
- Modify: `tests/test_flow_image_handler.py`
- Modify: `tests/test_flow_recovery.py`
- Modify: `tests/test_flow_security.py`
- Modify: `src/auraly_pipeline/flow/generation.py`
- Modify: `src/auraly_pipeline/images/flow_handler.py`

**Interfaces:**
- Consumes: complete Tasks 1–11 implementation.
- Produces: exhaustive crash matrix, boundary leak scans, structural safety assertions, and only the minimal production fixes those tests expose.

- [ ] **Step 1: Add the exact crash-point matrix**

```python
CRASH_POINTS = (
    "before_inputs_checkpoint",
    "after_inputs_checkpoint",
    "before_dispatch_intent",
    "after_dispatch_intent",
    "during_generate_click",
    "after_dispatch_confirmation_observed",
    "after_dispatch_confirmation_checkpoint",
    "after_grid_observed",
    "after_grid_evidence_published",
    "after_slot_0_intent",
    "after_slot_0_download_saved",
    "after_slot_0_download_checkpoint",
    "after_slot_0_final_publish",
    "after_slot_0_candidate_ingest",
    "after_slot_1_intent",
    "after_slot_1_download_saved",
    "after_slot_1_download_checkpoint",
    "after_slot_1_final_publish",
    "after_slot_1_candidate_ingest",
    "after_run_completed",
    "before_job_completion",
)
```

For every point, run once to crash, recover stale Job, invoke domain recovery when required, and assert: Generate clicks are `0` before intent or exactly `1` after intent; final paths never change; at most two candidate rows exist; matching evidence is preserved; ambiguous evidence blocks.

- [ ] **Step 2: Add a seeded sensitive-data corpus**

```python
DENY_VALUES = (
    "PRIVATE PROMPT phrase",
    "reference-secret.png",
    "person@example.com",
    "AUTHORIZATION_SECRET",
    "COOKIE_SECRET",
    "SIGNED_QUERY_SECRET",
    "FRAGMENT_SECRET",
    r"C:\Users\Private\reference-secret.png",
    "/home/private/reference-secret.png",
)
```

Inspect public CLI stdout/stderr, Job input/output/events, Flow run/slot text columns, result JSON, screenshot bytes after masking, expanded trace entries, and log capture. Exclude the immutable `ImageGeneration.prompt_snapshot` and configured reference field themselves because Goal 4A intentionally stores that generation intent; prove the deny values do not spread into operational/audit fields.

- [ ] **Step 3: Add structural source-boundary tests**

Parse AST/source and assert:

```text
images/service.py and images/repository.py do not import playwright
no generation module reads cookies/storage_state/profile files
no production target URL comes from CLI/environment/Job input
recovery functions contain no Generate click call
new worker does not call record_download_baseline or wait_for_download
no os.replace, overwrite open mode, coordinate click, xpath, nth-child, .nth(
private local target/factory seams are unreachable from CLI/public service arguments
```

- [ ] **Step 4: Run the new hardening tests and verify red**

Run: `uv run pytest tests/test_flow_generation_security.py tests/test_flow_security.py tests/test_flow_recovery.py -q`

Expected: at least one new assertion fails until all boundaries are explicitly satisfied. If all pass immediately, inject a temporary known leak/unsafe call, prove the relevant test fails, revert only that injection, and rerun green.

- [ ] **Step 5: Fix only demonstrated security/recovery gaps**

Use typed allowlisted errors, mask expansion, route revalidation, or a narrower interface as required by failing tests. Do not add speculative provider behavior, a live test, retention cleanup, semantic QC, or broader refactoring.

- [ ] **Step 6: Run all Goal 4C focused tests**

Run:

```bash
uv run pytest tests/test_job_service.py tests/test_image_domain.py tests/test_image_migrations.py tests/test_image_migrations_direct_insert.py tests/test_image_repository.py tests/test_image_service.py tests/test_image_cli.py tests/test_flow_generation_domain.py tests/test_flow_generation_locators.py tests/test_flow_artifacts.py tests/test_flow_generation.py tests/test_flow_image_handler.py tests/test_flow_recovery.py tests/test_flow_generation_security.py -q
```

Expected: PASS.

- [ ] **Step 7: Run Goal 4A/4B regressions and fast verification**

Run:

```bash
uv run pytest tests/test_image_handler.py tests/test_image_recovery.py tests/test_flow_domain.py tests/test_flow_config.py tests/test_flow_lock.py tests/test_flow_locators.py tests/test_flow_diagnostics.py tests/test_flow_runtime.py tests/test_flow_service.py tests/test_flow_cli.py tests/test_flow_security.py -q
uv run python scripts/verify.py fast
```

Expected: all tests, Ruff, and mypy pass.

- [ ] **Step 8: Commit Task 12**

```bash
git add tests/test_flow_generation_security.py tests/test_flow_generation.py tests/test_flow_image_handler.py tests/test_flow_recovery.py tests/test_flow_security.py src/auraly_pipeline/flow/generation.py src/auraly_pipeline/images/flow_handler.py
git commit -m "test: harden Flow generation recovery"
```

---

### Task 13: Extend deterministic Linux and Windows verification

**Files:**
- Modify: `tests/test_verify_harness.py`
- Modify: `.github/workflows/verify.yml`

**Interfaces:**
- Consumes: complete focused Goal 4C test set.
- Produces: Linux full and Windows focused deterministic coverage without live provider access.

- [ ] **Step 1: Write failing harness/workflow contract tests**

```python
def test_windows_flow_generation_suite_covers_persistence_browser_download_and_recovery() -> None:
    workflow = Path(".github/workflows/verify.yml").read_text(encoding="utf-8")
    for test_file in (
        "tests/test_image_migrations.py",
        "tests/test_flow_generation_locators.py",
        "tests/test_flow_artifacts.py",
        "tests/test_flow_generation.py",
        "tests/test_flow_image_handler.py",
        "tests/test_flow_recovery.py",
        "tests/test_flow_generation_security.py",
    ):
        assert test_file in workflow
    assert "labs.google" not in workflow
```

Assert Linux full still invokes `scripts/verify.py full`; Windows explicitly installs managed Chromium, runs the focused headed/local suite, and does not use Google credentials, secrets, a live URL, or a provider command.

- [ ] **Step 2: Run harness tests and verify red**

Run: `uv run pytest tests/test_verify_harness.py -q`

Expected: FAIL because the workflow does not list Goal 4C tests.

- [ ] **Step 3: Update the Windows-focused job minimally**

Keep the current Windows runner and Goal 4B browser setup. Add the smallest Goal 4C files needed to cover native path/hard-link behavior, SQLite migration/checkpoints, local headed Chromium, exact synthetic downloads, crash recovery, CLI, and sanitization. Keep provider navigation impossible.

- [ ] **Step 4: Run harness tests and inspect workflow syntax**

Run:

```bash
uv run pytest tests/test_verify_harness.py -q
uv run python -c "import yaml, pathlib; yaml.safe_load(pathlib.Path('.github/workflows/verify.yml').read_text(encoding='utf-8'))"
```

Expected: PASS and valid YAML.

- [ ] **Step 5: Run fresh deterministic local gates**

Run:

```bash
uv run playwright install --dry-run chromium
uv run python scripts/verify.py fast
uv run python scripts/verify.py full
```

Expected: Chromium dry-run exits zero; fast and full report every step passed. No Google/provider request appears in captured output.

- [ ] **Step 6: Commit Task 13**

```bash
git add tests/test_verify_harness.py .github/workflows/verify.yml
git commit -m "ci: verify Flow generation recovery"
```

---

### Task 14: Independent review, accepted-finding fixes, closure docs, and exact-SHA CI

**Files:**
- Modify only when a demonstrated review finding requires it: Goal 4C source/tests from Tasks 1–13.
- Modify for closure: `README.md`
- Modify for closure: `docs/GOAL-ROADMAP.md`
- Modify for closure: `docs/PROJECT-MEMORY.md`

**Interfaces:**
- Consumes: approved spec, this plan, complete Goal 4C implementation range, fresh deterministic evidence.
- Produces: independent review verdict, regression-backed accepted fixes, truthful documentation-only closure commit, and exact-final-SHA CI evidence.

- [ ] **Step 1: Record the implementation review range and run fresh full verification**

Run:

```bash
git rev-parse 1772957
git rev-parse HEAD
uv run playwright install --dry-run chromium
uv run python scripts/verify.py full
git status --short
```

Expected: spec baseline resolves, full verification passes, and the worktree contains no uncommitted implementation changes.

- [ ] **Step 2: Perform implementation self-review**

Compare every acceptance criterion in the spec to a source path and focused test. Inspect the full diff:

```bash
git diff --stat 1772957..HEAD
git diff --check 1772957..HEAD
git diff 1772957..HEAD -- src tests .github/workflows/verify.yml
```

Record any gap as a concrete finding with severity, file/line, violated criterion, and reproduction. Fix no unrelated issue.

- [ ] **Step 3: Request a fresh independent full-range review**

Use `superpowers:requesting-code-review` with a reviewer that did not implement the tasks. Supply:

```text
Design: docs/superpowers/specs/2026-08-27-goal-4c-flow-generation-download-recovery-design.md
Plan: docs/superpowers/plans/2026-08-27-goal-4c-flow-generation-download-recovery-plan.md
Base: 1772957
Head: exact output of `git rev-parse HEAD` from Step 1
Review priorities:
1. blind redispatch or unsafe retry
2. checkpoint/Job transaction gaps
3. candidate identity/download mis-correlation
4. overwrite/path escape
5. prompt/reference/account/token leakage
6. browser/lock cleanup
7. Goal 4A/4B regressions
8. scope creep into Goal 4D/provider verification
```

Require a verdict plus Critical/High/Medium/Low findings. Independent read-only review is mandatory even if self-review found nothing.

- [ ] **Step 4: Resolve every accepted Critical/High finding with red-green evidence**

For each accepted finding:

```text
write one focused regression reproducing the finding
run it and observe FAIL
implement the smallest fix
rerun and observe PASS
run the affected focused group
commit fix separately
```

Suggested commit format:

```bash
git status --short
git add src/auraly_pipeline tests
git diff --cached --check
git commit -m "fix: enforce reviewed Goal 4C invariant"
```

Do not accept a finding solely because it was reported; verify it against the spec and current code. Document rejected findings with concrete technical reasoning.

- [ ] **Step 5: Run fresh post-review verification**

Run:

```bash
uv run playwright install --dry-run chromium
uv run python scripts/verify.py fast
uv run python scripts/verify.py full
git diff --check 1772957..HEAD
git status --short
```

Expected: all deterministic gates pass and the worktree is clean.

- [ ] **Step 6: Update closure documentation truthfully**

Update only the three closure documents. Record:

```text
Goal 4C — Flow Generation, Download & Recovery

IMPLEMENTED       YES
LOCAL_VERIFIED    YES
PROVIDER_VERIFIED NOT ESTABLISHED

BROWSER_PREFLIGHT_VERIFIED NOT RUN / NOT ESTABLISHED
```

Document the two-candidate 2K contract, durable authorization/checkpoints, no-blind-redispatch recovery, exact Playwright download correlation, Linux/Windows deterministic evidence, review verdict, and final commit chain. State explicitly that no live Flow preflight/generation occurred and Goal 4D remains pending.

- [ ] **Step 7: Verify and commit documentation only**

Run:

```bash
git diff --check
git diff --name-only
```

Expected names exactly:

```text
README.md
docs/GOAL-ROADMAP.md
docs/PROJECT-MEMORY.md
```

Then:

```bash
git add README.md docs/GOAL-ROADMAP.md docs/PROJECT-MEMORY.md
git commit -m "docs: close Goal 4C implementation"
```

- [ ] **Step 8: Run the complete local gate on the documentation-only HEAD**

Run:

```bash
uv run playwright install --dry-run chromium
uv run python scripts/verify.py full
git status --short
git rev-parse HEAD
```

Expected: full verification passes, worktree is clean, and the exact closure SHA is recorded.

- [ ] **Step 9: Require Linux and Windows GitHub Actions on the exact final SHA**

Push through the approved repository workflow, then inspect Actions for the recorded SHA. Both the Linux full job and Windows focused job must succeed. A passing earlier SHA does not satisfy closure.

Do not run a live Google Flow command while waiting for CI. If CI fails, reproduce deterministically where possible, fix with a regression, rerun full local verification, update closure docs if the SHA chain changed, and require both jobs again on the new exact SHA.

- [ ] **Step 10: Report the final closure classification**

Report exact final SHA, commit list from `1772957..HEAD`, local full result, independent review verdict/counts, Linux result, Windows result, and:

```text
IMPLEMENTED       YES
LOCAL_VERIFIED    YES
PROVIDER_VERIFIED NOT ESTABLISHED
```

Do not begin Goal 4D and do not claim a browser preflight or provider canary.

---

## Execution Handoff

Plan execution must start from a clean checkout containing design commit `1772957`. At execution time, use `superpowers:using-git-worktrees` if isolation is needed, then choose exactly one execution workflow:

1. **Subagent-Driven (recommended):** use `superpowers:subagent-driven-development`, dispatch one fresh implementer per task, and run specification-compliance plus code-quality review between tasks.
2. **Inline Execution:** use `superpowers:executing-plans`, execute in small batches with explicit checkpoints.

Neither option is authorized by this planning task. Do not implement Goal 4C until the user explicitly starts execution.
