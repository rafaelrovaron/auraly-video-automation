# D4A.1 — Local Query API & Campaign Status

Date: 2026-10-02.
Status: design direction approved in conversation; written spec awaiting user review.
Base: main at 3409965, with D3B implemented/local verified and Linux/Windows Actions passing.

## Intent and milestone boundary

Rafael needs a simple local interface to see campaigns, approved inputs, downloaded
HeyGen videos and planned editorial variants without assembling information from CLI
commands. This slice supplies that information over HTTP; it does not operate the pipeline.

The user approved splitting D4A into:

1. D4A.1: queries and status, specified here.
2. D4A.2: operational actions and worker integration, a subsequent design/plan.

D4A is complete only after both slices. D4B adds React/approximate preview; D5 adds
rendering. This spec does not claim those future capabilities are delivered or approved
for implementation. Written-spec approval precedes the implementation plan.

Success: a future UI can list campaigns, inspect their inputs/jobs/renders, discover
profiles/plans and display factual next pending steps. GET requests never mutate
domain state, generate media, consume credits or silently migrate a database.

## Selected approach

Use FastAPI as a thin transport over existing Python application/query boundaries.
Keep SQLite/filesystem and the CLI; no subprocess bridge to CLI output, new database,
generic repository framework, event bus or distributed service.

The alternative of exposing all reads/mutations/workers at once is deferred: it would
mix the transport contract with paid dispatch and worker lifecycle. Building the UI first
against synthetic HTTP responses is also rejected: the first API must read real persisted
state. Synthetic fixtures remain legitimate test inputs, not provider evidence.

## Included and excluded

Included: loopback startup command, typed GET routes, OpenAPI/docs, explicit public
response projections, campaign progress/next pending step and readonly storage lifecycle.
FastAPI and Uvicorn are the only new direct runtime dependencies proposed; reuse installed
Pydantic, SQLAlchemy, sqlite3, Typer and HTTPX for contracts/persistence/tests.
Exact dependency constraints/lock changes are verified in the implementation plan.

Excluded: POST/PUT/PATCH/DELETE application routes, uploads, media/file serving, preview,
render/FFmpeg/ffprobe/ASR, OAuth/preflight/reconciliation calls, provider dispatch, worker
startup, automatic migration, new tables, queue changes, authentication/RBAC/LAN access,
WebSockets/SSE, caching and pagination infrastructure. GET metadata is not fresh media QC.
Lists are intentionally complete for personal-scale use; add pagination only when measured
volume requires it. No speculative endpoints for unimplemented final renders.

## Runtime and storage

Proposed command: `auraly api serve --port 8000`, with optional `--project-root`,
`--work-root` and `--database`. Bind is fixed to `127.0.0.1`; no host override, reload
or multiple workers in this MVP. Configuration is resolved once, not from request paths.
Reuse the current environment/defaults for AURALY_PROJECT_ROOT and AURALY_DATABASE_PATH;
WORK defaults to project-root/pipeline/work. No dependency installation in this design step.

Validate lexical root ancestors before canonicalization, reject links/junctions and
unsafe paths, and reuse existing POSIX/Windows safeguards. Project root is the trusted
asset root; work root must be contained in it. The database may be outside that root
(the existing default is under .auraly): its exact server-configured file and safe
ancestors are trusted separately. HTTP clients cannot choose a database or filesystem root.

Database must already exist as a regular file and have the current Alembic revision and
required tables. Startup fails with a sanitized configuration/storage error if absent,
unreadable or incompatible; it does not create a DB, directory or migration lock.
No `for_database()` service shortcuts: they migrate. No existing write-engine factory:
it creates directories and configures journal mode. Add only the small readonly engine
factory needed in the existing persistence boundary, using a sqlite3 URI with mode=ro,
correct escaping for spaces/#/? and thread-compatible connections. Do not set journal
mode or synchronous mode, or use immutable=1 against an active WAL database.

