# Auraly Codex Goal Roadmap

**Purpose:** sequence narrow, independently verifiable implementation Goals for Codex.

This is not a second PRD. Durable decisions live in `PROJECT-MEMORY.md`; target requirements
live in `PRD-MVP-MASS-VIDEO-AUTOMATION.md`; repository instructions live in `../AGENTS.md`.
Every Goal must remain inside its stated boundary and leave the repository green.

## Execution model

From Goal 4 onward, significant subgoals use this sequence:

```text
design/spec
→ user review
→ implementation plan
→ small independently testable tasks
→ TDD
→ small task-level commits
→ full verification
→ independent review
```

Approved specs live under `docs/superpowers/specs/`; plans live under
`docs/superpowers/plans/`. A task is an independently reviewable deliverable, not an individual
line of code. Prefer the cycle: failing focused test, confirmed expected failure, minimal
implementation, focused verification, then commit. Run the full applicable baseline before
closing a subgoal.

## Verification terminology and current status

- `IMPLEMENTED`: required production code and tests exist.
- `LOCAL_VERIFIED`: the required deterministic/local verification baseline executed
  successfully; no real external provider is implied.
- `PROVIDER_VERIFIED`: an explicitly approved real provider canary completed successfully.

Provider verification is never inferred from mocks, local tests, or a commit name containing
`[verified]`. No independent GitHub verification is claimed without separate evidence.

| Goal | IMPLEMENTED | LOCAL_VERIFIED | PROVIDER_VERIFIED |
| --- | --- | --- | --- |
| 0 — Repository Alignment | yes | yes | not applicable |
| 1 — Campaign Foundation | yes | yes | not applicable |
| 2 — Persistent Job Orchestration | yes | yes | not applicable |
| 3 — Voice Master | yes | yes | pending/unproven |
| 3C — ElevenLabs Provider Canary | pending | pending | pending |

## Resulting sequence

```text
Goal 0   Repository Alignment                         IMPLEMENTED / LOCAL_VERIFIED
Goal 1   Campaign Foundation                          IMPLEMENTED / LOCAL_VERIFIED
Goal 2   Persistent Job Orchestration                 IMPLEMENTED / LOCAL_VERIFIED
Goal 3   Voice Master                                 IMPLEMENTED / LOCAL_VERIFIED
Goal 3C  ElevenLabs Provider Canary                   PENDING
Goal 4A  Image Domain & Persistence
Goal 4B  Google Flow Browser Runtime
Goal 4C  Flow Generation, Download & Recovery
Goal 4D  Image QC, Review & Provider Canary
Goal 5A  HeyGen Preflight & Asset Upload
Goal 5B  Avatar Look & Avatar III Verification
Goal 5C  Video Generation, Polling & Source QC
Goal 5D  HeyGen Provider Canary
Goal 6A  Edit Manifest & Captions
Goal 6B  Deterministic Rendering
Goal 6C  Final QC & Delivery
Goal 6.5 Approval Lifecycle Hardening
Goal 7   End-to-End Canary
Goal 8   Local API/UI
```

This decomposition changes implementation granularity, not product architecture or sequence.

## Common verification baseline

Unless a Goal explicitly adds another check, run:

```bash
uv sync --locked --all-groups
uv run pytest
uv run ruff check src tests
uv run python -m mypy src
uv run python -m auraly_pipeline.schema
uv run python -m auraly_pipeline.cli export-image-generation-schema \
  --output schemas/image-generation.schema.json
uv pip check
npm ci
npm run hf:doctor
npm audit --omit=dev --audit-level=high
git diff --check
```

Type-check tests with `MYPYPATH=src uv run python -m mypy tests` on POSIX. On Windows
PowerShell, set `$env:MYPYPATH = "src"` and then run `uv run python -m mypy tests`.

External provider canaries require explicit approval and must never expose credentials or repeat
paid actions blindly.

### Verification Harness (implemented infrastructure)

Before Goal 4A design, `scripts/verify.py` provides:

```bash
uv run python scripts/verify.py fast
uv run python scripts/verify.py fast --pytest tests/test_verify_harness.py
uv run python scripts/verify.py full
```

