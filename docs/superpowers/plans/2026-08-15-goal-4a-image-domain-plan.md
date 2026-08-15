# Goal 4A — Image Domain & Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the durable ImageGeneration/ImageCandidate domain, SQLite persistence, linked Job orchestration, deterministic fake image generation, review lifecycle, CLI contracts, and recovery semantics without any external provider or browser execution.

**Architecture:** Preserve the modular monolith and existing Job orchestration. Add a focused `images/` module, one narrow public linked-submission seam in `JobService`, durable ImageGeneration/ImageCandidate persistence, and a deterministic local fake handler exercised through the normal worker path.

**Tech Stack:** Python 3.11, Pydantic, SQLAlchemy 2, SQLite WAL, Alembic, Typer, pytest, Ruff, mypy.

## Global Constraints

- Goal 4A has no network, provider, browser, or Playwright calls; `provider` remains `google_flow` and the only Goal 4A executor is `local_fake`.
- Do not broadly refactor Voice, add a provider plugin system or generic Unit of Work, or rewrite `image_generation.py` wholesale.
- Media is filesystem-based, never BLOB. Persist only workspace-relative trusted paths; validate Windows and POSIX semantics, canonical containment, and symlink/junction safety.
- Do not silently overwrite or delete artifacts, ImageGenerations, rejected candidates, or superseded candidates.
- Every regenerate has a new explicit idempotency key and creates a new ImageGeneration and linked Job.
- At most one ImageCandidate is approved per SceneVariant. Replacement is explicit, atomic, and preserves the former candidate as superseded.
- The fake handler creates exactly two deterministic, valid PNG candidates and uses normal Goal 2 claim/attempt/heartbeat/fencing/retry behavior.
- Use `uv run python scripts/verify.py fast --pytest tests/test_image_domain.py` during the first task and substitute the exact focused test path named by each later task; use the full harness before Goal closure. Provider verification is not applicable.

## Repository Map

| Responsibility | Existing / planned path |
| --- | --- |
| Campaign/SceneVariant rows and migration metadata | `src/auraly_pipeline/campaigns/db_models.py` |
| SQLite configuration and migration entry point | `src/auraly_pipeline/campaigns/persistence.py` |
| Alembic environment and head revisions | `src/auraly_pipeline/campaigns/migrations/env.py`, `versions/0001_campaign_foundation.py`, `0002_persistent_job_orchestration.py`, `0003_voice_master.py` |
| Job contracts and lifecycle | `src/auraly_pipeline/jobs/domain.py`, `state_machine.py` |
| Job transaction helper | `src/auraly_pipeline/jobs/repository.py:JobRepository.create_in_session` |
| Public Job API and handler wiring | `src/auraly_pipeline/jobs/service.py` |
| Built-in fake handler registry | `src/auraly_pipeline/jobs/handlers.py` |
| Existing private Voice linked mutation to avoid copying | `src/auraly_pipeline/voices/service.py` |
| Canonical artifact root | `src/auraly_pipeline/config_paths.py` |
| Existing generic image safety helpers | `src/auraly_pipeline/image_generation.py` |
| Typer CLI | `src/auraly_pipeline/cli.py` |
| New focused package | `src/auraly_pipeline/images/{__init__,domain,db_models,repository,service,handler}.py` |
| New tests | `tests/test_image_domain.py`, `test_image_repository.py`, `test_image_service.py`, `test_image_handler.py`, `test_image_recovery.py`, `test_image_review.py`, `test_image_cli.py`, `test_image_migrations.py` |

---

### Task 1: Image contracts and deterministic intent fingerprint

**Files:**
- Create: `src/auraly_pipeline/images/__init__.py`
- Create: `src/auraly_pipeline/images/domain.py`
- Create: `tests/test_image_domain.py`

