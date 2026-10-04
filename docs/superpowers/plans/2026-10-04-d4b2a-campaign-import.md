# D4B.2a Campaign and Manual Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Criar campanha/copy e importar/revisar três imagens pela UI local, sem CLI ou JSON manual no fluxo do operador.

**Architecture:** Reutilizar os contratos de campanha, serviço de imagens e jobs do worker atual. Acrescentar publicação imutável de associações e diagnóstico opt-in com snapshot dos arquivos. Formulários React usam HTTP/polling existentes, sem auto-POST ou auto-start.

**Tech Stack:** Python 3.11, Pydantic, FastAPI, SQLite/SQLAlchemy; React 19, TypeScript, Vite, Vitest/Testing Library e Playwright Python existentes.

**Spec:** `docs/superpowers/specs/2026-10-04-d4b2a-campaign-import-design.md` (aprovada em 2026-10-04).

**Execution:** Nativa, conforme preferência já fornecida pelo usuário; plano aguardando revisão. Nenhuma implementação, worktree ou push nesta etapa.

## Global Constraints

- Uso pessoal, delivery-first, reutilizando React, FastAPI, SQLite, serviços e worker existentes.
- Não criar tabelas, scheduler, serviço de arquivos genérico ou segundo motor de jobs.
- Não haverá chamadas pagas aqui.
- A campanha nasce com status draft.
- Budget e config começam como objetos vazios; não haverá editor JSON nem autorização implícita de gastos.
- O texto falado continua somente hook + body + CTA.
- Não aprovar imagens automaticamente.
- POST é enviado uma vez, com botão desabilitado durante a submissão; não há retry automático.
- Salvar/validar/importar não inicia o worker automaticamente.
- AGENTS.md e arquivos sincronizados em sources permanecem intactos.
- Sem Flow, watch folder, upload/file picker, voz/HeyGen, preview, CRUD de cenas persistidas ou novas dependências.
- Paths contidos, arquivos imutáveis e aprovações explícitas permanecem obrigatórios em Windows/POSIX.

## Review Focus

1. Caminho com espaços/Unicode ou barras Windows: preservar associação correta, sem escapar da pasta (Task 1).
2. Job completed com valid false: mostrar erros, nunca habilitar importação (Tasks 2 e 4).
3. Resultado atrasado após mudança de campanha/associações: não restaurar validação antiga nem submeter em outra campanha (Tasks 3 e 4).
4. JSON legado com whitespace distinto: comparar hash dos bytes, não o hash normalizado do plano (Task 2).
5. DTO parcial, código desconhecido ou resposta perdida: manter dados bons, erro seguro e nenhuma repetição de escrita (Tasks 3 e 4).

## Estrutura e contratos compartilhados

Modificar `images/import_batch.py` somente para publicação de manifests; inspeção/cópia continuam no serviço atual. Alterar `api/action_contracts.py`, `action_routes.py` e os ramos de imagem de `commands.py`; não refatorar os ramos HeyGen/voz.

Criar `web/src/CampaignForms.tsx` para campanha/copy e `web/src/ImageImportPanel.tsx` para batch/review; integrar em `CampaignPanel.tsx`. Criar `web/src/imageImportApi.ts` para os DTOs/validadores específicos, reutilizando `api.ts`. Sem novo hook genérico de comandos: estado local e barreira `RemoteState.refresh()/lastSuccessReadId` existentes.

Backend usa snake_case e aliases camelCase do ContractModel. Interfaces novas:

