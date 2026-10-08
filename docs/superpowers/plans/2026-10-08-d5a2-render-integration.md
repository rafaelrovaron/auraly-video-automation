# D5A.2 Render Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (Native, recommended here) or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Renderizar planos salvos pela UI local, acompanhar Jobs e abrir/baixar os masters por variante.

**Architecture:** Um Job `editing.render` por execução chama o RenderService existente. API enfileira/consulta e o worker atual drena somente esse tipo; React consome essas interfaces sem reinventar o renderer. Primeiro backend operável, depois UI.

**Tech Stack:** Python 3.11, Pydantic, SQLAlchemy/SQLite, FastAPI, React/TypeScript, pytest/Vitest/Playwright e FFmpeg locais já instalados.

**Spec:** `docs/superpowers/specs/2026-10-08-d5a2-render-integration-design.md`, aprovada pelo usuário em 2026-10-08. Base de produção `501d312`; design commit `1c46977`.

## Global Constraints

- Um Job por plano inteiro; variantes sequenciais pelo RenderService existente.
- Request: schemaVersion `1.0`, campaignId/videoId/planHash/executionId; executionId UUID do cliente.
- `MANUAL_ONLY`, `max_attempts=1`, sem retry automático; novo executionId somente para nova execução explícita.
- Sem migration/tabela de render: plano/recibo em arquivos e execução em Jobs.
- Sem regenerar imagem, voz ou HeyGen; sem chamadas externas pagas ou dependências novas.
- Sem timeline, preview frame-perfect, progresso fictício, paralelismo de variantes ou cancelamento de FFmpeg pela UI.
- CLI e recibos D5A.1 permanecem compatíveis; planHash do recibo identifica o produtor, não necessariamente o consumidor.
- QC editorial/aprovação/delivery são D5B; renderizado não significa aprovado/entregue.
- Preservar AGENTS.md, sources/, mudanças alheias e a pasta temporária não rastreada existente.
- Toda execução shell usa `rtk`; edits usam apply_patch. Isolar implementação com using-git-worktrees no início da execução, não nesta etapa documental.

## Review Focus

1. Resposta de POST perdida: reenviar o mesmo executionId retorna o Job original; lista permite reconciliação exata (Tasks 3/5/6).
2. Resultado completed com uma variante failed: UI mostra falha parcial, nunca sucesso integral (Tasks 1/3/6).
3. Master reutilizado de outro plano: download aceita provenance do produtor sem aceitar output/recibo de outra identidade (Task 4).
4. Worker já ocupado: Job permanece queued; iniciar depois não cria outro Job nem drena operações de providers (Tasks 2/6).
5. Troca de campanha/plano durante polling: resposta tardia não aparece no novo contexto e abandonar acompanhamento não cancela Job (Tasks 5/6).

## Arquivos e interfaces

- `editing/render_job_domain.py`: request persistido, sem dependência de API.
- `editing/render_handler.py`: adaptação RenderService → JobExecutionResult.
- `api/render_contracts.py`: submission/view e estado agregado.
- `api/render_commands.py`: admissão, consultas e seleção de mídia, usando interfaces públicas.
- `api/render_routes.py`: quatro endpoints finos; registrar em api/app.py.
- `api/commands.py`: somente wiring do renderer/handler e propriedade `editorial_renders`.
- `api/action_contracts.py`, `api/worker.py`: adicionar `editing_render`.
- `editing/schema.py`, schemas/: export dos novos contratos sem alterar exports existentes.
- `web/src/renderApi.ts`, `RenderPanel.tsx`: cliente e fluxo de render isolados.
- `web/src/EditingPanel.tsx`: entregar ao RenderPanel somente plano confirmado salvo/consultado.
- Testes novos acompanhando esses módulos; CI e documentação no fechamento.

Não mover código antigo ou criar framework de commands/progress/media. O serviço API
novo depende de JobService, EditBatchService, RenderService e callback require_campaign;
o handler depende só de RenderService. Não acessar `_repository` ou `_handlers`.

