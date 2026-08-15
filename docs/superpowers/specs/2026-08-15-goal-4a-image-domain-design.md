# Goal 4A — Image Domain & Persistence Design

## Status and boundary

This specification defines the future `Goal 4A — Image Domain & Persistence` implementation. It
does not implement Goal 4A. At this point Goal 4A is neither `IMPLEMENTED` nor
`LOCAL_VERIFIED`; `PROVIDER_VERIFIED` is not applicable.

Goal 4A creates only the durable image domain, SQLite persistence/migration, application service,
Job integration, deterministic local fake handler, CLI contracts, and restart/recovery semantics.
It makes no network or provider calls.

It explicitly excludes Google Flow browser/runtime work, Playwright, browser profiles or login,
Flow DOM locators and Generate dispatch, real downloads, image technical or semantic QC, a provider
canary, HeyGen, rendering, FastAPI, React, and UI. Those capabilities remain in Goals 4B–4D and
later Goals. No generic provider plugin framework, browser state machine, Unit of Work, event bus,
distributed worker, or broad Voice refactor belongs here.

## Existing-code reconciliation

The current repository already has:

- `CampaignRow` and `SceneVariantRow`, where a scene variant has a durable database ID, campaign
  ID, and a campaign-unique `variant_id`;
- a canonical work root from `configured_work_root()` and established artifact paths rooted below
  `work/campaigns/<campaign-id>/`;
- Job persistence that creates a queued Job and its `job.created`/`job.queued` events together,
  including `JobRepository.create_in_session()` for a caller-owned SQLAlchemy session;
- deterministic Job worker claim, attempt, heartbeat, fencing, completion, retry, and blocking
  behavior; and
- Voice code that currently reaches private Job internals for one transactional mutation.

Goal 4A must follow the first four patterns and must not copy the last one. In particular, the
public orchestration seam below is deliberately smaller than a generic transaction abstraction.
The existing `image_generation.py` continues to own compatible request-preparation and trusted
path helpers until a Goal 4A task needs a focused helper; it must not be rewritten wholesale.

## Domain model and state ownership

```text
Campaign
  └── SceneVariant (one environment/scene)
        └── ImageGeneration (one logical attempt; zero or more candidates)
              └── ImageCandidate (one persisted artifact)
```

A Campaign can have many SceneVariants, including different environments for the same character.
The approved-image invariant is per SceneVariant, never per Campaign or character.

State remains owned by one durable entity:

```text
Job.status                        execution and orchestration lifecycle
ImageGeneration.provider_state    logical provider-operation lifecycle
ImageCandidate.review_status      artifact and human-review lifecycle
```

`SceneVariant.status` is not a detailed duplicate of these states. Any future aggregate progress is
derived from the durable entities.

### ImageGeneration

`ImageGeneration` records one logical image-generation attempt, even when it has no candidates.
A durable zero-candidate row is mandatory: future browser execution can dispatch Generate and crash
before downloading an artifact, and that evidence must prevent blind regeneration.

Recommended domain and persistence fields are:

| Field | Recommended type / rule |
| --- | --- |
| `id` | UUID string primary key |
| `campaign_id` | non-null FK to `campaigns.id` |
| `scene_variant_id` | non-null FK to `scene_variants.id`; must belong to `campaign_id` |
| `job_id` | non-null unique FK to `jobs.id` |
| `generation_number` | positive integer, starts at 1 per SceneVariant |
| `idempotency_key` | validated non-empty safe identifier, globally unique for `image.generate` submission |
| `request_fingerprint` | lowercase SHA-256 of canonical stable generation intention |
| `prompt_snapshot` | immutable non-empty prompt text |
| `prompt_sha256` | lowercase SHA-256 of the exact prompt snapshot |
| `reference_image_path` | nullable validated workspace-relative artifact path |
| `reference_image_sha256` | nullable lowercase SHA-256; null iff no reference image is used |
| `provider` | literal `google_flow` |
| `executor` | literal `local_fake` in Goal 4A; future-compatible literal `playwright_python` only |
| `provider_state` | `created`, `queued`, `generating`, `completed`, `failed`, or `blocked` |
| `created_at`, `updated_at` | UTC audit timestamps |
| `dispatched_at`, `completed_at` | nullable UTC timestamps recording provider-operation boundaries |