- `ImageImportPublished(ContractModel)` em import_batch: campaign_id str, manifest_path Path, images_path Path, manifest_sha256 str, items list[ImageImportItem].
- `ImageImportService.publish_manifest(self, campaign_id: str, directory: Path, items: list[ImageImportItem]) -> ImageImportPublished`.
- `ImageManifestOperation(OperationRequest)`: operation Literal["image_manifest"], directory_path str, items list[ImageImportItem] com min_length 1.
- `ImageManifestResult(ContractModel)`: operation Literal["image_manifest"], manifest_path str, images_path str, manifest_sha256 Sha, items list[ImageImportItem]. Paths públicos relativos ao project root.
- `ImageSourceSnapshot(ContractModel)`: variant_id str no padrão de IDs existente, sha256 Sha.
- `ImageImportOperation`: acrescentar include_diagnostics bool = False, validation_id str | None = None (ID seguro, 1–120 chars), expected_sources list[ImageSourceSnapshot] | None = None. Diagnóstico/validationId apenas em dry_run; snapshot apenas em execute e sem duplicatas/vazio. Legados sem campos novos continuam aceitos.
- `ImageImportItemResult`: acrescentar sha256 Sha | None, width/height/size_bytes int | None, format str | None (defaults None, valores positivos quando presentes).
- `ImageImportDiagnostic(ContractModel)`: code str, variant_id str | None; código sanitizado por allowlist, sem mensagem de exceção.
- `ImageImportOperationResult`: acrescentar valid bool | None = None, manifest_sha256 Sha | None = None, validation_id str | None = None, issues list[ImageImportDiagnostic] = []. Legados conservam a semântica atual.
- POST `/api/v1/campaigns/{campaignId}/images/import/manifests`: ImageManifestOperation → OperationSubmission, 202. GET de resultados e worker existentes permanecem.

Requests de publicação normalizam itens por variantId antes da identidade do job. Manifest canônico: `json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")`, sem newline. Nome `image-import-<hash desses bytes>.json`.

## Preparação para execução

- [ ] Ler spec/plano, PROJECT-MEMORY/PRD e status Git; aplicar using-git-worktrees e criar/reutilizar isolamento adequado a partir do main com estes documentos. Preservar quaisquer mudanças alheias.
- [ ] Registrar baseline com `rtk proxy uv run python scripts/verify.py full`, antes de alterações de código. Dependências já fixadas; usar `npm --prefix web ci`, nunca gerar lock do web a partir do cwd raiz com npm install.
- [ ] Registrar progresso e evidências RED/GREEN em `work/` ignorado. Cada tarefa segue teste falhando → mudança mínima → verificação → revisão do diff → commit.

## Task 1: Publicação imutável das associações pelo worker

**Files:** Modify `src/auraly_pipeline/images/import_batch.py`, `src/auraly_pipeline/api/action_contracts.py`, `src/auraly_pipeline/api/action_routes.py`, `src/auraly_pipeline/api/commands.py`; Create `tests/test_image_import_manifest.py`, `tests/test_api_image_import_ui.py`.

**Interfaces:** Consumes ImageImportItem, prepare_directory, ApiCommands.submit_operation/execute_operation/get_operation e worker local atuais. Produces publish_manifest, ImageImportPublished, ImageManifestOperation/Result e rota definidos acima; adicionar aos unions OperationKind/LocalOperationRequest/OperationResult.

- [ ] **1. Escrever testes RED.** Em test_image_import_manifest, preparar campanha de três cenas usando valid_campaign_data e prepare_directory; testar publicação sem imagens, preservação do modelo e replay. Em test_api_image_import_ui, usar create_api_fixture/commands/request existentes e worker_once para provar fila antes de escrita, isolamento por campanha e 202 via TestClient. Assertions centrais:

```python
assert published.manifest_sha256 == hashlib.sha256(published.manifest_path.read_bytes()).hexdigest()
assert published.manifest_path.name == f"image-import-{published.manifest_sha256}.json"
assert json.loads(published.manifest_path.read_bytes())["approveImported"] is False
assert json.loads(published.manifest_path.read_bytes())["approvedBy"] is None
assert template.read_bytes() == original_template
assert service.publish_manifest(campaign_id, directory, list(reversed(items))) == published
```

