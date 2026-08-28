# Goal 4C Wave 1 — Task 6 report

## Scope and baseline

Implemented only Task 6, starting from `60363de09d1739a3ac9699b35f0a2d29c8213768` in
`auraly-goal-4c-wave1`. No live browser/provider preflight, provider navigation, download,
Wave 2 work, merge, or push was performed.

## RED / GREEN evidence

1. **Initial RED** — after adding the contract and local-page tests, ran:

   ```text
   uv run pytest tests/test_flow_generation_domain.py tests/test_flow_generation_locators.py -q
   ```

   Result: collection failed as expected with two `ModuleNotFoundError` errors for
   `auraly_pipeline.flow.generation_domain` (and, transitively, its locator module).

2. **Initial GREEN** — added the minimum typed domain contracts, semantic resolvers, package
   exports, and deterministic local pages. The same command passed: `33 passed in 2.80s`.

3. **Candidate disabled-state RED** — added the missing mutation-protection test for a
   `listitem` with `aria-disabled="true"` and ran:

   ```text
   uv run pytest tests/test_flow_generation_locators.py -q
   ```

   Result: one expected failure: the candidate-grid resolver accepted the disabled slot.

4. **Final GREEN / regression** — changed candidate enumeration and fingerprint matching to use
   the same actionable check as scalar controls, then ran:

   ```text
   uv run pytest tests/test_flow_generation_domain.py tests/test_flow_generation_locators.py tests/test_flow_locators.py tests/test_flow_runtime.py -q
   uv run ruff check src/auraly_pipeline/flow/generation_domain.py src/auraly_pipeline/flow/generation_locators.py src/auraly_pipeline/flow/locators.py src/auraly_pipeline/flow/__init__.py tests/test_flow_generation_domain.py tests/test_flow_generation_locators.py
   uv run mypy src/auraly_pipeline/flow/generation_domain.py src/auraly_pipeline/flow/generation_locators.py
   ```

   Results: `96 passed in 18.30s`; Ruff: `All checks passed!`; mypy: `Success: no issues found in 2 source files`.

## Delivered files

- `src/auraly_pipeline/flow/generation_domain.py`
- `src/auraly_pipeline/flow/generation_locators.py`
- `src/auraly_pipeline/flow/__init__.py`
- `src/auraly_pipeline/flow/locators.py` — extended the existing semantic-role type only, so
  Goal 4C can use `status`, `list`, and `listitem` without changing preflight behavior.
- `tests/fakes/flow-generation/{ready,upload-complete,generating,grid-two,grid-three,ambiguous-grid,missing-2k}.html`
- `tests/test_flow_generation_domain.py`
- `tests/test_flow_generation_locators.py`

## Decisions

- Candidate observations retain only a SHA-256 fingerprint, validated semantic order, and the
  completion fact. They reject extra raw URL/prompt/DOM fields.
- Fingerprints are SHA-256 over canonical JSON with only `slot_key` and normalized
  `completion_role`. The fixture-only safe attributes are `data-flow-candidate-id` and
  `data-flow-completion-role`; no URL, accessible text, DOM, or token is persisted.
- Every resolver requires one visible, enabled, non-`aria-disabled` semantic match and rejects a
  visible dialog/alertdialog before it resolves anything. Candidate identity or 2K action
  ambiguity fails closed.
- Route validation accepts only `https://labs.google/fx/tools/flow` or an exact descendant under
  `/fx/tools/flow/`, with no query or fragment. `file:` is deliberately a private local-fixture
  seam only when a caller supplies an exact allowlisted file URL; no public target or provider
  configuration was added.
- The cross-module structural scan covers Goal 4B locator/runtime plus the Goal 4C locator module
  and rejects XPath, `nth`, coordinate APIs, generated-class selectors, and image matching.

## Self-review

- `git diff --cached --check` was run before the implementation commit. The only whitespace
  finding was an end-of-file blank line in the new domain test; it is corrected in the report
  follow-up commit.
- The final focused suite preserves the existing Goal 4B locator and runtime contracts
  (`96 passed` total). No existing preflight JSON/status assertion was changed.
- Mutation coverage proves zero, multiple, hidden, disabled, blocking-overlay, and untrusted-route
  failures for scalar controls, candidate grids/slots, and exact 2K actions. It also proves a
  duplicated safe candidate identity cannot be selected.
- The new implementation has no click, fill, upload, network, provider, coordinate, XPath,
  position, blind-index, image-match, or raw-selector operation. It returns locators only for a
  later, checkpoint-controlled lifecycle task.

