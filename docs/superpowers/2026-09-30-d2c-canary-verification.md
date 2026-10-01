# D2C canary result — 2026-09-30

## Deterministic evidence

- Branch: `fix/heygen-mcp-canary-contract`; implementation reviewed through `47959af`.
- `uv run python scripts/verify.py full`: 14/14 steps passed.
- Pytest: 1430 passed, 19 skipped; Ruff, mypy, schemas and dependency checks passed.
- HyperFrames doctor exits successfully but reports optional runtime components absent and an
  available upgrade; no HyperFrames composition was changed or rendered by this slice.
- Independent whole-branch review: no critical/important findings; safe for one authorized canary.

## Real execution

- Campaign `d2c-001`, one scene, maximum one paid render, concurrency one.
- Imported voice approved by Rafael with reason: “Audio accepted for this technical test;
  original transcript review retained.” Original ASR/QC/comparison and artifact hashes retained.
- Processed WAV SHA-256: `f6399632aeba75c40db6dbda45eb6d376d388cdbb9df679084297cf12cc1ad44`.
- WAV duration: 7.217333 seconds. No reimport, voice generation, ASR rerun or audio edit.
- Database comparison against the pre-approval backup confirmed all original Voice Master
  fields unchanged except status/approver/time; the added review reason is preserved. Final
  database count is exactly one render reservation for this campaign.
- Real OAuth asset/video preflight passed with matching hashed account identity.
- One batch uploaded the existing image and WAV. Upload job
  `50617d9b-7085-4dd1-982b-588a896ae2bc`: completed, one attempt.
- Exactly one render reservation: `2b758b09-a760-4a25-8d7a-450b6dd50b3f`.
- One generation dispatch at 23:26:07 UTC. Response yielded no usable video ID to the adapter.
  Render/job blocked for reconciliation; no second dispatch or reservation was performed.
- Read-only `list_videos`/`get_video` located candidate
  `51010dfdc35da0a361b26167355c51cc`, created 23:26:16 UTC, completed, duration 7.21733 seconds.
  MCP does not expose its callback/image/audio identity. Time/duration alone are not an automatic
  binding guarantee: explicit human confirmation was requested before manual binding.
- Rafael subsequently confirmed manual binding of that exact candidate. Existing service
  reconciliation persisted the video ID and `manual_binding=true`; the same job resumed.
- Download/publication/QC completed: render `ready`, H.264/AAC, 1080×1920, 25 fps,
  duration 7.224 seconds, AAC 48 kHz stereo, 3,603,735 bytes, no probe warnings.
- MP4 SHA-256: `721f35a2d054742d3600ca5dd6f61459a948bcf32bc1663641fcaabe377dba69`.
- Full FFmpeg decode exited 0. Plan/submit/run replay returned the identical render with zero
  new reservations. Replay used an adapter whose create method raises immediately; no new
  paid call occurred. The original approved image/audio asset identities remain reused.
- Actual credits consumed/currency were not measured; no monetary cost is inferred.

## Remaining work and status

`IMPLEMENTED`, `LOCAL_VERIFIED` and **`PROVIDER_VERIFIED` for this one-render canary with
confirmed manual reconciliation**. Automatic create-response ID recovery and real multi-video
scale are not verified by this result. Do not generate another video to resolve the original
ambiguity. Human visual approval remains separate; D3A is the next planned development slice.
No media, signed URLs, tokens or personal account data are committed. No merge/push performed.

## Review rulings and limitations

- Review tests consolidated in one focused file: discoverability tradeoff, no behavior change.
- Namespaced username hash used when MCP lacks workspace/id: workspace changes within the same
  user are not independently detectable; provider ownership remains authoritative.
- Unexpected pagination and known queued uploads fail closed; no generalized retry/pagination
  subsystem added. Unknown creation never triggers another POST.
- Original minor: SQLite direct-SQL reason checks accepted some whitespace/sensitive strings
  rejected by CLI/service/domain. Only supported service approval was used for the canary.
  Follow-up on 2026-10-01 fixes Unicode blank-reason and raw-length validation through migration
  0011 INSERT/UPDATE triggers and the model CHECK, rejecting embedded NULs that truncate SQLite
  length checks, without rebuilding the table or editing history.
  Sensitive-text validation explicitly remains application-owned; no duplicated SQL regexes/UDFs.
- Reviewer declined real-provider success, workspace redesign and queued-upload recovery;
  these are not claimed proven by local mocks. Baseline was run by the implementer.

## SQL follow-up verification — 2026-10-01

Blank Unicode and embedded-NUL/length regressions reproduced before their fixes, then passed.
20 voice-review tests passed, including native SQL INSERT/UPDATE, model CHECK and migration
roundtrip preserving approved evidence, foreign keys and immutability. Independent review found
no critical/important issues; its NUL bypass finding was corrected and verified RED→GREEN.
Final full gate: 14/14 steps, 1432 passed/19 skipped (252.84 s pytest). No provider calls,
new dependencies, table rebuild, live media changes, merge or push in this follow-up.