Cobrir IDs repetidos/desconhecidos, cobertura incompleta, pasta fora do work root, template de outra campanha, destino existente conflitante, `../`, drive/UNC, symlink/junction escapando, espaços/Unicode e normalização de barras. Testes de junction específicos Windows; preservar fixtures em tmp_path e cleanup nativo seguro.
- [ ] **2. Rodar RED:** `rtk proxy uv run pytest tests/test_image_import_manifest.py tests/test_api_image_import_ui.py -q`. Falha esperada por interfaces/rota ausentes, não ambiente ou fixtures quebradas.
- [ ] **3. Implementar publicação.** Verificar template pela estrutura de preparação (paths vazios são permitidos no modelo), campaignId/cenas e images directory; validar containment com utilitários existentes novamente no worker. Sem probe de imagens. Gravar staging exclusivo na mesma pasta, flush/fsync e publicar via os.link(staging, destino), removendo staging em finally. FileExistsError exige comparação integral; outros erros falham sem fallback que sobrescreva. Não usar publisher de imagem que inspecione JSON como mídia. Converter paths para relativos no resultado da API. POST somente normaliza/submete; manter maxAttempts 1/manual_only.
- [ ] **4. Rodar GREEN:** comando RED, depois `rtk proxy uv run python scripts/verify.py fast --pytest tests/test_image_import_batch.py tests/test_api_operations.py tests/test_api_actions_http.py tests/test_image_import_manifest.py tests/test_api_image_import_ui.py`. Sem failures; payloads/resultados não vazam paths privados. Reversão temporária do incremento deve reproduzir RED se o teste inicial não o demonstrou.
- [ ] **5. Commit:** arquivos explícitos desta tarefa, mensagem `feat: publish manual image associations through local worker`.

## Task 2: Dry-run diagnóstico e execução vinculada aos arquivos

**Files:** Modify `src/auraly_pipeline/api/action_contracts.py`, `src/auraly_pipeline/api/commands.py`, `tests/test_api_image_import_ui.py`, `tests/test_api_operations.py`; reuse `tests/test_image_import_batch.py` para regressões de cópia.

**Interfaces:** Consumes Task 1 results e ImageImportService.plan/execute. Produces campos de diagnóstico/snapshot definidos no contrato compartilhado; valida snapshot contra plan.items antes de execute(plan).

- [ ] **1. Escrever RED:** `test_diagnostic_dry_run_reports_facts_without_writes`, `test_invalid_diagnostic_is_completed_but_not_valid`, `test_legacy_invalid_import_still_fails`, `test_validation_id_refreshes_file_facts`, `test_snapshot_blocks_changed_images`, `test_manifest_digest_uses_raw_bytes`. Assertions centrais:

```python
assert dry.valid is True and dry.created == dry.reused == dry.approved == 0
assert dry.manifest_sha256 == hashlib.sha256(manifest.read_bytes()).hexdigest()
assert dry.items[0].width == 360 and dry.items[0].height == 640
assert invalid.status == "completed" and invalid.result.valid is False
assert invalid.result.items == [] and invalid.result.issues
assert count_images(settings.database) == 0  # inclusive após execução com fonte alterada
```

Snapshot deve ter cobertura exata; testar duplicatas, IDs desconhecidos, hash malformado, imagem ausente/duplicada/landscape e conflito com aprovada. Mesmo validationId reusa job; ID novo cria dry-run novo e captura hash atualizado. JSON não canônico aceito mantém digest dos bytes. Alteração após enfileiramento também falha; manter teste de mudança durante cópia. Falha inesperada de I/O é failed, não valid false.
Fixar também replay de um job legado persistido antes dos campos novos: a mesma entrada deve recuperar o mesmo jobId, não mudar identidade só porque surgiram defaults.
- [ ] **2. Rodar RED:** `rtk proxy uv run pytest tests/test_api_image_import_ui.py tests/test_api_operations.py -q`; esperar ausência do diagnóstico/guard, confirmar causa.
- [ ] **3. Implementar contratos/ramos.** Catch de ImageImportValidationError somente no dry_run opt-in; copiar apenas code/variantId. Allowlist: image_import_manifest_invalid, image_import_campaign_not_found, image_import_variant_coverage_invalid, image_import_source_path_invalid, image_import_media_invalid, image_import_orientation_invalid, image_import_approved_candidate_conflict; desconhecido vira `image_validation_failed`. Retornar itens []/counts zero quando inválido; total é número de cenas esperadas. No válido expor fatos reais e validationId. Não chamar execute em dry_run. Comparar mapa esperado com mapa recalculado e negar alteração com artifact_invalid; conservar rechecagens de execute. Campos opcionais preservam shape funcional e falhas de legados.
Na serialização para identidade/input do job, omitir somente os novos campos quando em seus defaults (false/None); não alterar a serialização dos campos legados nem remover globalmente defaults existentes. Novos valores explícitos entram na identidade; leitura de inputs antigos permanece válida.
- [ ] **4. Rodar GREEN:** comando RED, depois `rtk proxy uv run python scripts/verify.py fast --pytest tests/test_api_image_import_ui.py tests/test_api_operations.py tests/test_image_import_batch.py tests/test_api_operations_e2e.py`. Verificar OpenAPI unions/aliases e ausência de mudanças nos schemas CLI existentes; só regenerar schemas registrados realmente afetados.
- [ ] **5. Commit:** `feat: bind manual image execution to validated source hashes` com arquivos desta tarefa.

