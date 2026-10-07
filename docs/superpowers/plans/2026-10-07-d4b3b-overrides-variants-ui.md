# D4B.3b Overrides & Variants UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Configurar overrides e variantes A/B de um MP4 HeyGen por formulário, validar e publicar um plano editorial sem regenerar assets upstream.

**Architecture:** Componentes React focados sobre EditBatchRequest e API D4A existentes. Operações locais persist=false/true, worker iniciado manualmente e confirmação do artefato por GET exato. Backend continua autoridade; nenhum resolver/hash paralelo no browser.

**Tech Stack:** React/TypeScript, Vite/Vitest/Testing Library, FastAPI/Pydantic e pytest/Playwright existentes. Nenhuma dependência nova.

**Spec:** `docs/superpowers/specs/2026-10-07-d4b3b-overrides-variants-ui-design.md`, aprovada em 2026-10-07.

## Global Constraints

- Um MP4 por request; render status `ready` e source não null, sem MP4 arbitrário/latest.
- EditBatchRequest/EditBatchPlan v1.0, EditManifest v2.0 e EditProfile v1.0 existentes.
- ProfileRef exato ID/version/hash; videoId inicialmente renderId, editável seguro; headline base obrigatória, sem copiar latest copy.
- Variante inicial key `a`, label `A`; lista explícita, keys seguras/únicas; maxOutputs=3, inteiro positivo ajustável, sem produto cartesiano.
- Precedência profile < campaign < video < outputVariant; todas as camadas pertencem ao request, sem alterar profile/campanha global.
- Herdar omite; Substituir envia valor; Limpar só para nullable envia null. Preservar false/zero/RGBA e vazio numérico inválido.
- Cobrir output/headline/captions/music/framing e todos os campos dos overrides atuais; captions não aceitam texto livre.
- AssetRef por path POSIX relativo ao project root + SHA-256 explícito; fonte/música/timing sem upload/catálogo/hash no browser.
- musicAccepted inicialmente false; reset ao trocar source/profile/referências de música; não reset por headline somente.
- persist=false cria/reutiliza Job/audit locais, mas não publica artefatos; persist=true revalida e publica plano. Nenhum Job pago/mídia upstream alterado.
- 202 é submissão, não conclusão. Worker local_operations manual nos controles existentes; polling só GET, nenhum auto-start/retry POST.
- Save exige validação do mesmo request; mesmo planHash e GET de artefato correspondente antes de sucesso.
- Unknown conserva payload sem repost; Job manual/plano listado não provam autoria de resposta perdida.
- Source path do render é work-relative; source.path do plano é project-relative. Comparar IDs/hashes/duração, não adivinhar prefixo.
- Draft em memória com confirmação de descarte; sem localStorage/autosave, clones de planos, preview/render ou backend/migration/schema novo.
- AGENTS.md/sources/providers preservados; apply_patch, rtk; checks e revisão independente antes de LOCAL_VERIFIED. Sem merge/push sem pedido.

## Review Focus

- GET de validação termina depois de mudança de campanha/seleção: resultado antigo não libera Save — Task 3.
- Backend devolve seções vazias e propriedades ausentes: comparar sem colapsar null explícito/false/zero — Tasks 1/2.
- POST Save persiste mas resposta perde Job: plano existente pode ser consultado, sem alegar autoria nem liberar repost — Tasks 3/4.
- Render/path válido vem de outra raiz sem prefixo comum: correlação por IDs/hash/duração e backend, não igualdade de paths — Task 1.
- Browser Back recusado com draft: permanece na campanha com valores e validação apropriada — Tasks 3/4.

## Arquivos e interfaces

Criar `web/src/editingApi.ts` (tipos/clientes/validação),
`EditOverridesForm.tsx` (draft/campos/herança), `EditingPanel.tsx` (request,
variantes/operações/lista/consulta), respectivos testes e
`editingTestSupport.ts` (fixtures exclusivas de teste). Modificar CampaignPanel
pontualmente para incluir painel; styles.css só por overflow comprovado.
Não refatorar ProfileForm nem worker. Criar `tests/test_web_editing_e2e.py`,
reutilizando PanelServers/panel_page/start_kind e seed ready existentes.
Documentação: quatro documentos de produto, spec/plano e
`docs/superpowers/2026-10-07-d4b3b-verification.md`.

