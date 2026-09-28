# Goal D1 Manual Image Batch Intake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir que o usuário prepare uma pasta, valide e importe explicitamente uma imagem manual por variante, deixando `ImageCandidate`s persistidos e opcionalmente aprovados para o HeyGen.

**Architecture:** Um novo `ImageImportService` síncrono planeja o lote inteiro sem mutações e depois publica arquivos content-addressed antes de persistir todos os candidatos numa transação SQLite. `ImageCandidate` ganha ownership direto da `SceneVariant` e provenance discriminada para continuar servindo tanto Google Flow quanto import manual, sem `ImageGeneration` falsa ou tabela de batch.

**Tech Stack:** Python 3.11, Pydantic 2, Typer, SQLAlchemy 2, Alembic, SQLite, Pillow, pytest.

**Spec:** `docs/superpowers/specs/2026-09-28-goal-d1-manual-image-batch-intake-design.md`

## Global Constraints

- Manter compatibilidade com Windows 10/11 x64 e Linux; todos os paths externos passam por `Path` e containment explícito.
- Não adicionar dependências; usar Pydantic, Pillow, SQLAlchemy, Alembic e helpers já instalados.
- Não alterar `AGENTS.md`.
- Não implementar watcher, Google Flow novo, HeyGen, FastAPI, React, crop, conversão ou associação heurística.
- `schemaVersion` do manifest é exatamente `1.0`; campos desconhecidos são rejeitados.
- O source root é sempre o diretório pai do manifest; sources são somente leitura e nunca movidos.
- O destination path é `campaigns/<campaign-id>/variants/<scene-variant-id>/images/imported/<sha256>.<ext>` sob o work root configurado.
- Um lote inválido não copia arquivos nem persiste linhas; um lote válido persiste candidatos numa única transação.
- Todo comando CLI emite exatamente um documento JSON em stdout; mensagens de erro são estáveis e sanitizadas.
- Preservar os defaults atuais de inspeção/publicação Google Flow, inclusive a exigência 2K.

## Review Focus

- Source trocado entre `plan()` e `execute()` deve falhar com `image_import_source_changed`, sem candidato persistido nem alteração do source — coberto na Task 4.
- Reimport do mesmo hash aprovado deve reutilizar; hash diferente para variante já aprovada deve bloquear o lote antes da cópia — coberto nas Tasks 3 e 4.
- Path via symlink/junction dentro da pasta de entrada não pode escapar do source root em Windows ou Linux — coberto na Task 2.
- Falha SQLite depois da publicação deve deixar zero candidatos parciais e permitir rerun usando o arquivo content-addressed — coberto na Task 4.
- Migration deve preservar candidatos e invariantes Flow existentes, inclusive slots/recovery ligados a `image_generation_id` — coberto na Task 1.

---

## File map

- Create `src/auraly_pipeline/images/import_batch.py`: contratos, erros, preparação da pasta, planejamento, execução e exportação do schema D1.
- Create `src/auraly_pipeline/campaigns/migrations/versions/0006_manual_image_import.py`: provenance manual e ownership direto de `ImageCandidate`.
- Modify `src/auraly_pipeline/images/domain.py`: campos e validação discriminada de `ImageCandidate`.
- Modify `src/auraly_pipeline/images/db_models.py`: ORM alinhado à migration.
- Modify `src/auraly_pipeline/images/repository.py`: queries por candidato/variante/hash e persistência em lote.
- Modify `src/auraly_pipeline/images/service.py`: review independente de `ImageGeneration` e consulta por variante.
- Modify `src/auraly_pipeline/images/handler.py`, `src/auraly_pipeline/images/flow_handler.py`: preencher ownership/provenance gerada nos candidatos existentes.
- Modify `src/auraly_pipeline/flow/artifacts.py`: parametrizar os helpers seguros existentes sem mudar defaults Flow.
- Modify `src/auraly_pipeline/cli.py`: `prepare-import`, `import-batch`, consulta por variante e export do schema.
- Create `schemas/image-import.schema.json`: schema gerado e versionado.
- Create `tests/test_image_import_batch.py`: contrato, plan, publicação, idempotência e recovery local.
- Modify `tests/test_flow_artifacts.py`, `tests/test_image_domain.py`, `tests/test_image_repository.py`, `tests/test_image_migrations.py`, `tests/test_image_migrations_direct_insert.py`, `tests/test_image_cli.py`: regressões nos boundaries existentes.
- Modify `README.md`, `docs/PROJECT-MEMORY.md`, `docs/GOAL-ROADMAP.md`, `docs/PRD-MVP-MASS-VIDEO-AUTOMATION.md`: operação entregue e status real.