### Task 1: Contratos e schemas

**Files:** Create `src/auraly_pipeline/editing/render_job_domain.py`, `src/auraly_pipeline/api/render_contracts.py`, `tests/test_render_job_domain.py`; modify `src/auraly_pipeline/editing/schema.py`, `scripts/verify.py`, `tests/test_verify_harness.py`; create `schemas/render-job-request.schema.json`, `schemas/render-job-submission.schema.json`, `schemas/render-job-view.schema.json`.

**Interfaces:** `RenderJobRequest(EditingModel)` com campos do spec (execution_id: UUID);
`RenderJobSubmission` inclui os mesmos campos e job_id; `RenderJobView` acrescenta
status: JobStatus, result: RenderBatchResult | None, render_status:
Literal['succeeded','partial_failure','failed'] | None e error_code: ErrorCode | None.
No módulo de domínio, `validate_job_result(result: RenderBatchResult) -> None` e
`render_status(result: RenderBatchResult) -> Literal['succeeded','partial_failure','failed']`;
API e handler compartilham essas funções sem dependência editing → api.
`export_render_job_schemas(output_dir: Path) -> tuple[Path, ...]`, chamado pelo módulo schema;
preservar export_render_schemas com seus dois arquivos atuais.

- [ ] Write `test_request_rejects_paths_overrides_unknown_fields_and_invalid_uuid`: payload literal válido é aceito; extra sourcePath/overrides, UUID inválido, videoId traversal e SHA inválido levantam ValidationError.

```python
with pytest.raises(ValidationError):
    RenderJobRequest.model_validate({"schemaVersion": "1.0", "campaignId": "campaign-one",
        "videoId": "video-one", "planHash": "a" * 64,
        "executionId": "11111111-1111-4111-8111-111111111111", "sourcePath": "../private.mp4"})
```
- [ ] Write `test_aggregate_requires_real_nonempty_results`: outcomes literais rendered/reused → succeeded, rendered/failed → partial_failure, failed → failed; resultado vazio, dryRun=true, planned e variante duplicada não são resultado válido de Job completed. `test_view_requires_result_only_on_completed` verifica coerência de status/result/renderStatus e planHash.
- [ ] Run `rtk uv run pytest tests/test_render_job_domain.py -q`; confirmar RED pela ausência dos contratos.
- [ ] Implementar os contratos/validadores e agregação mínima acima; não alterar RenderBatchResult da CLI para impor restrições exclusivas dos Jobs. Remover campos computed ao serializar para persistência; resposta API inclui hasFailures. Exportar request em mode validation e submission/view em mode serialization. Acrescentar os três paths à etapa editing schemas do harness e testar que drift é detectado.
- [ ] Run `rtk uv run python -m auraly_pipeline.editing.schema` e `rtk uv run pytest tests/test_render_job_domain.py tests/test_render_domain.py -q`; exigir PASS e apenas três schemas novos.
- [ ] Commit `feat(render): define persistent render job contracts` com somente arquivos desta tarefa.

### Task 2: Handler e worker dedicado

**Files:** Create `src/auraly_pipeline/editing/render_handler.py`, `tests/test_render_handler.py`; modify `src/auraly_pipeline/api/commands.py`, `src/auraly_pipeline/api/action_contracts.py`, `src/auraly_pipeline/api/worker.py`, `tests/test_api_worker.py`.

**Interfaces:** `RenderJobHandler(renderer: RenderService)`, retry_safety MANUAL_ONLY,
`execute(context: JobExecutionContext) -> JobExecutionResult`. ApiCommands cria o renderer
antes de construir JobService e registra `editing.render`. JOB_TYPES['editing_render'] =
'editing.render'; usar o loop/heartbeat atual, sem novo executor.

