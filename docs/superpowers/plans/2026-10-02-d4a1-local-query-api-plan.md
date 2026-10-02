# D4A.1 Local Query API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Native execution was selected by the user. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose real campaign, input, job, HeyGen and editorial-plan metadata through a read-only local API.

**Architecture:** FastAPI routes call one application query service backed by existing public domain reads. A read-only SQLite engine and integrity-checked artifact reads supply explicit response DTOs; status is presentation logic, never paid-action authorization.

**Tech Stack:** Python 3.11, Pydantic 2, SQLAlchemy/Alembic, SQLite, Typer, FastAPI/Uvicorn and existing HTTPX/pytest.

**Spec:** `docs/superpowers/specs/2026-10-02-d4a1-local-query-api-design.md` (written spec approved by the user).

## Global Constraints

- D4A is complete only after both slices; this implements D4A.1, not D4A.2, D4B or D5.
- Bind is fixed to `127.0.0.1`; no host override, reload or multiple workers in this MVP.
- GET requests never mutate domain state, generate media, consume credits or silently migrate a database.
- FastAPI and Uvicorn are the only new direct runtime dependencies proposed.
- Collections use `{"items": [...]}`; item routes return their typed DTO directly. CamelCase follows existing contracts.
- All errors use `{error: {code, message, field}}`, with field nullable and whitelisted.
- Unknown keys/query parameters are rejected with 422 rather than silently ignored.
- Missing editing directories mean empty lists; a present corrupt artifact is an error, not an empty result.
- No request-supplied roots, media serving, CORS, workers, providers, probes, ASR, migrations or new tables.
- Preserve D3A/D3B contracts, approval gates and existing CLI behavior. Do not edit AGENTS.md.
- No real provider canary is needed; do not claim PROVIDER_VERIFIED.

## Review Focus

1. A database outside the project root with spaces/# must open correctly without creating another file (Task 1).
2. A CLI update between GET requests must be visible; do not cache domain snapshots (Task 2).
3. Several ready renders/plans for one scene must remain visible without inventing a latest plan (Task 3).
4. Encoded traversal, unknown query keys and foreign-campaign job IDs must fail without reflected inputs (Task 4).
5. Startup errors and server access logs must not disclose private paths or request secrets (Tasks 1 and 5).

## File map

Create `src/auraly_pipeline/api/{__init__,contracts,queries,status,app,cli}.py`:
package marker; public HTTP models; domain composition/projection; pure status computation;
HTTP/lifespan/errors; Typer/Uvicorn command respectively. Do not add generic transport,
repository, dependency-injection or configuration frameworks.

Modify `campaigns/persistence.py` for the read-only engine, `editing/service.py` for a
public trusted-path wrapper, `editing/batch_service.py` for verified plan discovery and
`cli.py` to register API commands. Dependency changes belong in `pyproject.toml`/`uv.lock`.
Tests: `tests/api_helpers.py`, `test_api_storage.py`, `test_api_queries.py`,
`test_api_status.py`, `test_api_http.py`, `test_api_cli.py` and existing batch-service tests.
Documentation updates belong in README and the three current project documents only.

## Execution rules

Read the spec and applicable skills before execution. Use an isolated worktree through
using-git-worktrees; inspect existing attachments before creating one. Each task uses
test -> observed expected failure -> minimum implementation -> focused verification ->
reviewed small commit. All commands below retain the required `rtk` prefix.

### Task 1: Read-only storage and trusted startup settings

**Files:** Modify `src/auraly_pipeline/campaigns/persistence.py`,
`src/auraly_pipeline/editing/service.py`; create `api/contracts.py`, `tests/api_helpers.py`,
`tests/test_api_storage.py` and the package marker.

