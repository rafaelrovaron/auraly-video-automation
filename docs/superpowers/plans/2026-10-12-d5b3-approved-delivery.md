# D5B.3 Approved Master Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver exact approved masters to a configured local folder with verified idempotent receipts.
**Architecture:** Reuse HumanReviewService/open_verified_render and filesystem publication.
Direct local API/CLI operation, small UI panel; no new queue or persistence table.
**Tech Stack:** Python3.11/Pydantic/FastAPI, React/TypeScript, stdlib filesystem.
**Spec:** docs/superpowers/specs/2026-10-12-d5b3-approved-delivery-design.md

## Global Constraints

- No new voice, images, HeyGen, QC runtime, or encoding.
- No new Job or worker.
- No destination path in HTTP request.
- Always remove own temp; never overwrite or delete existing destinations.
- No success before receipt and fresh readback verify.
- No real campaigns/paid actions.
- Do not edit AGENTS.md, sources, unrelated scratch or overwrite user data.
- Native execution and recommended decisions preapproved by user on 2026-10-12.
- No new merge/push implied; keep branch until human requests integration.

## Review Focus

1. Same approved target concurrent POSTs: preserve original receipt and matching bytes.
2. Equal orphan MP4 after failed JSON publication: GET blocked, explicit POST reconciles.
3. Long Windows delivery root and broken symlink: validate ancestors, never follow links.
4. Review/QC/source changed during copy: no successful stale delivery.
5. Unknown POST followed by corrupted destination: no automatic resend or false badge.

### Task 1: Verified delivery service and contracts

**Files:** Create editing/delivery_domain.py, editing/delivery_service.py under src/auraly_pipeline;
tests/test_delivery_service.py.
**Interfaces:** consumes HumanReviewService.get_review/find_review, open_verified_render,
validate_editing_path/publish_editing_json. Produces DeliveryRequest/Record/State;
DeliveryService(*, project_root:Path, work_root:Path, delivery_root:Path|None=None);
deliver(request:DeliveryRequest)->DeliveryRecord; get(request:DeliveryRequest)->DeliveryRecord|None.

- [x] Write tests: approved_copy_replay (source/destination SHA equal; original UTC replay),
  unapproved_no_writes (missing/rejected), corrupt_destination_no_overwrite,
  malformed_receipt_not_absence, equal_orphan_recovery, conflicting_orphan_preserved,
  stale_source_review_qc, symlink_no_follow, concurrent_original_winner,
  failed_publication_cleanup, evidence_changed_during_copy, root_boundaries.
- [x] Run `rtk proxy uv run pytest tests/test_delivery_service.py -q`.
  Expected: missing module/service RED; fix fixture errors if any before implementation.
- [x] Implement exact signatures/contracts above and spec root/copy/publication rules.
- [x] Run `rtk proxy uv run python scripts/verify.py fast --pytest tests/test_delivery_service.py`.
  Expected: 3/3 PASS.
- [x] Commit feat(delivery): copy verified approved masters without overwrite.
- [x] task-done uses the same fast command.

### Task 2: HTTP/CLI and generated schemas

**Files:** Modify api/review_commands.py, review_routes.py, commands.py;
editing/cli.py, schema.py; scripts/verify.py; tests/test_api_delivery.py,
tests/test_delivery_cli.py, tests/test_api_http.py, test_api_actions_http.py,
test_verify_harness.py; schemas/delivery-{request,record,state}.schema.json.
**Interfaces:** consumes Task1 service/request/record; produces GET DeliveryState,
POST DeliveryRecord at existing output prefix + /delivery, CLI edit deliver and
edit delivery-get --request JSON [--delivery-root], same roots flags as review.

- [x] Write tests HTTP approved delivery/reload/replay, foreign body, unknown field,
  no approval and corrupted destination sanitized; CLI deliver/get/replay/invalid,
  env override, schemas validate real records. Route inventory now POST25/GET22.
