# D3B — planning verification

Date: 2026-10-02. Windows, Python 3.11.15.

## Scope and state

D3B implements explicit bounded editing variants, deterministic identities and hashes,
caption inputs linked to the approved copy/voice of an existing ready render, optional
validated timing, readonly SQLite input loading, JSON plan persistence and CLI/schemas.
No UI, renderer, alignment engine, new migrations/dependencies or provider calls.
Legacy v1 and D3A profile/manifest schema bytes remain unchanged; AGENTS.md is unchanged.

## Verification

- Pre-change baseline: 15/15 steps, 1491 passed, 20 skipped.
- D3B pre-review full gate: 15/15 steps, 1554 passed, 21 skipped, 305.24s pytest.
- Independent whole-branch review: no Critical, one Important and one Minor.
- Important fixed: incomplete SQLite schema raised sqlite3.Row IndexError; shared database
  boundary now sanitizes it. `test_database_missing_columns_is_sanitized` observed
  RED (IndexError), then GREEN; focused service/CLI gate: 24 passed, 1 skipped, 3/3 steps.
- Final post-fix full gate: 15/15 steps, 1555 passed, 21 skipped, 304.44s pytest.
  D3B is IMPLEMENTED and LOCAL_VERIFIED; no outstanding Critical/Important findings.

Mandatory checks include schemas/drift, Ruff, mypy source/tests, dependencies/audit and
whitespace. HyperFrames doctor returns exit 0 but reports optional whisper-cpp, Kokoro,
MusicGen and Docker unavailable, plus a version update. This is not renderer validation.
Windows skips include symlink cases requiring OS privileges; no bypass was introduced.
GitHub Actions has not run this unpushed branch. No new PROVIDER_VERIFIED claim.

## Real-media dry-run

Existing campaign `d2c-001`, ready render `2b758b09-a760-4a25-8d7a-450b6dd50b3f`.
This local render ID is different from the scene ID in the source directory and remote ID.
Existing operational database opened with mode=ro, without migrations.
The only publication was a reusable isolated test profile:
`editing/profiles/d3b-smoke/1/profile.json` under the operational work root, outside the campaign.
No plans or manifests were published. All editing features disabled in this minimal profile;
three headline texts test planning/hash variation, not visual rendering.

| Check | Observed |
|---|---|
| Outputs | 3 |
| Repeated plan | Identical |
| SQL logical dump | Unchanged |
| Campaign MP4/WAV SHA-256 and mtime | Unchanged |
| Timing | missing |
| Caption states | disabled for all three |
| Paid calls | 0 |

Plan hash: `616ca84ac00bbc91d6afa32a4f8bbf85b956efbcb6b75ecb9418a79e2fa8b1ad`.
Source SHA-256: `721f35a2d054742d3600ca5dd6f61459a948bcf32bc1663641fcaabe377dba69`.
Processed WAV SHA-256: `f6399632aeba75c40db6dbda45eb6d376d388cdbb9df679084297cf12cc1ad44`.
Timing fixtures in tests/examples are synthetic, not real alignment evidence.

## Execution decisions and deferred findings

- Native worktree tool did not recognize the nested repository; used a Git sibling worktree.
  Cost if wrong: manual worktree lifecycle.
- Existing fake HeyGen fixtures use intentionally inconsistent copy hashes; added opt-in
  canonical copy setup without changing defaults. Cost if wrong: fixture setup adjustment.
- CLI replay compares parsed contracts, not stdout key order. Cost if wrong: consumers must
  parse JSON rather than rely on presentation order.
- Actual synchronization/render quality remains a later slice. Cost if wrong: planner tests
  alone cannot establish usable subtitles or final visual quality.
- Snapshots provide checksum/semantic integrity, not signatures against an attacker replacing
  every reference/hash. Cost if wrong: malicious consistent replacement is not authenticated.
- Final docs and full gate are verified by the executor, not the independent code reviewer
  at reviewed HEAD. Cost if wrong: documentation/gate assessment has no second reviewer.
- Deferred Minor: resolution error identifies field/layer but not variant key. Larger batches
  may require inspecting variants to locate the failing input.

## Handoff

Next planned slice: D4A FastAPI Operational API, using existing application services.
UI/approximate preview follows in D4B; deterministic rendering in D5A.
Imported audio still needs a trustworthy timing sidecar or a future alignment slice for
synchronized captions. No invented timing, auto-approval, or upstream regeneration.
Integration remains pending user choice; implementation has not been merged or pushed.