**Interfaces:**
- Consumes: `pydantic.JsonValue`, current Campaign/SceneVariant string identifiers, existing SHA-256 validation conventions.
- Produces: `ImageProvider`, `ImageExecutor`, `ImageGenerationState`, `ImageCandidateReviewStatus`, `ImageGeneration`, `ImageCandidate`, `ImageGenerateRequest`, `ImageGenerationSubmission`, and `generation_request_fingerprint()`.

- [ ] **Step 1: Write failing domain tests**

`test_generation_fingerprint_is_canonical_and_excludes_submission_identity` builds two complete
otherwise-identical `ImageGenerateRequest` values with keys `image-a` and `image-b`, then asserts
equal fingerprints. `test_image_contract_rejects_invalid_state_hash_and_artifact_facts` supplies a
complete candidate fixture with negative index, zero width, malformed SHA, and unknown state in
separate parametrized cases, and asserts Pydantic validation fails in every case.

- [ ] **Step 2: Run the red tests**

Run: `uv run pytest tests/test_image_domain.py -q`
Expected: import/contract failure because `auraly_pipeline.images.domain` does not exist.

- [ ] **Step 3: Implement minimal contracts**

Define literals exactly as follows:

```python
ImageProvider = Literal["google_flow"]
ImageExecutor = Literal["local_fake", "playwright_python"]
ImageGenerationState = Literal["created", "queued", "generating", "completed", "failed", "blocked"]
ImageCandidateReviewStatus = Literal["pending_review", "approved", "rejected", "superseded"]
```

`ImageGenerateRequest` validates an explicit safe idempotency key, prompt snapshot, nullable
reference path/hash pair, `google_flow`, and `local_fake`. Implement the fingerprint as SHA-256 of
UTF-8 canonical JSON with `sort_keys=True`, `separators=(",", ":")`, and keys
`sceneVariantId`, `promptSha256`, `referenceImageSha256`, `provider`, `executor`,
`generationContractVersion`, and `fakeArtifactFormatVersion`. Do not include key, timestamps,
Job ID, or generation number.

- [ ] **Step 4: Run focused verification**

Run: `uv run pytest tests/test_image_domain.py -q`
Run: `uv run python scripts/verify.py fast --pytest tests/test_image_domain.py`

- [ ] **Step 5: Commit task**

```bash
git add src/auraly_pipeline/images/__init__.py src/auraly_pipeline/images/domain.py tests/test_image_domain.py
git commit -m "feat: add image domain contracts"
```

### Task 2: Image rows and dedicated migration

**Files:**
- Create: `src/auraly_pipeline/images/db_models.py`
- Create: `src/auraly_pipeline/campaigns/migrations/versions/0004_image_domain.py`
- Modify: `src/auraly_pipeline/campaigns/migrations/env.py`
- Create: `tests/test_image_migrations.py`

**Interfaces:**
- Consumes: Task 1 literals; `CampaignRow`, `SceneVariantRow`, `JobRow`, and shared `Base`.
- Produces: `ImageGenerationRow`, `ImageCandidateRow`, migration head `0004_image_domain`.

- [ ] **Step 1: Write migration and constraint tests**

```python
Test `test_image_migration_upgrades_0003_database_and_creates_image_tables` upgrades an explicit
0003 database and asserts both tables and 0004 head. Test
`test_image_rows_enforce_generation_and_candidate_number_constraints` inserts invalid 0/-1 values
and asserts SQLite rejects them. Test
`test_migration_trigger_rejects_second_approved_candidate_for_scene_variant` inserts two candidate
rows through different generations for one SceneVariant and asserts the second approval aborts.
```

- [ ] **Step 2: Run the red tests**

Run: `uv run pytest tests/test_image_migrations.py -q`
Expected: migration head/table imports fail before the new models and revision exist.

- [ ] **Step 3: Implement models and migration**

Create `image_generations` with non-null campaign/scene/job links, immutable intent columns,
provider/executor/state checks, timestamps, `CHECK(generation_number > 0)`, and
`UNIQUE(scene_variant_id, generation_number)`. Create `image_candidates` with candidate artifact
facts/review audit fields, `CHECK(candidate_index >= 0)`, positive metrics, and
`UNIQUE(image_generation_id, candidate_index)`.