- [x] Run `rtk proxy uv run pytest tests/test_api_delivery.py tests/test_delivery_cli.py -q`.
  Expected RED missing routes/CLI/schema.
- [x] Compose DeliveryService alongside reviews in ApiCommands. Add public
  ReviewCommands.delivery(campaign_id,job_id,output_variant_id,request)->DeliveryRecord
  and delivery_state(campaign_id,job_id,output_variant_id)->DeliveryState.
  Derive current approved review for GET; body must match exact reviewKey/consumer.
  Map no approval to operation_not_allowed, integrity errors to artifact_invalid.
  Export three delivery schemas, add to full generated registry; update inventory
  and registry expectations. No changing existing FinalReviewState contract.
- [x] Run `rtk proxy uv run python scripts/verify.py fast --pytest tests/test_api_delivery.py tests/test_delivery_cli.py tests/test_api_http.py tests/test_api_actions_http.py tests/test_verify_harness.py`.
  Expected 3/3 PASS; schemas generated and drift-free.
- [x] Commit feat(delivery): expose local delivery through API and CLI.
- [x] task-done same fast command.

### Task 3: Explicit local delivery UI

**Files:** Create web/src/deliveryApi.ts, DeliveryPanel.tsx and matching tests;
Modify web/src/ReviewPanel.tsx and test mocks only as needed.
**Interfaces:** consumes Task2 typed HTTP. DeliveryPanel({target:ReviewTarget,
review:HumanReviewRecord}); only mounted for approved review.

- [x] Write tests adapter validation (context/reviewKey/hash/path/time/schema),
  no automatic POST, approved copy explicit, original GET reload, unknown POST
  consulta-before-retry, corruption clears success and blocks sends, late GET
  cannot undo POST and target change/unmount aborts.
- [x] Run `rtk proxy npm --prefix web test -- deliveryApi.test.ts DeliveryPanel.test.tsx`.
  Expected RED missing imports.
- [x] Implement small panel, fixed sanitized notices, relative path/UTC/SHA,
  configuration instructions work/deliveries / AURALY_DELIVERY_ROOT, no cloud claim.
  Reuse api.read/post; lock/ref/abort/version, no automatic retry/polling.
- [x] Run `rtk proxy npm --prefix web test` and
  `rtk proxy npm --prefix web run build`; Expected all PASS.
- [x] Commit feat(delivery): deliver approved masters from local review panel.
- [x] task-done UI tests command.

### Task 4: End-to-end delivery and readiness

**Files:** Create tests/test_web_delivery_e2e.py; modify .github/workflows/verify.yml;
README, PROJECT-MEMORY, GOAL-ROADMAP, PRD; evidence docs/superpowers/2026-10-12-d5b3-verification.md.
**Interfaces:** consumes Task3 panel and Task2 configured service.

- [x] Write browser E2E using existing synthetic review fixtures: prepare/render/QC/
  approve/deliver, destination hash matches immutable master, reload original receipt,
  tamper destination => no delivered badge/retry; other output not delivered;
  upstream hashes and provider events/budget unchanged.
- [x] Run `rtk proxy uv run pytest tests/test_web_delivery_e2e.py -q`.
  Expected integration PASS (UI already shipped by Task3); any failure gets focused
  RED→GREEN, never relax production approval/integrity checks.
- [x] Add service/API/CLI/E2E tests once each to Windows CI. Document configuration,
  output tree, limitations, no overwrite/orphan recovery and implemented vs planned.
- [x] Run `rtk proxy uv run python scripts/verify.py full`. Expected 19/19 PASS.
- [x] Commit test(delivery): verify approved local delivery end to end.
- [x] task-done E2E command; full gate already run/read.
- [x] Dispatch one fresh whole-branch reviewer; fix Important/Critical in one TDD
  pass with full gate again, ledger/defer Minor; no re-review.
- [x] Commit evidence, preserve logs and keep feature branch; no merge/push.