## Task 3: Formulários de campanha e versões de copy

**Files:** Create `web/src/CampaignForms.tsx`, `web/src/CampaignForms.test.tsx`; Modify `web/src/api.ts`, `web/src/api.test.ts`, `web/src/CampaignPanel.tsx`, `web/src/CampaignPanel.test.tsx`, `web/src/styles.css`; backend sem novos endpoints.

**Interfaces:** Produces `CopyFields = {headline: string; hook: string; body: string; cta: string}`, `buildSourceText(copy: CopyFields): string`, `CampaignCreateForm({onCreated}: {onCreated: (campaignId: string) => void})`, `CopyVersionForm({campaignId, detail}: {campaignId: string; detail: RemoteState<CampaignDetail>})`. Expandir CopyMaster DTO com headline/sourceText/sha256/approvedBy para render/reconciliação; validar campos consumidos. Consumes POST campanha/copies existentes e usePolling/barreira D4B.1.

- [ ] **1. Escrever RED** com Testing Library. Rótulos exatos: “Criar campanha com copy aprovada” e “Adicionar versão de copy aprovada”. Checkbox inicia falso, ator obrigatório; IDs duplicados/localizações equivalentes e campos vazios não fazem POST. Payload inclui budget/config {}, status draft, sceneVariants com status not_started. Teste de texto canônico:

```ts
expect(buildSourceText({headline: 'H', hook: 'K', body: 'B', cta: 'C'}))
  .toBe('Headline:\nH\n\nHook:\nK\n\nBody:\nB\n\nCTA:\nC');
expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
```

Testar novas versões preservando histórico/headline, trim consistente, double click, perda de resposta, DTO faltando headline/hash, troca de campanha durante POST e saída com rascunho não salvo. GET anterior ao término do POST não reconcilia. Nova versão compara conteúdo e actor no histórico pós-barreira, sem auto-reenvio; correspondência ambígua permanece desconhecida.
- [ ] **2. Rodar RED:** `rtk proxy npm --prefix web run test -- src/CampaignForms.test.tsx src/api.test.ts` (Vitest precisa falhar pelos novos contratos/componentes).
- [ ] **3. Implementar forms mínimos.** Campos HTML nativos, personagem enum atual, presets textuais. sourceText segue função acima com valores trimados; não incluir spokenText no POST. Cenas editáveis apenas antes da criação, ao menos uma. Listagem mostra form colapsável, onCreated navega para hash da campanha; não adicionar rota “new” que colida com campaignId. CopyVersionForm refresca detail e exige GET pós-submissão para reconciliação. Navegação interna com formulário dirty pede confirmação, beforeunload cobre fechamento/reload; descartar/confirmar navegação é explícito, não persistir drafts. Ignorar resposta tardia após unmount/troca de campaignId. Atualizar fixtures antigas de DTO sem enfraquecer validadores.
- [ ] **4. Rodar GREEN:** comando RED, depois `rtk proxy npm run ui:test` e `rtk proxy npm run ui:build`; todos passam, inclusive regressões WorkerControls/usePolling. Checar labels, foco do primeiro erro e layout estreito.
- [ ] **5. Commit:** `feat: create campaigns and approved copy versions in local panel` com arquivos desta tarefa.

## Task 4: Batch manual, diagnóstico e revisão de imagens na UI

