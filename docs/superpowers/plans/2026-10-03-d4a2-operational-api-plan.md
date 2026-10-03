# D4A.2 Operational API & Local Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for Native execution, or superpowers:subagent-driven-development if explicitly selected. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Operate existing campaigns, manual assets, voice, HeyGen and editorial plans through typed local actions and an explicitly started worker.

**Architecture:** Preserve readonly GET services; compose commands against a separate existing writable database engine. Long actions use the current durable Job framework, with one local runner scoped to campaign/type and no automatic startup execution.

**Tech Stack:** Python 3.11, FastAPI/Pydantic, SQLAlchemy/Alembic/SQLite, existing provider adapters and stdlib threading/executor. No new dependencies or database tables.

**Spec:** `docs/superpowers/specs/2026-10-03-d4a2-operational-api-design.md` — written specification approved by the user.

## Global Constraints

- Do not edit AGENTS.md, synced sources, migrations or unrelated work.
- No product implementation before this plan is reviewed and execution method confirmed.
- All shell commands retain `rtk`; raw commands use `rtk proxy`.
- GET routes retain D4A.1 readonly engines and response contracts.
- Existing compatible DB required; no startup/request/worker migration or create_all.
- Only startup settings select roots; validate lexical ancestors, containment and links before operations.
- Short domain mutations only in requests; media hashing/copies/probes/ASR, remote calls and polling run in Jobs.
- Preserve approvals, budget limits, audit, idempotency, reconciliation and existing CLI behavior.
- Bind remains 127.0.0.1, one process; no reload, wildcard CORS, new auth stack or OAuth UI.
- Writes require application/json and absent or exact same-loopback Origin; reject foreign/null Origins before writes.
- One active runner, fixed kind, selected campaign. No automatic execution, force-resume, arbitrary job types or blanket recovery.
- D4B UI/preview, D5 rendering and Flow expansion remain excluded.
- No paid calls are needed; local/fake evidence does not establish PROVIDER_VERIFIED.
- Commits are small deliverables. Merge/push require explicit authorization; do not infer it from prior-stage publication.

## Review Focus

1. A stale or due-retry Job in another campaign/type must remain unchanged during a scoped run, not merely remain unclaimed (Task 2).
2. A process crash after a child reservation but before its wrapper completes must never cause another paid reservation/dispatch (Tasks 4–5).
3. Stop during HeyGen batch polling must prevent new claims while preserving the active remote video's checkpoints (Task 6).
4. A trusted manifest containing a foreign sourceRoot, or changed after enqueue, must fail without importing assets (Task 3).
5. A forged Origin/body campaign ID must cause no writes, provider construction/calls or probes (Task 7).

## File map and interfaces

Create `api/action_contracts.py` for action/operation/result/runner DTOs; `api/commands.py`
for public service composition, ownership and typed submissions; `api/operations.py`
for the existing-Job handler; `api/worker.py` for explicit lifecycle; `api/action_routes.py`
for HTTP actions. Extend existing app/contracts/query boundaries without generic frameworks.

Focused tests: `test_api_action_storage.py`, `test_api_operations.py`,
`test_api_voice_actions.py`, `test_api_heygen_actions.py`, `test_api_worker.py`,
`test_api_actions_http.py`, `test_api_operations_e2e.py`. Extend storage/Job/domain tests
where behavior is shared. Reuse `tests/api_helpers.py` and existing fake providers/media fixtures.

Common command interface, introduced in Task 3 and extended in Tasks 4–5:
`ApiCommands(settings: ApiSettings, engine: Engine, *, speech_provider: SpeechProvider | None = None,
transcriber: TranscriptProvider | None = None, heygen_provider: HeyGenProvider | None = None)`.
Dependencies are testable through existing provider protocols; construction performs no remote calls.
The app owns the engines; command services borrowing them never call domain close methods
that dispose those engines. Shutdown disposes only after the runner drains.

All local wrappers use fixed job_type `api.local.operation`, max_attempts=1 and
retry_safety=manual_only. Its typed discriminated input union is extended only by the
listed operations. No request can select a raw JobSubmit/job_type/handler.

### Task 1: Diagnose and minimally repair the Linux prerequisite

**Files:** `tests/test_api_storage.py`, `tests/test_campaigns.py` or existing persistence tests;
`campaigns/persistence.py` and `campaigns/migrations/env.py` only if root-cause evidence requires.

