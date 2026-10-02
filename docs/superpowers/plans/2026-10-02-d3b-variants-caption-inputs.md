# D3B Variants and Caption Inputs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Planejar variantes editoriais determinísticas de um MP4 existente e vincular legendas à copy/voz aprovadas, sem custo upstream.

**Architecture:** Contratos de batch separados e planner puro no domínio editing; serviço local consulta SQLite somente leitura e reutiliza EditingService. Um JSON completo embute manifests v2 intactos e caption input; CLI fina, sem Jobs ou renderer.

**Tech Stack:** Python 3.11, Pydantic, Typer, sqlite3/stdlib, ffprobe e pytest/Ruff/mypy existentes; nenhuma dependência nova.

**Spec:** `docs/superpowers/specs/2026-10-02-d3b-variants-caption-inputs-design.md`, aprovada em 2026-10-02.

## Global Constraints

- Contratos novos v1.0; EditProfile v1 e EditManifest v2 D3A e legado v1 ficam intactos.
- Lista explícita não vazia; key segura única; maxOutputs inteiro positivo estrito, default 3; sem matriz/cartesian product.
- Captions vêm de CopyMaster.spoken_text; tokens por split(), cues por intervalo inicial inclusivo/final exclusivo, texto unido por espaço.
- Timing somente timebase="source_mp4", origin manual/external_alignment, acceptedBy obrigatório; sem ASR/alinhamento ou timing artificial.
- Todos os outputs válidos antes de publicação; dry-run sem mkdir, manifests, planos, Jobs ou escrita SQL.
- Source/WAV preservados; banco somente leitura, sem migrate_database ou construtores de provider/JobService.
- Sem UI, preview, render, dependências/migrações novas, paid calls ou mudança de aprovação.
- Não alterar AGENTS.md/sources; prefixar comandos com rtk; usar apply_patch para edições manuais.
- Roots lexicais validados antes de resolve(); contenção, links/junctions, nomes e erros sanitizados continuam obrigatórios.

## Review Focus

- Copy aprovada mais nova não substitui a vinculada ao WAV do render — Task 3.
- DB com path contendo espaço/#/? é aberto readonly sem quebrar o URI nem criar outro arquivo — Task 3.
- Caption timing altera outputHash mesmo com captions disabled; label altera apenas planHash — Task 2.
- Snapshot adulterado com hashes recalculados mas cues/IDs/contadores incoerentes é rejeitado — Tasks 2/3.
- Variante final inválida não deixa manifests/planos das anteriores publicados — Task 3.

## Arquivos e interfaces

Criar `src/auraly_pipeline/editing/batch_domain.py`, `batch_planner.py`,
`batch_service.py`; modificar somente `editing/cli.py`, `editing/schema.py`,
`scripts/verify.py`, helper de sanitização em `editing/domain.py` e documentação necessária. Helpers filesystem existentes
de `editing/service.py` podem ser reutilizados dentro desse domínio, sem mover
internals de voz/HeyGen ou criar abstrações. Não modificar contratos D3A.

Testes novos: `tests/test_editing_batch_domain.py`, `test_editing_batch_planner.py`,
`test_editing_batch_service.py`, `test_editing_batch_cli.py`; estender
`test_editing_schema.py`/`test_verify_harness.py`. Builders comuns ficam em
`tests/editing_batch_helpers.py`; reutilizar `tests/editing_helpers.py` e
`tests/heygen_video_support.py` quando úteis, sem novo framework.

### Task 1: Contratos tipados de variantes, timing e plano

**Files:** Create `editing/batch_domain.py`, `tests/test_editing_batch_domain.py`,
`tests/editing_batch_helpers.py` (paths completos conforme mapa acima).