### Task 1: Tornar `ImageCandidate` compatível com origem gerada ou manual

**Files:**
- Create: `src/auraly_pipeline/campaigns/migrations/versions/0006_manual_image_import.py`
- Modify: `src/auraly_pipeline/images/domain.py`
- Modify: `src/auraly_pipeline/images/db_models.py`
- Modify: `src/auraly_pipeline/images/repository.py`
- Modify: `src/auraly_pipeline/images/service.py`
- Modify: `src/auraly_pipeline/images/handler.py`
- Modify: `src/auraly_pipeline/images/flow_handler.py`
- Modify: `tests/test_image_domain.py`
- Modify: `tests/test_image_repository.py`
- Modify: `tests/test_image_service.py`
- Modify: `tests/test_image_review.py`
- Modify: `tests/test_image_recovery.py`
- Modify: `tests/test_image_concurrency.py`
- Modify: `tests/test_flow_checkpoint_repository.py`
- Modify: `tests/test_flow_image_handler.py`
- Modify: `tests/test_flow_recovery.py`
- Modify: `tests/test_image_migrations.py`
- Modify: `tests/test_image_migrations_direct_insert.py`

**Interfaces:**
- Consumes: `SceneVariantRow`, `ImageGenerationRow`, review states e `ImageRepository.immediate_transaction()` existentes.
- Produces: `ImageCandidate.scene_variant_id: str`, `source_kind: Literal["generated", "manual_import"]`, `image_generation_id: str | None`, `import_manifest_sha256: str | None`, `import_source_path: str | None`; `ImageService.list_candidates_for_scene(scene_variant_id: str) -> list[ImageCandidate]`; `ImageService.get_approved_candidate(scene_variant_id: str) -> ImageCandidate | None`.

- [ ] **Step 1: Escrever testes de domínio que fixem as duas provenances válidas**

Adicionar `test_generated_candidate_requires_generation_and_forbids_import_metadata` e `test_manual_candidate_requires_scene_manifest_and_source_path` com asserts para casos válidos e `ValidationError` para combinações híbridas.

- [ ] **Step 2: Rodar os testes de domínio e confirmar RED**

Run: `uv run pytest tests/test_image_domain.py -q`

Expected: FAIL porque os novos campos e checks ainda não existem.

- [ ] **Step 3: Implementar o contrato discriminado em `images/domain.py`**

Adicionar:

```python
ImageSourceKind = Literal["generated", "manual_import"]
```

Atualizar `ImageCandidate` com os campos da interface e validator que exige:

```text
generated     => image_generation_id != null; import fields == null
manual_import => image_generation_id == null; import fields != null
```

Manter `candidate_index >= 0`; imports usam índice `0`.

- [ ] **Step 4: Rodar os testes de domínio e confirmar GREEN**

Run: `uv run pytest tests/test_image_domain.py -q`

Expected: PASS.

- [ ] **Step 5: Escrever testes da migration `0006_manual_image_import`**

Cobrir:

- upgrade de banco em `0005_flow_generation_recovery` preserva um candidato gerado e preenche `scene_variant_id`/`source_kind`;
- insert manual válido com `image_generation_id=NULL` funciona;
- provenance híbrida ou scene inexistente falha;
- duplicata manual `scene_variant_id + sha256` falha;
- segundo aprovado na mesma scene falha entre origens diferentes;
- update de ownership, hash, source kind ou import provenance falha;
- Flow slot não aceita candidato manual sem generation;
- downgrade retorna a `0005` somente quando não há candidatos manuais, recusando perda de dados caso contrário.

- [ ] **Step 6: Rodar testes de migration e confirmar RED**

Run: `uv run pytest tests/test_image_migrations.py tests/test_image_migrations_direct_insert.py -q`

Expected: FAIL porque a revision `0006_manual_image_import` e as novas colunas não existem.