Create readonly query dependencies in the app lifespan and dispose the engine at shutdown.
Use public constructors/read methods: CampaignService, ImageService, VoiceMasterService
and JobService with an explicitly empty handler registry where supported. Passive handler
objects created by an existing constructor must never execute/load ASR models or cause
network/authentication/filesystem writes. HeyGenVideoRepository already supplies public
typed get/list reads without requiring a provider; reuse those inside the application
query boundary instead of constructing a fake or real provider for a GET.
If a constructor cannot satisfy this lifecycle, make the smallest public read-construction
adjustment in its owning domain; do not access private service/repository internals.

SQLite logical contents and domain/media/profile/plan files must remain unchanged.
SQLite may maintain its own WAL/SHM coordination when reading a live database; do not
claim byte-identical auxiliary files or remove them. Missing editing directories mean
empty lists; a present corrupt artifact is an error, not an empty result.

## Components and data flow

`GET -> typed route -> application query service -> existing public reads -> public DTO`.

Routes perform parameter validation and HTTP mapping only. The application query service
coordinates reads, campaign ownership, explicit projection and status aggregation.
It does not duplicate approval/QC/idempotency/business mutations. Status aggregation is
presentation logic implemented once there, shared by list/detail/status responses.
Editing queries reuse EditingService and EditBatchService integrity-checked reads.

A small public `EditBatchService.list_plans(campaign_id)` may be added to discover canonical
plans under campaigns/<campaign>/editing/plans/<video>/<hash>/plan.json. Validate each with
get_plan; do not raw-dump JSON, recursively scan arbitrary roots or silently skip corruption.
Sort by videoId then planHash; use all verified persisted snapshots, without inventing an
active/latest plan because plans have no creation timestamp. No redundant manifest scan:
each D3B plan already embeds its manifests/output variants.

Independent queries are eventually consistent while existing CLI workers run. Do not promise
a cross-domain/filesystem atomic snapshot or derive a paid-action authorization from status.
Every DTO represents valid observed records; client polling refreshes the view.

## HTTP contract

Prefix application routes with `/api/v1`. GET returns HTTP 200; collections use
`{"items": [...]}`; item routes return their typed DTO directly. CamelCase follows
existing contracts. Stable ordering: campaigns by campaignId; per-domain lists by stable
ID (voice versions/generations may be secondary metadata); profiles by profileId/version;
plans by videoId/planHash. No estimated costs, invented percent-complete or finish times.

| Route | Result |
|---|---|
| /health | status=ok and apiVersion=1; no provider checks or private configuration |
| /api/v1/campaigns | campaign summaries, scene counts and operational status |
| /api/v1/campaigns/{campaignId} | campaign detail, copy versions and scene definitions |
| /api/v1/campaigns/{campaignId}/status | aggregate facts and nextPending |
| /api/v1/campaigns/{campaignId}/images | candidates grouped by sceneVariantId |
| /api/v1/campaigns/{campaignId}/voices | Voice Master summaries/history |
| /api/v1/campaigns/{campaignId}/heygen/renders | local render summaries and source refs |
| /api/v1/campaigns/{campaignId}/jobs | job summaries with status/error code |
| /api/v1/campaigns/{campaignId}/jobs/{jobId} | same public summary, enforcing ownership |
| /api/v1/editing/profiles | verified profiles plus profileHash |
| /api/v1/editing/profiles/{profileId}/{version} | verified profile plus profileHash |
| /api/v1/campaigns/{campaignId}/editing/plans | verified plan summaries/output variants |
| /api/v1/campaigns/{campaignId}/editing/plans/{videoId}/{planHash} | verified EditBatchPlan |

OpenAPI at /openapi.json and interactive documentation at /docs. No CORS middleware for
this API-only slice; D4B will allow its exact local development origin, never a wildcard.
Accept only localhost/127.0.0.1 Host headers; tests use the same loopback base URL.