Import `auraly_pipeline.images.db_models` in Alembic metadata setup. Migration adds campaign/scene
consistency triggers for generation insert/update and candidate approval triggers that join through
`image_generations` and abort when another approved candidate exists for the same SceneVariant.
Downgrade drops approval triggers, consistency triggers, indexes, then candidates before generations.

- [ ] **Step 4: Run focused verification**

Run: `uv run pytest tests/test_image_migrations.py tests/test_migrations.py -q`
Run: `uv run python scripts/verify.py fast --pytest tests/test_image_migrations.py tests/test_migrations.py`

- [ ] **Step 5: Commit task**

```bash
git add src/auraly_pipeline/images/db_models.py src/auraly_pipeline/campaigns/migrations/env.py src/auraly_pipeline/campaigns/migrations/versions/0004_image_domain.py tests/test_image_migrations.py
git commit -m "feat: persist image generations and candidates"
```

### Task 3: Image repository and database invariant operations

**Files:**
- Create: `src/auraly_pipeline/images/repository.py`
- Create: `tests/test_image_repository.py`

**Interfaces:**
- Consumes: Task 2 rows and `Session`; Task 1 domain contracts.
- Produces: `ImageRepository`, `allocate_generation_number()`, `get_generation()`,
  `list_generations()`, `get_candidate()`, `list_candidates()`, and session-scoped row creation.

- [ ] **Step 1: Write failing repository tests**

```python
`test_repository_lists_persisted_generations_and_candidates_in_numeric_order` persists out-of-order
numbers and asserts numeric ordered reads. `test_two_immediate_transactions_allocate_distinct_generation_numbers`
uses two independent sessions for one SceneVariant and asserts numbers 1 and 2. `test_repository_preserves_zero_candidate_generation_and_history_after_restart`
closes/reopens the service and asserts the zero-candidate generation and prior candidates remain.
```

- [ ] **Step 2: Run the red tests**

Run: `uv run pytest tests/test_image_repository.py -q`
Expected: `ImageRepository` import failure.

- [ ] **Step 3: Implement minimal repository operations**

Use the existing sessionmaker pattern. `allocate_generation_number(session, scene_variant_id)` runs
after the caller starts `BEGIN IMMEDIATE`, reads `max(generation_number)`, and returns 1 or max+1.
Do not make allocation a separate committed transaction. Repository queries return deterministic
numeric ordering and never delete historical rows.

- [ ] **Step 4: Run focused verification**

Run: `uv run pytest tests/test_image_repository.py tests/test_image_migrations.py -q`
Run: `uv run python scripts/verify.py fast --pytest tests/test_image_repository.py`

- [ ] **Step 5: Commit task**

```bash
git add src/auraly_pipeline/images/repository.py tests/test_image_repository.py
git commit -m "feat: add image persistence repository"
```

### Task 4: Public atomic linked Job submission

**Files:**
- Modify: `src/auraly_pipeline/jobs/service.py`
- Modify: `src/auraly_pipeline/jobs/repository.py`
- Modify: `src/auraly_pipeline/jobs/__init__.py`
- Modify: `tests/test_job_service.py`
- Modify: `tests/test_job_concurrency.py`

**Interfaces:**
- Consumes: `JobSubmit`, `Job`, `JobRow`, `Session`, `JobRepository.create_in_session`.
- Produces: `LinkedJobSubmission[T]` and `JobService.submit_linked_job(request, create_linked: Callable[[Session, JobRow], T], load_existing: Callable[[Job], T]) -> LinkedJobSubmission[T]`.

- [ ] **Step 1: Write failing JobService tests**

```python
`test_submit_linked_job_commits_job_events_and_linked_row_once` asserts one queued Job, its two
standard events, and one callback-created row. `test_submit_linked_job_rolls_back_job_events_when_callback_raises`
asserts no Job, events, or linked row survive. `test_submit_linked_job_reuses_matching_job_without_calling_callback`
asserts the callback counter remains one across two matching submissions.
```