- [ ] Write `test_handler_checks_campaign_and_calls_saved_plan`: contexto de campanha divergente falha sem render; válido chama render(campaign_id, video_id, plan_hash, dry_run=False) e persiste resultado completo. Para fluxo handler, double específico somente na operação de mídia; resultado literal independente.
- [ ] Write `test_partial_result_completes_with_all_variant_results`: outcome SUCCESS preserva uma saída failed e uma rendered; `test_global_failure_is_sanitized` cobre EditingError e exceção inesperada sem paths/logs internos.

```python
assert outcome.outcome == JobExecutionOutcome.SUCCESS
assert [item["status"] for item in outcome.result["outputs"]] == ["rendered", "failed"]
assert outcome.result["dryRun"] is False
```
- [ ] Add `test_render_worker_leaves_provider_and_other_campaign_jobs_queued` e `test_render_stop_keeps_active_job_and_heartbeat`: usar Events/lease curto como teste worker atual; Job ativo conclui, próximo permanece queued, heartbeat é observado e start concorrente retorna operation_conflict.
- [ ] Run `rtk uv run pytest tests/test_render_handler.py tests/test_api_worker.py -q`; confirmar RED esperado.
- [ ] Implementar handler com validação request/context e mapeamento de erro global sanitizado. Completed com falhas individuais continua SUCCESS; handler rejeita resultado inválido pelos contratos Task 1. Integrar wiring/kind sem alterar outros handlers.
- [ ] Run `rtk uv run python scripts/verify.py fast --pytest tests/test_render_handler.py tests/test_api_worker.py tests/test_render_job_domain.py`; exigir 3/3 PASS.
- [ ] Commit `feat(render): execute saved plans through the existing worker`.

### Task 3: Enfileiramento e consultas persistidas

**Files:** Create `src/auraly_pipeline/api/render_commands.py`, `tests/render_job_helpers.py`, `tests/test_render_job_commands.py`; modify `src/auraly_pipeline/api/commands.py`.

**Interfaces:** `RenderCommands(*, jobs: JobService, editing: EditBatchService,
renderer: RenderService, require_campaign: Callable[[str], object])`.
`submit(request: RenderJobRequest) -> RenderJobSubmission`;
`get(campaign_id: str, job_id: str) -> RenderJobView`;
`list(campaign_id: str, *, video_id: str | None = None, plan_hash: str | None = None) -> list[RenderJobView]`.
Expose `ApiCommands.editorial_renders`, sem criar wrappers para cada método.

Helper test-only `render_api_case(tmp_path: Path) -> RenderApiCase` contém settings,
commands, plan, request. Reusar create_api_fixture, make_render_plan e publish_test_plan;
fixture só mídia sintética local, campaign-one/video-one, executionId literal
`11111111-1111-4111-8111-111111111111`; caller fecha recursos existentes após teste.

- [ ] Write `test_submission_is_idempotent_and_does_not_encode`: mesmo request dá mesmo jobId; status queued, maxAttempts=1/manual_only e zero renders publicados. Proibir detect_runtime/encode durante submit para provar fronteira.

```python
first = case.commands.editorial_renders.submit(case.request)
assert case.commands.editorial_renders.submit(case.request).job_id == first.job_id
job = case.commands.jobs.get_job(first.job_id)
assert job.status == "queued" and job.max_attempts == 1
assert job.retry_safety == "manual_only"
assert not list(case.settings.work_root.glob("campaigns/*/editing/renders/*/*/*.mp4"))
```
- [ ] Write `test_explicit_new_execution_gets_new_job_and_reuses_outputs`: executar real renderer, guardar bytes/hash dos recibos, trocar somente executionId e executar; novo Job retorna reused e recibos ficam idênticos.
- [ ] Write `test_views_validate_job_plan_and_output_identity`: outra campanha/tipo → not_found; request/result/plan corrompidos, saída faltando/extra/reordenada ou planHash divergente → artifact_invalid. List filtra videoId/planHash e inclui executionId para resposta perdida.
- [ ] Run `rtk uv run pytest tests/test_render_job_commands.py -q`; confirmar RED esperado.
- [ ] Implementar chave `editing.render:<content_hash(payload)>`; usar JobSubmit, jobs.submit_job/get_job/list_jobs públicos. Validar plano antes da submissão; handler revalida via RenderService. Filtrar tipo/campanha antes de expor view. Checar outputs/result contra ordem/keys/ids do plano salvo; não exigir mesmo planHash produtor do receipt. Derivar renderStatus; erro global usa allowlist ERROR_MESSAGES.
- [ ] Run `rtk uv run python scripts/verify.py fast --pytest tests/test_render_job_commands.py tests/test_render_service.py tests/test_api_operations.py`; exigir 3/3 PASS.
- [ ] Commit `feat(render): queue and inspect immutable plan executions`.