Public allowlists, not a generic model_dump of SQL/provider/job records:

- Campaign summary: campaignId, character, storedStatus, sceneCount, createdAt,
  updatedAt, operationalStatus and nextPending. Detail adds proofObject, presets,
  typed copy versions/approval/hash/spokenText and typed scene definitions/prompts;
  excludes arbitrary config/budget JSON and internal filesystem paths.
- Image: candidate ID, scene ID, sourceKind, generation ID, reviewStatus, content hash,
  dimensions/format/size and approval/rejection metadata. Exclude importSourcePath;
  optional sourcePath is the stored work-relative path only.
- Voice: ID, campaign/copy ID/version, generation, origin/provider, status, processed
  SHA/duration/relative path, transcript match/headlineSpoken, QC findings and explicit
  approval/rejection/review-reason metadata. No raw provider payloads or transcripts.
- HeyGen: local render ID, campaign/scene/image/voice/job IDs, status, remoteVideoId,
  manualBinding, input content hashes, source metadata if present, errorCode and timestamps.
  Omit accountRef, upload asset IDs, callbacks, signed URLs and raw item JSON.
- Job: jobId/type, campaign/scene IDs, status, attemptCount/maxAttempts, retrySafety,
  timestamps and lastErrorCode. Omit input/output, events, attempts, worker/lease details
  and arbitrary stored error text; map known codes to static human messages if displayed.
- Profile: existing verified EditProfile plus hash, preserving asset paths as project-relative.
- Plan summary: videoId, renderId, planHash, outputCount, maxOutputs, timingStatus,
  copyRef/voiceRef/source and outputs with key/label/IDs/hashes/filename/captionState.
  Plan detail: verified existing EditBatchPlan, unchanged schema and aliases.

Secrets, cookies, OAuth tokens, signed URLs and absolute private paths must not appear in
responses/logs/errors/OpenAPI examples. Stored unsafe relative paths or invalid contracts
fail safely rather than being normalized into apparent success. Text copy/prompts are
intentional creative content; HTML rendering/escaping belongs to the future UI.

## Campaign status: factual progress, not authorization

Return counts and per-scene observations for approved copy/voice/images, current render
states and persisted editing plans. Keep stored Campaign.status separate; it is not a
reliable computed pipeline status. Profile existence is global configuration, not evidence
that a campaign has an edit plan. Captions disabled do not require timing.

operationalStatus is one of needs_input, needs_review, in_progress, needs_attention,
ready_for_editing or editing_planned. These labels stop before final rendering/delivery.
nextPending is null or {code, stage, entityId, message}; static human message by code.
Present the earliest pending item using the following deterministic order:

1. Current unresolved relevant job/render failure or reconciliation -> needs_attention,
   code attention_required. Only currently linked actionable records count; historical
   failed jobs and paused Google Flow work do not override successful replacement assets.
2. Active relevant job/render -> in_progress, code wait_for_job.
3. For a scene with a ready render, inspect plans referencing that exact render/source.
   No plan -> ready_for_editing, code editing_plan_missing. A planned enabled-caption
   output with missing timing -> needs_input, code caption_timing_missing.
   Otherwise editing_planned, code renderer_not_implemented (informational next stage).
4. For scenes without a ready render: no approved copy -> copy_approval_missing;
   no approved voice -> voice_review_required if a reviewable voice exists, otherwise
   voice_missing; no approved image -> image_review_required if reviewable candidates
   exist, otherwise image_missing; remaining scene -> heygen_render_missing.

Within the same priority use scene variant key then entity ID; return all per-scene pending
items so one nextPending does not hide parallel work. Aggregate prefers attention, then
in_progress, then needs_review, then needs_input, then ready_for_editing, then editing_planned; nextPending
must correspond to the chosen aggregate state (not just the first returned scene).
Input/review state follows the selected missing/review code. A campaign with multiple
scenes may have ready videos and incomplete scenes simultaneously; expose both facts.