`fast` runs low-cost Ruff/mypy checks and only the pytest targets explicitly supplied. `full` runs
the complete deterministic `AGENTS.md` baseline, including cross-platform test mypy and generated
schema drift detection. `.github/workflows/verify.yml` provides one Linux full job and one focused
Windows job. Local or CI evidence never implies provider verification, and the workflow invokes no
ElevenLabs, Google Flow, HeyGen, or other paid provider.

---

## Goal 0 — Repository Alignment

### Objective

Establish one internally consistent source of truth before feature implementation.

### Included

- Google Flow + Playwright Python as the sole image path;
- removal of active Google AI Studio assumptions;
- preservation of generic image-workflow security controls;
- latest compatible stable HyperFrames pinned and validated;
- truthful implemented/partial/planned status;
- `AGENTS.md` and this Goal roadmap.

### Explicitly excluded

- Campaign persistence, orchestration, provider runtimes, editing pipeline, API, and UI.

### Dependencies

None.

### Exit criteria

- architecture and documentation agree;
- Google Flow is the only active image architecture;
- HyperFrames is pinned to the locally validated stable release;
- browser runtime limitations are explicit;
- `AGENTS.md` is usable by Codex;
- complete existing suite is green;
- independent diff review has no blocking findings.

### Verification

Run the common baseline plus:

```bash
npm run hf:lint -- work/install-smoke/hyperframes
npm run hf:check -- --strict work/install-smoke/hyperframes
npm run hf:render -- work/install-smoke/hyperframes \
  --output work/install-smoke/hyperframes-canary.mp4 --quality draft
ffprobe -v error -show_entries stream=codec_name,width,height,r_frame_rate \
  -show_entries format=duration,size -of json work/install-smoke/hyperframes-canary.mp4
```

The smoke composition is local/ignored; if absent, create an ignored minimal canary without
adding media or generated output to Git.

---

## Goal 1 — Campaign Foundation

### Objective

Persist the core campaign domain and expose deterministic CLI operations that survive restart.

### Included

- `Campaign`, `CopyMaster`, and `SceneVariant` contracts;
- SQLite, SQLAlchemy 2, Alembic, repositories, and application service;
- CLI create/get/list;
- migrations, uniqueness rules, timestamps, and restart persistence tests.

### Explicitly excluded

- job queue/state machine;
- ElevenLabs, Google Flow runtime, HeyGen, rendering, FastAPI, and frontend;
- any paid or external provider call.

### Dependencies

Goal 0 complete.

### Exit criteria

- migrations create a fresh database;
- create/get/list operate through one application-service layer;
- duplicate and invalid campaign inputs fail safely;
- approved domain invariants are Pydantic-validated;
- campaign data remains available after process restart;
- CLI JSON output is tested and contains no secrets.

### Verification

Run the common baseline plus Goal 1 migration and persistence tests, for example:

```bash
uv run alembic upgrade head
uv run pytest tests/test_campaigns.py tests/test_campaign_domain.py tests/test_cli.py \
  tests/test_migrations.py
```

---

## Goal 2 — Persistent Job Orchestration

### Objective

Provide durable, resumable, idempotent execution without integrating external providers.

### Included

- `Job` model, explicit state machine, attempts, events/audit trail;
- persistent local queue, cross-process migration lock, worker lease semantics, and attempt fencing;
- idempotency keys and duplicate protection;
- persisted retry-safety policy, resume, cancellation, retryable/terminal failure states;
- deterministic fake handlers and restart/crash recovery tests.

### Explicitly excluded

- ElevenLabs, Google Flow, HeyGen, final editing, API, and UI;
- real network or paid operations.

### Dependencies

Goals 0–1.

### Exit criteria

- invalid transitions are rejected;
- duplicate submission does not duplicate work;
- interrupted jobs resume from persisted state;
- attempts/events are auditable;
- retries cannot silently repeat a non-idempotent action.

### Verification

Run the common baseline plus:

```bash
uv run pytest tests/test_jobs.py tests/test_job_state_machine.py tests/test_job_handlers.py \
  tests/test_job_service.py tests/test_job_concurrency.py tests/test_job_migrations.py \
  tests/test_migration_lock.py tests/test_cli.py tests/test_migrations.py
```

---

## Goal 3 — Voice Master

### Objective

Create, validate, and approve one reusable Voice Master through the official ElevenLabs API.

### Included

