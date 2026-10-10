# D5B.2A Human Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record and consult one definitive human decision for an exact existing final master and its QC evidence.

**Architecture:** Typed editing contracts plus a focused local service, with thin CLI adapters. Reuse `QcService.get_report`, existing trusted-path validation and exclusive JSON publication; never run QC, render, or provider operations. Persist review separately from technical QC.

**Tech Stack:** Python 3.11, existing Pydantic v2/Typer/pytest/Ruff/mypy, standard-library JSON/SHA-256/UTC time and filesystem operations; no new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-10-d5b2a-human-review-design.md` (written spec approved 2026-10-10).

## Global Constraints

- Request and record version `1.0`; decision `approved` or `rejected`.
- Approval requires QC `passed` and `watchedAndListened=true`; rejection requires a nonblank comment.
- One definitive decision per saved consumer-plan context and master identity; no undo or overwrite.
- Timestamp, decision, reviewer, comment, QC runtime, and QC key are not inputs to the version key.
- No HTTP endpoints, Job kinds, SQL migrations, React UI, automatic revision, delivery, or cloud upload.
- No FFmpeg/runtime detection or provider calls in review recording/consultation.
- Preserve masters, QC reports, unrelated work, `AGENTS.md`, and synced project sources.
- Prefix shell commands with `rtk`; use `apply_patch` for source/document edits.
- Status at plan creation: not implemented. Plan approval is required before execution.

## Review Focus

1. Lost acknowledgement followed by an identical request must preserve the original timestamp/bytes (Task 2 repeat test).
2. A different QC key for the same version must not create a second decision (Task 2 QC-key conflict test).
3. Simultaneous equal requests carry different generated timestamps but must converge; conflicting requests must have one winner (Task 2 concurrency tests).
4. A report swapped between verification and hashing/publication must never authorize review of different evidence (Task 2 evidence-change tests).
5. A foreign/stale review, or invalid input from CLI, must not become an approved result or leak private payloads (Tasks 2 and 3).

## File map and execution setup

- Create `editing/review_domain.py`: request/record validation and deterministic version key.
- Create `editing/review_service.py`: admission, exact evidence, immutable publication/reconciliation and consultation.
- Modify `editing/cli.py`, `editing/schema.py`: thin commands, safe errors, two schema exports.
- Create `tests/test_review_domain.py`, `tests/test_review_service.py`, `tests/test_review_cli.py`.
- Create `tests/review_helpers.py` only for shared real-media preparation needed by service/CLI tests; no fixture plugin registration from test modules.
- Generate `schemas/review-request.schema.json`, `schemas/review-record.schema.json`.
- Modify `scripts/verify.py`, `tests/test_verify_harness.py`, `.github/workflows/verify.yml` for schema drift and Windows test targets.
- Create `examples/review-request.json`; update README, PROJECT-MEMORY and GOAL-ROADMAP after verified implementation.
- Create `docs/superpowers/2026-10-10-d5b2a-verification.md` for final evidence, not advance capability claims early.

All production paths above are relative to `src/auraly_pipeline/`.
At execution, inspect Git/artifacts and use the worktree skill to resolve isolation;
preserve the existing untracked D4B.3C scratch. Start from the approved docs branch
containing this spec/plan and main `5aa6221`; confirm the actual fork point first.

### Task 1: Review contracts and schemas

**Files:** Create `src/auraly_pipeline/editing/review_domain.py`, `tests/test_review_domain.py`; modify `src/auraly_pipeline/editing/schema.py`, `scripts/verify.py`, `tests/test_verify_harness.py`; generate the two review schemas.

**Interfaces:**

- `HumanReviewRequest(EditingModel)`: `schema_version`, campaign/video/plan/variant/render/QC selectors, decision, reviewed_by, comment, watched_and_listened; camelCase aliases from existing base.
- `HumanReviewRecord(HumanReviewRequest)`: reviewed_at (timezone-aware UTC), review_key, master_path, master_sha256, size_bytes, receipt_sha256, qc_report_sha256, qc_status (`passed`/`blocked`).
- `review_key(*, campaign_id: str, video_id: str, plan_hash: str, output_variant_id: str, render_key: str, master_sha256: str, receipt_sha256: str) -> str` using existing `content_hash` with exactly these seven named fields.
- `export_review_schemas(output_dir: Path) -> tuple[Path, ...]`; called by the existing schema module entry point.

- [ ] Write `test_approval_requires_playback_confirmation`, `test_rejection_requires_comment`, `test_review_identity_and_normalization`, `test_record_requires_valid_approval_evidence`, and `test_review_key_uses_only_version_identity`. Use literal identity values, not expected hashes computed by the function under test. Assertions include:

```python
request = HumanReviewRequest.model_validate({
    "campaignId": "campaign-one", "videoId": "video-one", "planHash": "a" * 64,
    "outputVariantId": "variant-a", "renderKey": "b" * 64, "qcKey": "c" * 64,
    "decision": "approved", "reviewedBy": " Local reviewer ",
    "comment": " ", "watchedAndListened": True,
})
assert request.reviewed_by == "Local reviewer"
assert request.comment is None
# Validate mutations with pytest.raises(ValidationError), not unchecked model_copy:
# false/missing confirmation for approved; blank reviewer; rejected with blank comment;
# invalid identifiers/SHA/boolean/decision/extra field; record with blocked approval,
# naive timestamp, escaping master path, wrong derived key, or nonpositive size.
```

- [ ] Run `rtk proxy uv run pytest tests/test_review_domain.py -q`; confirm expected RED before implementing.
- [ ] Implement contracts and validators with existing identifier/SHA/relative-path conventions; apply the same approval rules on stored records. Do not add a policy abstraction or configurable version-key inputs.
- [ ] Add `export_review_schemas`, module entry-point invocation and both filenames to the full gate's generated-files list; update `test_editing_schemas_are_audited_by_full_gate`. Run schema export and prove exact names/models are present.
- [ ] Run `rtk proxy uv run python scripts/verify.py fast --pytest tests/test_review_domain.py tests/test_verify_harness.py` and `rtk proxy uv run python -m auraly_pipeline.editing.schema`; expect no failures or unrelated schema drift.
- [ ] Inspect staged diff; commit `feat(review): add human decision contracts and schemas` with only Task 1 files.

### Task 2: Immutable review service and verified consultation

**Files:** Create `src/auraly_pipeline/editing/review_service.py`, `tests/review_helpers.py`, `tests/test_review_service.py`.

**Consumes:** Task 1 contracts/key; `QcService.get_report(campaign_id, video_id, plan_hash, output_variant_id, render_key, qc_key) -> QcReport`; `publish_editing_json(root: Path, path: Path, payload: dict[str, Any]) -> None`; `validate_editing_path(root: Path, path: Path) -> Path`.

**Produces:**

- `HumanReviewService(*, project_root: Path, work_root: Path)` exposing the same normalized roots as QC service.
- `record(request: HumanReviewRequest) -> HumanReviewRecord`.
- `get_review(campaign_id: str, video_id: str, plan_hash: str, output_variant_id: str, render_key: str, review_key: str) -> HumanReviewRecord`.
- Test-only `review_case(tmp_path: Path) -> tuple[HumanReviewService, HumanReviewRequest, QcReport]` fixture, imported explicitly where needed. Use existing render helpers/real local RenderService/QcService to prepare one valid selected output; setup may encode/measure, review operations may not.

- [ ] Write `test_record_approval_and_exact_repeat` and `test_reject_passed_and_blocked_qc`, plus admission tests for absent/corrupt reports, missing master, blocked approval and foreign selection. Pin observable behavior:

```python
service, request, report = review_case
before = (service.work_root / report.master_path).read_bytes()
first = service.record(request)
path = (service.work_root / "campaigns" / request.campaign_id / "editing" /
        "reviews" / request.output_variant_id / request.render_key /
        first.review_key / "review.json")