**Interfaces:**
- `create_readonly_sqlite_engine(database_path: Path) -> Engine` in persistence.
- `validate_editing_path(root: Path, path: Path) -> Path` in editing/service: public wrapper over existing `_safe_path`, preserving EditingError behavior.
- `ApiSettings(project_root: Path, work_root: Path, database: Path)` typed frozen settings; `ApiSettings.from_options(*, project_root: Path | None = None, work_root: Path | None = None, database: Path | None = None) -> ApiSettings` resolves existing defaults after lexical ancestor validation.
- `validate_api_database(engine: Engine) -> None` in persistence checks packaged Alembic head through ScriptDirectory and required table/column names from current ORM metadata. Never executes upgrades or creates metadata; dispose on failed startup.
- `QueryError(code: str, field: str | None = None)` carries only allowlisted public codes/fields; static messages live in contracts, not exception text.
- Test helper `create_api_fixture(tmp_path: Path) -> ApiSettings` builds a migrated fixture before app startup using existing campaign/image/voice/render test patterns. Helpers may mutate fixtures, production query dependencies may not.
- The base fixture contains campaign-one with approved copy and one scene, no image/voice/render/jobs; richer fixtures extend it through existing helpers. Persistence functions use storage exceptions, not imports from API contracts; the API translates these into QueryError at its boundary.

- [ ] Write `test_readonly_engine_refuses_writes`, `test_missing_database_creates_nothing`, `test_database_head_and_schema_required`, `test_database_outside_project_with_special_characters`, `test_roots_reject_links_before_resolve`, `test_live_wal_reads_committed_updates` and `test_startup_error_is_sanitized`. Assert a failed INSERT, unchanged SQL dump, no missing-DB directory/lock creation, wrong revision/missing column rejected, outside-root DB usable, and no private path in public error text. Test '?' filenames on POSIX only; spaces/# on both platforms. Skip links only when OS permissions prohibit creation.

```python
def test_readonly_engine_refuses_writes(tmp_path: Path) -> None:
    settings = create_api_fixture(tmp_path)
    engine = create_readonly_sqlite_engine(settings.database)
    try:
        with engine.begin() as connection, pytest.raises(OperationalError):
            connection.execute(text("DELETE FROM campaigns"))
    finally:
        engine.dispose()
```

- [ ] Run `rtk proxy uv run pytest tests/test_api_storage.py -q`; confirm failures identify missing new interfaces rather than broken fixture setup.
- [ ] Implement the interfaces above. Resolve raw options/env/defaults without calling early-resolving config helpers first. Validate work containment and separately validate the exact DB plus ancestors; no requirement for DB containment. Use `sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, check_same_thread=False)` through SQLAlchemy's creator. Set only connection-local foreign_keys/busy_timeout if needed; no journal_mode/synchronous/immutable. Do not require absent editing subdirectories to exist.
- [ ] Run `rtk proxy uv run python scripts/verify.py fast --pytest tests/test_api_storage.py`; require exit 0. Inspect staged changes for secrets, generated files and unrelated changes.
- [ ] Commit `feat: add readonly API storage lifecycle` with only this task's files.

### Task 2: Verified artifact discovery and public query projections

**Files:** Modify `editing/batch_service.py`; create `api/queries.py`, extend
`api/contracts.py` and `tests/api_helpers.py`; create `tests/test_api_queries.py`;
extend `tests/test_editing_batch_service.py`.

**Interfaces:**
- `EditBatchService.list_plans(campaign_id: str) -> list[EditBatchPlan]` validates canonical `<videoId>/<planHash>/plan.json`, calls get_plan and sorts by videoId/planHash.
- `ApiQueries(settings: ApiSettings, engine: Engine)` composes CampaignService(engine), ImageService with an empty-registry JobService, VoiceMasterService, HeyGenVideoRepository(sessionmaker), EditingService and EditBatchService through public constructors. No `for_database` or provider construction.
- Define ContractModel-based allowlisted DTOs in contracts: `CampaignSummary`, `CampaignDetail`, `SceneImages`, `VoiceSummary`, `RenderSummary`, `JobSummary`, `ProfileView`, `PlanSummary`, `CampaignStatus`, `PendingItem` and `Items[T]`. Exact domain-field inclusion follows the spec's Public allowlists; profile view is `{profile: EditProfile, profileHash: str}`. Existing EditBatchPlan is the detail response, not a replacement schema.
- Query methods: `list_campaigns() -> list[CampaignSummary]`, `get_campaign(campaign_id: str) -> CampaignDetail`, `get_status(campaign_id: str) -> CampaignStatus`, `list_images(campaign_id: str) -> list[SceneImages]`, `list_voices(campaign_id: str) -> list[VoiceSummary]`, `list_renders(campaign_id: str) -> list[RenderSummary]`, `list_jobs(campaign_id: str) -> list[JobSummary]`, `get_job(campaign_id: str, job_id: str) -> JobSummary`, `list_profiles() -> list[ProfileView]`, `get_profile(profile_id: str, version: int) -> ProfileView`, `list_plans(campaign_id: str) -> list[PlanSummary]`, `get_plan(campaign_id: str, video_id: str, plan_hash: str) -> EditBatchPlan`.
- Until Task 3, get_status/list summary status are not exposed as HTTP routes; implement them in Task 3, not as synthetic placeholder status.