- [ ] **Step 7: Implementar migration e ORM**

A revision recria `image_candidates` via Alembic batch operation para tornar `image_generation_id` nullable, faz backfill de `scene_variant_id` pelo generation pai, adiciona `source_kind`, `import_manifest_sha256` e `import_source_path`, e recria:

- FKs e índices anteriores;
- índice único parcial `uq_manual_image_candidate_scene_sha` com `WHERE source_kind='manual_import'`;
- trigger de artifact/provenance imutável;
- triggers de um único aprovado usando `scene_variant_id` direto;
- triggers Flow slot/candidate preservando o vínculo por generation.

Atualizar `ImageCandidateRow` com exatamente as mesmas nulabilidades e nomes. Atualizar todo caller
de `ImageCandidate(...)` para candidatos gerados passar `scene_variant_id`,
`source_kind="generated"` e import fields nulos; não adicionar defaults que aceitem provenance
ambígua.

- [ ] **Step 8: Atualizar repository e review service para ownership direto**

Adicionar:

```python
def candidate_in_session(session: Session, image_candidate_id: str) -> ImageCandidateRow | None
def approved_candidate_for_scene_in_session(
    session: Session, scene_variant_id: str
) -> ImageCandidateRow | None
def list_candidates_for_scene(self, scene_variant_id: str) -> list[ImageCandidateRow]
```

Trocar `approve_candidate`, `reject_candidate` e `replace_approved_candidate` para usar
`candidate.scene_variant_id`, sem exigir join com `ImageGeneration`. Atualizar `_candidate_to_domain()`.

- [ ] **Step 9: Rodar a regressão completa do domínio de imagens**

Run: `uv run pytest tests/test_image_domain.py tests/test_image_repository.py tests/test_image_service.py tests/test_image_review.py tests/test_image_recovery.py tests/test_image_migrations.py tests/test_image_migrations_direct_insert.py tests/test_image_concurrency.py tests/test_flow_checkpoint_repository.py tests/test_flow_image_handler.py tests/test_flow_recovery.py -q`

Expected: PASS, incluindo candidatos Flow existentes e invariantes concorrentes.

- [ ] **Step 10: Commit**

```bash
git add src/auraly_pipeline/images/domain.py src/auraly_pipeline/images/db_models.py src/auraly_pipeline/images/repository.py src/auraly_pipeline/images/service.py src/auraly_pipeline/images/handler.py src/auraly_pipeline/images/flow_handler.py src/auraly_pipeline/campaigns/migrations/versions/0006_manual_image_import.py tests/test_image_domain.py tests/test_image_repository.py tests/test_image_service.py tests/test_image_review.py tests/test_image_recovery.py tests/test_image_concurrency.py tests/test_flow_checkpoint_repository.py tests/test_flow_image_handler.py tests/test_flow_recovery.py tests/test_image_migrations.py tests/test_image_migrations_direct_insert.py
git commit -m "feat: support manual image candidate provenance"
```

### Task 2: Generalizar inspeção e publicação segura de imagem

**Files:**
- Modify: `src/auraly_pipeline/flow/artifacts.py`
- Modify: `tests/test_flow_artifacts.py`
- Create: `tests/test_image_import_batch.py`

**Interfaces:**
- Consumes: containment, identity binding, Pillow/container validation e publicação exclusiva já implementados em `flow/artifacts.py`.
- Produces: `resolve_trusted_image_path(path: Path, *, trusted_root: Path) -> Path`; `inspect_image_artifact(path: Path, *, minimum_long_edge: int | None = None) -> FlowArtifactFacts`; `publish_image_artifact_exclusive(staging: Path, final: Path, *, trusted_root: Path, minimum_long_edge: int | None = None) -> FlowArtifactFacts`. Wrappers Flow mantêm `minimum_long_edge=2048`.

- [ ] **Step 1: Escrever regressões para API genérica e defaults Flow**

Adicionar testes que provem:

- retrato PNG/JPEG/WebP abaixo de 2048 é aceito por `inspect_image_artifact(..., minimum_long_edge=None)`;
- o mesmo arquivo continua rejeitado por `inspect_flow_artifact()`;
- extensão diferente dos bytes falha;
- source via symlink ou junction que escape do root falha;
- publicação genérica recupera final idêntico e nunca sobrescreve final divergente.