- [ ] **Step 2: Run the red tests**

Run: `uv run pytest tests/test_job_service.py -q`
Expected: `JobService.submit_linked_job` attribute failure.

- [ ] **Step 3: Implement the narrow public seam**

Add the generic `LinkedJobSubmission[T]` result carrying `job: Job`, `linked: T`, and
`reused: bool`. `submit_linked_job` validates handler/retry/reference semantics, begins the
repository’s immediate transaction, checks idempotency before the callback, creates standard Job
and events through `create_in_session`, invokes the callback in that same Session, validates the
persisted Job, commits once, and reloads. On callback/integrity error it rolls back. A matching
idempotent reuse returns existing Job plus a caller-supplied `load_existing` callback result; a
changed Job request raises the existing Job idempotency conflict. Do not expose repository fields or
create a generic transaction framework.

- [ ] **Step 4: Run focused verification**

Run: `uv run pytest tests/test_job_service.py tests/test_job_concurrency.py -q`
Run: `uv run python scripts/verify.py fast --pytest tests/test_job_service.py tests/test_job_concurrency.py`

- [ ] **Step 5: Commit task**

```bash
git add src/auraly_pipeline/jobs/service.py src/auraly_pipeline/jobs/repository.py src/auraly_pipeline/jobs/__init__.py tests/test_job_service.py tests/test_job_concurrency.py
git commit -m "feat: add linked job submission"
```

### Task 5: Image application service and idempotent generation

**Files:**
- Create: `src/auraly_pipeline/images/service.py`
- Create: `tests/test_image_service.py`

**Interfaces:**
- Consumes: Tasks 1–4; `ImageRepository`; `JobService.submit_linked_job`.
- Produces: `ImageService.for_database()`, `generate()`, `get_generation()`, `list_generations()`, `get_candidate()`, `list_candidates()`, `regenerate()`; image-specific public errors.

- [ ] **Step 1: Write failing service tests**

```python
`test_generate_creates_one_linked_generation_and_queued_job` asserts shared campaign/scene/Job
identity and generation number 1. `test_generate_same_key_and_fingerprint_returns_original_generation_and_job`
asserts both IDs are reused. `test_generate_same_key_and_changed_fingerprint_raises_image_idempotency_conflict`
changes the prompt and asserts the public error. `test_regenerate_same_prompt_with_new_key_creates_next_generation_number`
asserts the same prompt/reference with a fresh key creates number 2 and a new Job.
```

- [ ] **Step 2: Run the red tests**

Run: `uv run pytest tests/test_image_service.py -q`
Expected: `ImageService` import failure.

- [ ] **Step 3: Implement service operations**

Implement `ImageService` with its own session factory and the existing database/work-root setup
pattern. `generate` calls only `submit_linked_job`; its linked callback allocates a number and
inserts ImageGeneration with `provider_state="queued"`, job ID, immutable intent, and timestamps.
It returns `ImageGenerationSubmission(generation, job, reused)`. Its reuse loader reads the
generation by Job ID and compares the ImageGeneration fingerprint, mapping mismatches to
`ImageIdempotencyConflictError`. `regenerate` requires a new key and calls the same creation path.
Add sanitized `ImageGenerationNotFoundError`, `ImageCandidateNotFoundError`,
`ImageIdempotencyConflictError`, `ImageCandidateSceneMismatchError`,
`ImageApprovedCandidateExistsError`, `ImageArtifactMissingError`, `ImageArtifactConflictError`, and
`ImageTransitionError` with stable public messages.

- [ ] **Step 4: Run focused verification**

Run: `uv run pytest tests/test_image_service.py tests/test_job_service.py -q`
Run: `uv run python scripts/verify.py fast --pytest tests/test_image_service.py`

- [ ] **Step 5: Commit task**

```bash
git add src/auraly_pipeline/images/service.py tests/test_image_service.py
git commit -m "feat: add image generation service"
```

