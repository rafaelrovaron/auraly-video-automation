# D4A.2 — Operational Actions & Local Worker

Date: 2026-10-03.
Status: design/spec and implementation plan approved; Native implementation in progress.
Base: main cc6f849, D4A.1 implemented and locally verified.

## Intent and boundary

Rafael needs to operate the existing delivery-first pipeline from the future local UI:
create campaigns, import manually prepared assets, review inputs, submit HeyGen batches
and create editorial plans. This slice exposes those operations without duplicating
domain rules or making HTTP requests wait for media processing/provider polling.

Approved approach: application-service actions plus one explicitly started, in-process
worker runner. Jobs remain durable in the existing SQLite queue. No new queue product,
database tables, distributed scheduler or service deployment is required.

D4A completes after D4A.1 and D4A.2 pass their implementation/verification gates.
React and approximate preview remain D4B; final rendering remains D5. Google Flow stays
paused and non-blocking. OAuth connection/disconnection stays in the existing CLI;
the API does not become a browser-login implementation.

## Current capability versus new work

Existing: D4A.1 GET endpoints, readonly SQLite queries, typed public projections,
verified profiles/plans, domain approvals, queue leases/idempotency/reconciliation,
manual image intake, voice generation/import, HeyGen assets/video services and editing CLI.

New: typed action endpoints, writable service composition without migrations, local
operation Jobs for expensive actions not currently queued, safe result projections,
and explicit campaign/type-scoped worker lifecycle. These are PLANNED, not shipped.

The D4A.1 local gate passed 15/15 with 1,627 tests/23 skips on Windows. Actions run
37117992340 subsequently failed on Linux: test_database_question_mark_uri expects
the migration fixture to create a database named a?.db, but the readonly factory
reports that exact file absent. This is an unresolved D4A.1 verification prerequisite,
not permission to skip/disable the test. Diagnose and repair in a separate minimal
change before claiming D4A.2 LOCAL_VERIFIED. Windows/local success is not Linux evidence.

## Architecture

- Keep D4A.1 GET routes and response schemas unchanged; they retain readonly engines.
- Compose writable application services separately with validated startup settings.
  Require an existing, compatible database. Never call migrations/create_all from
  startup, requests or workers. The normal CLI remains responsible for upgrades.
- Reuse public constructors/contracts. In particular ImageImportService currently
  migrates in its constructor: add an explicit existing-engine construction path,
  preserving its CLI behavior. VoiceMasterService.worker_once currently claims an
  unfiltered queue: add optional public campaign/type filters before API usage.
- No private Job repository access from the API. Reuse linked submission transactions
  where domain entities and Jobs already require atomic persistence.
- Execution refinement: VoiceImportService.import_audio accepts an optional public
  before_commit(Session, JobRow) callback for its child creation/reuse transaction.
  The API callback stores safe child IDs on the local operation Job before committing,
  closing the crash window between creating a child and recording its identity.
  The CLI default is None, preserving its behavior and engine ownership.
- HeyGen submit_assets accepts an optional before_commit(Session, JobRow) callback;
  submit_videos accepts before_commit(Session, list[HeyGenRender]), and reconcile_video
  accepts before_commit(Session, HeyGenRender). These public callbacks checkpoint the
  local operation in the same native submission/resumption transaction, including
  reused submissions. Defaults preserve existing CLI behavior. HeyGen wrappers carry
  a caller-stable requestId: retry reuses the checkpoint; a fresh operator action after
  changed prerequisites uses a new requestId without weakening native idempotency.
- Short mutations delegate to services in the request. Media hashing/probes/ASR/media copies,
  external calls and polling run in queued work, not route handlers.
- API-only long operations use narrow handlers on the existing Job framework, not a
  second workflow engine. Their output is projected, never raw provider data.

## HTTP action surface

All paths below have prefix /api/v1. Bodies are typed ContractModel JSON with unknown
fields rejected. Campaign path ownership must agree with body/entity ownership.
Existing campaign, copy, import, generation, profile and editing request contracts are
reused; wrappers add only action-specific fields such as source path or approval reason.