### Task 4: HTTP e acesso ao master

**Files:** Create `src/auraly_pipeline/api/render_routes.py`, `tests/test_api_render_http.py`, `tests/test_api_render_media.py`; modify `src/auraly_pipeline/api/app.py`, `src/auraly_pipeline/api/render_commands.py`.

**Interfaces:** `register_render_routes(app: FastAPI) -> None`; usar Commands,
matching e aliases validados atuais. Paths e respostas exatamente seção 4 do spec;
list retorna Items[RenderJobView]. `RenderCommands.open_media(campaign_id: str,
job_id: str, output_variant_id: str) -> tuple[BinaryIO, str]`: handle validado e
filename conhecido do plano. Caller possui/fecha handle.

- [ ] Write `test_post_only_queues_and_openapi_has_typed_contracts`: 202 sem encoder/provider, mismatch body/path → erro atual, GET recupera Job após novo app/commands.

```python
response = client.post("/api/v1/campaigns/campaign-one/editing/renders",
    json=case.request.model_dump(mode="json", by_alias=True))
assert response.status_code == 202
assert response.json()["executionId"] == "11111111-1111-4111-8111-111111111111"
assert case.commands.jobs.get_job(response.json()["jobId"]).status == "queued"
```
- [ ] Write `test_media_serves_verified_master_inline_and_attachment`: rendered/reused válido → video/mp4, filename conhecido, Content-Disposition inline/attachment segundo download e Cache-Control no-store. Corromper hash/recibo, symlink, variante failed, Job estrangeiro ou path fora de root → erro sem bytes privados.
- [ ] Write `test_media_allows_reuse_from_another_producer_plan`: master reused com receipt.planHash anterior serve; mudar outputHash/renderKey/variant no receipt é rejeitado. `test_media_reads_verified_open_handle` troca o pathname depois da validação e comprova que resposta usa o handle validado, não reabre um arquivo diferente.
- [ ] Run `rtk uv run pytest tests/test_api_render_http.py tests/test_api_render_media.py -q`; confirmar RED esperado.
- [ ] Implementar rotas finas e open_media: get view/plano, validar saída/receipt/path canônico conhecido, identities, source/input hashes declarados no plano, render_key com runtime do recibo, tamanho e SHA do handle aberto. Hash e rewind do mesmo handle; StreamingResponse lê chunks/fecha em finally, inclusive erro/abandono. Não carregar master inteiro em RAM ou rodar FFmpeg em GET. Não exigir FFmpeg atual igual ao runtime produtor para baixar master existente.
- [ ] Run `rtk uv run python scripts/verify.py fast --pytest tests/test_api_render_http.py tests/test_api_render_media.py tests/test_render_job_commands.py tests/test_api_actions_http.py`; exigir PASS. Testes verificam cleanup do handle e preservação de arquivos.
- [ ] Commit `feat(api): expose render executions and verified masters`.

### Task 5: Cliente React tipado

**Files:** Create `web/src/renderApi.ts`, `web/src/renderApi.test.ts`; modify `web/src/api.ts`, `web/src/api.test.ts`, somente para incluir editing_render no WorkerKind e validação do estado do worker.