- [ ] **Step 2: Rodar testes e confirmar RED**

Run: `uv run pytest tests/test_flow_artifacts.py tests/test_image_import_batch.py -q`

Expected: FAIL porque a API genérica não existe.

- [ ] **Step 3: Extrair apenas os entrypoints genéricos no módulo existente**

Parametrizar `_inspect_artifact()`/`_inspect_open_stream()` com `minimum_long_edge: int | None`; adicionar os três entrypoints da interface; manter `inspect_flow_artifact()` e `publish_flow_artifact_exclusive()` como wrappers compatíveis com `2048`.

Não mover ou duplicar o código de binding/publicação nesta Task.

- [ ] **Step 4: Rodar regressões de artifacts**

Run: `uv run pytest tests/test_flow_artifacts.py tests/test_image_import_batch.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/auraly_pipeline/flow/artifacts.py tests/test_flow_artifacts.py tests/test_image_import_batch.py
git commit -m "refactor: share trusted image artifact helpers"
```

### Task 3: Implementar contrato, preparação de pasta e dry-run

**Files:**
- Create: `src/auraly_pipeline/images/import_batch.py`
- Modify: `tests/test_image_import_batch.py`

**Interfaces:**
- Consumes: `CampaignService.get_campaign()`, artifact entrypoints da Task 2 e consultas por scene/hash da Task 1.
- Produces: `ImageImportBatch`, `ImageImportItem`, `ImageImportPlanItem`, `ImageImportPlan`, `ImageImportPrepared`, `ImageImportIssue`, `ImageImportError`, `ImageImportValidationError`; `ImageImportService.for_database(database_path: Path, *, work_root: Path | None = None)`; `prepare_directory(campaign_id: str, output: Path) -> ImageImportPrepared`; `plan(manifest_path: Path) -> ImageImportPlan`.

Os contratos fixam estes campos:

```text
ImageImportItem: variant_id, path
ImageImportBatch: schema_version, campaign_id, approve_imported, approved_by, items
ImageImportPlanItem: variant_id, scene_variant_id, source_relative_path, format,
                     width, height, size_bytes, sha256, destination_path,
                     action(create|reuse), review_status
ImageImportPlan: batch, manifest_sha256, source_root interno, items
ImageImportPrepared: campaign_id, manifest_path, images_path, variant_count
ImageImportIssue: code, variant_id opcional, message sanitizada
```

- [ ] **Step 1: Escrever testes do manifest e `prepare_directory()`**

Fixar o JSON mínimo da spec e testar:

- aliases camelCase e `extra="forbid"`;
- `approveImported=true` exige `approvedBy`; false o proíbe;
- versão diferente de `1.0`, item vazio, path absoluto e traversal falham;
- helper cria `images/` e manifest com variantes ordenadas e paths vazios;
- output existente não é sobrescrito.

- [ ] **Step 2: Rodar testes e confirmar RED**

Run: `uv run pytest tests/test_image_import_batch.py -q`

Expected: FAIL por ausência dos contratos e serviço.

- [ ] **Step 3: Implementar contratos e preparação atômica**

Usar `ContractModel` para os payloads públicos e dataclasses frozen somente para paths absolutos internos do plano. `prepare_directory()` escreve primeiro um temporário adjacente e publica sem overwrite; não cria filenames para imagens.

- [ ] **Step 4: Escrever testes de planejamento completo**

Cobrir:

- três variantes válidas geram três ações `create`, ordenadas por `variantId`;
- batch incompleto, variante duplicada/desconhecida e source físico duplicado reportam todos os erros determinísticos;
- PNG/JPEG/WebP verticais registram formato, dimensões, bytes e hash;
- quadrado/horizontal, corrupto e extensão falsa falham;
- candidato manual existente com mesmo hash vira `reuse`;
- candidato aprovado com hash diferente produz `image_import_approved_candidate_conflict`;
- nenhuma falha cria `work/campaigns` ou muda o banco.

- [ ] **Step 5: Implementar `plan(manifest_path)`**

O método resolve o source root pelo pai real do manifest, calcula o hash do JSON canônico validado, resolve `variantId` para UUID, inspeciona todos os itens e só então consulta ações persistidas. Retornar um único `ImageImportValidationError` com issues ordenadas por `variantId` e código.