| POST path | Delegation / result |
|---|---|
| /campaigns | CampaignService.create_campaign; public campaign detail, 201 |
| /campaigns/{campaignId}/copies | add_copy_master_version; public campaign detail, 201 |
| /campaigns/{campaignId}/images/import/prepare | queued prepare_directory under configured work root; operation Job, 202 |
| /campaigns/{campaignId}/images/import | manifestPath + mode dry_run/execute; operation Job, 202 |
| /campaigns/{campaignId}/images/{candidateId}/review | approve/reject/replace using existing image review methods; ImageSummary, 200 |
| /campaigns/{campaignId}/voices/generate | VoiceGenerateRequest; existing submission, projected Job/Voice IDs, 202 |
| /campaigns/{campaignId}/voices/import | VoiceImportRequest + sourcePath; queued source copy/submission; projected operation Job, 202 |
| /campaigns/{campaignId}/voices/{voiceId}/review | queued approve/reject with explicit actor/reason (approval hashes artifacts); operation Job, 202 |
| /campaigns/{campaignId}/heygen/assets/prepare | queued plan_assets + submit_assets; projected operation Job, 202 |
| /campaigns/{campaignId}/heygen/videos/plan | config + maxPaidRenders; queued preview, 202 |
| /campaigns/{campaignId}/heygen/videos/submit | config + maxPaidRenders + approvedBy; queued preflight/reservation, operation Job, 202 |
| /campaigns/{campaignId}/heygen/renders/{renderId}/reconcile | optional videoId + explicit manual-binding confirmation; queued reconciliation, 202 |
| /editing/profiles | create_profile; ProfileView, 201 |
| /editing/profiles/{profileId}/{baseVersion}/versions | create_profile_version; ProfileView, 201 |
| /campaigns/{campaignId}/editing/plans | EditBatchRequest + persist bool; queued plan/dry-run, 202 |
| /campaigns/{campaignId}/jobs/{jobId}/cancel | existing cancel transition; JobSummary, 200 |
| /campaigns/{campaignId}/jobs/{jobId}/resume | existing ordinary resume transition; JobSummary, 200 |
| /campaigns/{campaignId}/worker/start | fixed kind below; explicit local runner admission, 202 |
| /campaigns/{campaignId}/worker/stop | stop claiming additional Jobs, not forced termination; runner state, 200 |

Also GET /campaigns/{campaignId}/operations/{jobId}: typed allowlisted operation result
or pending state, and GET /campaigns/{campaignId}/worker: local runner state. Foreign
campaign IDs return 404. No generic HTTP JobSubmit, arbitrary job_type, script/command
execution, force-resume or blanket queue recovery endpoint.

A 202 envelope contains jobId (or jobIds for an existing batch), campaignId and operation;
worker/start instead returns campaignId, kind and state. It never promises completion.
Worker state is idle/running/stopping with nullable campaignId/kind and static errorCode.
Operation results use a discriminated union: image coverage/import summary, safe asset
summary, voice submission/review summary, video preview/submit summary, render summary or EditBatchPlan. Pending/failed
results have null result; failures expose only a static error code/message. Detailed
results must omit source absolute paths, account/upload IDs, URLs, auth state and payloads.
Existing D4A.1 job responses do not suddenly expose their input/output fields.

## Long operation Jobs and retries

Use fixed typed local-operation job kinds for image preparation/intake, voice import submission/review, HeyGen asset preparation,
video preview/submission/reconciliation and editorial planning. Preserve existing
voice.generate, voice.import, heygen.asset.upload and heygen.video.generate Jobs.
Operation kinds that submit children record their resulting IDs in the existing Job
output; replay reuses the domain service's deterministic idempotency, not a new paid batch.

Image intake dry_run never imports/approves candidates; it only persists its Job/result.
Prepare creates the existing inbox/manifest layout; Rafael places the files there manually,
then explicitly requests dry_run/execute. There is no folder watcher or automatic import.
Editing dry-run never publishes a plan. The persistent queue record is not a violation
of those domain dry-run guarantees. Source/manifest references are revalidated at execution;
changed manifest content fails instead of silently importing a different batch.

Local wrapper submissions derive idempotency from canonical request content, campaign,
mode and referenced manifest content hash. They use manual_only retry safety unless an
existing service explicitly supplies a stronger policy. No generic automatic retry of
local artifact-writing actions. Voice import submission uses a caller-stable requestId
in its wrapper identity; source content hashing and the existing domain idempotency run
in the worker. A new user import uses a new requestId; retry of the same wrapper must
reuse its recorded child IDs, not re-import a changed source. Replacing inputs produces a new request identity.
Native voice/HeyGen Jobs retain their current retry/reconciliation policies.