**Interfaces:** Modelos `EditVariant`, `EditBatchRequest`, `CaptionTimingCue`,
`CaptionTimingInput`, `ResolvedCaptionCue`, `CaptionInput`, `EditPlannedOutput`,
`EditBatchPlan` herdam EditingModel; reutilizam EditOverrides/AssetRef/IdentityRef/
SourceVideoRef/EditManifestV2 e safe_id. Dataclass interna `BatchInputs` contém
`source: SourceVideoRef`, `copy: CopyMaster`, `voice_ref: IdentityRef`,
`image_ref: IdentityRef`; não serializar timestamps da copy no plano.
Modelo `CopyRef` contém `id: str`, `version: int` positivo estrito e `hash: Sha`;
nos manifests v2 usar IdentityRef(ID/hash), sem acrescentar version ao contrato v2.

Fields/aliases seguem a spec. Fixar nomes adicionais: request `timingRef: AssetRef | None`;
CaptionInput `copyRef` (ID/version/hash), `voiceRef`, `text`, `timingStatus`,
`timingRef`, `origin`, `acceptedBy`, `timebase`, `cues`; nulls de provenance somente
quando missing. Plan `plannerVersion="1.0"`, `campaignId`, `renderId`, `videoId`,
`source`, `copyRef`, `voiceRef`, `imageRef`, `maxOutputs`, `outputCount`,
`captionInput`, `outputs`, `planHash`. Output `key`, `label`, `outputVariantId`,
`manifest`, `manifestHash`, `outputHash`, `filename`, `captionState`.
Source.id deriva do renderId local; imageRef.hash é SHA da imagem; voiceRef.hash
é SHA do WAV processado. IDs de entidades existentes podem ser UUID; somente
IDs/key usados como segmentos obedecem safe_id.

- [x] Escrever `test_default_limit_is_three`: `assert request.max_outputs == 3`.
  `test_limit_and_keys`: rejeitar 4 outputs com limite 3, lista vazia, key duplicada,
  maxOutputs bool/zero/float, extras, IDs reservados/paths inválidos.
  `test_timing_contract`: origem/timebase inválidas, NaN/Infinity, índices não
  inteiros, cue end<=start e acceptedBy sensível falham; zero startSec válido.
- [x] Rodar `rtk uv run python -m pytest tests/test_editing_batch_domain.py -q`;
  confirmar RED pelo contrato ausente.
- [x] Implementar contratos, limites e relações locais (missing/provided,
  metadados/cues coerentes); revalidar no limite do serviço. Usar validação pública
  de identidade do operador já existente e mensagens com campos conhecidos.
- [x] Rodar `rtk uv run python scripts/verify.py fast --pytest tests/test_editing_batch_domain.py tests/test_editing_domain.py`;
  exigir PASS.
- [x] Commit `feat: add editing batch and caption input contracts`.

### Task 2: Planner puro, captions e identidade determinística

**Files:** Create `editing/batch_planner.py`, `tests/test_editing_batch_planner.py`;
extend builders e contratos Task 1 para invariantes completas.

**Interfaces:** `variant_requests(request: EditBatchRequest, inputs: BatchInputs)
-> list[tuple[EditVariant, EditResolveRequest]]` ordenado por key;
`build_batch_plan(request: EditBatchRequest, inputs: BatchInputs,
manifests: list[EditManifestV2], *, timing: CaptionTimingInput | None = None)
-> EditBatchPlan`; `verify_batch_plan(plan: EditBatchPlan) -> None`.
Manifests têm mesma ordem dos requests. Reutilizar content_hash/verify_manifest_hash
e resolve_manifest para fixtures puras; nenhuma função lê disk/SQL/env/relógio.

Identidade: payload campaignId/videoId/sourceId/sourceSha256/key, `ab-`+hash[:60].
Output hash: payload exato `{manifest, captionInput, plannerVersion}`; serializar
aliases/defaults normalizados. Filename `<outputVariantId>-<outputHash>.mp4`.
Plan hash exclui somente planHash; labels/limites incluídos. Nenhum campo aleatório.