**Interfaces:** Preserve public migration/engine APIs; repair exact path handling at the
shared faulty boundary, not by skipping the question-mark test or inventing another DB path.

- [ ] Read Actions 37117992340 failed log. Trace sqlite_url, create_sqlite_engine and migration connection construction. Reproduce POSIX URL parsing using stdlib/SQLAlchemy without relying on Windows supporting '?' filenames.
- [ ] Add a regression that asserts the engine URL's database equals the exact supplied filename, including '?', '#', '%' and spaces; existing POSIX test must also validate the migrated exact DB through readonly storage.

```python
# For each supported lexical filename, independently of filesystem creation:
assert engine.url.database == str(database.resolve())
# POSIX integration:
assert database.is_file()
validate_api_database(create_readonly_sqlite_engine(database))
```

- [ ] Run `rtk proxy uv run pytest tests/test_api_storage.py tests/test_campaigns.py -q`; expected RED for the demonstrated path mismatch, not missing OS filename permissions. Linux reproduction remains required when available; Windows-only pass is not Linux proof.
- [ ] Apply the smallest root-cause correction. Prefer passing a SQLAlchemy URL object intact where rendering/reparsing loses filename identity; preserve Alembic's public config/connection behavior and existing migration locks.
- [ ] Run `rtk proxy uv run python scripts/verify.py fast --pytest tests/test_api_storage.py tests/test_campaigns.py`; expected 3/3. Commit `fix: preserve exact SQLite filenames across migration setup`. No push without authorization; record Linux verification pending until actual evidence exists.

### Task 2: Existing-engine writes and genuinely scoped queue maintenance

**Files:** `campaigns/persistence.py`, `images/import_batch.py`, `voices/service.py`,
`jobs/service.py`, `jobs/repository.py`; `tests/test_api_action_storage.py` and relevant Job tests.

**Interfaces:**
- `create_existing_sqlite_engine(database_path: Path) -> Engine`: existing regular DB only, SQLite mode=rw, no DDL/migration/journal changes; public compatibility validation reused.
- `ImageImportService.from_engine(engine: Engine, *, work_root: Path) -> ImageImportService`: borrowed engine, no migration/disposal ownership; existing CLI constructor unchanged.
- Voice `worker_once` gains optional `campaign_id: str | None = None` and `job_type: str | None = None`; API calls pass voice.generate explicitly, while defaults preserve existing CLI behavior.
- Repository `recover_stale(now, *, campaign_id=None, job_type=None)` and `activate_due_retries(now, *, campaign_id=None, job_type=None)` gain matching optional filters. JobService.claim_next_job forwards its existing scope to both; defaults preserve unscoped CLI semantics.

- [ ] Write tests for missing DB creating nothing, migration spies never called, borrowed engine not disposed, readonly engine still rejects writes, and mixed queued/running-stale/due-retry Jobs across campaigns/types.

```python
assert selected_job.campaign_id == selected_campaign
assert selected_job.job_type == selected_type
assert other_campaign_dump_after == other_campaign_dump_before
assert other_type_dump_after == other_type_dump_before
```

- [ ] Run `rtk proxy uv run pytest tests/test_api_action_storage.py -q`; expected missing-interface/scoped-maintenance RED.
- [ ] Implement the interfaces with existing SQL predicates and session factories. Do not add API private Job access or a new repository abstraction.
- [ ] Run `rtk proxy uv run python scripts/verify.py fast --pytest tests/test_api_action_storage.py tests/test_job_service.py tests/test_voice_service.py`; expected 3/3. Commit `feat: add existing-engine actions and scoped queue maintenance`.

### Task 3: Local operation Jobs, image intake and editorial planning

**Files:** New `api/action_contracts.py`, `api/commands.py`, `api/operations.py`,
`tests/test_api_operations.py`; extend `tests/api_helpers.py` if needed.