- ElevenLabs API adapter and secret boundary;
- idempotent request/reconciliation behavior;
- raw preservation and non-destructive audio processing;
- transcript comparison, duration, WPM, LUFS, true peak, and format checks;
- approval/rejection and reusable audio-asset metadata;
- fake-server tests and deterministic/local verification.

### Explicitly excluded

- ElevenLabs web automation;
- Google Flow, HeyGen, final rendering, API, and UI.

### Dependencies

Goals 0–2.

### Exit criteria

- one approved Voice Master can be reused by SceneVariants;
- headline/directions are absent from narration;
- raw and processed assets are immutable/versioned;
- provider retries are reconciled and cost-safe.

### Verification

Run the common baseline plus:

```bash
uv run pytest tests/test_elevenlabs_provider.py tests/test_voice_domain.py \
  tests/test_voice_service.py tests/test_voice_audio.py tests/test_voice_retry_safety.py
ffmpeg -v error -i <ignored-approved-voice> -f null -
ffprobe -v error -show_streams -show_format -of json <ignored-approved-voice>
```

A real ElevenLabs canary belongs to Goal 3C and is not implied by this local verification.

---

## Goal 3C — ElevenLabs Provider Canary

### Objective

Prove the implemented Goal 3 path with one explicitly approved real ElevenLabs request.

### Included

- operator-approved budget and configured secret outside Git;
- one real request through the official API path;
- artifact inspection and sanitized canary evidence;
- reconciliation and duplicate/cost review.

### Explicitly excluded

- ElevenLabs web automation;
- feature development unrelated to canary findings;
- automatic or unapproved paid calls.

### Dependencies

Goal 3 `IMPLEMENTED` and `LOCAL_VERIFIED`.

### Exit criteria

- a real Voice Master artifact completes the Goal 3 technical and human gates;
- evidence contains no API key, signed URL, or private artifact;
- no blind duplicate paid request occurs;
- the durable Job/event and Voice Master manifest/QC records identify the canary without storing
  credentials, signed URLs, or private media in Git;
- Goal 3C becomes `IMPLEMENTED / LOCAL_VERIFIED / PROVIDER_VERIFIED`, and its evidence establishes
  Goal 3 `PROVIDER_VERIFIED`.

### Scheduling and verification

Goal 3C does not block Goal 4 development. It may run before Goal 5 or at another appropriate
integration checkpoint, but must complete before the end-to-end pipeline depends on a real Voice
Master. Run the Goal 3 baseline and real canary only after explicit operator approval.

---

## Goal 4A — Image Domain & Persistence

### Objective

Create durable campaign image-generation state and candidate history without browser execution.

### Included

- intentional `SceneVariant -> ImageGeneration -> 0..N ImageCandidate` model;
- logical generation number, Campaign/SceneVariant/Job links, prompt snapshot/hash, reference
  path/hash, provider/executor, provider state, dispatch timestamp, and audit timestamps;
- per-downloaded-candidate path, SHA-256, dimensions, format, size, technical QC state, review
  state, and approval/rejection metadata;
- persistence invariants, migrations, repository/application service, CLI, and restart/security
  regression coverage;
- deterministic fake image Job handler where needed;
- public orchestration/transaction contract when atomic domain entity + Job + audit creation
  requires it.

### Design rules

`ImageGeneration` is the logical Flow operation; `ImageCandidate` is a resulting artifact. A
generation must remain durable when Generate was dispatched but a browser crash prevented any
candidate from being persisted, because blind regeneration is unsafe.

State ownership remains separate:

```text
Job.status                       = execution/orchestration state
ImageGeneration.provider_state  = Google Flow operation state
ImageCandidate.review_status    = artifact/review state
```

Do not turn `SceneVariant.status` into a second detailed source of truth. A later global progress
view should preferably derive from persisted entities. Do not duplicate current private Job
repository access; the exact public coordination contract belongs to the Goal 4A design.

Introduce focused `images/` modules as required. Reuse compatible `image_generation.py` code
incrementally; no big-bang rewrite.

### Explicitly excluded

- Playwright, browser launch, Flow selectors, login, or real generation;
- image QC implementation or provider canary;
- HeyGen, rendering, API, and UI.

### Dependencies

Goals 0–2 and approved Goal 4A design/plan. Goal 3C is not a dependency.