### Task 6: Deterministic PNG artifact helper and registered fake handler

**Files:**
- Create: `src/auraly_pipeline/images/handler.py`
- Modify: `src/auraly_pipeline/jobs/service.py`
- Create: `tests/test_image_handler.py`

**Interfaces:**
- Consumes: ImageGeneration/Repository/Service data, `JobExecutionContext`, `JobExecutionResult`, `configured_work_root`.
- Produces: `ImageGenerateHandler`, `deterministic_png_bytes(generation_id: str, candidate_index: int) -> bytes`, and registered `image.generate` handler.

- [ ] **Step 1: Write failing handler tests**

```python
`test_fake_handler_creates_exactly_two_valid_distinct_png_candidates` decodes the two PNG headers
and asserts indexes 0 and 1 differ. `test_fake_handler_persists_actual_sha_dimensions_format_and_size`
hashes and inspects each actual file against the stored facts. `test_fake_handler_uses_generation_scoped_non_overwriting_paths`
asserts two generation directories and rejects a conflicting pre-existing candidate file.
```

- [ ] **Step 2: Run the red tests**

Run: `uv run pytest tests/test_image_handler.py -q`
Expected: handler import failure.

- [ ] **Step 3: Implement deterministic artifacts and handler**

First inspect `pyproject.toml` direct dependencies. If none is an explicitly appropriate direct PNG
encoder, implement a minimal stdlib encoder using PNG signature, IHDR, IDAT with `zlib.compress`,
and IEND. Use a fixed small RGB width/height and derive pixels from SHA-256 of
`generation_id`, candidate index, and `fake-png-v1`; candidate indexes 0/1 must yield distinct,
byte-identical retry outputs. Decode enough PNG structure in tests or with a tiny stdlib parser;
do not add Pillow solely for this fake.

Use relative path `campaigns/<campaign_id>/images/<scene_variant_id>/generation-<number:04d>/candidate-<index:04d>.png`.
Resolve beneath `configured_work_root()`, require containment after resolution, reject pre-existing
different contents, and persist relative path only. Register `ImageGenerateHandler` in
`JobService.for_database` alongside Voice’s handler without disturbing existing fake handlers.

- [ ] **Step 4: Run focused verification**

Run: `uv run pytest tests/test_image_handler.py tests/test_job_handlers.py -q`
Run: `uv run python scripts/verify.py fast --pytest tests/test_image_handler.py`

- [ ] **Step 5: Commit task**

```bash
git add src/auraly_pipeline/images/handler.py src/auraly_pipeline/jobs/service.py tests/test_image_handler.py
git commit -m "feat: add deterministic image fake handler"
```

### Task 7: Fake-handler restart and artifact recovery

**Files:**
- Modify: `src/auraly_pipeline/images/handler.py`
- Modify: `src/auraly_pipeline/images/repository.py`
- Create: `tests/test_image_recovery.py`

**Interfaces:**
- Consumes: Task 6 deterministic bytes/path functions and existing `JobExecutionOutcome.BLOCKED`.
- Produces: idempotent candidate reconciliation and safe blocked outcomes.

- [ ] **Step 1: Write failing recovery tests**

```python
`test_retry_reuses_valid_candidate_zero_and_creates_only_candidate_one` records candidate 0 bytes,
reruns, and asserts unchanged candidate 0 plus created candidate 1. `test_matching_orphan_file_is_reconciled_without_overwrite`
precreates exact deterministic bytes and asserts a row is added. `test_candidate_row_without_file_blocks_job_and_generation`
removes a recorded file and asserts BLOCKED. `test_unexpected_existing_file_blocks_without_overwrite`
precreates different bytes and asserts BLOCKED with untouched file contents.
```

- [ ] **Step 2: Run the red tests**

Run: `uv run pytest tests/test_image_recovery.py -q`
Expected: recovery cases fail because handler always assumes clean output.

- [ ] **Step 3: Implement explicit recovery branches**