- [x] Escrever `test_three_headlines_reuse_upstream`: `assert plan.output_count == 3`;
  `assert len({o.manifest.headline.text for o in plan.outputs}) == 3`;
  `assert all(o.manifest.source == inputs.source for o in plan.outputs)`.
  `test_order_and_replay`: request reversed gera model_dump/hash/filenames iguais.
  `test_headline_is_not_caption_text`: caption.text==copy.spoken_text e não concatena headline;
  tokens da fala coincidentes com headline não são removidos.
- [x] Escrever `test_caption_timing_coverage_and_identity`: cues de todos tokens
  em ordem produzem textos esperados; gaps/overlap/duplicação, hash/copy/timebase
  divergentes, intervalos fora da duração falham. Sem timing: cues=[], status missing;
  enabled por variante determina disabled/timing_missing/timing_provided.
  `test_timing_and_label_hash_boundaries`: mudar timing muda outputHash mesmo disabled;
  mudar label mantém outputHash/filename e muda planHash.
  `test_rehashed_semantically_invalid_plan`: rejeitar contagem, ID, source refs,
  manifestHash, cue.text/index/provenance e filename incoerentes mesmo rehashed.
- [x] Rodar `rtk uv run python -m pytest tests/test_editing_batch_planner.py -q`;
  confirmar RED por funções ausentes.
- [x] Implementar geração de requests e plano, textos de cue por tokens canônicos,
  cobertura completa e checks cross-field. Revalidar inputs/planos recebidos;
  verificar manifests v2, IDs/hashes/filenames, referências comuns, estado captions,
  outputCount==len(outputs)<=maxOutputs e ordem/keys únicas. Relatar key/campo
  sanitizado; incluir origin/acceptedBy/timebase dentro do snapshot.
- [x] Rodar `rtk uv run python scripts/verify.py fast --pytest tests/test_editing_batch_domain.py tests/test_editing_batch_planner.py tests/test_editing_resolver.py`;
  exigir PASS.
- [x] Commit `feat: plan deterministic headline variants and caption inputs`.

### Task 3: Serviço local readonly e publicação exclusiva

**Files:** Create `editing/batch_service.py`, `tests/test_editing_batch_service.py`;
extend `tests/editing_batch_helpers.py`.

**Interfaces:** `EditBatchService(*, project_root: Path, work_root: Path)`;
`plan(request: EditBatchRequest, *, database_path: Path, persist: bool = True)
-> EditBatchPlan`; `get_plan(campaign_id: str, video_id: str, plan_hash: str)
-> EditBatchPlan`. Database existe antes da chamada e está sob project root.
Abrir conexão de duração da chamada por sqlite3.connect(Path.as_uri()+"?mode=ro",
uri=True), após validar ancestry/arquivo regular; transaction de leitura coerente,
close em finally. Não usar immutable=1 em banco WAL ativo ou helpers que migram.

Consultar por parâmetros `heygen_renders`, `voice_masters`, `copy_masters`,
`image_candidates`; usar modelos públicos VideoPlanItem/VideoSource/CopyMasterContent
para validar JSON e recalcular copy hash/spoken_text. Render row campaign/IDs
devem coincidir com item_json; status ready/source presente. Voz aprovada exata
e copy aprovada vinculada; hashes de item.audio/image coincidem com registros.
Carregar CopyMaster usando fields persistidos; não escolher versão latest.
Construir BatchInputs com refs verificadas, source path convertido work→project.
Validar WAV processado por path/hash e MP4 por serviço D3A; transcrição/ASR não
vira timing. Metadata de source_json não substitui hash/probe reais.