## Commits

- `a5bd53d4f8b2378791e1e3bbb0f380fc2159061d` — `feat: define Flow generation UI contract`
- `5ddb2a59fa8d255199ab2062ddf6c7dfcb711fcf` — `docs: record Flow generation UI contract`
  (report plus a test-format whitespace cleanup; it was not documentation-only).
- `9a0275bc4b7fba2257b8a382e6410a0dbb031233` — `fix: harden Flow generation locator trust`

## Remaining concerns / handoff

- Semantic labels and safe slot attributes are deterministic local-contract hypotheses. They are
  not evidence of a live Flow UI and do not establish browser-preflight or provider verification.
- Task 6 deliberately does not upload, fill prompts, dispatch Generate, record checkpoints, take
  grid evidence, or download artifacts. Those behaviors remain for Tasks 8–10 under their own
  red/green cycles.

## Review correction round 1/5

### Accepted findings and minimum fix

1. **Critical — route trust:** replaced the broad `startswith()` and arbitrary `file:` acceptance
   with private `_GenerationLocatorTarget` policy injection. The production policy permits only
   `https://labs.google/fx/tools/flow` and descendants under the slash boundary, with no query or
   fragment. `_local_test_target()` requires caller-supplied exact `file:` URLs with no suffixes.
2. **Important — grid revalidation:** `resolve_candidate_2k_action()` now calls the same complete
   grid validator as observation. It rejects every hidden, disabled, loading, failed, malformed,
   unknown, or duplicate slot before returning even the selected slot's action.
3. **Important — fixture identity:** `data-flow-candidate-id` and
   `data-flow-completion-role` now exist only in `_LOCAL_FIXTURE_IDENTITY_SOURCE`. The production
   policy's explicitly allowlisted semantic source is `data-candidate-id` plus
   `data-completion-role`; a provider page lacking either fails closed. This is a contract choice,
   not a claim that a live Flow UI exposes the attributes.
4. **Minor — fixtures/report truth:** added `loading-grid.html`, `failed-grid.html`, and a local
   provider-semantic identity fixture. Corrected the description of `5ddb2a5` above.

### RED / GREEN evidence

1. **Policy/identity RED:** the new local-policy tests initially failed at collection because the
   target-policy helpers did not exist. After the private target and identity-source policy were
   added, the locator suite passed: `31 passed in 6.48s`.
2. **Grid revalidation RED:** against the prior target-only action resolver, the focused
   non-target invalid-slot suite failed for hidden, disabled, malformed, loading, and failed
   slots (`5 failed, 1 passed`). The duplicate case was then corrected to duplicate two
   *non-target* identities in a three-slot grid, so it protects global uniqueness rather than only
   the selected fingerprint.
3. **Hidden-slot RED:** after reusing the validator, the hidden non-target case still failed
   because ordinary role enumeration omits hidden nodes. The minimal correction enumerates grid
   slots with `include_hidden=True` and validates their visibility before accepting the grid.
4. **Final GREEN:**

   ```text
   uv run pytest tests/test_flow_generation_locators.py -q
   # 37 passed in 4.64s

   uv run pytest tests/test_flow_generation_domain.py tests/test_flow_generation_locators.py tests/test_flow_domain.py tests/test_flow_config.py tests/test_flow_lock.py tests/test_flow_locators.py tests/test_flow_runtime.py -q
   # 201 passed, 2 skipped in 24.47s

   uv run pytest tests/test_flow_service.py tests/test_flow_cli.py tests/test_flow_security.py -q
   # 75 passed, 1 skipped in 19.29s

   uv run pytest tests/test_flow_diagnostics.py -q
   # 62 passed in 1.33s

   uv run ruff check src/auraly_pipeline/flow/generation_locators.py src/auraly_pipeline/flow/locators.py tests/test_flow_generation_locators.py
   uv run mypy src/auraly_pipeline/flow/generation_domain.py src/auraly_pipeline/flow/generation_locators.py src/auraly_pipeline/flow/locators.py
   uv run python scripts/verify.py fast
   ```

   Ruff passed; mypy passed for the focused modules and then all `56` source files through
   `verify.py fast`.

### Round-1 files

- `src/auraly_pipeline/flow/generation_locators.py`
- `src/auraly_pipeline/flow/locators.py`
- `tests/test_flow_generation_locators.py`
- `tests/fakes/flow-generation/loading-grid.html`
- `tests/fakes/flow-generation/failed-grid.html`
- `tests/fakes/flow-generation/production-safe-grid.html`