- [ ] Write `test_query_projections_are_allowlisted`, `test_query_reads_live_updates`, `test_job_ownership_is_enforced`, `test_query_constructors_are_passive`, `test_plan_discovery_is_canonical_and_verified`, `test_missing_artifact_not_corruption` and `test_queries_preserve_sql_and_files`. Assert creative copy is retained, config/budget/job payloads/account/upload IDs/raw error text/importSourcePath omitted, relative paths retained, second read sees fixture update, foreign job yields not_found, provider/model/worker/probe spies stay unused and SQL/media SHA/mtime remain unchanged. A missing requested artifact is not_found; a present invalid artifact is artifact_invalid. Discovery must not skip corruption.

```python
def test_query_projections_are_allowlisted(tmp_path: Path) -> None:
    settings = create_api_fixture(tmp_path)
    engine = create_readonly_sqlite_engine(settings.database)
    try:
        payload = ApiQueries(settings, engine).get_campaign("campaign-one").model_dump(by_alias=True)
        assert payload["campaignId"] == "campaign-one"
        assert {"budget", "config"}.isdisjoint(payload)
    finally:
        engine.dispose()
```

- [ ] Run `rtk proxy uv run pytest tests/test_api_queries.py tests/test_editing_batch_service.py -q`; confirm failures for new behavior.
- [ ] Implement queries and DTO projection explicitly, using public `CampaignService.get_campaign/list_campaigns`, `ImageService.list_candidates_for_scene`, `VoiceMasterService.get/list(campaign_id=...)`, `JobService.get_job/list_jobs(campaign_id=...)`, `HeyGenVideoRepository.get/list_campaign` and existing editing reads. Verify campaign existence before scoped reads. Check requested artifact existence through validated canonical paths before calling readers; do not classify every EditingError as 404. Missing discovered directories give empty lists. No private repository access or read-time media validation.
- [ ] Run `rtk proxy uv run python scripts/verify.py fast --pytest tests/test_api_queries.py tests/test_editing_batch_service.py`; require exit 0 and review the diff.
- [ ] Commit `feat: expose verified local query projections`.

### Task 3: Deterministic campaign status from observed history

**Files:** Create `api/status.py`, `tests/test_api_status.py`; extend
`api/contracts.py`, `api/queries.py` and `tests/api_helpers.py`.

**Interfaces:** `compute_campaign_status(campaign: Campaign, *, images: list[ImageCandidate], voices: list[VoiceMaster], renders: list[HeyGenRender], jobs: list[Job], plans: list[EditBatchPlan]) -> CampaignStatus`. It is pure, accepts observed domain objects and returns counts, per-scene pending facts, operationalStatus and nextPending. ApiQueries.get_status calls it; list/detail reuse that result without changing storedStatus.

- [ ] Write parametrized `test_status_priority_and_pending_match`, `test_ready_render_retains_pinned_history`, `test_historical_failure_and_paused_flow_do_not_block`, `test_multiple_renders_and_plans_remain_visible`, `test_disabled_captions_need_no_timing`, `test_missing_timing_needs_input`, `test_mixed_scenes_and_tie_order` and `test_current_voice_pins_copy`. Assert exact labels/codes from the spec and aggregate order: needs_attention, in_progress, needs_review, needs_input, ready_for_editing, editing_planned. New draft copy cannot invalidate a ready render. Reversing input list order must not change the output.

```python
def test_missing_voice_status(tmp_path: Path) -> None:
    settings = create_api_fixture(tmp_path)
    engine = create_readonly_sqlite_engine(settings.database)
    try:
        status = ApiQueries(settings, engine).get_status("campaign-one")
        assert status.operational_status == "needs_input"
        assert status.next_pending is not None
        assert status.next_pending.code == "voice_missing"
    finally:
        engine.dispose()
```