For each candidate index, read expected deterministic bytes/hash/metadata before mutation. A valid
row+file is reused. Matching file/no row inserts a reconciled row in a transaction. Row/no file and
unexpected file content set generation `blocked`, retain evidence, and return
`JobExecutionResult(outcome=JobExecutionOutcome.BLOCKED, error_code="image_artifact_missing" or
"image_artifact_conflict", sanitized message)`. Never delete or replace the file. Update
provider state inside the same service/repository transaction; do not add a Job state.

- [ ] **Step 4: Run focused verification**

Run: `uv run pytest tests/test_image_recovery.py tests/test_image_handler.py tests/test_job_concurrency.py -q`
Run: `uv run python scripts/verify.py fast --pytest tests/test_image_recovery.py`

- [ ] **Step 5: Commit task**

```bash
git add src/auraly_pipeline/images/handler.py src/auraly_pipeline/images/repository.py tests/test_image_recovery.py
git commit -m "fix: recover partial image generation safely"
```

### Task 8: Candidate review and explicit atomic replacement

**Files:**
- Modify: `src/auraly_pipeline/images/service.py`
- Modify: `src/auraly_pipeline/images/repository.py`
- Create: `tests/test_image_review.py`

**Interfaces:**
- Consumes: candidate ownership chain and SQLite approval trigger from Tasks 2–3.
- Produces: `approve_candidate(candidate_id, approved_by)`, `reject_candidate(candidate_id, rejected_by, rejection_reason)`, and `replace_approved_candidate(scene_variant_id, new_candidate_id, approved_by)`.

- [ ] **Step 1: Write failing review tests**

```python
`test_first_pending_candidate_approval_succeeds_and_second_direct_approval_conflicts` asserts
the first is approved and the second raises the public conflict. `test_replace_approved_candidate_atomically_supersedes_old_and_approves_new`
asserts both row states and the old row's successor link. `test_replace_rejected_candidate_retains_immutable_rejection_audit`
asserts rejection actor/time/reason survive replacement. `test_review_rejects_cross_scene_candidate_and_rolls_back_partial_replacement`
asserts no partial state survives either error.
```

- [ ] **Step 2: Run the red tests**

Run: `uv run pytest tests/test_image_review.py -q`
Expected: service methods are absent.

- [ ] **Step 3: Implement transactional lifecycle**

Use a repository `BEGIN IMMEDIATE` transaction. Ordinary `approve_candidate` permits only
`pending_review -> approved`; it rejects any existing approved candidate in the same SceneVariant.
`reject_candidate` permits eligible non-approved rows and requires a sanitized reason; it refuses an
approved row. `replace_approved_candidate` accepts a target in `pending_review` or `rejected`,
preserves all rejection audit fields for a rejected target, supersedes the current approved row, and
approves the target in one commit. It validates Candidate → Generation → SceneVariant before every
mutation and maps trigger/constraint errors to the declared public error types.

- [ ] **Step 4: Run focused verification**

Run: `uv run pytest tests/test_image_review.py tests/test_image_migrations.py -q`
Run: `uv run python scripts/verify.py fast --pytest tests/test_image_review.py`

- [ ] **Step 5: Commit task**

```bash
git add src/auraly_pipeline/images/service.py src/auraly_pipeline/images/repository.py tests/test_image_review.py
git commit -m "feat: add image candidate review lifecycle"
```

### Task 9: Concurrency, restart, and security regression

**Files:**
- Modify: `tests/test_image_repository.py`
- Modify: `tests/test_image_service.py`
- Modify: `tests/test_image_recovery.py`
- Create: `tests/test_image_concurrency.py`

**Interfaces:**
- Consumes: all prior image APIs, trusted artifact paths, and Job worker semantics.
- Produces: regression coverage proving the database is the final race guard.

- [ ] **Step 1: Write failing cross-process and path tests**

