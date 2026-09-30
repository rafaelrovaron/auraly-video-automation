# D2C canary checkpoint — 2026-09-30

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
- Actual credits consumed/currency were not measured; no monetary cost is inferred.

## Remaining work and status

`IMPLEMENTED` and `LOCAL_VERIFIED`; **not `PROVIDER_VERIFIED`**. Await confirmation of the
candidate ID, then reconcile the existing render, download and validate the MP4 through existing
probe/full-decode QC. Do not generate another video. Human visual approval remains separate.
No media, signed URLs, tokens or personal account data are committed. No merge/push performed.

## Review rulings and limitations

- Review tests consolidated in one focused file: discoverability tradeoff, no behavior change.
- Namespaced username hash used when MCP lacks workspace/id: workspace changes within the same
  user are not independently detectable; provider ownership remains authoritative.
- Unexpected pagination and known queued uploads fail closed; no generalized retry/pagination
  subsystem added. Unknown creation never triggers another POST.
- Minor deferred: SQLite direct-SQL reason checks accept some whitespace/sensitive strings that
  CLI/service/domain reject. Only supported service approval was used. Tighten SQL whitespace
  parity and explicitly document application-only sensitive-text validation before merge.
- Reviewer declined real-provider success, workspace redesign and queued-upload recovery;
  these are not claimed proven by local mocks. Baseline was run by the implementer.