**Interfaces:**
- `LocalOperationRequest`: discriminated ContractModel union on `operation`, initially image_prepare/image_import/edit_plan. Requests include campaignId and typed operation data; import includes manifestPath/mode and stored manifest SHA; edit uses EditBatchRequest/persist.
- `OperationSubmission(job_id, campaign_id, operation)` and `OperationView(job_id, campaign_id, operation, status, result, error_code)` use camelCase aliases. Result is a typed union, never an arbitrary dict; pending/failed result is null.
- `OperationResult` is discriminated by operation with the same operation literals as its request union and allowlisted operation-specific data.
- `ApiCommands.submit_operation(request: LocalOperationRequest) -> OperationSubmission`, `get_operation(campaign_id: str, job_id: str) -> OperationView`, `execute_operation(request: LocalOperationRequest) -> OperationResult`.
- `ApiOperationHandler(commands: ApiCommands).execute(context: JobExecutionContext) -> JobExecutionResult` implements the current handler protocol, using commands.jobs (public JobService). Register only api.local.operation plus native allowlisted handlers when introduced.

- [ ] Tests: same request reuses Job; changed manifest hash/new mode creates different identity; unknown operation/foreign campaign rejected; prepare produces existing inbox; dry-run returns coverage without candidates; execute imports once; foreign sourceRoot/links/changed queued manifest cause no domain imports; editorial dry-run has no plan artifact and persisted plan remains canonical/hash verified.

```python
assert first.job_id == replay.job_id
assert dry_run_view.result.operation == "image_import"
assert candidates_after_dry_run == candidates_before
assert not plan_path.exists()  # persist=False
assert "importSourcePath" not in view.model_dump_json(by_alias=True)
```

- [ ] Run `rtk proxy uv run pytest tests/test_api_operations.py -q`; expected missing-operation RED.
- [ ] Implement typed requests/results and narrow delegation to prepare_directory/plan/execute and EditBatchService.plan. Small manifest JSON may be hashed at enqueue; source media hashing stays in the worker. Verify manifest sourceRoot containment before domain service execution. Persist safe result data/child IDs in existing Job.output; projections exclude absolute paths. New source data is never silently substituted.
- [ ] Run `rtk proxy uv run python scripts/verify.py fast --pytest tests/test_api_operations.py tests/test_image_import_batch.py tests/test_editing_batch_service.py`; expected 3/3. Commit `feat: queue manual intake and editorial actions`.

### Task 4: Voice actions without synchronous media work

**Files:** `api/action_contracts.py`, `api/commands.py`, `api/operations.py`,
`tests/test_api_voice_actions.py`; existing voice modules only for necessary public composition.

**Interfaces:** Extend LocalOperationRequest/OperationResult with voice_import and voice_review.
Import carries VoiceImportRequest, trusted sourcePath and caller-stable requestId.
Review carries voiceId, action approve/reject, actor and relevant existing review reason.
`ApiCommands.submit_voice(request: VoiceGenerateRequest) -> OperationSubmission`
queues the existing voice.generate submission; imported source copy/voice review run inside wrappers.

- [ ] Tests: generation submission performs no provider execution; imported source not copied before wrapper runs; missing actor/foreign ownership rejected; approval QC/hash gates preserved; source outside root rejected; replay of completed import wrapper returns recorded child IDs even after original source changes; crash/replay around child creation creates no duplicate voice/job/artifact.

```python
assert speech_provider_calls == []  # submission only
assert source_copy_count_before_worker == 0
assert first_import_child_id == replay_import_child_id
assert failed_review_does_not_approve
```

- [ ] Run `rtk proxy uv run pytest tests/test_api_voice_actions.py -q`; expected missing-action RED.
- [ ] Delegate via existing VoiceMasterService/VoiceImportService/handlers, using borrowed engines and scoped Jobs. Import wrapper identity includes requestId; use existing content-based voice import idempotency inside the worker. Check recorded child identities on retries before reading a potentially changed source. Existing human review reason/PCM/ASR behavior remains unchanged.
- [ ] Run `rtk proxy uv run python scripts/verify.py fast --pytest tests/test_api_voice_actions.py tests/test_voice_external_import.py tests/test_voice_human_review.py`; expected 3/3. Commit `feat: expose queued voice import and review actions`.

### Task 5: HeyGen preparation, reservation and reconciliation actions

**Files:** `api/action_contracts.py`, `api/commands.py`, `api/operations.py`,
`tests/test_api_heygen_actions.py`.

**Interfaces:** Extend request/result unions with heygen_assets, heygen_video_plan,
heygen_video_submit and heygen_reconcile. Inputs reuse HeyGenVideoConfig, maxPaidRenders,
approvedBy and manual binding flags. Results expose operation status, safe counts,
domain render/Job IDs and RenderSummary; omit account/upload IDs and provider payloads.