```python
`test_concurrent_new_keys_allocate_unique_generation_numbers_for_one_scene_variant` asserts two
concurrent keys create numbers 1 and 2. `test_same_idempotency_key_race_returns_one_generation_and_one_job`
asserts both callers observe the same IDs. `test_concurrent_candidate_approval_leaves_exactly_one_approved_candidate`
asserts exactly one committed approval. `test_image_artifact_path_rejects_traversal_and_escape`
asserts traversal and an escape fixture never create an artifact outside the root.
```

- [ ] **Step 2: Run the red tests**

Run: `uv run pytest tests/test_image_concurrency.py -q`
Expected: race/path behavior is not yet fully exercised or exposes a missing transactional guard.

- [ ] **Step 3: Implement only gaps exposed by tests**

Use existing thread/process test patterns from `tests/test_job_concurrency.py`. Keep allocation and
linked creation under immediate transaction, let unique constraints/triggers resolve final races,
and map their conflicts to public errors. Reuse existing dual `PurePosixPath`/`PureWindowsPath`
validation and resolved-work-root containment patterns; add no parallel path framework. Test
symlink/junction escape only on platforms where the test fixture can create it reliably.

- [ ] **Step 4: Run focused verification**

Run: `uv run pytest tests/test_image_concurrency.py tests/test_image_repository.py tests/test_image_service.py tests/test_image_recovery.py -q`
Run: `uv run python scripts/verify.py fast --pytest tests/test_image_concurrency.py`

- [ ] **Step 5: Commit task**

```bash
git add tests/test_image_repository.py tests/test_image_service.py tests/test_image_recovery.py tests/test_image_concurrency.py
git commit -m "test: harden Goal 4A persistence and concurrency"
```

### Task 10: Image CLI surface and sanitized JSON errors

**Files:**
- Modify: `src/auraly_pipeline/cli.py`
- Create: `tests/test_image_cli.py`

**Interfaces:**
- Consumes: `ImageService` public methods/errors.
- Produces: Typer `image_app` with `generate`, `generation get/list`, `candidate get/list/approve/reject/replace-approved`, and `regenerate` commands.

- [ ] **Step 1: Write failing CLI tests**

```python
`test_image_generate_and_generation_get_emit_structured_json` invokes Typer with a temporary
database/root and asserts returned generation/job IDs. `test_image_candidate_review_commands_emit_sanitized_domain_error_json`
asserts public error codes without traceback. `test_image_cli_does_not_call_provider_or_expose_absolute_artifact_path`
asserts local fake completion and relative path output.
```

- [ ] **Step 2: Run the red tests**

Run: `uv run pytest tests/test_image_cli.py -q`
Expected: `auraly image` command group is absent.

- [ ] **Step 3: Implement narrow Typer commands**

Add `image_app = typer.Typer(help="Persist and review deterministic local image generations.", no_args_is_help=True)` registered as `app.add_typer(image_app, name="image")`. Follow
current `_json_echo` and domain-error mapping style. Commands take explicit database/work-root and
the request fields required by `ImageGenerateRequest`; they invoke ImageService only, serialize
workspace-relative paths, and never print traceback/private absolute paths.

- [ ] **Step 4: Run focused verification**

Run: `uv run pytest tests/test_image_cli.py tests/test_cli.py -q`
Run: `uv run python scripts/verify.py fast --pytest tests/test_image_cli.py`

- [ ] **Step 5: Commit task**

```bash
git add src/auraly_pipeline/cli.py tests/test_image_cli.py
git commit -m "feat: expose image CLI"
```

### Task 11: Integration verification and Windows-focused CI coverage

**Files:**
- Modify: `.github/workflows/verify.yml`
- Modify: `tests/test_verify_harness.py`
- Modify: `README.md`
- Modify: `docs/GOAL-ROADMAP.md`
- Modify: `docs/PROJECT-MEMORY.md` only if implemented behavior sharpens this approved design.

**Interfaces:**
- Consumes: full Goal 4A test suite and existing fast/full harness workflow.
- Produces: Windows focused selection that includes image path/persistence/concurrency coverage and truthful documentation.