- [x] Escrever `test_readonly_plan_and_replay`: setup usa fixtures existentes com
  FakeHeyGenProvider/MP4 sintético e fecha serviços antes de snapshot SQL; execução
  D3B não toca provider. Comparar dados SQL/Jobs/IDs e SHA/mtime MP4/WAV antes/depois;
  `assert second.plan_hash == first.plan_hash` e get_plan==first.
  `test_pinned_copy_not_latest`: nova copy aprovada não troca copyRef/text da voz original.
  `test_db_uri_special_characters`: path com espaço/#/? quando SO permitir, sem novoarquivo.
- [x] Escrever `test_missing_or_wrong_upstream`: DB/schema ausente, render não ready,
  campanha/row/item divergentes, voz/copy não aprovada, hash errado, MP4/WAV
  adulterado falham sem publicar. DB ausente não é criado; SQL error sem path bruto.
  `test_last_invalid_variant_publishes_nothing`: primeira válida, última endSec
  além da duração; nenhum plan.json/manifest.json novo.
  `test_dry_run_does_not_create_editing_directory`: profiles já existentes,
  restante work tree e DB inalterados. `test_corrupt_plan_not_overwritten`:
  JSON truncado/hash inválido e invariantes inválidas rehashed falham no replay/get.
  Paths POSIX/Windows, ancestors junction/symlink, roots fora de contenção e
  timing adulterado não são aceitos; skip apenas link impossível pelo SO.
- [x] Rodar `rtk uv run python -m pytest tests/test_editing_batch_service.py -q`;
  confirmar RED pelo serviço ausente.
- [x] Implementar leitura readonly, ligação de registros e assets, timing opcional,
  variant_requests → EditingService.resolve(persist=False) → build_batch_plan.
  Publicar somente plano completo em campaigns/<campaign>/editing/plans/<video>/<hash>/plan.json
  com helpers seguros de editing; replay/get validam schema/hash/invariantes/identidade
  do path. Não persistir v2s duplicados. Serializar somente refs públicas, sem
  account refs, signed URLs ou paths absolutos; erros sem valores de entrada.
- [x] Rodar `rtk uv run python scripts/verify.py fast --pytest tests/test_editing_batch_service.py tests/test_editing_batch_planner.py tests/test_editing_service.py`;
  exigir PASS.
- [x] Commit `feat: persist editing batch plans from read-only campaign inputs`.

### Task 4: CLI, schemas e exemplos sintéticos

**Files:** Modify `editing/cli.py`, `editing/schema.py`, `editing/domain.py` (helper somente), `scripts/verify.py`,
`tests/test_editing_schema.py`, `tests/test_verify_harness.py`;
create `tests/test_editing_batch_cli.py`, `schemas/edit-batch-request.schema.json`,
`schemas/caption-timing.schema.json`, `schemas/edit-batch-plan.schema.json`,
e respectivos exemplos sem `.schema` em `examples/`.

**Interfaces:** Manter register_editing_commands; adicionar comandos plan e plan-get.
`export_editing_schemas(output_dir: Path) -> tuple[Path, ...]` passa a exportar seis
schemas; os três D3A mantêm bytes. Módulo export atual permanece único.
CLI plan --request/--database/--dry-run e roots D3A; plan-get --campaign-id/
--video-id/--plan-hash e roots, sem DB. Reutilizar roots lexicais da CLI sem resolve
prematuro e ampliar sanitização de loc para novos modelos, sem ecoar keys arbitrárias.
Extensão compatível: `validation_field(exc: ValidationError, *,
extra_models: tuple[type[EditingModel], ...] = ()) -> str`; CLI fornece os modelos
batch, reutilizando a whitelist existente. Nenhum schema D3A muda.

- [x] Escrever CliRunner tests `test_plan_dry_run_and_get`, `test_plan_errors_are_sanitized`:
  `assert result.exit_code == 0`; stdout valida EditBatchPlan; dry-run não publica;
  limite/campo inválido/DB ausente exit=1 sem stdout parcial/paths/URLs/tokens.
  Default/env root junction é recusado. Fluxos CLI legado/v2 continuam funcionando.
