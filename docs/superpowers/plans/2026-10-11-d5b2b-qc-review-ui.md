# D5B.2B QC / Human Review API/UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Executar QC e registrar review humano por variante na tela de render, com recuperação após reload.

**Architecture:** Reutilizar QcService, HumanReviewService, mídia verificada e a fila
api.local.operation. Consultas descobrem evidência do alvo exato; QC roda no worker
local_operations, enquanto a decisão humana é salva diretamente pelo serviço existente.

**Tech Stack:** Python 3.11, Pydantic, FastAPI, SQLAlchemy/Jobs existentes, React,
TypeScript, Vitest, pytest e Playwright; nenhuma dependência nova.

**Spec:** `docs/superpowers/specs/2026-10-11-d5b2b-qc-review-ui-design.md`

## Global Constraints

- Design escrito aprovado em 2026-10-11; plano ainda aguarda aprovação e método de execução.
- Nenhuma nova dependência, tabela ou migration.
- QC explícito por um output rendered/reused de um Job completed e plano salvo.
- Reutilizar `api.local.operation` e worker `local_operations`; nenhum Job type novo.
- Polling serial a cada 2 s somente para QC ativo; sem retry ou reenvio automático.
- Aprovar exige QC passed, nome e confirmação explícita de assistir/ouvir; rejeitar exige comentário.
- Decisão definitiva por versão D5B.2A; sem editar, revogar ou herdar aprovação.
- Nenhum provider, encode, delivery, proxy/contact sheet ou playback tracking novo.
- Flow pausado; AGENTS.md, sources sincronizados e temporários alheios preservados.
- Todos os comandos shell usam `rtk`; edições locais usam apply_patch.
- Implementar em worktree isolado via using-git-worktrees; não iniciar nesta branch documental.
- TDD por tarefa; gate full e revisão independente antes de integração autorizada separadamente.

## Review Focus

1. QC de outro plano no mesmo diretório render/variante: filtrar contexto válido, nunca adotar (Task 1).
2. Registro malformado ou symlink quebrado: erro de integridade, nunca ausência (Task 1).
3. QC concluído com output error: Job completed não implica passed nem permite decisão (Tasks 2–3).
4. Worker ocupado por outro fluxo: manter QC queued sem duplicar envio (Task 3).
5. Review gravado por outro cliente durante envio incerto: consultar vencedor definitivo, não sobrescrever (Tasks 2–3).

## Files and boundaries

- `editing/qc_service.py`, `editing/review_service.py`: descoberta pública verificada; sem acesso API/Jobs.
- `api/review_contracts.py` (novo): alvo, visão QC e estado de review tipados.
- `api/review_commands.py`, `api/review_routes.py` (novos): contexto de render, consultas e decisões HTTP.
- `api/action_contracts.py`, `api/commands.py`, `api/app.py`: conectar operação edit_qc e registrar rotas.
- `editing/schema.py`, `scripts/verify.py`, `schemas/`: exportar e proteger schemas novos.
- `web/src/reviewApi.ts`, `ReviewPanel.tsx` (novos): contrato HTTP e fluxo por output.
- `web/src/RenderPanel.tsx`: compor ReviewPanel sem mudar rascunho/preview/render.
- Testes novos específicos abaixo; helpers existentes importados explicitamente.

Todos os paths Python abaixo são relativos a `src/auraly_pipeline/`, salvo tests/scripts.
Não reorganizar ApiCommands nem introduzir acesso a internals privados de Jobs.

### Task 1: Descoberta de QC e decisão da versão atual

**Files:** Modify `editing/qc_service.py`, `editing/review_service.py`.
Create `tests/test_review_discovery.py`; reuse `tests/review_helpers.py`.

**Interfaces:**
- Consumes `QcService.get_report(campaign_id, video_id, plan_hash, output_variant_id, render_key, qc_key) -> QcReport`.
- Consumes `HumanReviewService.get_review(campaign_id, video_id, plan_hash, output_variant_id, render_key, review_key) -> HumanReviewRecord`.
- Produces `QcService.list_reports(campaign_id: str, video_id: str, plan_hash: str, output_variant_id: str, render_key: str) -> list[QcReport]`.
- Produces `HumanReviewService.find_review(campaign_id: str, video_id: str, plan_hash: str, output_variant_id: str, render_key: str) -> HumanReviewRecord | None`.

- [ ] **1. Write discovery tests**, importing review_case explicitly. Core assertion:

```python
def test_discovery_returns_verified_original(review_case):
    service, request, report = review_case
    target = (request.campaign_id, request.video_id, request.plan_hash,
              request.output_variant_id, request.render_key)
    assert service.qc.list_reports(*target) == [report]
    assert service.find_review(*target) is None
    record = service.record(request)
    assert service.find_review(*target) == record
```

Add `test_foreign_plan_qc_not_adopted`, `test_corrupt_or_broken_link_is_not_absence`,
`test_current_review_stale_evidence_errors`, `test_discovery_never_runs_ffmpeg`.
Assert changed master/receipt/QC bytes fail, no writes on GET, reports sorted by
qc_key, valid foreign-plan reports excluded, malformed directories/reports error.

- [ ] **2. RED:** `rtk proxy uv run pytest tests/test_review_discovery.py -q`;
  expect missing public methods, not fixture/media setup failure.
- [ ] **3. Implement methods above.** Validate exact saved plan/master before discovery.
  Enumerate only qc/{variant}/{renderKey}, validate directory/key/JSON context;
  include same-plan reports only after get_report. Compute current review_key from
  verified master/receipt and use get_review; only a genuinely missing expected
  directory/file yields None. Reject malformed/broken links, do not mask OSError
  or validation errors as absence. Existing empty parent directories are allowed.
- [ ] **4. GREEN:** `rtk proxy uv run python scripts/verify.py fast --pytest tests/test_review_discovery.py tests/test_review_service.py tests/test_qc_service.py`;
  all selected tests, Ruff and typing pass.
- [ ] **5. Commit** explicit two services and new test:
  `feat(review): discover verified QC and current human decision`.

### Task 2: Output-scoped API and queued QC operation

**Files:** Create `api/review_contracts.py`, `api/review_commands.py`, `api/review_routes.py`,
`tests/test_api_final_review.py`; modify `api/action_contracts.py`, `api/commands.py`,
`api/app.py`, `editing/schema.py`, `scripts/verify.py`, `tests/test_verify_harness.py`.
Generate `schemas/final-qc-operation.schema.json`, `final-qc-run-view.schema.json`,
`final-review-state.schema.json`. Reuse `tests/render_job_helpers.py`.

**Interfaces:**
- In action_contracts: `EditQcOperation(OperationRequest)` with operation literal
  `edit_qc`, schema_version `1.0`, job_id (render Job UUID), video_id,
  plan_hash, output_variant_id, render_key, execution_id UUID, plus inherited campaign_id.
  IDs/hashes validated with existing types; include in OperationKind/LocalOperationRequest.
- `EditQcResult(ContractModel)` fields operation=`edit_qc`, request: EditQcOperation,
  result: QcBatchResult; include in OperationResult. Validate one matching output/context.
- In review_contracts: `ReviewTarget` fields schema_version=`1.0`, campaign_id,
  job_id, video_id, plan_hash, output_variant_id, render_key; typed validators as above.
- `QcRunView(OperationView)` adds request: EditQcOperation; validate submission,
  payload/result context, statuses, UUIDs and operation=`edit_qc`.
- `FinalReviewState(ReviewTarget)` adds reports: list[QcReport],
  review: HumanReviewRecord | None, runs: list[QcRunView]; all entries must match
  target consumer context; duplicate qc_keys/execution_ids rejected.
- `ReviewCommands(renders: RenderCommands, qc: QcService, reviews: HumanReviewService,
  jobs: JobService, get_operation: Callable[[str, str], OperationView])` keyword constructor.
  Public `state(campaign_id, job_id, output_variant_id) -> FinalReviewState`,
  `validate_qc(request: EditQcOperation) -> QcRequest`,
  `record(campaign_id, job_id, output_variant_id, request: HumanReviewRequest) -> HumanReviewRecord`.
  String parameters use str; consumed Task 1 methods retain their exact signatures.

- [ ] **1. Write HTTP tests** with real saved plan/completed render from render_case:
  `test_qc_post_only_enqueues`, `test_qc_execution_identity_survives_restart`,
  `test_state_empty_then_qc_then_review`, `test_completed_qc_error_not_passed`,
  `test_foreign_failed_stale_target_rejected`, `test_review_rules_and_definitive_conflict`,
  `test_review_concurrent_client_winner`, `test_review_state_invalid_evidence_errors`.
  Assert POST qc is 202, identical execution returns same jobId, different execution
  can reuse report, no analyzer on HTTP/GET; state restart discovers original.
  Approve without checkbox/with blocked QC fails; rejected with comment succeeds;
  identical decision returns original, altered decision conflicts without overwrite.
  Assert no private paths/secrets/raw exception messages and unknown body/query fields rejected.