### Exit criteria

- generation state survives restart with zero candidates;
- every intentionally persisted download has a distinct non-overwriting candidate record;
- persistence constraints prevent ambiguous duplicate generation/candidate history;
- fake/local job integration preserves idempotency, fencing, and audit invariants;
- no new application-service dependency on private Job repository internals is added.

### Suggested task sequence

```text
Task 1 — Image domain
Task 2 — DB models / migration
Task 3 — persistence invariants
Task 4 — application service
Task 5 — job integration
Task 6 — CLI
Task 7 — restart/security regression
Task 8 — full verification
```

Each task uses the TDD cycle and a small independently reviewable commit.

---

## Goal 4B — Google Flow Browser Runtime

### Objective

Prove safe browser interaction and preflight without performing a complete generation lifecycle.

### Included

- dedicated persistent Chromium profile outside Git and manual login;
- browser launch, Flow navigation, authentication detection, and UI verification;
- centralized semantic locator contract;
- local single-browser lock and concurrency 1;
- sanitized diagnostic screenshot, Playwright trace, and stop-safe
  `human_intervention_required`.

### Explicitly excluded

- Generate dispatch, candidate download, 2K finalization, or real generation;
- blind coordinate clicks and the personal main Chrome profile;
- Google AI Studio or another image provider.

### Dependencies

Goal 4A and approved Goal 4B design/plan.

### Exit criteria

The application can safely answer: Can Flow launch? Is the operator authenticated? Is the UI
understood? Can execution safely continue? Unknown state stops without semantic-locator fallback
to coordinates, and profile/session artifacts remain outside Git.

### Verification

Run the common baseline plus Goal-created local browser/preflight tests and:

```bash
uv run playwright install --dry-run chromium
```

No real generation is required.

---

## Goal 4C — Flow Generation, Download & Recovery

### Objective

Connect the durable image domain to the verified browser runtime for resumable generation and
download.

### Included

- reference upload and verification;
- persisted prompt insertion and verification;
- Generate dispatch with provider state persisted at the safety boundary;
- candidate slot/state detection and screenshot/grid evidence;
- required-candidate selection, 2K request, deterministic download correlation, and artifact
  ingestion;
- restart/resume and ambiguous post-dispatch recovery.

P0 does not require downloading every visible candidate. Every intentionally downloaded
candidate must be preserved, get its own `ImageCandidate`, and never overwrite another.

### Explicitly excluded

- blind resubmission after ambiguous post-dispatch failure;
- image semantic approval or provider canary;
- requirement to download all visible candidates unless mechanically necessary.

### Dependencies

Goals 4A–4B and approved Goal 4C design/plan.

### Exit criteria

- upload/prompt/dispatch verification is evidence-backed;
- a crash after dispatch cannot authorize blind second generation;
- downloads correlate deterministically and ingest non-destructively;
- restart recovers or stops for intervention without losing generation evidence.

### Verification

Run the common baseline plus focused fake/local Flow generation, download, and recovery tests.

---

## Goal 4D — Image QC, Review & Provider Canary

### Objective

Validate image artifacts, provide durable review history, and prove one approved real Flow
generation.

### Included

- format, dimensions, 9:16, required 2K, corruption/decode, and SHA-256 checks;
- approve, reject, regenerate, and `approved_for_scene_variant` lifecycle;
- preservation of approved/rejected and downloaded-candidate history;
- one explicitly approved single-scene Google Flow provider canary.

Semantic and creative review remains human/AI.

### Explicitly excluded

- autonomous semantic approval;
- automatic multi-scene paid generation;
- downloading every visible candidate as a P0 requirement.

### Dependencies

Goals 4A–4C and approved Goal 4D design/plan.

### Exit criteria

- 1K, unverified, corrupt, or wrong-aspect artifacts cannot satisfy the 2K image gate;
- every downloaded candidate has independent durable QC/review state;
- approved/rejected history is immutable and restart-safe;
- one real canary completes only with explicit operator/budget approval and sanitized evidence.

### Verification

Run the common baseline plus focused image QC/review tests. The approved canary must verify 2K
dimensions, screenshot/grid evidence, non-overwriting candidate history, recovery behavior, and
absence of browser profile/cookies/secrets from Git.

---