- [ ] Tests: submit only enqueues wrapper; provider untouched until execution; preview consumes no generation budget; reserved count respects exact cap/approvals; changed prerequisites fail instead of exceeding cap; replay after child reservation-before-wrapper-completion has same render IDs/reservation count; reconciliation without required confirmation fails; OAuth/ambiguous dispatch errors remain blocked/sanitized; no token/URL/account IDs in results.

```python
assert provider.create_video_calls == 0  # preview/reservation wrappers
assert reserved_after_replay == reserved_before_replay
assert render_ids_after_replay == render_ids_before_replay
assert paid_dispatch_after_ambiguous_failure == 1
```

- [ ] Run `rtk proxy uv run pytest tests/test_api_heygen_actions.py -q`; expected missing-delegation RED.
- [ ] Compose existing providers/services without their migration factories; create adapters lazily for explicit worker execution. Delegate plan_assets/submit_assets, plan_videos/submit_videos/reconcile_video. Native asset/video handlers retain official MCP/OAuth, recorded approvals, checkpoints and rate/poll timing. A wrapper does not generate the submitted videos; that requires the separate heygen_videos runner.
- [ ] Run `rtk proxy uv run python scripts/verify.py fast --pytest tests/test_api_heygen_actions.py tests/test_heygen_service.py tests/test_heygen_video_service.py`; expected 3/3. Commit `feat: queue HeyGen operational actions with existing gates`.

### Task 6: One explicit campaign-scoped local runner

**Files:** New `api/worker.py`, `tests/test_api_worker.py`; extend `heygen/video_service.py`
only to support cooperative stop between claims.

**Interfaces:**
- WorkerKind literal local_operations/voice_generate/voice_import/heygen_assets/heygen_videos.
- WorkerState(state: idle/running/stopping, campaign_id: str | None, kind: WorkerKind | None, error_code: str | None).
- `LocalApiWorker(commands: ApiCommands).start(campaign_id: str, kind: WorkerKind) -> WorkerState`, `stop(campaign_id: str) -> WorkerState`, `status(campaign_id: str) -> WorkerState`, `shutdown() -> None`.
- `HeyGenVideoService.run_videos(campaign_id: str, *, stop_requested: Callable[[], bool] | None = None) -> VideoRunSummary`; default remains existing CLI behavior, check stop before each new claim, never interrupt an active handler.

- [ ] Tests with Events/barriers, not timing sleeps: concurrent starts admit exactly one; wrong-campaign status/stop returns not_found; fixed-kind mixed queue isolation; future retries not busy-polled; stop preserves active work/heartbeat and refuses another claim; shutdown waits before engine disposal; new process starts idle; stale/ambiguous paid recovery does not repeat dispatch.

```python
assert admitted_starts == 1
assert rejected_start.value.code == "operation_conflict"  # pytest.raises(QueryError)
assert next_job_attempt_count_after_stop == 0
assert not engine_disposed_while_handler_active
assert restarted_worker.status(campaign_id).state == "idle"
```

- [ ] Run `rtk proxy uv run pytest tests/test_api_worker.py -q`; expected missing-runner/cooperative-stop RED.
- [ ] Use one executor worker, one admission lock and stop Event. Drain selected campaign/types until no eligible work; use native service loops for their fixed scope. Do not hold an admission lock during provider calls or wait for shutdown inside a request. Stop closes claims; shutdown drains then returns. Unexpected failures produce static runner errorCode and persisted Job evidence.
- [ ] Run `rtk proxy uv run python scripts/verify.py fast --pytest tests/test_api_worker.py tests/test_heygen_video_service.py`; expected 3/3. Commit `feat: add explicit local worker lifecycle`.

### Task 7: Typed HTTP actions, origin boundary and writable lifecycle

**Files:** New `api/action_routes.py`, `tests/test_api_actions_http.py`; modify
`api/app.py`, `api/contracts.py`, `api/commands.py`, `api/action_contracts.py`.

**Interfaces:** `register_action_routes(app: FastAPI) -> None`; dependencies access app-owned
commands/worker. Add operation_conflict and operation_not_allowed static 409 errors.
Route paths/statuses/bodies are exactly the spec's 19 POST and two additional GET routes.
Short command methods mirror existing campaign/copy/image-review/profile/Job services;
queued endpoints build the appropriate LocalOperationRequest. All responses have concrete DTOs.