- [ ] **Step 1: Write failing CI-contract tests**

```python
`test_windows_ci_includes_goal_4a_cross_platform_image_targets` parses the workflow and asserts its
focused command includes `tests/test_image_migrations.py`, `tests/test_image_concurrency.py`, and
`tests/test_image_recovery.py`.
```

- [ ] **Step 2: Run the red test**

Run: `uv run pytest tests/test_verify_harness.py::test_windows_ci_includes_goal_4a_cross_platform_image_targets -q`
Expected: workflow selection lacks the new image tests.

- [ ] **Step 3: Update focused coverage and docs**

Add the smallest Windows-focused targets covering image migrations, paths, and concurrency without
making Windows execute the Node/full baseline. Update README/roadmap to say Goal 4A is implemented
only after all checks pass; update PROJECT-MEMORY only for durable final details. Do not claim
provider verification.

- [ ] **Step 4: Run integration checks**

Run: `uv run pytest tests/test_image_domain.py tests/test_image_migrations.py tests/test_image_repository.py tests/test_image_service.py tests/test_image_handler.py tests/test_image_recovery.py tests/test_image_review.py tests/test_image_concurrency.py tests/test_image_cli.py tests/test_verify_harness.py -q`
Run: `uv run python scripts/verify.py fast --pytest tests/test_image_concurrency.py tests/test_image_cli.py tests/test_verify_harness.py`

- [ ] **Step 5: Commit task**

```bash
git add .github/workflows/verify.yml tests/test_verify_harness.py README.md docs/GOAL-ROADMAP.md docs/PROJECT-MEMORY.md
git commit -m "docs: close Goal 4A implementation"
```

### Task 12: Final closure verification and review

**Files:**
- Modify only files required by review fixes from Tasks 1–11.

**Interfaces:**
- Consumes: completed Goal 4A implementation and CI workflow.
- Produces: evidence-backed `IMPLEMENTED` / `LOCAL_VERIFIED` status; GitHub CI evidence remains separate.

- [ ] **Step 1: Run the full deterministic gate**

Run: `uv run python scripts/verify.py full`
Expected: all 13 steps pass, schemas do not drift, and no provider call occurs.

- [ ] **Step 2: Inspect final scope and request independent review**

```bash
git diff --check
git status --short
git diff --stat f6f7686685fea58442aa47a1f173943732744423..HEAD
```

Request an independent review of constraints, transactions, path safety, recovery, and absence of
private Job repository access.

- [ ] **Step 3: Confirm GitHub Actions evidence**

Confirm `Linux full verification` and `Windows focused verification` succeeded for the final
commit. Do not rerun providers and do not label this provider verification.

- [ ] **Step 4: Commit only review fixes, if any**

Use a focused follow-up task for every accepted review finding: first add a failing regression test,
then make the minimal fix, rerun its focused test, and commit that task with a message describing the
specific corrected behavior. Re-run this final closure task after the resulting commit.

## Plan Self-Review

| Approved requirement | Implementing task(s) |
| --- | --- |
| Domain fields, states, immutable intent, fingerprint | 1, 5 |
| Fresh/upgrade migration, constraints, trigger, downgrade | 2 |
| Persistence/history and number allocation | 3, 9 |
| Public atomic Job seam and no private repository access | 4, 5, 12 |
| Two deterministic PNG candidates and safe paths | 6, 9 |
| Retry/recovery cases and blocked Job mapping | 7 |
| Approval/reject/atomic replacement/rejection history | 8, 9 |
| Regeneration and idempotency cases | 5, 9 |
| CLI/error JSON | 10 |
| Windows focused CI, docs, fast/full verification | 11, 12 |

The plan creates every referenced `images/` and `tests/test_image_*.py` file before later tasks
consume it. It intentionally has no browser/Playwright, external provider, QC, HeyGen, rendering,
API/UI, canary, generic plugin, or broad Voice-refactor task.