**Files:** Create `web/src/imageImportApi.ts`, `web/src/imageImportApi.test.ts`, `web/src/ImageImportPanel.tsx`, `web/src/ImageImportPanel.test.tsx`; Modify `web/src/CampaignPanel.tsx`, `web/src/CampaignPanel.test.tsx`, `web/src/styles.css`.

**Interfaces:** Consumes Task 1/2 camelCase DTOs e detail/images/jobs RemoteState existentes. Produces `ImageImportPanel({campaignId, detail, images, jobs}: {campaignId: string; detail: RemoteState<CampaignDetail>; images: RemoteState<Items<SceneImages>>; jobs: RemoteState<Items<JobSummary>>})`; `imageManifestResult(value: unknown): boolean`, `imageImportResult(value: unknown): boolean`, `imageOperationView(value: unknown): boolean` em imageImportApi, com types correspondentes aos contratos compartilhados. Reutiliza post/read/campaignPath, sem alterar o timeout 15s ou auto-retry.

- [ ] **1. Escrever RED:** preparar→publicar→validar→confirmar importar com POSTs explícitos. Checar body de execute:

```ts
expect(executeBody).toMatchObject({mode: 'execute', manifestSha256: dry.manifestSha256,
  expectedSources: dry.items.map(({variantId, sha256}) => ({variantId, sha256}))});
expect(screen.getByRole('button', {name: 'Importar batch validado'})).toBeDisabled();
// Disabled após editar associação, valid:false, reload ou DTO incompleto.
```

Testar ID novo por clique de validar mas estável durante submissão, queued sem worker-start, escolher publicação persistida após reload, resultado atrasado/manifest diferente, códigos desconhecidos com mensagem segura e nenhuma inspeção parcial fabricada. Perda de resposta exige GET pós-barreira/inspeção de jobs; selecionar resultado só se campanha/itens/path/hash correspondem. Sem match inequívoco permanece unknown e não faz POST. Review approve/replace exige confirmação+actor; reject exige motivo. POST review perdido não duplica; refresh images pós-barreira confirma apenas estado solicitado, sem atribuir autoria que o DTO não expõe.
- [ ] **2. Rodar RED:** `rtk proxy npm --prefix web run test -- src/ImageImportPanel.test.tsx src/imageImportApi.test.ts`.
- [ ] **3. Implementar DTOs e painel.** Uma linha por cena com contexto e input relativo; pasta/outputPath em work-root-relative só no prepare, directoryPath project-relative derivado de manifestPath retornado. Paths copiáveis e instruções Explorer; sem upload/watch/inferência de associação. Seleção manual de jobs locais existentes permite consultar OperationView e recuperar publicações. Polling a cada 2s somente para operação selecionada, cancelado/pausado pelos hooks atuais. Match local por campanha, associações ordenadas, manifestPath/hash e validationId atual; mudança limpa validação/checkbox. Exigir todos os fatos/snapshot antes de execute; valid false mostra “Validação concluída com erros”, válido “Batch válido”. Novo clique de validar usa crypto.randomUUID(), nunca auto-POST. Usar ações review existentes e mostrar conflitos sem auto-replace. Form dirty/navegação seguem Task 3. Integrar no lugar do aviso de importação CLI atual e refrescar imagens/status/jobs após ação.
- [ ] **4. Rodar GREEN:** comando RED, depois `rtk proxy npm run ui:test` e `rtk proxy npm run ui:build`; preservar testes de consumo seguro de DTOs, polling e workers.
- [ ] **5. Commit:** `feat: import and review manual image batches in local panel` com arquivos desta tarefa.

## Task 5: Fluxo real HTTP/browser, gates e documentação operacional

**Files:** Create `tests/test_web_import_e2e.py`; Modify `tests/web_panel_support.py`, `.github/workflows/verify.yml`, `README.md`, `docs/PROJECT-MEMORY.md`, `docs/GOAL-ROADMAP.md`, `docs/PRD-MVP-MASS-VIDEO-AUTOMATION.md`; manter `scripts/verify.py` se os gates existentes já cobrem tudo.

