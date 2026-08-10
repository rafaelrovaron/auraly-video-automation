# Auraly Repository Instructions

## Repository intent

Auraly is a local, resumable, auditable mass-video-production pipeline. Keep the
implementation a modular monolith. Application code performs deterministic mechanical work;
Hermes/AI supplies creative judgment and prompts; required human approval gates remain
explicit.

## Sources of truth

Use this priority order:

1. the explicit current task or Codex Goal;
2. `docs/PROJECT-MEMORY.md` for durable product and architecture decisions;
3. `docs/PRD-MVP-MASS-VIDEO-AUTOMATION.md` for the target MVP;
4. repository contracts and tests for implemented behavior;
5. `README.md` for operational guidance and current-state summaries.

`docs/GOAL-ROADMAP.md` sequences implementation work but does not override the sources above.
If documentation and implementation conflict, do not silently choose one. Inspect the conflict,
resolve it only when it is in Goal scope, or report it as a blocker.

## Current boundaries

- Google Flow through Playwright Python is the only image-generation path.
- Image request preparation, trusted-root validation, download correlation, finalization,
  manifests, and sanitized diagnostics are implemented; the Google Flow browser runtime and
  image QC are not yet implemented.
- ElevenLabs production access is API-only. Do not automate its website.
- HeyGen production access is official MCP/OAuth-only. Do not automate its website.
- Do not claim planned PRD capabilities are implemented until real execution verifies them.

## Engineering rules

- Use Python 3.11 and typed Python.
- Use Pydantic for versioned contracts and regenerate affected JSON Schemas.
- Keep Ruff, mypy, and pytest green.
- Add or update tests before changing behavior; preserve security regressions.
- Keep media operations non-destructive. Never silently overwrite workspaces or artifacts.
- Validate trusted roots, canonical paths, containment, symlinks/junctions, and filenames before
  filesystem mutations. Apply both POSIX and Windows path semantics where relevant.
- Never commit or log secrets, OAuth tokens, cookies, signed URLs, browser profiles, storage
  state, generated media, downloads, models, `.venv`, `node_modules`, or temporary work data.
- Remote paid actions require explicit budget/approval, idempotency, duplicate protection, and
  reconciliation. Persist external IDs before advancing state or polling.
- Put deterministic operations in application services, not prompts. Hermes/AI owns creative
  choices; application code owns mechanical execution and audit evidence.
- Do not bypass copy, image, voice, canary, or final-video approval gates.
- Do not expand a Goal without necessity. Preserve existing behavior unless the Goal explicitly
  changes it.
- Stop safely when provider/browser state cannot be verified. Never replace a missing semantic
  locator with blind coordinate clicks.
- Do not commit or push until applicable checks pass and the diff has been reviewed for secrets,
  generated media, private paths, and scope creep.

## Required checks

Run from the repository root:

```bash
uv sync --locked --all-groups
uv run pytest
uv run ruff check src tests
uv run mypy src
uv run python -m auraly_pipeline.schema
uv run auraly export-image-generation-schema --output schemas/image-generation.schema.json
uv pip check
npm ci
npm run hf:doctor
npm audit --omit=dev --audit-level=high
git diff --check
```

When tests themselves are type-checked on Windows, use:

```bash
MYPYPATH=src uv run mypy tests
```

When a Goal changes a HyperFrames composition, also run against that composition:

```bash
npm run hf:lint -- <composition-dir>
npm run hf:check -- --strict <composition-dir>
npm run hf:render -- <composition-dir> --output <ignored-work-path>.mp4 --quality draft
ffprobe -v error -show_entries stream=codec_name,width,height,r_frame_rate \
  -show_entries format=duration,size -of json <ignored-work-path>.mp4
```

Provider canaries are Goal-specific, may incur cost, and require explicit approval. Never use a
paid external call merely to make a cleanup or unit-test gate pass.
