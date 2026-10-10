# D5B.2A — Human review backend/CLI verification

Date: 2026-10-10. Execution: Native, approved design and implementation plan.
Branch: `feat/d5b2a-human-review`, documentary base `2b2b638`, shipped baseline
`5aa6221`. No merge/push or paid provider execution for this slice.

## Scope

Typed request/record 1.0, exact-version key, immutable local service and CLI
`edit review|review-get`; two exported schemas and synthetic example.
Approval requires valid passed QC and explicit watch/listen confirmation.
Rejection requires comment, accepts passed/blocked QC and exits 0 as a successful
human decision. Playback confirmation is an assertion, not measured watch time.
One definitive decision per consumer-plan/master version; identical repeat and
equal concurrent submissions return the original record/date. Changed requests,
including another QC key, conflict. Consultation revalidates original evidence.
No HTTP/UI, SQL migration, Job, worker, delivery, dependencies or media regeneration.
QC status and `humanReviewRequired` remain unchanged. Flow remains paused.

## TDD and focused checks

- Task 1: contracts RED (module absent), schema exporter RED, tracked-output
  harness RED; GREEN fast gate 67 tests, schema export without unrelated drift.
- Task 2: service RED (module absent); real-media fixture corrected its output
  count before rendering. GREEN service/domain/QC gate 81 tests (125.70 s final
  task run); Ruff and mypy source passed. A byte-only QC replacement during
  consultation reproduced an accepted stale result (RED); final byte comparison
  made the regression GREEN. Covers conflicts, first-decision concurrency,
  independent plan/variant contexts, corrupt/changed evidence and no processing.
- Task 3: missing CLI command RED, missing example RED, missing Windows target
  RED (count 0); GREEN fast gate 113 tests (72.01 s final task run), CLI-first 45 (71.56 s),
  CLI-alone 11 (8.02 s). Explicit shared fixture imports, no test-module plugin.
- `MYPYPATH=src` mypy all tests passed: 141 files. Source mypy: 122 files.
  Two review modules only; no new dependencies. Windows workflow adds exactly
  the three review targets and preserves prior targets/timeout/encoder handling.

## Full gate

Fresh `rtk proxy uv run python scripts/verify.py full` in the isolated Windows
worktree, sole active harness: exit 0, 19/19. Python 3.11.15: 2,070 passed / 27
unchanged platform skips in 950.76 s. Frontend: 378 passed / 22 files (32.20 s).
Ruff, mypy (122 source / 141 tests), build, dependency checks and all exported
schemas passed without drift. No review UI was added; existing frontend regressed.

UI audit: zero vulnerabilities. Root audit: five preexisting moderate transitive
findings below the existing high threshold; no dependency upgrade attempted.
Doctor exits 0 with known update/Docker/optional transcription/TTS/music warnings.
FFmpeg/ffprobe 8.1.1; no provider canary. PowerShell renders redirected native stderr
as NativeCommandError decoration even for successful uv sync; harness and process
both exited 0. Git LF/CRLF notices were warnings, not whitespace errors.
Linux local/Actions for this new branch not claimed; publication remains unauthorized.

## Independent review

One fresh-context read-only whole-branch review by gpt-6-astra over
`5aa6221..6815eb9` completed after all three tasks and the complete gate.
Verdict: ready to merge; zero Critical, zero Important, one Minor. Reviewer
focused repetition/QC-key/concurrency/evidence tests: 11 passed / 23 deselected
in 20.83 s; whitespace and checkout status clean. No production fix required,
no second reviewer. Status: `IMPLEMENTED`, `LOCAL_VERIFIED` (Windows).
No merge/push or Actions for this slice; implementation authorization is not
integration authorization.

Minor deferred: the equal-concurrency regression synchronizes publication but
does not force/assert two different candidate timestamps. Production winner
reconciliation is correct; a future test-only change should stub two UTC times.

## Execution rulings

- Native app worktree creation did not recognize the nested Git checkout; used
  ignored project-local Git worktree fallback. Cost if wrong: unmanaged checkout
  requiring explicit Git cleanup. Other worktrees/scratch and synced sources preserved.
- Added one explicit model tuple type annotation to the Task 1 schema test in
  Task 2, after test typing exposed ambiguous metaclass inference. No behavior
  change; cost if wrong: undo one test annotation.
- Coherent rewriting/deletion/rollback of all local evidence remains excluded:
  integrity model, not hostile-operator attestation. Cost if wrong: a coherent
  local operator can forge/remove approval evidence.
- External changes after final validation have no filesystem snapshot guarantee;
  pre-publication checks and subsequent consultation revalidation stand. Cost if
  wrong: a returned decision may immediately become stale and needs reconsultation.
- Authentication, measured playback, revocation, API/UI and delivery remain
  excluded per approved backend/CLI scope. Cost if wrong: this slice cannot
  authenticate, measure watching, revoke, or complete UI/delivery workflow.
- Resource limits for abnormal local JSON payloads remain deferred under personal
  use/no general hardening. Cost if wrong: huge files can exhaust memory/resources.

## Remaining scope

D5B.2B: connect technical QC and human decisions to existing API/UI after its
own approved design. D5B.3: verified delivery of approved masters. No new provider
verification; no real editorial approval inferred from synthetic test records.