**Interfaces:** Consumes PanelServers/panel_servers e panel_page existentes. Importar fixture panel_page de test_web_panel_e2e com alias provide_panel_page; reutilizar provider fake já instalado. Não substituir submit/plan/execute pelo mock: batch usa backend real. Fixture de holding image_prepare mantém regressão anterior; novo teste libera seu Event explicitamente.

- [ ] **1. Escrever RED** `test_create_campaign_import_three_images_and_review_through_panel`. Usar nova campaignId, três locais/variantes diferentes; três imagens portrait com cores/bytes distintos, geradas por Pillow apenas em tmp_path. Criar/aprovar copy, preparar pasta e iniciar local_operations explicitamente pela UI. Copiar arquivos pelo helper do teste (equivalente ao Explorer), preencher paths e completar publicação/validação/import/review pela UI. Assertions:

```python
assert len(campaign.scene_variants) == 3
assert campaign.copy_masters[0].spoken_text == "Hook\n\nBody\n\nCTA"
assert "Headline" not in campaign.copy_masters[0].spoken_text
assert count_images(database) == 3
assert all(candidate.review_status == "approved" for candidate in candidates)
assert all(path.read_bytes() == original for path, original in original_sources)
```

Definir count_images reutilizado de test_api_operations; ler candidates via image_review.get_candidate com IDs observados no GET, campaign via commands.campaigns.get_campaign. Verificar zero chamadas pagas pelo fake provider, duas versões de copy preservadas, reload não dispara POST e nenhum auto-start. Acrescentar `test_changed_source_requires_revalidation_through_panel` (mutação após dry-run, execute falha sem candidatos, novo validar mostra novo hash) e largura 390px/labels/teclado sem overflow.
- [ ] **2. Rodar RED:** `rtk proxy uv run pytest tests/test_web_import_e2e.py -q`; confirmar falha de requisito real (testes devem ser escritos antes da integração final, não criar artificialmente uma falha após tudo funcionar). Se fluxo já passar, demonstrar RED removendo temporariamente a integração relevante e restaurar em seguida.
- [ ] **3. Integrar gate/docs.** Linux full já coleta novo pytest; adicionar test_web_import_e2e, test_image_import_manifest, test_api_image_import_ui e test_image_import_batch à lista Windows focused. Não mudar políticas de segurança ou retries para fazer teste passar. README explica Explorer, paths, worker explícito, validação e review; memória/roadmap/PRD marcam IMPLEMENTED/LOCAL_VERIFIED apenas após gate, CI pendente até run real. Voz/HeyGen/preview continuam PLANNED. Não usar PROVIDER_VERIFIED.
- [ ] **4. Rodar GREEN completo:** `rtk proxy uv run pytest tests/test_web_import_e2e.py tests/test_web_panel_e2e.py -q`, depois `rtk proxy uv run python scripts/verify.py full`. Exigir todos os passos aprovados, sem schema drift, segredos, mídia/node_modules ou dependência file:.. no diff. Verificar diff e escopo; preservar logs no work ignorado.
- [ ] **5. Commit:** `test: verify campaign and manual import delivery flow` com testes/CI/docs desta tarefa; commits adicionais só para correções demonstradas por RED/GREEN.

## Revisão, integração e handoff

- [ ] Após tarefas verificadas, executar uma revisão independente da branch conforme AGENTS/método Nativa. Tratar achados com teste RED/GREEN e correção mínima; repetir full se houver alteração comportamental.
- [ ] Entregar SHA, evidências locais e limites (sem provedores pagos). Não declarar CI verde sem run no SHA. Merge/push dependem de autorização atual do usuário; não inferir de permissões antigas já cumpridas.
- [ ] Quando publicação for autorizada, acompanhar Actions Linux full/Windows focused e corrigir a menor causa demonstrada caso falhem. Antes disso, CI é pendente, não requisito fingidamente satisfeito.

## Auto-revisão do plano

Spec coberta: campanha/copy Task 3; publicação Task 1; diagnóstico/byte guards Task 2;
UI/review/recovery Task 4; browser, plataformas e docs Task 5. Os cinco Review Focus
têm testes nas tarefas indicadas. Os novos contratos são aditivos, sem migrações;
aprovação, validação e execução continuam distintas. Execução ainda não iniciada.