## Explicit runner lifecycle

One active runner per API process, admitted under a small lock; simultaneous or duplicate
starts return 409 operation_conflict. Use the stdlib executor/thread mechanism, with no
new dependency. Existing HeyGen batch concurrency remains governed by its own config.

Fixed runner kinds: local_operations, voice_generate, voice_import, heygen_assets and
heygen_videos. Each dispatch is restricted to its selected campaign and allowlisted job
types; a voice runner must not claim and block unrelated HeyGen/Flow Jobs.

Startup restores no runner and executes nothing. Submission only queues/reserves;
worker/start is a separate operator action. The runner drains eligible work for its
scope and returns idle when none is presently runnable. Future scheduled retries require
another explicit start once due; no continuously polling scheduler in this MVP.

Stop/shutdown closes admission and stops claiming new work, while the currently executing
operation finishes with its existing heartbeat/checkpoints. Do not kill threads, dispose
engines before active work ends, or cancel a remote render just because the server exits.
An OS/process crash leaves durable Jobs for existing stale-lease/reconciliation behavior.
After restart the operator must explicitly start the runner again; ambiguous remote work
must reconcile before another dispatch. In-memory runner state is not execution evidence.

## Approvals, trusted inputs and errors

Preserve all copy/image/voice approvals, voice QC, paid budgets, asset hash validation,
duplicate protection and manual-binding confirmation. A successful preview or an API
202 is not human approval. Video submit recomputes/validates current prerequisites and
budget; changed inputs may conflict and require a fresh preview. Never reserve beyond
the submitted limit or infer approvedBy from a previous chat/canary.

Only startup settings select project/work/database roots. HTTP source/manifest paths
must be within the configured trusted project tree and pass lexical ancestor, containment,
symlink/junction and filename validation before file operations. Manifest-declared source
roots are checked against the same trust boundary, not accepted as new trusted roots.
No arbitrary roots, media serving, file uploads or delete/overwrite endpoints in this slice.

Writes require application/json. Accept Origin absent for local CLI clients or exactly
the server's loopback origin; reject foreign/null Origins. No wildcard CORS. Future React
development origin is an explicit D4B addition. This is a basic write boundary, not RBAC,
LAN exposure, session authentication or multiuser hardening.

Reuse sanitized D4A.1 error envelopes. Add operation_conflict (409) and
operation_not_allowed (409) for busy/invalid transitions or unsatisfied approval gates.
Invalid request is 422, absent/foreign entity 404, incompatible storage 503, unexpected
failure 500. Expected domain errors map to stable static messages, never exception text.
Worker failures must be visible through persisted Jobs and safe runner/result status.
No raw traceback, provider payload, OAuth token, signed URL or private source path in logs.

## Acceptance and verification

- Existing GET/CLI behavior and historical plans/renders remain compatible.
- Actions persist through existing services and preserve their audit/approval semantics.
- Long actions return 202 without running media/provider code in the request.
- Real mixed-queue tests prove campaign/type isolation and unrelated Jobs unchanged.
- Duplicate submissions/starts, changed manifests, foreign IDs and invalid transitions
  have deterministic outcomes and no duplicate paid dispatch/reservation.
- Dry-run coverage/plans are retrievable without importing assets/publishing plans.
- Fake provider end-to-end flow covers manual intake, voice, asset preparation, video
  reservation/run/poll/download and editorial plan; GET polling observes persisted updates.
- Startup/stop/shutdown/crash cases verify no automatic execution, engine ownership,
  in-flight checkpoint preservation and explicit restart/reconciliation.
- Negative Origin/content-type/path tests perform no writes, provider calls or probes.
- Run focused TDD, full deterministic gate and independent whole-slice review.
  Resolve the known Linux prerequisite first. No paid call is required by this slice.

## Handoff and self-review

This is design only: no production code, new dependencies, workers or migrations have
been introduced. No new IMPLEMENTED/LOCAL_VERIFIED/PROVIDER_VERIFIED claim for D4A.2.
After written-spec approval, create an implementation plan with small coherent tasks;
obtain its approval and execution choice before coding. Do not edit AGENTS.md.

Self-review: scope retains approved worker approach; GET/write engines are separate;
all long route families have Job/result ownership; runner scope/lifecycle, paid boundaries,
dry-run persistence and current constructor/filter incompatibilities are explicit.
No placeholders or implicit provider approval. CI failure remains a recorded prerequisite.
