# External Voice Master Import — verification

Date: 2026-09-30. Branch: `feat/external-voice-import`. Base: `1136e40`.

## Status

- IMPLEMENTED: yes, tasks 1–3 committed.
- LOCAL_VERIFIED: yes, final post-review Windows gate passed 14/14.
- PROVIDER_VERIFIED: no. No HeyGen/ElevenLabs request or credits consumed.
- No merge/push; AGENTS.md and source media unchanged.

## Delivered and evidence

Imported provenance/contract and migration: `83b0e33`.
Local import, decode/processing and independent QC: `d6b0202`.
CLI and default/scoped worker integration: `7ac89ac`.

Baseline: 1358 passed / 18 skipped. Focused task gates: 49 passed; 49 passed / 1 skipped;
87 passed. Source/tests mypy and Ruff passed. Full gate before review correction: 14/14,
1387 passed / 19 skipped. The extra skip is unavailable Windows symlink creation.

One fresh independent whole-branch review identified one Important preservation issue and one
Minor manifest-label issue. Reproduced the Important issue with two failing tests, then fixed:

- `test_import_rejects_preexisting_voice_directory`: first intake reserves the directory
  exclusively; collision preserves files and persists neither job nor voice.
- `test_import_preserves_preexisting_processing_partial`: the shared audio processor refuses
  an existing `.partial` instead of deleting it, covering import and generation callers.

Correction focused gate: 51 passed / 1 skipped; Ruff and both mypy gates passed.
No second reviewer; the single correction pass is checked with RED→GREEN and the full gate.
Final `uv run python scripts/verify.py full`: 14/14 steps passed; 1389 tests passed / 19 skipped
in 207.39 seconds. Schemas had no drift and npm production audit found zero vulnerabilities.
HyperFrames doctor returned success but reports optional runtimes/Docker absent and an available
upstream upgrade; this gate does not establish a full renderer environment or provider verification.

Offline operational probe: faster-whisper and cached `small.en` CPU/int8 loaded successfully;
the selected original MP3 produced 21 recognized words. No download, original edit, transcript
publication or approval. The prior Windows Application Control failure did not reproduce.
This establishes neither transcript accuracy against an approved copy nor provider readiness.

## Rulings made, in order

1. Git fallback for isolation: native app tool could not target the nested repository.
   Cost: no app registration for this worktree.
2. Focused external migration test file and updated legacy head assertions.
   Cost: another focused test file to maintain.
3. Atomic SQLite migration-only transaction with foreign keys checked before commit.
   Required for rebuilding a referenced table; normal application connections retain FK checks.
   Cost: all migrations use this wrapper; rollback and referring-row preservation are tested.
4. Audio-specific ancestor/link validation before resolving roots, rather than image-root helper.
   Cost: a duplicated small ancestor walk; no shared image behavior changes.
5. Ignore source access-time changes; retain inode, size, nanosecond mtime and hash checks.
   Cost: access-time-only changes are not an integrity signal.
6. Integration uses the required three scenes, rather than the brief's single-scene fixture.
   Cost: a one-scene real canary requires a separately approved intake/selector change.
7. Real Whisper recognition is an operational probe, not Copy Master approval.
   Cost: the canary may still stop at processing/QC.
8. Do not test paid providers/OAuth/canary in this code slice.
   Cost: actual interoperability remains unproved.
9. Preserve the three-scene campaign restriction for this slice.
   Cost: delayed single-render canary until a separately scoped change.
10. No generic crash recovery/retry for imports; manual-only failures stay explicit.
    Cost: operator diagnosis is required for failed imports.
11. Reuse existing English transcription, loudness and approval behavior.
    Cost: another language or changed pacing requires separate work.
12. No redesign against hostile local administrators, forged timestamps or privileged DB edits.
    Cost: not hardened against such adversaries; ordinary intake boundaries remain checked.

## Deferred minors

- Standalone import manifest does not explicitly label original format/size. SQLite retains
  them; the nested processing report describes the intermediate WAV. Add separate labels later.

## Next operational prerequisites

The user authorized one real paid canary, not this slice's merge/push. Before dispatch:
resolve the one-scene planner conflict without raising the authorized render limit; review
actual copy/image/processed voice and obtain their approvals; connect OAuth/preflight.
Use concurrency one and at most one paid render. Ambiguity means reconcile the same ID, not create again.