## Goal 5A — HeyGen Preflight & Asset Upload

### Objective

Verify official MCP/OAuth capabilities and complete durable, cost-safe asset upload.

### Included

- sanitized MCP/OAuth preflight and capability checks;
- reusable audio/image asset upload lifecycle;
- exact preservation of signed upload headers;
- durable remote IDs before advancing;
- idempotency and ambiguous-request reconciliation.

### Explicitly excluded

- HeyGen web automation;
- avatar look creation, video generation, final editing, API, and UI;
- real paid canary.

### Dependencies

Goal 4D, an approved Voice Master, and approved Goal 5A design/plan. Goal 3C must complete before
this sequence relies on a real Voice Master, but it does not block local/fake design work.

### Exit criteria

- tokens and signed URLs never reach persistence/logs;
- signed upload headers are used exactly and duplicate-safe;
- remote asset IDs persist before dependent operations.

---

## Goal 5B — Avatar Look & Avatar III Verification

### Objective

Create a durable photo-avatar look and prove it supports Avatar III before video creation.

### Included

- photo-avatar look creation and polling;
- durable look/provider states and remote IDs;
- `supported_api_engines` verification;
- explicit stop when `avatar_iii` is unavailable.

### Explicitly excluded

- silent engine fallback, video generation, source QC, and provider canary.

### Dependencies

Goal 5A and approved Goal 5B design/plan.

### Exit criteria

- look creation/polling is restart-safe and duplicate-protected;
- Avatar III support is verified before any video request.

---

## Goal 5C — Video Generation, Polling & Source QC

### Objective

Create, retrieve, and technically validate durable Avatar III source videos.

### Included

- explicit `engine.type = avatar_iii` request;
- durable dispatch state/remote ID before polling;
- idempotency, ambiguous-response reconciliation, download, and source-video QC;
- fake MCP coverage for restart and failure handling.

### Explicitly excluded

- provider canary, final editing, API, and UI.

### Dependencies

Goal 5B and approved Goal 5C design/plan.

### Exit criteria

- ambiguous paid requests are reconciled rather than blindly repeated;
- downloaded source passes format, decode, duration, audio, and visual technical checks;
- provider state survives restart without duplicate video creation.

---

## Goal 5D — HeyGen Provider Canary

### Objective

Prove the Goal 5 path with one explicitly approved real Avatar III request.

### Included

- real MCP/OAuth preflight, asset/look/video lifecycle, download, and source QC;
- budget approval, duplicate review, and sanitized evidence;
- explicit human review with approve/reject evidence for the first campaign source-video canary.

### Explicitly excluded

- HeyGen web automation and automatic batch generation.

### Dependencies

Goals 3C and 5A–5C.

### Exit criteria

- one approved canary downloads and passes source QC;
- Avatar III use and durable remote IDs are evidenced;
- no credential, signed URL, private artifact, or blind duplicate is persisted;
- later campaign video generation remains blocked until the first source-video canary is human
  approved.

### Verification

Run the common baseline plus Goal 5 fake/local tests. With explicit approval only, run full
ffmpeg/ffprobe inspection against the ignored canary artifact. Real MCP/OAuth and paid actions
are never part of deterministic CI.

---

## Goal 6A — Edit Manifest & Captions

### Objective

Create the versioned renderer-neutral edit contract and deterministic captions from approved
spoken copy.

### Included

- immutable/versioned edit manifest;
- headline as visual text only;
- caption text/timing and safe-zone validation;
- input hashes and deterministic editorial parameters.

### Explicitly excluded

- rendering, provider generation, publishing, API, and UI.

### Dependencies

Goal 5D and approved source assets.

### Exit criteria

- headline cannot enter narration;
- caption text derives from approved spoken copy and respects safe zones;
- manifest validation and hashes are deterministic.

---

## Goal 6B — Deterministic Rendering

### Objective

Render an immutable 1080×1920 Reel deterministically from approved inputs and manifest.

### Included

- subtle zoom, approved music, captions, headline, and renderer adapter;
- H.264/AAC 1080×1920 master and non-destructive artifact handling;
- deterministic audio mix including `amix ... normalize=0`.

### Explicitly excluded

- final delivery, publishing/social APIs, FastAPI, and React UI.

### Dependencies

Goal 6A.