### Task 1: Cliente editorial completo e confirmação tipada

**Files:** editingApi.ts, editingApi.test.ts, editingTestSupport.ts.

**Interfaces:** Reutilizar tipos de profileApi e HeyGenRenderView.
Exportar `EditOverrides` (cinco seções opcionais Partial dos estilos; headline
inclui text), `EditBatchRequest`, `EditBatchPlan`, `EditManifest`, `PlanSummary`,
`EditSubmission`, `EditOperationView` conforme Pydantic/schemas, sem any ou DTO parcial.
Exportar:

- `editOverrides(v: unknown): v is EditOverrides`;
- `editBatchRequest(v: unknown): v is EditBatchRequest`;
- `editBatchPlan(v: unknown): v is EditBatchPlan`;
- `planMatchesRequest(plan: EditBatchPlan, request: EditBatchRequest, render: HeyGenRenderView): boolean`;
- `samePlan(a: EditBatchPlan, b: EditBatchPlan): boolean` (conteúdo conhecido completo, sem hash próprio);
- `submitEditPlan(request: EditBatchRequest, persist: boolean): Promise<EditSubmission>`;
- `getEditOperation(campaignId: string, jobId: string, signal: AbortSignal): Promise<EditOperationView>`;
- `listEditPlans(campaignId: string, signal: AbortSignal): Promise<PlanSummary[]>`;
- `getEditPlan(campaignId: string, videoId: string, planHash: string, signal: AbortSignal): Promise<EditBatchPlan>`.

- [x] Escrever testes de payload/resultados. Assertion mínima:
  ```ts
  expect(editOverrides({music:{volumeDb:0,loop:false,asset:null}})).toBe(true);
  expect(editOverrides({headline:{anchor:['top']}})).toBe(false);
  expect(editOverrides({captions:{text:'replacement'}})).toBe(false);
  expect(editBatchRequest({...REQUEST,maxOutputs:2,variants:[A,B,C]})).toBe(false);
  expect(planMatchesRequest(PLAN,REQUEST,RENDER)).toBe(true);
  expect(planMatchesRequest({...PLAN,renderId:'other'},REQUEST,RENDER)).toBe(false);
  ```
  Fixtures completas sintéticas devem representar paths de raízes diferentes
  (`RENDER.source.path='videos/a.mp4'`, `PLAN.source.path='work/videos/a.mp4'`),
  overrides com seções vazias, hashes/manifest/provenance/caption bindings coerentes.
  Testar source/copy/voice/image/profile, count/keys/labels/limits/timingRef,
  props ausentes vs null e headline resolvida conforme último text fornecido.
  Rejeitar enum array, NaN/Infinity, DTO incompleto/extra, hashes/IDs inseguros,
  cues/invariantes inconsistentes. Não confiar em type assertions no guard.
- [x] RED: `rtk npm --prefix web test -- src/editingApi.test.ts`.
  Expected: falha por módulo/comportamento ausente, não ambiente.
- [x] Implementar tipos/guards e clientes usando read/post/campaignPath existentes.
  Partials validam só propriedades fornecidas; não validar safe zones combinadas
  usando defaults inventados. Plano exige invariantes completos, sorted keys,
  caption states e referências internas coerentes; hash é verificado pela API.
  Comparação de overrides normaliza só seções vazias conhecidas, sem inserir
  campos escalares; textos usam precedência dos overrides, não outro resolver.
  Submission exige campaignId/operation exatos e jobId válido; resposta POST
  incompatível → command_unknown. GET exige identidade exata, sem retry.
- [x] GREEN: comando focado e `rtk npm --prefix web run typecheck`.
  Expected: exit 0 nos dois. Revisar diff e commit
  `feat: add typed editorial planning client`.