**Interfaces:** RenderJobRequest/Submission/View espelham JSON Task 1;
`submitRender(request: RenderJobRequest): Promise<RenderJobSubmission>`;
`getRender(campaignId: string, jobId: string, signal: AbortSignal): Promise<RenderJobView>`;
`listRenders(campaignId: string, videoId: string, planHash: string, signal: AbortSignal): Promise<Items<RenderJobView>>`;
`renderMediaUrl(campaignId: string, jobId: string, outputVariantId: string, download?: boolean): string`.
`startRenderWorker(campaignId: string): Promise<WorkerState>` usa post no endpoint
worker/start atual com kind editing_render; status do worker usa read no endpoint
worker atual e o contrato WorkerObservation existente. Reusar read/post/campaignPath,
sem retry automático nem novo endpoint de worker.

- [ ] Write `rejects inconsistent completed outcomes`: fixtures literais das três agregações; result dryRun/planned/vazio, erro incoerente, executionId inválido e path externo são invalid_response.
- [ ] Write `preserves submitted execution identity and aborts stale reads`: response de POST deve corresponder campaign/video/plan/execution enviados; sinais AbortSignal preservados. URLs codificam ids, nenhum path de mídia do JSON vira URL navegável.

```typescript
expect(renderMediaUrl('campaign-one','job-one','variant-a',true)).toBe(
  '/api/v1/campaigns/campaign-one/editing/renders/job-one/outputs/variant-a/media?download=true');
// Fixture de response com executionId diferente do request:
await expect(submitRender(request)).rejects.toMatchObject({code:'invalid_response'});
```
- [ ] Run `rtk npm --prefix web run test -- src/renderApi.test.ts`; confirmar RED esperado.
- [ ] Implementar types/guards/client. Campos do resultado incluem schemaVersion/planHash/dryRun/outputs/hasFailures conforme JSON API; manter constraints de Task 1 e matching de identidade, sem parser genérico novo.
- [ ] Run `rtk npm --prefix web run test -- src/renderApi.test.ts src/editingApi.test.ts`; exigir PASS e rodar build UI do harness.
- [ ] Commit `feat(ui): add typed render job client`.

### Task 6: Controles e acompanhamento na edição

**Files:** Create `web/src/RenderPanel.tsx`, `web/src/RenderPanel.test.tsx`; modify `web/src/EditingPanel.tsx`, `web/src/EditingPanel.test.tsx`, `web/src/WorkerControls.tsx`, `web/src/WorkerControls.test.tsx`.

**Interfaces:** `RenderPanel({campaignId, plan}: {campaignId: string; plan: EditBatchPlan | null})`.
Pai passa somente saved/view confirmado correspondente à seleção de plano, nunca
validated/draft. Plano consultado e plano salvo expostos com videoId/planHash.

- [ ] Write `requires saved plan and freezes submission`: draft/validated sem saved/view não habilitam Renderizar; duplo clique envia uma vez, edição de rascunho não muda request. Identidade plana exibida; crypto.randomUUID gera executionId uma vez por envio explícito.

```tsx
render(<RenderPanel campaignId="campaign-one" plan={null}/>);
expect(screen.getByRole('button',{name:'Renderizar',exact:true})).toBeDisabled();
```
- [ ] Write `busy worker keeps known queued job without resubmit`: POST sucesso + start conflito deixa Job conhecido queued; botão Iniciar render pendente só chama start(kind editing_render). Resposta POST perdida mostra Consultar execuções e reconcilia exclusivamente executionId/campanha/vídeo/plano correspondente.
- [ ] Write `polls serially and ignores late results after plan change`: intervalo 2s iniciado só depois do GET anterior; termina no status terminal, cleanup abort/timer ao mudar seleção/desmontar. Abandonar acompanhamento não chama cancel/stop.
- [ ] Write `shows partial results and explicit reuse attempt`: completed/partial_failure mostra rendered/reused/failed separadamente; somente sucessos têm Abrir MP4/Baixar MP4. Nova execução muda executionId sem mudar plano e avisa reaproveitamento. Reload lista execuções persistidas e permite selecionar Job anterior.
- [ ] Run `rtk npm --prefix web run test -- src/RenderPanel.test.tsx src/EditingPanel.test.tsx`; confirmar RED esperado.
- [ ] Implementar componente com estado local/request congelado, seletor de execuções/list refresh, polling limitado ao Job selecionado e estados de erro sanitizados. Tratar start incerto por status do worker antes de repetir start. Acrescentar editing_render / Renderizar planos salvos à seleção WorkerControls e seu teste existente para operação manual após reload. Textos nunca chamam renderizado de aprovado/entregue. Não reescrever formulário/preview.
- [ ] Run `rtk uv run python scripts/verify.py ui`; exigir testes, typecheck e build verdes.
- [ ] Commit `feat(ui): render saved plans and inspect per-variant masters`.