The request fingerprint is the SHA-256 of canonical JSON with sorted keys, UTF-8 encoding, compact
separators, and at least `sceneVariantId`, `promptSha256`, `referenceImageSha256`, `provider`, and
`executor`. It also includes the generation contract version and any stable fake-output format
version selected by implementation. It excludes `idempotency_key`, timestamps, Job ID, and generation
number so a retry of the same logical submission compares the same intent. The key is still explicit:
a new key can legitimately create a new generation with identical prompt/reference content.

After creation, scene identity, generation number, prompt snapshot/hash, reference path/hash,
provider, executor, and request fingerprint are immutable. A changed prompt or legitimate new
attempt creates a new ImageGeneration rather than mutating an old one.

### ImageCandidate

`ImageCandidate` records a real persisted artifact from exactly one ImageGeneration. Goal 4A does
not define full QC; dimensions, format, byte count, and decodability are mechanical artifact facts.

| Field | Recommended type / rule |
| --- | --- |
| `id` | UUID string primary key |
| `image_generation_id` | non-null FK to `image_generations.id` |
| `candidate_index` | non-negative integer |
| `source_path` | immutable validated workspace-relative artifact path |
| `sha256` | immutable lowercase SHA-256 |
| `width`, `height`, `size_bytes` | positive integers |
| `format` | normalized non-empty format label; Goal 4A fake artifacts use `png` |
| `review_status` | `pending_review`, `approved`, `rejected`, or `superseded` |
| `approved_at`, `approved_by` | both null or both populated; only for approved history |
| `rejected_at`, `rejected_by`, `rejection_reason` | rejection audit values; reason is required on rejection |
| `superseded_at`, `superseded_by_candidate_id` | both null or both populated; superseding candidate is same SceneVariant |
| `created_at`, `updated_at` | UTC audit timestamps |

Candidate metadata and source artifacts are never silently overwritten or automatically deleted.
Rejected and superseded candidates remain available as history. Goal 4A deliberately does not add
`approved_with_known_deviations`, technical-QC status, crops, or semantic scoring.

## Persistence invariants and migration design

Goal 4A adds a dedicated Alembic revision that upgrades both a fresh database and a database at the
current Goals 1–3 head. It must register its SQLAlchemy models with the existing migration metadata.
No database reset is permitted.

Required relational rules are:

- `UNIQUE(scene_variant_id, generation_number)` on `image_generations`;
- `CHECK(generation_number > 0)`;
- `UNIQUE(image_generation_id, candidate_index)` on `image_candidates`;
- `CHECK(candidate_index >= 0)`, positive artifact dimensions/size, and provider/executor/state
  checks appropriate to SQLite;
- foreign keys from generation to campaign, scene variant, and job; from candidate to generation;
- a database trigger on generation insert/update that rejects a SceneVariant whose campaign does
  not match `campaign_id`, following existing `jobs` campaign/SceneVariant trigger semantics;
- indexes for generation lookup by SceneVariant/number and idempotency key, candidate lookup by
  generation/index, and approved-candidate lookup through generation/SceneVariant; and
- database-enforced one-active-approval protection, not merely an application convention.

SQLite’s partial unique index cannot directly span candidates and their generations. The migration
therefore creates `BEFORE INSERT` and `BEFORE UPDATE OF review_status` triggers on
`image_candidates` that, when a row is made `approved`, reject it if another approved candidate
exists through an ImageGeneration for the same SceneVariant. The service also serializes approval
and replacement with `BEGIN IMMEDIATE`, validates ownership in the transaction, and maps integrity
or trigger conflicts to a sanitized domain conflict. This provides database protection against
concurrent connections and transactional service semantics.

Generation numbering is allocated inside the same `BEGIN IMMEDIATE` linked-creation transaction as
the Job. The service reads the current maximum for that SceneVariant and inserts the next positive
number; the unique constraint remains the final race guard. On an idempotency race, it reloads the
existing generation and Job, compares the immutable fingerprint, and either returns both or raises
an idempotency conflict.

## Public linked Job transaction boundary

Goal 4A extends `JobService` with one small public linked-submission operation, named in the
implementation plan after current method naming is reviewed; `submit_linked_job` is the preferred
name. It accepts a validated `JobSubmit` plus a callback that receives the same `Session` and newly
created `JobRow` and returns the linked domain row or domain result.

Its contract is:

1. validate the registered handler, retry safety, Campaign, and SceneVariant references exactly as
   `submit_job` does;
2. acquire the existing SQLite immediate transaction at the repository boundary;
3. reuse the existing Job on matching idempotency key before invoking the callback, or raise the
   existing Job idempotency conflict on a changed Job request;
4. create the Job and its standard audit events using `create_in_session`;
5. create and link the ImageGeneration, including its own required image audit event if the
   implementation uses one; and