Ready renders retain their pinned copy/voice/image history. A newer draft copy, superseded
image or replacement voice cannot invalidate the editing readiness of an existing ready
render. Unrendered scenes use the current approved voice and its linked approved copy;
without such a voice, the highest approved copy is the informational prerequisite.
Jobs related to retired entities are historical. Persisted plan validity does not mean
media was freshly hashed or approved by a human. Status never calls providers, probes
media, synthesizes caption timing, automatically resumes anything or grants paid authority.

## Validation and errors

Reuse ID/UUID/hash/version validators at routes and existing contract validation on reads.
Validate campaign existence on all campaign-scoped routes; unknown campaign/entity -> 404.
A job belonging to another campaign is also 404, even if its global ID exists.
Unknown keys/query parameters are rejected with 422 rather than silently ignored.
Application mutations on listed resources return 405. No request-supplied path resolution.

All errors use `{error: {code, message, field}}`, with field nullable and whitelisted.
422 invalid_request, 404 not_found, 409 artifact_invalid (stored profile/plan checksum,
identity or semantic corruption), 503 storage_unavailable (DB missing/locked/incompatible
at runtime), 500 internal_error (unexpected failure). Messages are static/sanitized;
never include exception repr, validation input values, SQL or raw storage paths.
Override default FastAPI validation/HTTP error bodies to preserve this contract.
Do not catch programmer failures and present an empty successful list.

## Tests and acceptance

TDD and existing fast/full harness; add API tests and deterministic OpenAPI validation
without changing D3A/D3B schemas. Verify locked FastAPI/Uvicorn installation and platform
compatibility. No browser UI, paid/provider test or real render required in this slice.

- TestClient exercises actual query services against existing migrated fixture DBs;
  fixture setup migrations occur before readonly app startup, never inside requests.
- GET lists/details/status reflect persisted records and updates between requests;
  repeated reads do not change logical SQL, jobs/IDs or domain file SHA/mtime.
- Empty valid DB returns empty campaigns; absent profile directories return empty profiles
  and an existing campaign without plans returns empty plans. Missing/incompatible DB fails
  startup, without creating files/locks or invoking migrations.
- All campaign-scoped lookups enforce ownership; invalid IDs/hash/version, traversal,
  path inputs and unknown query keys fail without leaking raw values.
- Roots/ancestors links and junctions, DB URI special characters and SQLite write refusal
  have cross-platform regression tests (skip only OS-disallowed link creation).
- Zero provider/auth/worker/probe/ASR calls, including constructors that could load models.
- Profile/plan reads use existing integrity checks; corrupt/rehashed invalid snapshots
  return sanitized errors and are not repaired, hidden or overwritten.
- Mixed scene progress, active vs historical failures, manually bound ready renders,
  new draft copy alongside pinned old ready render, captions disabled/missing timing,
  multiple plans per source and paused Flow jobs have explicit status cases.
- Response allowlists/error serialization prevent account/auth/signed URL/raw job payload
  and private path leakage; OpenAPI declares real response/error models.
- Full gate passes; independent review addresses relevant findings before LOCAL_VERIFIED.
- Optional readonly real-canary HTTP smoke compares SQL/media before/after and consumes
  zero credits. No new PROVIDER_VERIFIED claim and no approval changes.

## Handoff and deferred work

Implement only D4A.1 after this written spec and its implementation plan are approved.
D4A.2 gets a separate spec for mutations, job submission/worker lifecycle, paid approval
and any long-running local import/edit validation. Do not introduce a generic HTTP
run-any-job endpoint or FastAPI BackgroundTasks as a replacement for durable jobs here.
No roadmap status is changed to delivered merely by committing this design.

Self-review: no placeholders; first slice is reads only; readonly lifecycle avoids existing
migration/provider shortcuts; historical identities survive status projection; D4A completion
and D4A.2 design remain explicitly separate. Plan/spec approval gates remain intact.
