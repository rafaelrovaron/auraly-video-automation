# D5B.3 — Approved Master Delivery

## Intent / approval

Close the personal local workflow: after definitive human approval, copy that
exact immutable master to a delivery folder and verify the copy. No new voice,
images, HeyGen, QC runtime, or encoding. User on 2026-10-12 explicitly authorizes
recommended decisions and implementation without intermediate approval stops.
Recommended execution: Native, four coherent tasks, one final independent review.

## Approach

Use existing filesystem JSON contracts and verified render/review services.
Alternative SQL receipts require a migration with no benefit here; cloud delivery
requires credentials and is a separate feature. Recommend local copy plus receipt.

DeliveryService owns copy/inspection; API derives the consumer from the existing
render Job; CLI accepts a typed request. A small DeliveryPanel inside ReviewPanel
appears only for an approved definitive review. No new Job or worker.

## Contracts and roots

DeliveryRequest schemaVersion 1.0: campaignId, videoId, planHash, outputVariantId,
renderKey, reviewKey. IDs/hashes use existing safe validators; no destination path
in HTTP request. DeliveryRecord extends request: deliveredAt UTC, path,
masterSha256, sizeBytes, receiptSha256, qcReportSha256, reviewSha256.
The path must equal campaignId/outputVariantId/reviewKey/master.mp4, relative to
the configured delivery root. A local receipt is delivery.json beside master.mp4.
DeliveryState: request, record nullable; absence means neither file exists.

Root is explicit constructor/CLI --delivery-root, otherwise AURALY_DELIVERY_ROOT,
otherwise work_root/deliveries. Root is resolved once per service, validated without
following symlinks/reparse points. Reject filesystem root, work root/ancestors,
or a managed-work child outside work_root/deliveries. Use existing extended Windows
path support. Public receipts expose only relative paths, never absolute roots.
UI documents default folder and environment variable; operator configures external
root before starting server. No folder picker or arbitrary filesystem browser.

## Delivery and verification

Validate the exact review with get_review; require approved/passed. Verify review
JSON bytes/hash and current master/receipt/QC using existing services, no FFmpeg.
GET must verify source evidence and destination bytes; missing receipt with an
existing MP4 is an integrity error, never successful delivery.

POST creates root only after approval/evidence validation. Stream verified source
to a temporary file in final directory, flush/fsync, verify size/hash, revalidate
evidence before publishing with no-overwrite hard link. Always remove own temp.
Publish JSON using existing publish_editing_json. Same target replay returns
original verified receipt/time without copy. Existing equal MP4 with no receipt
may be reconciled by explicit POST after checking source and destination hashes.
Different bytes or malformed/stale receipt are errors; never overwrite or delete
existing destinations. Concurrent identical requests reconcile original winner.
Failure can leave a verified orphan MP4; GET is blocked, explicit POST can recover.
No success before receipt and fresh readback verify. Evidence change blocks GET/POST.

## HTTP / UI

GET and POST /api/v1/campaigns/{campaignId}/editing/renders/{jobId}/outputs/{outputVariantId}/delivery.
GET no-store; POST 200 DeliveryRecord. Body is DeliveryRequest matching derived
Job/output context; reject unknown fields. No review/target => operation_not_allowed;
invalid files/root => artifact_invalid. Sanitized existing API errors.

UI readonly receipt path/hash/UTC, explicit Entregar master aprovado and Consultar
entrega. GET on mount/review change; no automatic POST. Lock duplicate clicks and
discard late GET results/abort on unmount. Unknown POST response blocks new sends
until successful GET; if receipt found show original; if genuinely absent allow
explicit idempotent POST retry. GET error removes success badge and keeps sends
blocked until successful inspection. No cloud/upload claim.

## Verification / boundaries

Real synthetic render+QC+review service tests cover approval gating, hash/receipt
replay, destination corruption, malformed receipts, orphan recovery, wrong review,
symlink, failure cleanup and simultaneous publication. HTTP/CLI tests exercise
typed boundaries and sanitized errors. UI tests cover explicit action, lost
response/reload and invalid evidence. Browser E2E delivers one approved output,
verifies byte identity and unchanged source/upstream, then reloads receipt.
Export delivery request/record/state schemas and include drift checks/CI.
Full local gate and final fresh reviewer required. No real campaigns/paid actions.
Do not edit AGENTS.md, sources, unrelated scratch or overwrite user data.