### Task 2: Campos de overrides com herança explícita

**Files:** EditOverridesForm.tsx, EditOverridesForm.test.tsx.

**Interfaces:** Exportar `OverrideDraft`: cinco seções com fields tipados pelas
keys de EditOverrides, cada campo `{mode:'inherit'|'replace'|'clear', value:string|boolean|{path:string;sha256:string}}`.
Draft preserva texto numérico inválido; todos os modos iniciais inherit.
`newOverrideDraft(): OverrideDraft` e
`readOverrides(draft: OverrideDraft): {overrides: EditOverrides|null; invalidField:string|null}`.
Exportar `OverrideHints = EditDefaults & {headline: HeadlineStyle & {text:string}}`
para os valores herdados de estilo e texto base, sem status de validação.
Componente `EditOverridesForm({draft, inherited, disabled, label, onChange})`,
inherited: OverrideHints, label:string, onChange:
`(draft: OverrideDraft, field: string) => void`. Não contém submit/form aninhado;
pai decide validação/foco com nomes de campo prefixados pelo label.

- [x] Escrever `omits_inherited`, `preserves_false_zero_alpha`,
  `clear_is_not_inherit`, `all_fields_roundtrip`, `invalid_numbers_and_assets`:
  ```ts
  expect(readOverrides(newOverrideDraft()).overrides).toEqual({});
  // Interação: substituir volume com '0', loop com false e color '#FFFFFF80'.
  expect(result).toMatchObject({music:{volumeDb:0,loop:false},headline:{color:'#FFFFFF80'}});
  // Limpar asset envia null; voltar a herdar remove a propriedade.
  expect(cleared.music).toEqual({asset:null});
  expect(inherited.music?.asset).toBeUndefined();
  expect(readOverrides(blankRequired).overrides).toBeNull();
  ```
  Testar todos os campos atuais, AssetRef parcial/path/hash inválido, nullable
  end/trim/font/asset e modo clear proibido em non-nullable. Controls disabled
  por fieldset usam :disabled; labels acessíveis não incluem textos das opções.
- [x] RED: `rtk npm --prefix web test -- src/EditOverridesForm.test.tsx`.
  Expected: falha por componente ausente.
- [x] Implementar controles nativos focados, cinco seções details avançadas;
  campo text de headline visível e modes explícitos. Mostrar inherited como
  ajuda, não como payload. Usar editOverrides do Task 1 para validação escalar;
  sem generic schema engine/dependência/refactor ProfileForm. Error field seguro
  e foco/details aberto ficarão no submit do pai. Valores raw nunca viram zero
  por conversão de vazio. onChange informa o campo para reset de musicAccepted.
- [x] GREEN: teste focado e typecheck, Expected: exit 0. Commit
  `feat: add inherited editing override controls`.

### Task 3: Painel, variantes e fluxo validar/salvar

**Files:** EditingPanel.tsx, EditingPanel.test.tsx, CampaignPanel.tsx/test,
styles.css somente se necessário.

**Interfaces:** `EditingPanel({campaignId, renders})`, renders:
`RemoteState<Items<HeyGenRenderView>>` recebido do CampaignPanel; painel busca
profiles/plans/GET de detalhe e operações. Consome clientes Task 1 e drafts
Task 2; nenhum acesso ao backend privado. Inicializa um variant key a/label A,
maxOutputs raw='3', base headline vazia, musicAccepted=false. Validado guarda
request/render/profile snapshot e plano; submissão guarda persist, payload,
jobId opcional e geração, independentemente da lista readonly.