- [x] Escrever tests de schemas/example validation/extras e drift: três schemas D3A
  e legado byte-identical; cada novo schema aparece em generated_files do step
  editing schemas; seis exports determinísticos, sem step redundante no harness.
- [x] Rodar `rtk uv run python -m pytest tests/test_editing_batch_cli.py tests/test_editing_schema.py tests/test_verify_harness.py -q`;
  confirmar RED por comandos/schemas ausentes.
- [x] Implementar CLI fina, exporter expandido e exemplos sintéticos coerentes.
  Exemplo sem timing e exemplo sidecar completo não alegam ter mídia disponível;
  schemas seguem aliases/defaults/extra-forbid. Atualizar registro generated_files.
- [x] Rodar `rtk uv run python -m auraly_pipeline.editing.schema` e
  `rtk uv run python scripts/verify.py fast --pytest tests/test_editing_batch_cli.py tests/test_editing_schema.py tests/test_verify_harness.py tests/test_editing_cli.py tests/test_schema.py`;
  exigir PASS/sem drift.
- [x] Commit `feat: expose editing batch planning CLI and schemas`.

### Task 5: Evidência completa e handoff

**Files:** Modify README.md, docs/PROJECT-MEMORY.md, docs/GOAL-ROADMAP.md,
docs/PRD-MVP-MASS-VIDEO-AUTOMATION.md; create docs/superpowers/2026-10-02-d3b-verification.md.

- [x] Documentar CLI, refs/caption states, caminhos, pendência de timing e plano
  separado do manifest v2. Capacidades D3B só marcadas entregues após evidência;
  UI/render/alinhamento continuam futuros. Atualizar checkboxes conforme execução.
- [x] Rodar `rtk uv run python scripts/verify.py full`; exigir todos steps PASS,
  registrar contagem observada, não a estimada.
- [x] Smoke com render D2C existente: consultar DB real somente leitura, executar
  dry-run com três headlines e profile exclusivo `d3b-smoke` no diretório
  editing/profiles do work root operacional (fora de campaigns/d2c-001).
  Se esse ID já existir, usar versão já compatível ou outro ID explícito sem overwrite.
  A única publicação permitida no smoke é esse profile de teste; nenhum plano
  ou manifest é persistido. Registrar artefato de teste criado;
  conferir replay estável, source/WAV/SQL intactos, zero paid calls. Não inserir
  sidecar fictício para alegar timing real; registrar missing no áudio importado.
  Não escrever na pasta de canário; se input indisponível, registrar bloqueio real.
- [x] Executar uma revisão independente do diff completo conforme skill de execução
  Nativa, focada em contratos, SQL readonly, paths/publicação e spec. Corrigir
  findings relevantes com RED→GREEN, commits estreitos e gate repetido.
- [x] Revisar diff por secrets/mídia/paths privados/scope, rodar
  `rtk git diff --check`; commit `docs: record D3B planning verification and usage`.
- [x] Handoff com evidência IMPLEMENTED/LOCAL_VERIFIED e limitações de timing;
  nenhum novo PROVIDER_VERIFIED. Merge/push de implementação só quando solicitado.

## Revisão e execução

Método Nativa previamente escolhido: superpowers:executing-plans, tarefas
sequenciais com TDD/commits e uma revisão independente final. Após aprovação deste
plano, usar using-git-worktrees para criar/reutilizar worktree isolada na base que
inclui spec/plano commitados. Não começar implementação nesta etapa de planejamento.

Self-review: contratos→Task 1; identidade/captions→Task 2; upstream/SQL/arquivos→Task 3;
CLI/schemas→Task 4; verificação/docs→Task 5. Cinco Review Focus têm testes nas tarefas
proprietárias; assinaturas/types dos produtores e consumidores coincidem. Este
plano foi aprovado pelo usuário em 2026-10-02; execução Nativa concluída, gate 15/15 (1555 passed/21 skipped), revisão e correção verificadas.