6. commit once, or roll back all created Job, Job events, and ImageGeneration state.

The Image service owns image validation, intent fingerprint comparison, generation number, and
image rows; JobService owns Job validation, registration, and its own event semantics. The public
operation returns a typed linked result containing the ImageGeneration and Job so an application
service never reads `JobService._repository`. It must not become a generic Unit of Work or a
multi-domain callback framework. VoiceMaster may adopt this narrow public seam in a future isolated
cleanup, but Goal 4A does not refactor Voice.

There must never be a committed ImageGeneration without its Job, nor a committed linked Job without
its ImageGeneration. A Job is referenced by exactly one ImageGeneration in Goal 4A.

## Application service and idempotency

The focused `images/` package is expected to contain `domain.py`, `db_models.py`, `repository.py`,
`service.py`, and `handler.py`, with `__init__.py` exporting stable public contracts. A `qc.py` is
not introduced unless a future implementation proves a tiny strictly mechanical helper belongs
there.

The service contract is conceptually:

```text
generate(...)                         -> ImageGeneration + Job
get_generation(...), list_generations(...)
get_candidate(...), list_candidates(...)
approve_candidate(...), reject_candidate(...)
replace_approved_candidate(...)
regenerate(...)                       -> new ImageGeneration + new Job
```

`image.generate` requires an explicit idempotency key. With the same key and the same image
fingerprint, `generate` returns the original ImageGeneration and original Job without new rows or
artifacts. The same key with a different fingerprint raises `image_idempotency_conflict`. A new key
always creates a new legitimate attempt, including when its prompt and reference hashes equal a
prior generation. `regenerate` is exactly that new-attempt path: it requires a new key, allocates
the next generation number, creates a new linked Job, and never mutates the preceding generation.

## Fake handler, artifacts, and recovery

Goal 4A registers a deterministic local `image.generate` handler through the ordinary Job handler
registry. It uses the existing worker path—submit, queue, claim, attempt, heartbeat/fencing,
handler, result, and completion/retry/blocking—and creates no bypass.

The handler creates exactly two valid, decodable, distinct PNG artifacts, candidate indexes 0 and
1. It prefers an already declared direct image dependency only if suitable; otherwise it supplies a
small deterministic stdlib PNG encoder/helper. It must never import a transitive image package
without declaring it as a direct dependency. The fake is mechanical infrastructure, not a Flow
visual simulation or quality system.

Artifact paths are workspace-relative in persistence and resolved only from the canonical trusted
work root. The conceptual layout is:

```text
work/campaigns/<campaign-id>/images/<scene-variant-id>/generation-0001/candidate-0000.png
work/campaigns/<campaign-id>/images/<scene-variant-id>/generation-0001/candidate-0001.png
```

The implementation derives the path from durable IDs and validated generation/index values, not raw
user strings. It validates both POSIX and Windows path semantics, resolves all paths, verifies
containment below the trusted root, rejects symlink/junction escape, and stores normalized relative
paths. It creates parent directories safely and never replaces an existing file. Each persisted
candidate stores SHA-256, dimensions, normalized format, and byte count read from the actual file.

The handler is idempotent while running and on retry:

1. If candidate 0 is validly persisted and candidate 1 is absent, retain/validate candidate 0 and
   create only candidate 1.
2. If a deterministic candidate file exists but its database row does not, read its actual metadata
   and hash. Reconcile by creating the row only when it exactly matches the deterministic expected
   artifact for that generation/index. Otherwise set `ImageGeneration.provider_state` to `blocked`,
   return the existing Job-compatible blocked outcome, and preserve the file.
3. If a database candidate row exists but the artifact is missing, do not regenerate it. Mark the
   generation blocked and return a blocked Job outcome with sanitized diagnostics.
4. If an artifact exists with unexpected content or hash, do not overwrite it. Mark blocked and
   require intervention.

`ImageGeneration.provider_state = blocked` describes the domain outcome; it does not replace or
invent a Job status. The handler maps that outcome to the existing `JobExecutionOutcome.BLOCKED`
and current Job state-machine behavior. `failed` is used only for an ordinary provider-operation
failure that existing retry policy permits; Goal 4A does not create browser-specific recovery states.

## Review, approval, and replacement

All review operations first validate the ownership chain Candidate → ImageGeneration →
SceneVariant, and any caller-supplied SceneVariant/Campaign scope must match it. A cross-scene
candidate reference raises `image_candidate_scene_mismatch`.