- [x] Escrever `one_mp4_three_headlines`, `validation_required_for_save`,
  `editing_invalidates_validation`, `music_acceptance_resets_selectively`,
  `lost_post_stays_unknown`, `divergent_result_is_not_success`,
  `late_operation_does_not_unlock_save`, `refresh_keeps_draft`,
  `failed_job_preserves_input`, `save_requires_exact_get`,
  `discard_cancel_keeps_values`, `collapsed_invalid_field_receives_focus`:
  ```ts
  expect(posts[0].body).toMatchObject({operation:'edit_plan',persist:false});
  expect(posts[0].body.request).toMatchObject({maxOutputs:3,variants:[
    {key:'a',label:'A',overrides:{headline:{text:'A'}}},
    {key:'b',label:'B',overrides:{headline:{text:'B'}}},
    {key:'c',label:'C',overrides:{headline:{text:'C'}}} ]});
  expect(saveButton.matches(':disabled')).toBe(true); // 202 ainda queued
  expect(posts[1].body.request).toEqual(posts[0].body.request);
  expect(posts[1].body.persist).toBe(true);
  ```
  Cobrir >limite/keys repetidas/blank base/timing parcial antes de POST,
  source/profile trocado durante GET, duplo clique, 202 malformado, 503 após
  persistência, resultado outro persist/render/hash, GET404/divergente após Save,
  consulta readonly de outro plano sem trocar draft. Toggling música asset limpa
  acceptance; alterar headline não limpa. ID safe e paths de raízes diferentes.
- [x] RED: `rtk npm --prefix web test -- src/EditingPanel.test.tsx src/CampaignPanel.test.tsx`.
  Expected: falha no painel ainda ausente; preservar testes legados.
- [x] Implementar seção Edição e variantes no CampaignPanel, seleção ready/profile
  exatos, request completo, camada campaign/video e lista variante explícita.
  Adicionar/remover variantes sem renumerar keys restantes; hints herdados seguem
  último campo fornecido na camada anterior, sem alegar resolução validada.
  Submit valida request; em erro abre details/foca primeiro campo e limpa aria
  após correção. Refresh não reseta draft; confirmar source/profile/descarte.
- [x] Implementar estados idle/pending/validated/saving/saved/unknown/failed.
  Lock síncrono impede clique duplo. 202 guarda Job; GET operação com polling
  2000ms somente enquanto não terminal, AbortController/generation/unmount guards.
  Explicar início manual local_operations nos controles existentes; não iniciar
  worker. Validar resultado via Task 1 antes de liberar Save. Save reusa request,
  exige samePlan/plano validado e GET exato confirmado; hash divergente é conflito.
  Unknown mantém payload bloqueado e oferece consulta quando identidade existe;
  sem Job mostrar inspeção manual e abandono com aviso/confirm, nunca repost.
  Falha conhecida libera correção sem reaproveitar validação. Rascunho em memória,
  useUnsavedChanges incluindo saída para profiles; consulta saved readonly separada.
- [x] GREEN: focados, `rtk npm --prefix web test`, `rtk npm --prefix web run build`.
  Expected: exit 0 nos três. Revisar/commit
  `feat: plan headline variants from campaign UI`.

### Task 4: Browser/API/worker reais, persistência e proteção do draft

**Files:** tests/test_web_editing_e2e.py; reutilizar suporte existente sem novos servidores.

**Interfaces:** Fixtures panel_servers: PanelServers, panel_page: Page,
providers fake, seed ready `create_ready_api_fixture` via fixture. `start_kind`
de test_web_voice_e2e inicia worker explicitamente. Usar profile existente da
seed ou criar profile sintético via endpoint; fontes/timing de teste devem ter
bytes/hash reais quando habilitados, não fake path aprovado pelo browser.

- [x] Escrever testes com assertions de resultado, não sleeps arbitrários:
  `test_three_headlines_validate_save_reload`: validar três rows; iniciar
  local_operations; assert lista de planos continua vazia após persist=false;
  Save/iniciar worker/GET confirmado; assert três outputs textos A/B/C,
  source/WAV/imagem bytes/hash intactos, profile bytes intactos, eventos fake
  providers e budgets/aprovações inalterados. Jobs/audit locais podem crescer.
  `test_caption_timing_missing_is_pending`: profile/captions habilitados com fonte
  válida, sem timing; plano salvável indica timing_missing, não render ready.
  `test_lost_save_response_never_reposts`: route.fetch POST real, trocar resposta
  por 503, iniciar worker manualmente; plano pode existir, UI continua unknown
  sem Job vinculável, mesmo após listar/consultar; somente um POST persist=true.
  `test_editing_navigation_and_back_cancel`: chegar via lista de campanhas,
  editar draft, page.go_back com dialog dismiss; mesmo hash e valores; testar
  troca profile/source recusada e link profiles. `test_editing_narrow_width`
  parametrizado [320,390]: scrollWidth<=innerWidth, teclado/labels, long text/path.