- [ ] **2. RED:** `rtk proxy uv run pytest tests/test_api_final_review.py -q`;
  expect missing routes/contracts. Add failing exporter/harness assertions for three schemas.
- [ ] **3. Implement interfaces and routes.** Prefix is existing render output URL.
  GET review-state -> FinalReviewState, POST qc -> OperationSubmission (202),
  POST review -> HumanReviewRecord (200); Cache-Control no-store for state.
  Match URL campaign/job/variant with body, derive plan/context from RenderCommands.get,
  verify successful output and master handle; mismatches invalid_request, missing
  Job/output not_found, failed output operation_not_allowed, corrupt evidence artifact_invalid.
  Reuse existing sanitized error contracts; definitive conflict operation_conflict.
  Register routes in app; compose services as ApiCommands.editorial_reviews.
- [ ] **4. Connect queued operation.** ApiCommands.submit_operation validates edit_qc
  before enqueue; existing hash-based identity includes execution_id. Handler calls
  validate_qc again then QcService.run; wrap EditQcResult. No new worker kind/type.
  state discovers runs using jobs.list_jobs/get_job and existing get_operation,
  validates payload/result and includes only matching render Job/context/output.
  Persisted completed result is informational; state reports/review use verified services.
- [ ] **5. Export schemas** via export_final_review_schemas in editing/schema.py;
  serialization mode for views/state, validation for operation; register generated
  paths in harness. Existing domain QC/review schemas remain unchanged.
- [ ] **6. GREEN:** `rtk proxy uv run python scripts/verify.py fast --pytest tests/test_api_final_review.py tests/test_api_render_http.py tests/test_api_operations.py tests/test_verify_harness.py`;
  also run `rtk proxy uv run python -m auraly_pipeline.editing.schema`, inspect schema-only diff.
- [ ] **7. Commit** explicit API/exporter/tests/schema paths:
  `feat(review): expose verified state and queue per-output QC`.

### Task 3: QC, player and definitive review in RenderPanel

**Files:** Create `web/src/reviewApi.ts`, `ReviewPanel.tsx`, `reviewApi.test.ts`,
`ReviewPanel.test.tsx`; modify `web/src/RenderPanel.tsx` and `RenderPanel.test.tsx`.

**Interfaces:** TS JSON types mirror Task 2 camelCase and existing QC/review schemas.
`ReviewTarget`, `EditQcOperation`, `QcRunView`, `FinalReviewState`, `HumanReviewRequest`,
`HumanReviewRecord`, `QcReport` are exported from reviewApi.ts.
Export `OperationSubmission` matching the existing API submission: jobId, campaignId,
operation literal `edit_qc`, voiceMasterId nullable; its jobId is the QC operation,
not the render Job ID carried in EditQcOperation/ReviewTarget.
`getReviewState(target: ReviewTarget, signal: AbortSignal): Promise<FinalReviewState>`;
`submitQc(body: EditQcOperation): Promise<OperationSubmission>`;
`submitReview(target: ReviewTarget, body: HumanReviewRequest): Promise<HumanReviewRecord>`;
`startQcWorker(campaignId: string): Promise<WorkerState>` (local_operations).
`ReviewPanel({target}: {target: ReviewTarget})` exported; keyed by entire target.
Reuse api.ts read/post/errors/readWorker and renderApi.ts renderMediaUrl.

- [ ] **1. Write adapter tests** `rejects_foreign_or_malformed_state`,
  `rejects_mismatched_qc_submission`, `rejects_mismatched_review_record`;
  assert schema/context/key/status consistency, nullable result rules and escaped URLs.
  Add React tests `qc_is_explicit_and_completed_error_is_not_passed`,
  `worker_busy_preserves_queued_qc`, `lost_qc_response_is_consulted_not_reposted`,
  `lost_review_response_recovers_original_or_retries_frozen_request`,
  `another_client_decision_is_readonly`, `multiple_qc_requires_selection`,
  `stale_response_or_target_change_clears_old_approval`, `human_gate_and_rejection_comment`.
  Assert player controls/preload metadata/no autoplay; name+checkbox required for
  approve, name+comment for reject; permanent technical-vs-human warning; no edit/revoke.
- [ ] **2. RED:** `rtk proxy npm --prefix web test -- reviewApi.test.ts ReviewPanel.test.tsx`;
  expect missing module/component or specified behavior, not server fixture errors.