### Exit criteria

- same inputs/manifest render functionally equivalent output;
- reruns never overwrite an immutable final;
- composition-specific lint/check/draft-render passes when HyperFrames is selected.

---

## Goal 6C — Final QC & Delivery

### Objective

Technically validate immutable final artifacts and deliver them non-destructively.

### Included

- full decode, resolution, FPS, codec, duration, loudness, true peak, and hash checks;
- proxy/contact-sheet/QC report as required;
- durable final review lifecycle `review_required -> approved | rejected`, including operator,
  comment/reason, and timestamps;
- verified copy to the local synchronized delivery folder only after human approval.

### Explicitly excluded

- claim of cloud upload without sync/API evidence;
- automatic social publishing, API, and UI.

### Dependencies

Goal 6B.

### Exit criteria

- final full-decode, FPS, loudness, true peak, and duration checks pass;
- rejected or not-yet-approved renders cannot be delivered;
- master and delivery copy have verified size/SHA-256;
- immutable artifacts are not overwritten.

### Verification

Run the common baseline plus focused edit/caption/renderer/final-QC tests and full ffmpeg/ffprobe
inspection of ignored final artifacts. If HyperFrames is selected, run its composition-specific
lint/check/draft render required by `AGENTS.md`.

---

## Goal 6.5 — Approval Lifecycle Hardening

### Objective

Ensure the end-to-end canary exercises real application approval workflows rather than inferred
approval state.

### Included

- CopyMaster lifecycle `draft -> human review -> approved`;
- approval-gate audit and regression coverage needed before Goal 7.

### Explicitly excluded

- Goal 4 image work, broad domain redesign, provider calls, API, and UI.

### Dependencies

Goals 1–6C.

### Exit criteria

- CopyMaster no longer effectively starts approved;
- Goal 7 cannot bypass copy, voice, image, canary, or final-video approval gates.

---

## Goal 7 — End-to-End Canary

### Objective

Prove the CLI pipeline with one campaign and three resumable SceneVariants.

### Included

```text
1 Copy Master
→ 1 Voice Master
→ 3 SceneVariants
→ 3 approved Flow images
→ 3 Avatar III HeyGen videos
→ 3 final renders
```

Also included: one simulated interruption/resume, audit review, cost/duplicate review, and final
artifact QC.

### Explicitly excluded

- FastAPI, React UI, automatic publishing, performance optimization, and distributed workers.

### Dependencies

Goals 3C, 4D, 5D, 6C, and 6.5 (and their prerequisite subgoals).

### Exit criteria

- all three variants complete with required approvals and immutable artifacts;
- simulated interruption resumes successfully;
- zero blind duplicate paid actions;
- every paid action has durable IDs/reconciliation evidence;
- all masters pass technical and human review gates.

### Verification

Run the common baseline plus:

```bash
uv run pytest tests/test_e2e_fake_pipeline.py tests/test_e2e_resume.py
uv run auraly campaign status <canary-campaign-id>
```

Then inspect sanitized campaign/job/QC manifests and run full ffmpeg/ffprobe validation on all
three ignored final masters. Real provider execution requires explicit budget approval.

---

## Goal 8 — Local API/UI

### Objective

Expose the proven CLI/application-service pipeline through a local-only operator interface.

### Included

- FastAPI bound to `127.0.0.1`;
- React/TypeScript local UI;
- campaign/job status, QC evidence, logs, and approval gates;
- voice/image/video review actions;
- API/UI use of existing application services, not duplicate provider logic.

### Explicitly excluded

- public hosting, multi-user auth, mobile app, distributed execution, automatic social posting,
  and a full NLE timeline.

### Dependencies

Goals 0–7; CLI end-to-end canary must be proven first.

### Exit criteria

- normal pilot operations can be completed through the local UI;
- local-only binding is verified;
- approval gates cannot be bypassed by API or UI;
- UI/API restart preserves campaign/job state;
- provider and deterministic logic remain in shared application services.

### Verification

Run the common baseline plus the Goal-created checks, expected to include:

```bash
uv run pytest tests/test_api.py tests/test_api_approval_gates.py
npm test -- --run
npm run build
```

Verify the server listens only on `127.0.0.1` and complete an operator smoke test against the
already proven canary data without repeating paid operations.