- [x] RED: `rtk uv run python -m pytest tests/test_web_editing_e2e.py -q`.
  Expected: demonstrar falha real se existir; se integra de primeira, registrar
  GREEN sem inventar RED, mantendo TDD dos Tasks 1–3. Correção nova exige reprodução.
- [x] Corrigir só comportamento demonstrado, sem retries/timeouts genéricos ou
  relaxamento de overflow/correlation/paths. Browser Back pode revelar defeito
  no hook compartilhado: diagnosticar callers e correção mínima com regressão
  compartilhada, não workaround que perde o draft. Preservar guards atuais.
- [x] GREEN: mesmo comando; `rtk uv run python scripts/verify.py fast --pytest tests/test_web_editing_e2e.py tests/test_api_operations_e2e.py tests/test_editing_batch_service.py`.
  Expected: exit 0. Commit `test: verify editorial UI planning and recovery`.

### Task 5: Revisão independente, gate e documentação

**Files:** README.md, docs/PROJECT-MEMORY.md, docs/GOAL-ROADMAP.md,
docs/PRD-MVP-MASS-VIDEO-AUTOMATION.md, spec/plano e evidência acima.

- [x] Rodar `rtk uv run python scripts/verify.py full`, Expected: 19/19 PASS;
  registrar contagens reais/skips, schemas sem drift, build/audits conforme harness.
- [x] Uma revisão independente da branch completa conforme método escolhido e
  AGENTS.md, com Review Focus e spec/plan. Corrigir Critical/Important com
  regressões RED→GREEN e repetir gate após mudanças; registrar Minor adiado.
- [x] Documentar somente D4B.3b IMPLEMENTED/LOCAL_VERIFIED quando checks passam:
  worker manual, Jobs locais, diferença Validar/Salvar/render, música/timing,
  limites/unknown/draft. D4B.3c preview e D5 renderer continuam PLANNED;
  nenhum PROVIDER_VERIFIED novo. Registrar ponto atual de integração sem
  transformar checkpoints históricos em fatos atuais de merge/CI.
- [x] Self-review de diff por secrets/mídia/paths privados/escopo e
  `rtk git diff --check`, Expected: exit 0; commit
  `docs: record editorial variants UI verification`. Sem merge/push automático.
- [x] Handoff com evidência, pendências e próxima etapa.

## Self-review e execução

Cobertura: contratos/correlação → Task 1; todos os campos/herança → Task 2;
source/profile/variants/acceptance/unknown/worker/consulta → Task 3;
persistência/navegação/viewport/upstream intacto → Task 4; docs/gate → Task 5.
Interfaces produtoras/consumidoras acima coincidem; fixtures são completas,
normalização não apaga null e nenhum cliente depende de resolver/hashes novos.
Os cinco Review Focus têm regressões atribuídas. Sem placeholders de produto.

Execução Nativa foi previamente escolhida pelo usuário no projeto; recomendada
aqui pelos três componentes sequenciais dependentes e revisão final única.
Confirmar que o plano captura o solicitado antes de começar. Reutilizar/criar
checkout isolado com using-git-worktrees na execução; não implementar em main,
não apagar scratch de outros planos. Especificação/plano aprovados; execução Nativa concluída em 2026-10-07.
Revisão independente final, uma rodada de correções RED→GREEN e gate completo
Windows 19/19: 341 frontend, 1.807 Python / 25 skips. Evidência e decisões em
`docs/superpowers/2026-10-07-d4b3b-verification.md`.
Sem merge/push/Actions desta branch; D4B.3c/D5 continuam planejados.
