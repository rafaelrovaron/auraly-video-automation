# D2C Human Voice Review and MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Native execution was selected by the user; retain it, with one independent final review.

**Goal:** Approve the existing imported WAV honestly and complete the authorized single-render HeyGen canary through the real MCP contract.

**Architecture:** Extend the existing Voice Master approval path with an explicit, narrowly scoped human-review reason. Align the existing MCP adapter with discovered schemas; retain application jobs, checkpoints and budget controls. No new subsystem or dependency.

**Tech Stack:** Python 3.11, Pydantic, SQLAlchemy/Alembic SQLite, existing MCP/OAuth SDK, pytest and FFmpeg.

**Spec:** `docs/superpowers/specs/2026-09-30-d2c-human-voice-review-mcp-design.md` (user approved).

## Global Constraints

- No máximo uma geração paga, concorrência 1.
- Preserve the existing WAV, source media, ASR transcript, score, findings, manifests and hashes.
- Override only imported voices with comparison `review_required` and the sole transcript-review finding; never `mismatched`, spoken headline or technical QC failures.
- No new dependencies, UI, renderer, ElevenLabs behavior change or AGENTS.md edits.
- No secrets, signed URLs, personal account data, private media paths or generated media in commits/logs.
- Ambiguous creation never authorizes automatic re-creation; known IDs are persisted before polling.
- Full deterministic verification and independent review precede paid generation. Merge/push requires a separate request.

## Review Focus

1. Whitespace-only or unsafe review reason must not unlock approval (Task 1).
2. Approval exceptions must survive restart without rewriting evidence; completed approval remains immutable (Task 1).
3. A table rebuild with existing HeyGen references must retain foreign keys, approval triggers and history (Task 1).
4. Transport/context-manager errors after allocation may hide provider failures; they must not cause duplicate allocation (Task 2).
5. A paginated asset response must not silently drop assets; ID correlation must be exact before readiness (Task 2).

## Task 1: Audited imported-voice review approval

**Files:** Modify `src/auraly_pipeline/voices/{domain,db_models,service}.py`, `src/auraly_pipeline/cli.py`, `schemas/voice-master.schema.json`.
Create `src/auraly_pipeline/campaigns/migrations/versions/0010_voice_human_review.py`.
Tests: `tests/test_voice_external_import.py`, `tests/test_voice_external_migration.py`, `tests/test_voice_domain.py`, `tests/test_voice_cli.py`, and existing migration-head assertions located by `rg`.

**Interfaces:** Extend `VoiceMasterService.approve(self, voice_master_id: str, *, approved_by: str, approval_review_reason: str | None = None) -> VoiceMaster`.
Add optional `approval_review_reason` to domain/row/output and CLI option `voice approve --review-reason TEXT`. Existing callers require no changes.

- [ ] Write `test_imported_transcript_review_approval_preserves_evidence`: reuse `setup_import`, supply an ASR transcript with a single missing token and enough narration that comparison is `review_required`, not `mismatched`; retain real local processing and these assertions:

```python
assert before.transcript_match_status.value == "review_required"
with pytest.raises(VoiceMasterReviewError):
    voices.approve(before.voice_master_id, approved_by="rafael")
approved = voices.approve(before.voice_master_id, approved_by="rafael",
                          approval_review_reason="Audio accepted for this technical test.")
assert approved.status == "approved"
assert approved.approval_review_reason == "Audio accepted for this technical test."
assert approved.approved_by == "rafael" and approved.approved_at is not None
assert approved.transcript_match_status == before.transcript_match_status
assert approved.qc_findings == before.qc_findings
assert approved.transcript_sha256 == before.transcript_sha256
assert approved.manifest_sha256 == before.manifest_sha256
assert approved.processed_sha256 == before.processed_sha256
```

- [ ] Add parameterized rejection cases for `mismatched`, headline spoken, another QC finding, incomplete metadata, changed artifacts and ElevenLabs origin even with a reason. Assert `VoiceMasterReviewError` and unchanged approval state. Add blank/unsafe reason validation; restart preserves reason/evidence and a subsequent evidence/reason update is rejected by SQLite immutability.
- [ ] Add direct SQL CHECK/trigger tests for equivalent allowed/blocked transitions; migration preserves legacy records, linked jobs and HeyGen references, reason defaults to NULL, downgrade refuses existing exceptions. Keep existing normal approval tests.
- [ ] Run `rtk proxy uv run python -m pytest tests/test_voice_external_import.py tests/test_voice_external_migration.py tests/test_voice_domain.py tests/test_voice_cli.py -q`; confirm expected failure on the absent approval argument/field before production edits.
- [ ] Implement the optional reason using existing safe-error validation, max length 512, existing approver/time and artifact checks. Permit exactly `provider='imported'`, comparison `review_required`, headline false and findings `["The narration transcript requires human review."]`; normal approval stays unchanged. Reject a reason outside this explicit exception to prevent misleading metadata. Keep evidence immutable.
- [ ] Implement migration revision `0010_voice_human_review` after `0009_external_voice_import`, following the existing trigger-preserving table-rebuild pattern. Update approval CHECK and `enforce_voice_approval_gate` coherently, preserve all other triggers and references; fail downgrade rather than erase exception history.
- [ ] Regenerate with `rtk proxy uv run python -m auraly_pipeline.voices.schema`. Run all voice and migration tests, Ruff and mypy through the fast harness; review staged diff for schema drift and scope.
- [ ] Commit the coherent approval deliverable: `feat(voice): audit human acceptance of imported transcript review`.

## Task 2: Official MCP transport contract