- [ ] HTTP tests for every route/OpenAPI, exact 200/201/202 semantics and operation envelopes; foreign body/entity ownership returns 404 or body/path inconsistency 422 before mutation; null/foreign Origin and non-JSON writes rejected with static 422; absent Origin and exact local server origin accepted. Invalid known transition/busy runner is 409; no raw errors/secrets.

```python
assert foreign_origin_response.status_code == 422
assert database_dump_after == database_dump_before
assert provider_calls == probes == []
assert queued_response.status_code == 202
assert get_jobs_payload_has_no_input_or_output
```

- [ ] Run `rtk proxy uv run pytest tests/test_api_actions_http.py -q`; expected missing-route/boundary RED.
- [ ] Enforce write boundary before dependency side effects. Startup owns one readonly and one existing compatible writable engine; dispose both on failed startup and after worker shutdown. No provider/auth/model preflight or execution during startup. Preserve sanitized Uvicorn logs, loopback CLI and all old GET models. Implement campaign-copy/profile short actions and scoped cancel/resume through existing methods, never force-reconcile.
- [ ] Run `rtk proxy uv run python scripts/verify.py fast --pytest tests/test_api_actions_http.py tests/test_api_http.py tests/test_api_cli.py`; expected 3/3. Commit `feat: expose typed local operational endpoints`.

### Task 8: Fake end-to-end flow, full gate and handoff

**Files:** New `tests/test_api_operations_e2e.py`; relevant regression fixes only;
`README.md`, `docs/PROJECT-MEMORY.md`, `docs/GOAL-ROADMAP.md` and
`docs/PRD-MVP-MASS-VIDEO-AUTOMATION.md` for verified state/usage.

- [ ] Add a deterministic HTTP/service integration fixture with real local media and fake provider/transcriber: campaign/copy, prepare inbox, manually placed files, dry-run/import, voice import/process/review, asset preparation/upload, video preview/reserve/run/download, editorial dry-run/persist. Assert each required worker start is explicit, intermediate GET polling sees real state, and final plan has pinned source/voice/copy identities. A/B must not add voice/provider generation calls.
- [ ] Run `rtk proxy uv run pytest tests/test_api_operations_e2e.py -q`; expected failures identify any missing integration behavior; fix through focused RED→GREEN, not mocks that hide an invalid domain fixture.
- [ ] Run `rtk proxy uv run python scripts/verify.py full`; expected 15/15 successful steps. Record exact code commit, pass/skip totals/platform. Obtain independent whole-branch review per AGENTS/selected execution skill; relevant findings require reproduction and a post-fix full gate.
- [ ] Confirm actual Linux evidence for Task 1 before declaring cross-platform success. If no Linux runner is available without push authorization, keep that verification explicitly pending and request publication authority rather than claiming it passed.
- [ ] Update operational docs with enqueue/start/poll/stop examples, no startup migration/auto-dispatch, dry-run Job persistence and remaining D4B/D5 work. Mark IMPLEMENTED/LOCAL_VERIFIED only for achieved milestones; no new PROVIDER_VERIFIED. Inspect full diff for secrets/private paths/generated media/schema drift/unrelated changes. Commit `docs: record D4A2 operational verification and usage` only after applicable evidence.
- [ ] Hand off branch/commits, review results and verification limits. Merge/push only if explicitly requested for this implementation.

## Self-review and execution handoff

Spec coverage: Linux prerequisite (1); engine ownership/public filters (2); local intake,
plans/idempotency/result projections (3); voice gates/import replay (4); HeyGen budgets,
preview/reservation/reconciliation (5); explicit lifecycle/restart (6); all HTTP actions,
write boundary/short mutations/lifespan (7); fake end-to-end, docs/full review (8).
Review Focus cases are assigned to their owning RED tests. Interface names and worker
kinds are consistent across consumers; no new dependencies, auth product or tables.

This plan is documentation, not implementation. Review/confirm it before execution.
Native is recommended: tasks share command/contracts and benefit from one continuous
context, with one independent whole-branch reviewer at the end. Preserve Native if the
user confirms their previous execution preference; do not spawn implementers by default.