### Task 7: E2E, integração CI e documentação

**Files:** Create `tests/test_web_render_e2e.py`; modify `.github/workflows/verify.yml`, `tests/test_verify_harness.py`, `README.md`, `docs/PROJECT-MEMORY.md`, `docs/GOAL-ROADMAP.md`; create `docs/superpowers/2026-10-08-d5a2-verification.md`.

**Interfaces:** Reusar PanelServers/panel_page e helpers editoriais atuais para
salvar plano; fixture real FFmpeg local/MP4 sintético, nenhuma chamada paga.

- [ ] Write `test_saved_plan_renders_three_variants_then_reuses_after_reload`: salvar três headlines, Renderizar pela UI, esperar Job completo, baixar três MP4 distintos, probe/check_master, recarregar, consultar mesmo Job e executar novo Job com reused. Guardar hashes dos upstream/receipts e exigir preservação.

```python
assert first_view["renderStatus"] == "succeeded"
assert [item["status"] for item in first_view["result"]["outputs"]] == ["rendered"] * 3
assert second_view["jobId"] != first_view["jobId"]
assert [item["status"] for item in second_view["result"]["outputs"]] == ["reused"] * 3
assert upstream_before == upstream_after
assert receipt_bytes_before == receipt_bytes_after
```
- [ ] Write `test_editorial_draft_does_not_change_saved_render_target`: alterar rascunho depois de salvar; render mantém planHash/inputs antigos, preview continua explicitamente aproximado.
- [ ] Run `rtk uv run pytest tests/test_web_render_e2e.py -q`; observar RED pelo comportamento ausente e depois GREEN com integração real. Se já verde, comprovar que o teste falha ao remover temporariamente o wiring relevante e restaurá-lo via apply_patch, sem descartar trabalho alheio.
- [ ] Adicionar todos os novos testes Python e E2E ao focused Windows existente; Linux full descobre suite inteira. Teste de harness verifica inclusão e worker/render fixture; manter bypass do shim FFmpeg e limites atuais de Actions. Não pular teste por SO/fonte/runtime ausente.
- [ ] Run `rtk uv run python scripts/verify.py full`; exigir 19/19 e registrar contagens/tempo reais, sem copiar evidência D5A.1. Revisar diff/secrets/paths/media; atualizar docs separando entregue e planejado, D5B posterior, e preservar histórico de CI D5A.1 corrigido.
- [ ] Commit `test(render): verify local UI render workflow across platforms` para testes/CI e commit separado `docs: record D5A.2 capability and verification` para documentação verificada.
- [ ] Revisão independente da branch: em execução Native, um revisor ao final; corrigir findings necessários e repetir checks afetados/gate completo. Registrar resultado sem inventar revisão ou CI.
- [ ] Solicitar autorização de merge/push ao concluir (não inferir da aprovação do plano). Após integração autorizada, registrar resultado Windows/Linux separadamente de LOCAL_VERIFIED; nenhuma verificação de provider é necessária ou alegada.

## Handoff

Plano aguarda revisão e autorização de execução. Recomendação: **Native**, porque
as tarefas compartilham contratos e wiring, sem subsistema externo novo; execução
inline com um revisor independente no final evita contextos extras por tarefa.
Escolha alternativa disponível: Subagent-driven, implementador/revisor por tarefa.
Não iniciar implementação antes do aceite deste plano e da escolha do método.