- [ ] **Step 6: Rodar testes do plan e confirmar GREEN**

Run: `uv run pytest tests/test_image_import_batch.py -q`

Expected: PASS para contrato, preparação e dry-run; ainda sem execução.

- [ ] **Step 7: Commit**

```bash
git add src/auraly_pipeline/images/import_batch.py tests/test_image_import_batch.py
git commit -m "feat: plan manual image batch imports"
```

### Task 4: Publicar e persistir o batch de forma idempotente

**Files:**
- Modify: `src/auraly_pipeline/images/import_batch.py`
- Modify: `src/auraly_pipeline/images/repository.py`
- Modify: `tests/test_image_import_batch.py`

**Interfaces:**
- Consumes: `ImageImportPlan` da Task 3, publicação da Task 2 e `ImageCandidate` manual da Task 1.
- Produces: `ImageImportResult`; `ImageImportService.execute(plan: ImageImportPlan) -> ImageImportResult`; `ImageImportService.import_batch(manifest_path: Path, *, dry_run: bool = False) -> ImageImportPlan | ImageImportResult`.

`ImageImportResult` contém `schema_version`, `status="completed"`, `campaign_id`,
`manifest_sha256`, `total`, `created`, `reused`, `approved` e items ordenados com
`image_candidate_id`, `variant_id`, `scene_variant_id`, `source_path`, `sha256`, `action` e
`review_status`.

- [ ] **Step 1: Escrever o happy-path end-to-end**

`test_execute_imports_three_images_and_survives_restart` deve afirmar:

```text
created == 3
reused == 0
approved == 3
cada destination contém os mesmos bytes do source
sources permanecem byte-identical
serviço reaberto encontra um aprovado manual por scene com manifest hash/path relativo
```

- [ ] **Step 2: Rodar o happy-path e confirmar RED**

Run: `uv run pytest tests/test_image_import_batch.py::test_execute_imports_three_images_and_survives_restart -q`

Expected: FAIL porque `execute()` não existe.

- [ ] **Step 3: Implementar publicação e transação em lote**

Para cada item `create`, copiar para staging privado adjacente, reinspecionar e chamar `publish_image_artifact_exclusive(..., minimum_long_edge=None)`. Depois usar `BEGIN IMMEDIATE` para revalidar conflitos e inserir todos os candidatos com um único timestamp; `approveImported` define `approved` ou `pending_review`.

O candidate UUID é criado apenas dentro da transação. A resposta é ordenada por `variantId`.

- [ ] **Step 4: Escrever regressões de idempotência, race e rollback**

Adicionar:

- rerun idêntico retorna `created=0`, `reused=3` e não muda IDs/mtimes;
- source trocado após `plan()` falha antes do commit;
- aprovado divergente inserido após `plan()` é rechecado dentro da transação;
- falha injetada no segundo insert deixa zero candidatos do batch;
- falha de banco limpa somente finals criados por esta execução quando ainda não referenciados;
- residue content-addressed de crash é reutilizado no rerun;
- conflito de bytes no destination nunca sobrescreve o arquivo existente;
- duas execuções concorrentes resultam em um candidato manual por scene/hash.

- [ ] **Step 5: Implementar cleanup limitado e mapeamento de erros**

Registrar os finals realmente criados pela execução. Em erro normal, remover somente final ainda byte-identical, dentro do work root e sem referência no banco; falha de cleanup não mascara o erro primário. Mapear os códigos listados na spec.

- [ ] **Step 6: Rodar toda a suíte D1 e regressões de review**

Run: `uv run pytest tests/test_image_import_batch.py tests/test_image_repository.py tests/test_image_concurrency.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/auraly_pipeline/images/import_batch.py src/auraly_pipeline/images/repository.py tests/test_image_import_batch.py
git commit -m "feat: execute idempotent image batch imports"
```

### Task 5: Expor CLI JSON, consultas e schema

**Files:**
- Modify: `src/auraly_pipeline/cli.py`
- Modify: `src/auraly_pipeline/images/import_batch.py`
- Modify: `tests/test_image_cli.py`
- Create: `schemas/image-import.schema.json`