**Files:** Modify `src/auraly_pipeline/heygen/provider.py`; only modify `handler.py`/`service.py` if needed to preserve known-allocation recovery and prevent duplicate POSTs.
Tests: `tests/test_heygen_provider.py`, `tests/test_heygen_video_provider.py`, `tests/test_heygen_handler.py`; sanitized fixtures in `tests/fixtures/heygen/` if useful.

**Interfaces:** Keep `HeyGenMcpAdapter` public methods and provider protocols unchanged. `idempotency_key` remains local input, not an unsupported remote argument. `AssetBatchAllocation`, `AssetBatchState`, `ProviderVideo` and `VideoPreflight` remain application results.

- [ ] Read authenticated `list_tools` and `get_current_user` through the existing OAuth session without uploads/rendering. Inspect schemas and response structure using sanitized field names/types; never print personal values or signed URLs. Use actual schema definitions for transport fixtures, not imagined API fields.
- [ ] Update session fixtures and `test_video_payload_exact` to camelCase and retain nested snake_case:

```python
assert payload["image"] == {"type": "asset_id", "asset_id": "image-one"}
assert payload["audioAssetId"] == "audio-one"
assert payload["aspectRatio"] == "9:16"
assert "idempotency_key" not in payload and "engine" not in payload
assert session.calls[-1] == ("get_video", {"videoId": "video-one"})
```

- [ ] Update allocation/completion/status tests: files retain `filename`, `content_type`, `size_bytes`, `checksum_sha256`; completion and batch reads send `batchId`; bulk reads send `assetIds="asset-one,asset-two"`. Schema missing required supported properties still fails before dispatch.
- [ ] Add parser tests against confirmed response envelopes, allocation metadata and status item identities (including documented `video_id` asset status alias). Test unsafe HTTPS/headers, missing/wrong IDs, pagination if present, and missing required metadata without inventing values. Confirm account hashing remains consistent across asset and video preflight.
- [ ] Add `test_allocation_unknown_outcome_never_recreates`: simulate POST timeout/malformed allocation and session-context wrapping; assert ambiguity persists and retry does not call allocation again. Known batch polling/reconciliation resumes its ID. Keep existing malformed-video-create and no-second-paid-call regressions.
- [ ] Run `rtk proxy uv run python -m pytest tests/test_heygen_provider.py tests/test_heygen_video_provider.py tests/test_heygen_handler.py -q`; confirm the existing snake_case implementation fails expected contract assertions.
- [ ] Align preflight and requests to discovered camelCase properties; validate supported payloads against actual schemas. Remove unsupported remote idempotency arguments. Parse only confirmed envelopes/aliases and maintain sanitized failure categories, especially after dispatch. Reuse existing recovery paths; no generic retry framework.
- [ ] Run all HeyGen tests and fast harness, inspect the diff, then commit: `fix(heygen): align adapter with official MCP transport schemas`.

## Task 3: Verified one-render canary and evidence

**Files:** Update `README.md`, `docs/PROJECT-MEMORY.md`, `docs/GOAL-ROADMAP.md`, `docs/PRD-MVP-MASS-VIDEO-AUTOMATION.md` only for verified capability/status; record sanitized result in `docs/superpowers/2026-09-30-d2c-canary-verification.md`.
Operational config/evidence/media stay outside Git in the existing approved project work root.

**Interfaces:** Use existing approval, `HeyGenService.plan_assets/submit_assets`, public scoped JobService claims, `HeyGenVideoService` and CLI. Campaign `d2c-001`; reuse existing approved image and processed imported Voice Master. Explicitly pass the canonical work root for every HeyGen operation; never use cwd default `work`.

- [ ] Run `rtk proxy uv run python scripts/verify.py full`; require all steps exit 0. Review the complete diff for secrets/media/private paths. Obtain one independent whole-branch code review and resolve findings with focused tests before paid work.
- [ ] Record the user's approval via `voice approve VOICE_ID --approved-by Rafael --review-reason "Audio accepted for this technical test; original transcript review retained."`. Verify persisted decision and unchanged WAV/transcript/manifest hashes. Do not rerun import/trim/ASR or replace evidence.
- [ ] Run real asset/video preflight; verify account identity and usable uploaded-audio schema. If unsupported or unverified, stop before paid dispatch.
- [ ] Submit the existing image plus processed WAV via `heygen prepare-assets d2c-001 --work-root WORK_ROOT --yes`. Execute only the returned upload job through public scoped claims. Persist allocation IDs before PUT/finalization; poll/reconcile only the known batch. Inspect response contracts safely during authorized upload if unavailable read-only; schema surprises require focused regression/fix, not a duplicate allocation.
- [ ] Save ignored operational `HeyGenVideoConfig` with `concurrency=1`, `aspect_ratio=9:16`, `resolution=1080p`, `output_format=mp4`, default polling limits. `plan-videos` must show exactly one new render and reuse of the approved audio asset.
- [ ] Reserve once with `generate-videos d2c-001 --config CONFIG --max-paid-renders 1 --approved-by Rafael --work-root WORK_ROOT --yes`. Execute `run-videos d2c-001 --work-root WORK_ROOT`; allow bounded polling of the same persisted ID. Ambiguity without ID stops for reconciliation, never a second generation.
- [ ] Verify downloaded source through existing ffprobe/duration/full-decode QC; confirm one reservation, one render, asset reuse, valid local source and safe evidence. Replay must return the same render without another paid call. Technical success does not replace human final-video approval.
- [ ] Document actual outcome: only a successfully downloaded/validated real MP4 establishes `PROVIDER_VERIFIED`; otherwise record exact blocked/failed stage and preserved identifiers safely. Keep shipped capability separate from next roadmap. Run applicable checks and commit sanitized evidence/docs. Do not merge/push without a separate request.
