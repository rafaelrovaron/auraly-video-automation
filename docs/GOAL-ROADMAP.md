# Auraly Codex Goal Roadmap

**Purpose:** sequence narrow, independently verifiable implementation Goals for Codex.

This is not a second PRD. Durable decisions live in `PROJECT-MEMORY.md`; target requirements
live in `PRD-MVP-MASS-VIDEO-AUTOMATION.md`; repository instructions live in `../AGENTS.md`.
Every Goal must remain inside its stated boundary and leave the repository green.

## Common verification baseline

Unless a Goal explicitly adds another check, run:

```bash
uv sync --locked --all-groups
uv run pytest
uv run ruff check src tests
uv run mypy src
MYPYPATH=src uv run mypy tests
uv run python -m auraly_pipeline.schema
uv run auraly export-image-generation-schema --output schemas/image-generation.schema.json
uv pip check
npm ci
npm run hf:doctor
npm audit --omit=dev --audit-level=high
git diff --check
```

External provider canaries require explicit approval and must never expose credentials or repeat
paid actions blindly.

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
- persistent local queue and worker lease semantics;
- idempotency keys and duplicate protection;
- resume, cancellation, retryable/terminal failure states;
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
uv run pytest tests/test_jobs.py tests/test_job_state_machine.py \
  tests/test_job_idempotency.py tests/test_job_restart.py
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
- fake-server tests and one explicitly approved canary.

### Explicitly excluded

- ElevenLabs web automation;
- Google Flow, HeyGen, final rendering, API, and UI.

### Dependencies

Goals 0–2.

### Exit criteria

- one approved Voice Master can be reused by SceneVariants;
- headline/directions are absent from narration;
- raw and processed assets are immutable/versioned;
- provider retries are reconciled and cost-safe;
- canary evidence is sanitized and contains no key or signed URL.

### Verification

Run the common baseline plus:

```bash
uv run pytest tests/test_elevenlabs_adapter.py tests/test_voice_master.py \
  tests/test_audio_qc.py
ffmpeg -v error -i <ignored-approved-voice> -f null -
ffprobe -v error -show_streams -show_format -of json <ignored-approved-voice>
```

A real ElevenLabs canary runs only with explicit approval and configured secrets outside Git.

---

## Goal 4 — Google Flow Campaign Integration

### Objective

Implement the real Playwright Flow runtime and connect resumable image jobs to SceneVariants.

### Included

- dedicated persistent Chromium profile outside Git;
- manual initial login setup;
- centralized versioned semantic locators;
- concurrency fixed at 1 initially;
- page-state verification and stop-safe `human_intervention_required`;
- prompt submission, candidate preservation, and requested-candidate selection;
- highest supported required 2K download;
- Playwright screenshots/checkpoints and trace on relevant failure;
- existing trusted roots, safe downloads, manifests, QC artifacts, and review states;
- approval/rejection/regeneration with rejected versions preserved.

### Explicitly excluded

- blind coordinate clicking;
- use of the personal main Chrome profile;
- Google AI Studio or any parallel image provider;
- HeyGen, final editing, API, and UI.

### Dependencies

Goals 0–2; Campaign and job persistence must exist.

### Exit criteria

- mock/local UI tests prove semantic locator and stop-safe behavior;
- restart resumes without losing candidates or repeating a completed generation;
- 1K/unverified files cannot satisfy a 2K requirement;
- candidate/QC/review evidence is durable and sanitized;
- one real Flow canary is completed only with explicit approval.

### Verification

Run the common baseline plus:

```bash
uv run playwright install --dry-run chromium
uv run pytest tests/test_google_flow.py tests/test_flow_locators.py \
  tests/test_flow_resume.py tests/test_image_qc.py
```

The approved canary must verify 2K dimensions, preserved candidates, trace-on-failure behavior,
and absence of browser profile/cookies/traces from Git.

---

## Goal 5 — HeyGen

### Objective

Create and retrieve durable Avatar III video jobs through official HeyGen MCP/OAuth.

### Included

- MCP/OAuth preflight and sanitized capability checks;
- signed upload lifecycle and exact signed-header preservation;
- reusable audio asset, photo avatar look, and polling;
- explicit `engine.type = avatar_iii` and `supported_api_engines` gate;
- durable remote IDs before advancing/polling;
- idempotency, reconciliation, download, and source-video QC;
- fake MCP tests and one explicitly approved canary.

### Explicitly excluded

- HeyGen web automation;
- silent fallback to another engine;
- final editing, API, and UI.

### Dependencies

Goals 0–4; approved Voice Master and Flow image.

### Exit criteria

- ambiguous paid requests are reconciled rather than blindly repeated;
- Avatar III support is verified before video creation;
- signed URLs/tokens never reach persistence or logs;
- one approved canary downloads and passes source QC.

### Verification

Run the common baseline plus:

```bash
uv run pytest tests/test_heygen_mcp.py tests/test_heygen_upload.py \
  tests/test_heygen_idempotency.py tests/test_heygen_qc.py
ffmpeg -v error -i <ignored-heygen-canary> -f null -
ffprobe -v error -show_streams -show_format -of json <ignored-heygen-canary>
```

Real MCP/OAuth and paid actions require explicit approval.

---

## Goal 6 — Deterministic Editing

### Objective

Produce an immutable, technically validated 1080×1920 final Reel from approved inputs.

### Included

- captions from approved spoken copy;
- headline as visual text only;
- subtle zoom, approved music, and deterministic renderer adapter;
- versioned edit manifest;
- H.264/AAC 1080×1920 master, proxy, hashes, and QC report;
- non-destructive, immutable final artifacts.

### Explicitly excluded

- new provider generation;
- publishing/social APIs;
- FastAPI and React UI.

### Dependencies

Goals 0–5 and approved source assets.

### Exit criteria

- same inputs/manifest render functionally equivalent output;
- headline is not narrated;
- captions remain in safe zones;
- final full-decode, FPS, loudness, true peak, and duration checks pass;
- reruns never overwrite an immutable final.

### Verification

Run the common baseline plus:

```bash
uv run pytest tests/test_edit_manifest.py tests/test_captions.py \
  tests/test_renderer.py tests/test_final_qc.py
ffmpeg -v error -i <ignored-final-master> -f null -
ffprobe -v error -show_streams -show_format -of json <ignored-final-master>
```

If HyperFrames is the selected adapter, also run lint/check and a draft render for its
composition.

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

Goals 0–6.

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