**Interfaces:**
- Consumes: `ImageImportService` completo da Task 4 e `ImageService.list_candidates_for_scene()` da Task 1.
- Produces: comandos `image prepare-import`, `image import-batch`, `export-image-import-schema`; `image candidate list` aceita exatamente um de generation ID ou `--scene-variant-id`; `export_image_import_schema(output: Path) -> Path`.

- [ ] **Step 1: Escrever testes CLI RED**

Cobrir:

- `prepare-import` retorna um JSON de sucesso e cria template;
- `import-batch --dry-run` retorna `status=valid` e não cria destination;
- `import-batch` retorna counts e items sem path absoluto;
- erro conhecido retorna `{success:false,error:{code,message}}` e exit 1;
- erro inesperado retorna somente `image_operation_failed`;
- `candidate list --scene-variant-id` inclui importados;
- generation positional anterior continua funcionando;
- fornecer ambos ou nenhum seletor da listagem falha com erro sanitizado;
- export gera schema com `$id=https://auraly.local/schemas/image-import.schema.v1.json`.

- [ ] **Step 2: Rodar testes CLI e confirmar RED**

Run: `uv run pytest tests/test_image_cli.py -q`

Expected: FAIL porque os comandos não existem.

- [ ] **Step 3: Implementar comandos e payloads JSON**

Carregar o manifest dentro do service; a CLI não replica validação. Fechar services no `finally` usando o padrão `_close_image_service()`. Serializar models com `by_alias=True, mode="json"`.

- [ ] **Step 4: Gerar e versionar o schema**

Run: `uv run auraly export-image-import-schema --output schemas/image-import.schema.json`

Expected: arquivo criado com schema `1.0`, aliases camelCase e `additionalProperties: false`.

- [ ] **Step 5: Rodar testes CLI e schema**

Run: `uv run pytest tests/test_image_cli.py tests/test_models.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/auraly_pipeline/cli.py src/auraly_pipeline/images/import_batch.py tests/test_image_cli.py schemas/image-import.schema.json
git commit -m "feat: add manual image import CLI"
```

### Task 6: Documentar a capability entregue e verificar o Goal

**Files:**
- Modify: `README.md`
- Modify: `docs/PROJECT-MEMORY.md`
- Modify: `docs/GOAL-ROADMAP.md`
- Modify: `docs/PRD-MVP-MASS-VIDEO-AUTOMATION.md`

**Interfaces:**
- Consumes: comportamento verificado nas Tasks 1–5.
- Produces: instrução operacional copiable e separação atualizada entre capability entregue e roadmap; D2A passa a ser o próximo Goal.

- [ ] **Step 1: Atualizar documentação somente com comportamento comprovado**

Adicionar o fluxo:

```text
prepare-import → colocar imagens/preencher paths → import-batch --dry-run → import-batch
```

Marcar D1 `IMPLEMENTED`/`LOCAL_VERIFIED` somente depois dos checks abaixo; mover `NEXT` para D2A. Manter Google Flow como preservado/pausado e não afirmar HeyGen implementado.

- [ ] **Step 2: Rodar verificação focada D1**

Run: `uv run python scripts/verify.py fast --pytest tests/test_image_import_batch.py tests/test_image_cli.py tests/test_image_migrations.py tests/test_flow_artifacts.py`

Expected: todas as etapas e testes passam sem failures.

- [ ] **Step 3: Rodar verificação completa**

Run: `uv run python scripts/verify.py full`

Expected: Ruff, mypy, testes e demais gates do harness passam.

- [ ] **Step 4: Revisar diff e critérios de saída**

Run: `git diff --check && git status --short`

Expected: nenhum whitespace error; somente arquivos previstos no File map modificados.

Confirmar manualmente, a partir dos testes, os sete critérios de saída da spec: import explícito, três variantes prontas, invalid batch sem mutação, rerun idempotente, source intacto, restart com provenance e regressão Flow verde.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/PROJECT-MEMORY.md docs/GOAL-ROADMAP.md docs/PRD-MVP-MASS-VIDEO-AUTOMATION.md
git commit -m "docs: mark manual image batch intake delivered"
```

- [ ] **Step 6: Registrar verificação Windows/Linux**

Confirmar o run Windows local no handoff. Após push, aguardar os checks Windows e Linux do GitHub antes de declarar o Goal verificado nas duas plataformas.