- [ ] Run `rtk proxy uv run pytest tests/test_api_status.py -q`; confirm expected failure.
- [ ] Implement the pure function and connect query status methods. Relevant jobs are IDs currently linked by current voice/image-generation/render records, not every campaign job. Ignore paused Flow entirely as a non-blocking manual-intake alternative. Active jobs: queued/running/retry_scheduled; actionable failures: failed/blocked, plus render failed/reconciliation_required. Completed/cancelled/retired jobs do not override replacements. Use newest approved voice by copyMasterVersion/generation/createdAt/ID, linked approved copy, and current approved image for unrendered prerequisites. Reviewable voice is review_required; image is pending_review. Copy approval missing is needs_input. If no approved voice exists, restrict current pending/review voice candidates to the highest approved copy before applying generation order; older-copy failures are historical.
- [ ] Preserve every ready render's pinned references and inspect all matching plan snapshots by exact render ID/source hash, without selecting a latest plan. Return one pending observation per ready render (and per missing-timing output where needed); aggregate all scenes with the spec priority and tie order scene key/entity ID/code. Editing-planned renderer_not_implemented is informational, not a failed job. No plan -> editing_plan_missing; any enabled output missing timing -> caption_timing_missing. No approved prerequisites -> missing/review codes; otherwise heygen_render_missing is needs_input. A malformed ready record without source is artifact_invalid, not ready. No I/O/probes in the pure function.
- [ ] Run `rtk proxy uv run python scripts/verify.py fast --pytest tests/test_api_status.py tests/test_api_queries.py`; require exit 0 and review the diff.
- [ ] Commit `feat: compute factual campaign progress and pending steps`.

### Task 4: Typed HTTP routes, lifespan and sanitized errors

**Files:** Create `api/app.py`, `tests/test_api_http.py`; extend `api/contracts.py`;
modify `pyproject.toml`, `uv.lock`.

**Interfaces:** `create_app(settings: ApiSettings) -> FastAPI`; lifespan owns one read-only engine and ApiQueries, disposes on shutdown/startup failure. Routes have concrete response models, typed IDs/version/hash and declared error models. `ErrorBody` wraps `{error: {code, message, field}}`; no raw validation inputs or exception strings.

- [ ] Add FastAPI/Uvicorn as the only new direct runtime dependencies with major upper bounds (`fastapi<1`, `uvicorn<1`); `rtk proxy uv lock` resolves compatible releases against existing Python/MCP/Pydantic. Record chosen versions from uv.lock, not guesses. This is environment setup for this deliverable, not API implementation.
- [ ] Write `test_all_spec_get_routes_and_openapi`, `test_http_unknown_and_encoded_inputs_are_sanitized`, `test_http_foreign_job_is_404`, `test_http_errors_follow_contract`, `test_http_mutations_are_405`, `test_http_host_restricted`, `test_http_startup_and_shutdown`, `test_http_reads_do_not_operate_pipeline`. Use TestClient lifespan and base_url `http://127.0.0.1`. Assert all 13 routes in the spec, /docs and /openapi.json, collection envelopes, response aliases, nullable field, 422/404/409/503/500 mapping, no reflected sensitive values, no wildcard CORS and no unimplemented render routes. Monkeypatch provider/auth/worker/probe/ASR calls to fail if invoked. Assert engine is disposed even after startup validation fails.

```python
def test_http_unknown_inputs_are_sanitized(tmp_path: Path) -> None:
    with TestClient(create_app(create_api_fixture(tmp_path)), base_url="http://127.0.0.1") as client:
        response = client.get("/api/v1/campaigns?unexpected=private-request-value")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"
        assert "private-request-value" not in response.text
```

- [ ] Run `rtk proxy uv run pytest tests/test_api_http.py -q`; confirm missing-app/route failure.
- [ ] Implement app/lifespan, response projections and all GET routes exactly as the spec table. Validate slug IDs with campaign/editing validators, job IDs with existing UUID pattern, hashes with Sha, versions positive integers. Explicitly reject unknown query keys. Restrict Host via TrustedHostMiddleware to localhost/127.0.0.1; sanitize its rejection to the same error envelope (400 invalid_request). Centralize known error mapping; unexpected errors -> static internal_error, no stacktrace/request values in application logs. Keep debug false. Unknown HTTP route -> 404 envelope; unsupported method -> 405 envelope with method_not_allowed. Do not turn programming errors into empty lists.
- [ ] Run `rtk proxy uv run python scripts/verify.py fast --pytest tests/test_api_http.py tests/test_api_queries.py tests/test_api_status.py tests/test_api_storage.py` and `rtk proxy uv pip check`; require exit 0 and review dependency/diff scope.
- [ ] Commit `feat: add readonly FastAPI routes and OpenAPI contract`.