First approval changes one `pending_review` candidate to `approved` when the SceneVariant has no
active approved candidate. It records actor and timestamp. Directly approving another candidate for
that SceneVariant fails with `image_approved_candidate_exists`; it never silently replaces the old
artifact.

Replacement is the distinct, explicit `replace_approved_candidate(scene_variant_id,
new_candidate_id, approved_by)` operation. In a single immediate transaction it verifies the target
belongs to the SceneVariant, verifies the target is eligible (Goal 4A permits `pending_review` or
`rejected` only when its rejection audit remains preserved), finds the current approved candidate,
changes the old row `approved -> superseded` with the new candidate ID/timestamp, and changes the
new row to `approved` with actor/timestamp. Any validation, trigger, or persistence failure rolls
back both changes. It cannot leave a committed state with an old candidate superseded and no new
candidate approved. It does not call HeyGen, create a paid Job, render, or contact any provider.

Reject changes an eligible non-approved candidate to `rejected` and records actor, timestamp, and
sanitized non-empty reason. An approved candidate must be replaced explicitly rather than rejected
as a shortcut. Superseded history and artifacts remain immutable.

## CLI and error contract

Goal 4A exposes a small deterministic CLI contract for application and test use:

```text
auraly image generate
auraly image generation get
auraly image generation list
auraly image candidate get
auraly image candidate list
auraly image candidate approve
auraly image candidate reject
auraly image candidate replace-approved
auraly image regenerate
```

Commands use the application service, emit structured JSON, and do not optimize a future UI/API.
Public error codes include `image_generation_not_found`, `image_candidate_not_found`,
`image_idempotency_conflict`, `image_candidate_scene_mismatch`,
`image_approved_candidate_exists`, `image_artifact_missing`, `image_artifact_conflict`, and
`image_invalid_transition`. Messages are actionable but never expose secrets, environment values,
private absolute paths, signed URLs, raw tracebacks, browser state, or provider credentials.

## Future test design

The implementation plan must create focused tests before behavior changes, covering at least:

- domain validation: provider/executor literals, minimal provider/review states, positive generation
  numbers and artifact metrics, non-negative candidate index, and SHA validation;
- migration fresh install and upgrade from current Goals 1–3 schema; foreign keys, uniqueness,
  campaign/scene consistency, historical persistence, and restart behavior;
- idempotency: same key/fingerprint returns the original generation and Job, changed fingerprint
  conflicts, a new key creates a new generation, and identical prompt with a new key is allowed;
- linked transaction rollback and success: no orphan Job or ImageGeneration, public JobService API
  only, and standard Job events/fencing remain intact;
- fake handler: exactly two deterministic valid PNGs, distinct candidates, actual SHA/metadata,
  trusted canonical paths, no overwrite, and normal worker orchestration;
- recovery: candidate 0 resume, matching orphan-file reconciliation, missing artifact block,
  unexpected content block, and restart safety; and
- approval: initial approval, second direct approval conflict, explicit atomic replacement,
  superseded history, one active approval per SceneVariant under concurrency, and cross-scene
  rejection.

Implementation runs `uv run python scripts/verify.py fast` during task-level TDD and
`uv run python scripts/verify.py full` before Goal closure. It also requires the Linux full and
Windows focused GitHub Actions jobs to pass. No provider canary is part of Goal 4A.

## Exit criteria

Goal 4A is complete only when all of the following are evidenced by future implementation and
verification:

1. SceneVariant → ImageGeneration → ImageCandidate persists, including zero-candidate generations.
2. Legitimate regeneration creates a distinct generation with safe per-SceneVariant numbering.
3. Idempotency prevents accidental duplicates while allowing identical intent under a new key.
4. ImageGeneration, Job, and required Job audit state are created atomically through public APIs.
5. No new application-service access to private JobRepository internals exists.
6. The fake handler produces exactly two deterministic valid synthetic artifacts with real hashes and
   metadata in trusted non-overwriting paths.
7. All specified restart/recovery cases stop or reconcile safely.
8. At most one active approved candidate exists per SceneVariant; replacement is explicit and atomic.
9. Rejected/superseded history and artifacts remain preserved.
10. Fresh and upgrade migrations succeed without database reset.
11. No external provider, Playwright runtime, Goal 4B/4C/4D work, or unrelated scope is pulled in.
12. Fast and full Verification Harness checks plus Linux full and Windows focused CI pass.

The expected future classification after those conditions is `IMPLEMENTED = yes`,
`LOCAL_VERIFIED = yes`, and `PROVIDER_VERIFIED = not applicable`.
