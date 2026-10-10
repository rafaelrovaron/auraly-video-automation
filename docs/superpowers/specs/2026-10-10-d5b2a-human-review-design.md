# D5B.2A — Human Review Backend / CLI

Date: 2026-10-10

Status: conversational scope and rules approved; written spec awaiting user review.
Baseline: main `5aa6221`; D5B.1 QC backend/CLI already shipped.
This document describes new work, not a shipped approval capability.

## Intent and success criteria

For personal local use, record an explicit human decision about one existing
final MP4 variant. Technical QC does not replace watching and listening.
An approval must refer to the exact reviewed master and QC evidence, survive
reload, and never transfer silently to another render or editing plan.

The user approved splitting D5B.2 into backend/CLI first and UI integration
second. They approved a definitive decision per version, without undo, with
reviewer/date/decision/comment, mandatory rejection comment, and explicit
watch/listen confirmation for approval. Reuse local JSON publication; no new SQL
table. No master or review overwrite.

## Scope

Included:

- Versioned typed request and immutable decision contracts, with JSON Schemas.
- A small editing review service to record and retrieve one exact decision.
- Thin CLI commands to submit a request file and consult an exact review.
- Tests, synthetic examples, and documentation identifying shipped versus planned work.

Excluded:

- HTTP endpoints, Job kinds, worker orchestration, SQL migrations, and React UI.
- Running QC, FFmpeg analysis, encoding, or generating any upstream asset.
- Approval batches, automatic human judgment, undo, revocation, and mutable history.
- Creating a revision automatically after rejection.
- Proxy/contact sheets, delivery copies, cloud upload, new dependencies, or general hardening.

D5B.2B will connect QC and review to the existing application/UI after its own
design. D5B.3 will deliver approved masters. This slice does neither.

## Components and reuse

Place review contracts and service in focused modules under
`src/auraly_pipeline/editing/`; add commands to the existing editing CLI and
exports to the existing schema/harness flow.

Reuse `QcService.get_report` to resolve an explicitly selected stored report and
verify its saved plan, master, and receipt identities. Do not invoke `QcService.run`
or detect a runtime. Reuse `content_hash`, trusted-path validation, long Windows
path handling, and the existing exclusive/no-overwrite JSON publication helper.
Do not change QC status or `humanReviewRequired`; human decisions are separate
artifacts. No new persistence framework or application-service access to private
Job internals.

## Request and record

A version `1.0` request selects exactly one target with:

- `campaignId`, `videoId`, `planHash`, `outputVariantId`, `renderKey`, `qcKey`.
- `decision`: `approved` or `rejected`.
- `reviewedBy`: trimmed, nonblank plain text identifying the local reviewer.
- `comment`: trimmed text or null; nonblank required for rejection.
- `watchedAndListened`: explicit boolean; true required for approval.

Use existing safe identifier and SHA validators. Reject unknown fields, malformed
identity, invalid decision/boolean, blank reviewer, or incomplete rejection.
Blank optional approval comments normalize to null. Reviewer and comment are
local user-entered information, not authentication credentials or proof of playback.

The service generates the UTC review timestamp; callers cannot choose it. The
stored `1.0` record contains the normalized request, timestamp, `reviewKey`,
master relative path/SHA/size, receipt SHA, QC report SHA, and the report's status.
It retains the selected QC key and context; it does not copy or alter the MP4.
Record validators enforce approval prerequisites and derived identity consistency
when reading as well as writing. Hash the exact report file bytes used as evidence.

## Version identity and immutability

For this slice, a reviewed version is the exact saved consumer-plan context plus
master identity: campaign/video/plan hash/variant/render key/master SHA/receipt SHA.
Compute `reviewKey` with the existing canonical content-hash helper over those
named fields. Timestamp, decision, reviewer, comment, QC runtime, and QC key are
not inputs to this version key.

This intentionally prevents a second decision for the same version merely by
running QC again with another runtime or changing a comment. A different saved
plan is a different review context even if it reuses identical MP4 bytes; approval
is not inherited. Different variants are independent.

Publish one artifact at:

```text
campaigns/{campaignId}/editing/reviews/{outputVariantId}/{renderKey}/{reviewKey}/review.json
```

Never overwrite a review. If a request repeats the same normalized selection,
decision, reviewer, comment, and confirmation, return the existing validated record
with its original timestamp. A different request for an already decided version
fails as a conflict, including a different QC key. Concurrent identical requests
converge on one record; concurrent conflicting requests allow at most one winner.
Use the existing exclusive publisher and a focused read/reconcile path, not locks
or a new transaction layer. A damaged existing record fails safely, not as a cache miss.

## Admission, publication, and consultation

1. Validate the request before filesystem mutation.
2. Resolve the exact saved QC report with `get_report`; no latest-report inference.
3. Approval requires report status `passed` and `watchedAndListened=true`.
   Rejection accepts a valid `passed` or `blocked` report and requires a comment.
   A missing/error QC result has no valid report and cannot be reviewed here.
4. Verify current master/receipt/report identities and derive the version key.
5. Reconcile an existing decision, or publish a new one with a service-generated
   UTC timestamp. Recheck master/receipt/report evidence before publication so a
   detected input change leaves no new review.
6. Return the stored decision, never infer human approval from QC alone.

Consultation selects campaign/video/plan/variant/render/review key explicitly.
Read the record, resolve its original QC key, and validate all context and evidence
against the current bytes. Missing, corrupt, foreign, or stale evidence returns an
error, never an approved decision. Consultation writes nothing and requires no
FFmpeg installation. A request must not supply arbitrary output/report paths.

The safeguards are the existing personal-workspace integrity model, not protection
against a hostile local operator coherently rewriting all evidence. Playback
confirmation is a human assertion; this slice does not measure watch time.

## CLI behavior and errors

Proposed commands are `edit review --request <json>` and `edit review-get` with
the six explicit context/review selectors and existing project/work root options.
Both emit a complete record as JSON and exit 0 for a valid stored decision,
including rejection. Exit 1 for invalid input, unmet approval prerequisites,
conflict, missing/stale evidence, or failed publication. A rejection is an
intentional successful operation, not a technical execution failure.

Use existing sanitized diagnostics; do not print tracebacks, absolute private
paths, raw subprocess output, or review payloads on failure. Unknown results can
be consulted or retried identically; there is no automatic retry with another
decision. Never execute providers, render, worker start, or QC as a fallback.

## Rejection and future consumers

Rejection preserves the existing version and record. The operator makes the
needed edit and uses the existing render workflow to produce a new version, runs
its QC, then reviews it independently. No regeneration is triggered by rejection.

Future UI/delivery consumers must consult the verified review service, not infer
approval from file presence or trust a client-provided decision. D5B.2A exports
that service contract but does not implement those consumers.

## Verification and completion evidence

TDD must cover:

- Passed QC plus explicit playback confirmation permits approval.
- Blocked QC, missing confirmation, blank reviewer, or absent/invalid evidence
  prevents approval without publishing a record.
- Rejection requires a comment; both passed and blocked QC can be rejected.
- Exact repeat preserves bytes/timestamp; changed decisions, actor, comment, or
  QC key conflict. Independent variants and new plan/render contexts do not inherit approval.
- Concurrent identical/conflicting submissions respect first-decision semantics.
- Master, receipt, or QC report changes before publication or consultation are
  detected; foreign/corrupt records and unsafe paths fail without overwrites.
- Record consultation is readonly and works with runtime detection/analysis
  disabled. Rendering and provider calls are never performed.
- CLI JSON/exit codes, schema examples/export drift, Windows path behavior, and
  original master/report byte preservation.

Use small coherent commits and existing test helpers; shared fixtures must work
with service-first, CLI-first, and isolated collection. Add relevant new test files
to the Windows focused workflow without changing its established safeguards.
Run focused tests, the full deterministic harness, and independent review before
claiming `LOCAL_VERIFIED`. Mocked/synthetic evidence is not provider verification.

## Approval handoff

This document is the written design for user review. No implementation is
authorized by its creation. After written-spec approval, produce a separately
reviewed implementation plan and obtain the user's execution-method choice.