- [ ] **3. Implement adapter and component.** Runtime-validate unknown JSON using
  existing validator style; frozen target/request, one submission lock, cancel
  reads/discard late replies. Poll GET review-state serially every 2000 ms only
  while QC active. Consult unknown executionId; never silently create another UUID.
  Worker busy leaves job queued; explicit start/consult, warning about other local ops.
  Review POST stays direct; unknown response disables actions until successful GET.
  Absent decision permits explicit retry of exact frozen request; another winner
  becomes readonly. Consultation error removes actionable approval, not treated as absence.
  One report auto-selected; multiple require choice; existing decision selects original qcKey.
  No QC report allows Executar QC, not review. Invalid state blocks all sends;
  busy/unknown/existing decision disable the respective inappropriate actions.
  Explicit consultation/abandonment never cancels QC or resubmits it; an unknown
  QC not found after successful consultation may be abandoned with confirmation
  before a voluntary new execution. Failed consultation never authorizes this.
- [ ] **4. Compose into successful output articles** in RenderPanel using job's
  saved context/renderKey; failed outputs unchanged. Use native video and existing
  media URL/links; labels scoped to variant; name/comment/checkbox and explicit
  confirmation of definitive decision. No localStorage authority or playback timers.
- [ ] **5. GREEN:** `rtk proxy npm --prefix web test -- reviewApi.test.ts ReviewPanel.test.tsx RenderPanel.test.tsx EditingPanel.test.tsx`;
  `rtk proxy npm run ui:build`; selected tests, TypeScript and build pass.
- [ ] **6. Commit** six explicit frontend files:
  `feat(review): add explicit QC and human decisions to render outputs`.

### Task 4: Real local E2E and delivery checkpoint

**Files:** Create `tests/test_web_final_review_e2e.py`; modify `.github/workflows/verify.yml`
only to include new focused Python tests once, preserving existing timeout/encoder rules.
Update `README.md`, `docs/PROJECT-MEMORY.md`, `docs/GOAL-ROADMAP.md`,
`docs/PRD-MVP-MASS-VIDEO-AUTOMATION.md`; create
`docs/superpowers/2026-10-11-d5b2b-verification.md` for evidence, not raw logs.
Reuse explicit helper imports/patterns from tests/test_web_render_e2e.py.

- [ ] **1. Write E2E** `test_render_output_qc_approval_reload_and_rejection`: synthetic
  campaign/render with two successful outputs; queue/run QC through UI, inspect
  delivered player metadata, approve first, reload and recover exact decision;
  reject second with comment and recover it. Snapshot hashes and upstream/budget
  before QC; assert unchanged after review and no renderer/provider call during QC.
  Add `test_changed_evidence_removes_actionable_approval` with sanitized UI error.
- [ ] **2. Run E2E and confirm its assertions.** If it exposes missing behavior,
  record actual failing assertion (RED) before smallest correction; otherwise
  record successful integration verification, not fabricated RED.
  `rtk proxy uv run pytest tests/test_web_final_review_e2e.py -q`.
- [ ] **3. Verify all UI tests/build** with `rtk proxy npm run ui:test` and
  `rtk proxy npm run ui:build`; update Windows focused target list and assert no duplicates.
- [ ] **4. Run sole-active full gate:** `rtk proxy uv run python scripts/verify.py full`;
  read complete log and exit, require 19/19. Preserve ignored logs; no paid canary.
- [ ] **5. Update docs** with actual counts/status/limits; separate delivered D5B.2B
  from planned D5B.3. Record missing tools/audit warnings and deferred findings honestly.
  Run `rtk git diff --check`, review scoped changes for secrets/media/private paths.
- [ ] **6. Commit** scoped E2E/workflow/docs:
  `test(review): verify local QC and human review end to end`.
- [ ] **7. Independent whole-branch review** per requesting-code-review after all
  tasks; one fresh reviewer for Native. Correct Critical/Important with focused
  RED→GREEN and repeat affected verification; disclose any deferred Minor.
  Commit final evidence separately. No automatic merge/push; ask for integration direction.

## Self-review and execution handoff

Tasks 1–2 own evidence and HTTP/worker integrity; Task 3 owns user intent/recovery;
Task 4 proves their composition without changing real campaigns. All five Review
Focus cases have named tests; no speculative scheduling/indexing/authentication.
State discovery is read-only; QC is queued; review is direct; these do not conflict.
Schemas and JSON naming align across tasks; old review keys/contracts are preserved.

Recommend Native: four sequential interface-dependent tasks, using mature services
and one whole-branch independent review. Subagent-driven is an alternative with
independent task reviews and higher context cost. Approval of this written plan
and execution method is required before worktree setup or product-code edits.