### Task 5: Loopback CLI and operational documentation

**Files:** Create `api/cli.py`, `tests/test_api_cli.py`; modify
`src/auraly_pipeline/cli.py`, `README.md`, `docs/PROJECT-MEMORY.md`,
`docs/GOAL-ROADMAP.md`, `docs/PRD-MVP-MASS-VIDEO-AUTOMATION.md`.

**Interfaces:** `register_api_commands(app: typer.Typer) -> None`; registers
`auraly api serve --port 8000` and optional --project-root/--work-root/--database.
`serve_command` builds ApiSettings then calls `uvicorn.run(create_app(settings), host="127.0.0.1", port=port, workers=1, access_log=False)`; port range 1..65535, no host/reload/worker flags. Disable access logging to avoid leaking request query values. Startup failures print a static message and exit nonzero; never echo the selected private DB/root.

- [ ] Write `test_api_serve_defaults_and_options`, `test_api_serve_rejects_invalid_port_and_host_override`, `test_api_serve_startup_error_has_no_private_path`, `test_api_serve_disables_access_logs` using CliRunner and patched Uvicorn, without launching a long-running server. Assert env/default root resolution and unchanged existing CLI help/commands.

```python
def test_api_serve_rejects_host_override() -> None:
    result = CliRunner().invoke(app, ["api", "serve", "--host", "0.0.0.0"])
    assert result.exit_code != 0
    assert "No such option" in result.output
```

- [ ] Run `rtk proxy uv run pytest tests/test_api_cli.py tests/test_cli.py -q`; confirm expected new-command failure.
- [ ] Implement registration and command. Update docs with launch/read examples, existing-DB prerequisite and separate delivered D4A.1 vs planned D4A.2/D4B/D5. Do not mark D4A complete. Keep historical provider evidence unchanged and Flow automation non-blocking. Do not mark LOCAL_VERIFIED before Task 6 passes.
- [ ] Run `rtk proxy uv run python scripts/verify.py fast --pytest tests/test_api_cli.py tests/test_cli.py`; require exit 0 and review docs against the actual command/HTTP models.
- [ ] Commit `feat: expose local API command and usage guidance`.

### Task 6: Whole-slice verification and independent review

**Files:** Only relevant fixes/tests and verified-state entries in the current docs;
no unrelated cleanup or new milestone functionality.

- [ ] Run `rtk proxy uv run python scripts/verify.py full`; require all existing gates successful, including locked dependencies, pytest, Ruff/mypy for source/tests, schema drift and Node checks. Capture pass/skip counts and exact commit; local success is not independent GitHub Actions evidence.
- [ ] Obtain an independent whole-branch correctness/scope review as required by AGENTS.md and Native execution. The reviewer checks this plan/spec, actual responses, readonly storage and status history, not just test totals. Follow requesting-code-review; do not prescribe a model unavailable to this harness.
- [ ] For relevant findings, reproduce with a failing focused test, apply the minimum fix, rerun focused checks and commit coherently. Rerun the full gate after code changes; no claimed pass from a pre-fix run.
- [ ] Review final diff for secrets, generated media/private paths, dependency drift, accidental schema/migration changes and scope expansion. Mark D4A.1 IMPLEMENTED/LOCAL_VERIFIED only with recorded evidence; leave D4A.2 planned and PROVIDER_VERIFIED unchanged. Commit verified-state documentation if needed after checks.
- [ ] Hand off branch, commit and verification results. Merge/push only when explicitly requested. Optional real read-only smoke needs an already-existing DB and creates no media/paid actions; it is not a prerequisite for this slice.

## Self-review and handoff

Coverage: storage/lifecycle (1), every public projection and artifact integrity (2),
historical/mixed progress (3), all HTTP/OpenAPI/errors (4), runtime/docs (5), full gates
and independent review (6). Review Focus cases have owning tests. No implementation
is performed by writing this plan. User review of this plan precedes Native execution.
