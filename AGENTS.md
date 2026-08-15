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

## Milestone verification terminology

Use these terms independently; never collapse them into a generic `verified` label:

- `IMPLEMENTED`: required production code and tests exist.
- `LOCAL_VERIFIED`: the required deterministic/local verification baseline completed
  successfully. This does not imply a real provider was exercised.
- `PROVIDER_VERIFIED`: an explicitly approved real provider canary completed successfully.

Mocked/local tests cannot establish `PROVIDER_VERIFIED`. Commit names, including names containing
`[verified]`, are not independent CI or provider evidence.

## Incremental Goal workflow

From Goal 4 onward, every significant feature or subgoal follows:

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

Store approved designs in `docs/superpowers/specs/` and implementation plans in
`docs/superpowers/plans/`. Do not combine independently reviewable subsystems into one Goal or
one implementation prompt.

For behavior changes, use the task-level cycle:

```text
write a failing test
→ run the focused test and confirm the expected failure
→ implement the minimum change
→ run focused verification
→ commit the independently reviewable deliverable
```

A commit need not correspond to an individual line or assertion; its boundary should be a small
deliverable with a reasonably narrow file set. Run the full applicable baseline before declaring
the subgoal `LOCAL_VERIFIED`.

Goal 4 work must preserve module boundaries. Do not add new application-service access to private
Job internals such as `self._jobs._repository`. If atomic domain entity + Job + audit persistence
needs a new public orchestration/transaction contract, define it in the Goal 4A design. Add future
image and Flow behavior through focused modules rather than placing all new code in
`src/auraly_pipeline/image_generation.py`; refactor compatible existing code only as a subgoal
requires it, never as a big-bang rewrite.

## Required checks

Use the fast harness during task-level TDD:

```bash
uv run python scripts/verify.py fast
uv run python scripts/verify.py fast --pytest tests/test_verify_harness.py
```

Run the full deterministic gate before declaring `LOCAL_VERIFIED`:

```bash
uv run python scripts/verify.py full
```

The harness resolves the repository root from its own location, stops on the first failure, and
checks generated schema drift without reverting user work. `full` executes this auditable baseline:

```bash
uv sync --locked --all-groups
uv run pytest
uv run ruff check src tests
uv run ruff check scripts
uv run python -m mypy src
MYPYPATH=src uv run python -m mypy tests
uv run python -m auraly_pipeline.schema
uv run python -m auraly_pipeline.cli export-image-generation-schema \
  --output schemas/image-generation.schema.json
uv pip check
npm ci
npm run hf:doctor
npm audit --omit=dev --audit-level=high
git diff --check
```

The displayed `MYPYPATH=src` line is POSIX notation only; the Python harness supplies the same
environment through `subprocess` on every platform, including Windows.

GitHub Actions invokes the same harness for independent deterministic evidence. A successful local
or CI run does not establish `PROVIDER_VERIFIED`.

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
