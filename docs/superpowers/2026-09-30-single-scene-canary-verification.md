# Single-scene canary preparation — verification

Status: `IMPLEMENTED`, `LOCAL_VERIFIED`; not `PROVIDER_VERIFIED`.

## Approved scope

- CampaignCreate accepts one or more scenes, with existing distinctness validation; no migration.
- One-scene manual image and external Voice Master import through existing services.
- Reuse existing ffprobe for integer PCM WAV validation, including FFmpeg PCM24 extensible
  output that Python 3.11 wave rejects. Preserve hashes, approvals and 50 ms duration tolerance.
- Fake HeyGen asset upload and one-render reservation/replay; no paid create-video call.

## Evidence

- RED: one/two-scene campaign tests failed on the previous minimum of three.
- RED: integrated real audio processing reached HeyGen planning and failed on WAV format 65534.
- RED: new WAV probe tests could not import the not-yet-implemented helper.
- GREEN: focused fast harness 3/3, 54 tests passed; after the test-only replay variable rename,
  integrated regression passed again and mypy tests passed.
- Full Windows gate: `uv run python scripts/verify.py full`, 14/14 steps passed;
  1402 tests passed, 19 skipped in 212.13 seconds.
- Independent read-only review: no Critical/Important findings; stale minimum-three blocker
  in PROJECT-MEMORY was corrected. Reviewer focused suite: 58 passed.
- Prior main commit 724fe58 Actions run 36760960475 passed Linux full and Windows focused gates;
  that run does not establish CI evidence for this new branch.

## Limits and next operation

No real provider call, real ASR test or paid render was performed in this slice. Integration uses
a fake transcript and provider, but real local FFmpeg processing. Real canary still requires
approved matching copy/image/voice, OAuth preflight, max_paid_renders=1 and concurrency=1.
Existing Windows paths over 260 characters can fail HeyGen local-file checks; use a short work
root/campaign ID for the canary. Generic long-path support is outside this approved fix.
HyperFrames doctor exited zero but reported optional tools absent and an available update;
the gate does not prove a complete final-render environment.