stored = path.read_bytes()
second = service.record(request)
assert second == first
assert path.read_bytes() == stored
assert (service.work_root / report.master_path).read_bytes() == before
assert first.decision == "approved" and first.qc_status == "passed"
```

- [ ] Run `rtk proxy uv run pytest tests/test_review_service.py -q --maxfail=1`; confirm expected RED.
- [ ] Implement admission and record at the spec's exact reviews path. Derive master facts from validated evidence, not user fields. Read and hash the exact QC report bytes from a trusted deterministic path and validate their decoded record equals the report verified by `get_report`; never hash an unverified replacement. Revalidate all evidence before publication. Do not use private QC/Job methods, invoke analysis, or modify existing QC contracts.
- [ ] Implement readonly `get_review`, including request/context/derived key and evidence checks. A failed read is an error, not absent approval. Reuse stored original QC key, not a latest-report search. Never repair damaged artifacts automatically.
- [ ] Add conflict and concurrency tests. Parameterize changed decision/reviewer/comment/confirmation/QC key; generate another valid QC report for the same version for the QC-key case. Assert `EditingError`, unchanged review bytes, and no second decision. With two threads, identical requests return equal records with one timestamp; conflicting requests produce one record plus one conflict. Reconcile the winner after exclusive publisher conflict without suppressing evidence errors.
- [ ] Add `test_review_does_not_transfer_to_new_plan_or_variant`: two valid contexts produce distinct keys/records, with no inherited approval. Add corrupt-record and changed master/receipt/report consultation tests; assert the original review is retained but never returned as valid approval.
- [ ] Add evidence-change tests at verification/hash and pre-publication boundaries; change one of master/receipt/report between checks and assert `EditingError` and no new `review.json`. Use focused monkeypatches at existing read/publish boundaries, not production test hooks.
- [ ] Add `test_record_and_get_never_analyze_or_render` with runtime detection, QC analysis, and rendering patched to fail after fixture preparation. Assert record/get still work and the masters/reports' bytes remain unchanged. Cover unsafe IDs, supplied roots with symlinks/reparse points, and long Windows paths using existing platform fixture patterns; do not add general hostile-local-operator hardening.
- [ ] Run `rtk proxy uv run python scripts/verify.py fast --pytest tests/test_review_domain.py tests/test_review_service.py tests/test_qc_service.py`; require all passing.
- [ ] Inspect staged diff; commit `feat(review): persist definitive decisions for verified masters`.

### Task 3: CLI, operational documentation and CI coverage

**Files:** Modify `src/auraly_pipeline/editing/cli.py`, `.github/workflows/verify.yml`, `tests/test_verify_harness.py`, README and the two current-state docs; create `tests/test_review_cli.py`, `examples/review-request.json`, verification evidence document.

**Consumes:** Task 2 `HumanReviewService.record` and `get_review`; existing CLI root/error helpers.
**Produces:** `edit review --request <json>` and `edit review-get --campaign-id ... --video-id ... --plan-hash ... --output-variant-id ... --render-key ... --review-key ...`; both accept existing root options and return record JSON/exit 0 for approved or rejected records.

- [ ] Write `test_cli_records_and_gets_approval`, `test_cli_rejection_is_success`, `test_cli_conflicts_and_stale_evidence_fail_safely`, and `test_cli_validation_does_not_leak_input`. Assert exit 0/record status for rejection; exit 1/empty stdout for invalid input, conflict or stale lookup; no tracebacks, private absolute paths or private reviewer/comment markers in diagnostics. Validate the example against the exported request schema.
- [ ] Run `rtk proxy uv run pytest tests/test_review_cli.py -q --maxfail=1`; expect RED for missing commands.
- [ ] Register the two thin commands; include request/record models in validation error allowlisting. Build a safe synthetic approval example with explicit confirmation, without real private paths or identities. Do not add implicit confirmations or automatic fallback operations.
- [ ] Add three new test filenames exactly once to Windows focused verification; extend harness workflow assertions without weakening timeouts, FFmpeg handling or other test targets.
- [ ] Run actual tests service-first and CLI-first, not only collect-only checks:

```text
rtk proxy uv run python scripts/verify.py fast --pytest tests/test_review_domain.py tests/test_review_service.py tests/test_review_cli.py tests/test_verify_harness.py
rtk proxy uv run pytest tests/test_review_cli.py tests/test_review_service.py -q
rtk proxy uv run pytest tests/test_review_cli.py -q
```

- [ ] Update README, PROJECT-MEMORY and GOAL-ROADMAP: D5B.2A implementation is distinct from planned D5B.2B UI/D5B.3 delivery; explain explicit playback assertion, definitive decisions, rejection/new revision and lack of runtime requirement for consultation. Keep relevant technical history and `AGENTS.md` unchanged.
- [ ] Run `rtk proxy uv run python scripts/verify.py full` as the sole active harness, read the complete output and record actual test counts/skips/warnings/schema drift in the evidence document. Do not label provider verification or claim UI shipped.
- [ ] Inspect diff for secrets/media/private paths/scope; commit `feat(review): expose decision CLI and document verified backend` only after applicable checks pass.

## Whole-branch review and integration handoff

- [ ] Request one fresh independent whole-branch review for Native execution; address Critical/Important findings, note any deferred Minor issues. Preserve actual failed-run evidence and rerun affected tests/full gate when fixes require it.
- [ ] Confirm spec coverage, schemas, CLI, immutable artifacts, first-decision behavior and both collection orders. Do not silently remove an agreed approval prerequisite to pass tests.
- [ ] Report implemented/local-verification status and remaining D5B.2B/D5B.3 scope. Merge/push only with user authorization for that action; this plan does not authorize external Git writes.

## Execution choice

Recommend **Native**: three sequential deliverables share a small set of exact
contracts, so implementation in one session plus a fresh whole-branch review
keeps cost/process small. Subagent-driven execution is available if the user wants
independent task-level review. Written-plan approval and execution-method
confirmation are required before any implementation.
