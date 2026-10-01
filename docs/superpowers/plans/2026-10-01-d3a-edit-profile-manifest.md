# D3A Edit Profile and Manifest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolver e persistir configurações de edição versionadas sem alterar mídia ou executar providers.

**Architecture:** Pequeno domínio `editing`, resolver puro e serviço filesystem com CLI fina. Contratos novos coexistem com o legado v1 intacto; não criar tabelas, Jobs, workers ou framework de configuração.

**Tech Stack:** Python 3.11, Pydantic, Typer, stdlib, pytest/Ruff/mypy existentes; ffprobe existente para verificar mídia local.

**Spec:** `docs/superpowers/specs/2026-10-01-d3a-edit-profile-manifest-design.md` (aprovada em 2026-10-01).

## Global Constraints

- Precedência fixa: `profile < campaign < video < outputVariant`.
- Campo omitido herda. Zero e false são valores explícitos, não ausência.
- O resolver recebe inputs já verificados e não lê arquivos, banco, relógio, variáveis de ambiente ou rede.
- Datas de criação/publicação ficam fora do snapshot resolvido e do seu hash.
- Não há merge recursivo de dicionários livres, concatenação de listas ou aceitação silenciosa de campo desconhecido.
- Não converter automaticamente v1 em v2; preservar ingest, modelos, exemplos e schema v1.
- Não modificar AGENTS.md ou arquivos sincronizados em sources/.
- Sem renderer, UI, captions text/timing, planejamento batch A/B, nova dependência ou chamada paga.
- Comandos shell prefixados por `rtk`; alterações manuais com apply_patch. Não incluir mídia, credenciais ou paths privados nos commits.

## Review Focus

- Override round-trip JSON não transforma campos omitidos em null explícito — teste na Task 2.
- Profile alterado depois de publicado não é aceito pela versão antiga — teste na Task 3.
- Replay após publicação interrompida não reutiliza JSON truncado ou sobrescreve destino — teste na Task 3.
- MP4 com duração declarada diferente do probe falha antes de publicar — teste na Task 3.
- Fonte habilitada ausente, inválida ou apontando para junction não usa fallback do sistema — teste na Task 3.

## Arquivos e interfaces comuns

Criar somente `editing/__init__.py`, `domain.py`, `resolver.py`, `service.py`, `cli.py` e `schema.py`.
Testes por responsabilidade: `test_editing_domain.py`, `test_editing_resolver.py`,
`test_editing_service.py`, `test_editing_cli.py`, `test_editing_schema.py`.
`tests/editing_helpers.py` reúne apenas os builders de dados/arquivos usados por esses testes,
sem nova camada de fixtures/framework. Não mover código legado por organização estética.

Reutilizar `ContractModel`, `configured_project_root`, `configured_work_root`,
`probe_media` e validações públicas compatíveis. Os helpers de hash/publicação de voz
e HeyGen são privados: usar `hashlib`/`json` e o padrão existente de staging + publicação
exclusiva, sem importar internals ou refatorar domínios não relacionados.

### Task 1: Contratos tipados e legado preservado

**Files:** Create `src/auraly_pipeline/editing/{__init__,domain}.py`, `tests/test_editing_domain.py`, `tests/editing_helpers.py`.

**Interfaces:** `domain.py` produz `EditProfile`, `EditResolveRequest`, `EditOverrides`,
`EditManifestV2`, `AssetRef`, `SourceVideoRef`, `ProfileRef` e `EditingError(ValueError)`.
Todos os contratos herdam a política camelCase/extra-forbid existente, rejeitam NaN/Infinity
e validam novamente no limite do serviço. `EditingError` contém `field: str`,
`layer: str | None` e mensagem pública, sem input bruto.

Decisões de contrato:

- Profile: schemaVersion `1.0`, profileId, name, version inteiro >0, createdAt timezone-aware e defaults.
- Request: schemaVersion `1.0`, profileRef (ID/version/hash), campaignId, videoId,
  outputVariantId, source, headlineText, campaign/video/outputVariant overrides.
  Copy/Voice refs opcionais são ID/hash; musicAccepted boolean explícito, default false.
- Source: ID, path relativo, SHA-256 lowercase de 64 hex, durationSec finito >0.
  AssetRef: path relativo, SHA-256; tipos font/music distinguem os campos consumidores.
- Manifest: schemaVersion `2.0`, resolverVersion `1.0`, input refs, profileRef,
  configurações resolvidas, overrides, provenance `dict[str, Literal[profile,input,source,campaign,video,outputVariant]]`, manifestHash.
- Headline/captions: defaults enabled=false, styleId `plain`, font nullable,
  fontWeight 100..900, fontSizePx >0, lineHeight >0, color `#RRGGBB[AA]`,
  strokeWidthPx >=0, strokeColor, shadowEnabled/color/offsetX/offsetY,
  backgroundEnabled/color/paddingPx >=0, anchor top/center/bottom,
  x/y e safeTop/Right/Bottom/Left 0..1, maxLines inteiro >0,
  fitPolicy wrap/shrink/error. Caption highlightEnabled/color; nenhum texto/timing.
- Headline style adiciona startSec >=0/endSec nullable >0; texto só request/overrides/resolved.
  Não aceitar spoken, exceto constant false no resolved para documentar visual-only.
- Output defaults 1080×1920, 30 FPS, MP4/H.264. Music defaults enabled=false,
  asset nullable, volumeDb=-22, duckUnderVoiceDb=-8, loop=true,
  trimStartSec=0, trimEndSec nullable, fadeInSec=.4/fadeOutSec=1, todos finitos.
- Framing defaults fit cover (contain também suportado), scale=1 em [1,1.25],
  x=y=.5 em [0,1], zoomStart=zoomEnd=1 em [1,1.25]. Sem eventos/keyframes.
- Overrides são modelos parciais com campos explicitamente fornecidos rastreados;
  nullable apenas font, music.asset e ends opcionais. Zero/false não são descartados.
  Safe zones opostas somam <1; fades/trim respeitam duração; texto habilitado exige fonte.
- IDs de diretório: ASCII `[a-z0-9][a-z0-9_-]{0,63}`, sem reserved Windows device names,
  sem normalização silenciosa. Names/text não usam essa restrição; rejeitar vazio/NUL.

- [ ] Escrever `test_profile_rejects_campaign_text_and_source`, `test_override_rejects_unknown_field`, `test_contract_rejects_nonfinite_and_unsafe_paths`, `test_disabled_text_allows_no_font` e `test_legacy_still_validates`.
  Assertions: extras/NaN/Infinity/absolute/drive/traversal falham com localização;
  defaults desabilitados validam sem fontes; exemplo v1 ainda valida no modelo legado.
  Exemplo de assertion: `assert EditProfile.model_validate(profile_data()).defaults.headline.enabled is False`.
- [ ] Rodar `rtk uv run python -m pytest tests/test_editing_domain.py -q`; confirmar RED pelo contrato ausente.
- [ ] Implementar contratos e builders; separar estilo de headline de texto. Relações finais que dependem de source são validadas na Task 2.
- [ ] Rodar `rtk uv run python scripts/verify.py fast --pytest tests/test_editing_domain.py tests/test_models.py tests/test_schema.py`; exigir PASS.
- [ ] Commit `feat: add typed editing contracts alongside legacy v1` com somente arquivos desta tarefa.

### Task 2: Resolver puro, hash e proveniência

**Files:** Create `src/auraly_pipeline/editing/resolver.py`, `tests/test_editing_resolver.py`; ajustar contratos/helpers da Task 1 se necessário.

**Interfaces:** `profile_hash(profile: EditProfile) -> str`;
`resolve_manifest(profile: EditProfile, request: EditResolveRequest) -> EditManifestV2`;
`verify_manifest_hash(manifest: EditManifestV2) -> None`.
Recebem somente contratos, sem roots/serviços. `profile_hash` exclui createdAt.
Manifest sem datas; hash exclui manifestHash. Usar UTF-8, ensure_ascii=False,
sort_keys=True, separators=(',', ':'), allow_nan=False; serializar aliases/defaults.

- [ ] Escrever `test_precedence_and_field_provenance` com headlineSize profile=60/campaign=64/video=68/variant=72: resultado 72, origem outputVariant; campo color não substituído conserva profile.
  `test_false_zero_and_nullable_overrides`: enabled=false e volumeDb=0 preservados;
  music.asset=null permitido quando desabilitada; fontSizePx=null rejeitado com camada/campo.
- [ ] Escrever `test_roundtrip_keeps_omitted_fields_absent`, `test_replay_and_key_order_are_deterministic`, `test_headline_only_changes_manifest_not_inputs`, `test_wrong_profile_hash_fails` e `test_invalid_final_interval_reports_origin`.
  Assertions: round-trip não cria keys omitidas; dumps/hash idênticos no replay;
  headline B altera hash mas source/voice/copy refs permanecem; end>duration indica headline.endSec e sua camada.
  Mesmo valor explícito registra nova origem; end omitido resolve durationSec com origem source.
  Assertions principais: `assert result.headline.font_size_px == 72`;
  `assert result.provenance['headline.fontSizePx'] == 'outputVariant'`;
  `assert resolve_manifest(profile, request).manifest_hash == result.manifest_hash`.
- [ ] Rodar `rtk uv run python -m pytest tests/test_editing_resolver.py -q`; confirmar RED pela ausência do resolver.
- [ ] Implementar iteração apenas pelos campos conhecidos/fornecidos dos modelos parciais;
  validar identidade/hash do profile, aplicar camadas e revalidar relações completas.
  Serializar overrides com exclude_unset para conservar semântica. Validar hash em leituras posteriores;
  não confiar que frozen Pydantic torne dicionários profundamente imutáveis.
- [ ] Rodar `rtk uv run python scripts/verify.py fast --pytest tests/test_editing_domain.py tests/test_editing_resolver.py`; exigir PASS.
- [ ] Commit `feat: resolve editing overrides with deterministic provenance`.

### Task 3: Serviço local e publicação não destrutiva

**Files:** Create `src/auraly_pipeline/editing/service.py`, `tests/test_editing_service.py`; extend `tests/editing_helpers.py`.

**Interfaces:** `EditingService(*, project_root: Path, work_root: Path)`;
`create_profile(profile: EditProfile) -> Path`;
`get_profile(profile_id: str, version: int) -> EditProfile`;
`list_profiles() -> list[EditProfile]`;
`create_profile_version(profile_id: str, base_version: int, replacement: EditProfile) -> Path`;
`resolve(request: EditResolveRequest, *, persist: bool = True) -> EditManifestV2`;
`get_manifest(campaign_id: str, video_id: str, output_variant_id: str, manifest_hash: str) -> EditManifestV2`.
Nova versão exige replacement ID igual/baseVersion+1; nenhuma edição da anterior.

- [ ] Escrever `test_profile_version_and_conflict`: create/get/list ordenados; nova versão preserva bytes antigos; mesma identidade com conteúdo diferente falha.
  `test_resolve_replay_preserves_source`: replay retorna mesmo hash/bytes, MP4 SHA e mtime invariáveis, sem DB/Job/provider.
- [ ] Escrever `test_tampered_profile_and_manifest_fail`, `test_incomplete_publication_is_not_reused`, `test_wrong_source_duration_fails_before_publication`,
  `test_font_has_no_system_fallback`, `test_music_needs_explicit_acceptance` e testes paramétricos de absolute/traversal/symlink/junction/reserved IDs.
  Assertions: corrupção não sobrescrita; JSON truncado causa erro; duração divergir >.01s falha;
  fonte ausente/assinatura inválida/junction falha; música ativa sem musicAccepted falha.
  Cobrir staging existente e root/link inseguro; skip de criação de link somente quando SO negar permissão.
  Assertions principais: `assert service.resolve(request).manifest_hash == first.manifest_hash`;
  `assert source.read_bytes() == original_bytes`;
  `assert service.get_profile(profile.profile_id, 1) == profile`.
- [ ] Rodar `rtk uv run python -m pytest tests/test_editing_service.py -q`; confirmar RED.
- [ ] Implementar validação antes de I/O/mkdir, roots canônicos sem aceitar reparse points,
  checks dos ancestrais até a raiz, paths relativos project-root e hashes via file_digest.
  MP4: probe H.264/AAC, dimensões/duração válidas, tolerância .01s para descritor;
  não reexecutar download/full decode de HeyGen. Fonte TTF/OTF: validar header e tabela
  sfnt estrutural (offsets/tamanhos dentro do arquivo), não só extensão.
  Música: probe com áudio existente/duração positiva; trim <= duração e fade <= trecho.
  Assets referenciados são verificados mesmo desabilitados; nullable sem asset dispensa I/O.
- [ ] Implementar publicação com staging único em mesmo diretório, flush/fsync e link exclusivo,
  cleanup apenas de staging criado pela chamada. Destination existente exige contrato/hash íntegros;
  conflito/corrupção para com erro sanitizado. Revalidar diretório antes de publicar.
  Paths exatamente como spec, perfis carregados comparados ao request.profileRef.hash;
  listar não esconde corrupção. persist=false verifica/resuelve sem publicar.
  Profile.json guarda envelope `{profile, profileHash}`; get/list revalidam hash
  antes de retornar o contrato. Replay com conteúdo/hash igual conserva a data
  publicada original. Esse checksum detecta corrupção, não é assinatura contra
  adulteração deliberada de ambos os campos; o request ainda exige o hash exato.
- [ ] Rodar `rtk uv run python scripts/verify.py fast --pytest tests/test_editing_service.py tests/test_editing_resolver.py`; exigir PASS.
- [ ] Commit `feat: persist immutable editing profiles and manifests locally`.

### Task 4: CLI operacional, schemas e exemplos

**Files:** Create `src/auraly_pipeline/editing/{cli,schema}.py`, `tests/test_editing_{cli,schema}.py`;
modify `src/auraly_pipeline/cli.py` (registro somente), `scripts/verify.py`, `tests/test_verify_harness.py`;
create `schemas/edit-profile.schema.json`, `schemas/edit-resolve.schema.json`, `schemas/edit-manifest.v2.schema.json`,
`examples/edit-profile.json`, `examples/edit-resolve.json`, `examples/edit-manifest.v2.json`.

**Interfaces:** `register_editing_commands(app: typer.Typer) -> None`;
`export_editing_schemas(output_dir: Path) -> tuple[Path, Path, Path]`.
`python -m auraly_pipeline.editing.schema` exporta os três schemas acima por default.
CLI `edit profile-create --request`, `profile-get --profile-id --version`, `profile-list`,
`profile-new-version --profile-id --base-version --request`, `resolve --request [--dry-run]`,
`manifest-get --campaign-id --video-id --output-variant-id --manifest-hash`, `validate --manifest`.
Comandos de serviço aceitam `--project-root`/`--work-root`; export via módulo, sem opção redundante.
stdout JSON público; erro exit=1 somente campo/camada/mensagem sanitizados.

- [ ] Escrever testes CliRunner create/list/get/new-version/resolve/replay/dry-run;
  assertions exit=0, saída validável no contrato, dry-run sem publicação;
  erro exit=1 sem path absoluto ou valores brutos. `edit validate` aceita v2 íntegro e recusa v1 explicitamente;
  comando legado `validate` continua aceitando exemplo v1 sem mudanças.
  Assertions principais: `assert result.exit_code == 0`;
  `assert EditManifestV2.model_validate_json(result.stdout).manifest_hash == expected_hash`.
- [ ] Escrever schema tests check_schema/validar exemplos/rejeitar extras e spoken=true;
  regeneração idêntica, v1 schema byte-identical. No harness testar o novo step argv/generated_files,
  drift em cada um dos três arquivos e contagem full atualizada sem apagar steps existentes.
- [ ] Rodar `rtk uv run python -m pytest tests/test_editing_cli.py tests/test_editing_schema.py tests/test_verify_harness.py -q`; confirmar RED.
- [ ] Implementar CLI fina e schemas by_alias. Exemplos usam IDs/hashes sintéticos,
  headline/captions/music desabilitados para fluxo sem fontes instaladas; documentar
  que SHA do MP4 precisa ser substituído pelo arquivo real, não fingir que exemplo é executável sem mídia.
  Acrescentar um único step `editing schemas` com os três generated_files ao harness.
- [ ] Rodar `rtk uv run python -m auraly_pipeline.editing.schema` e
  `rtk uv run python scripts/verify.py fast --pytest tests/test_editing_cli.py tests/test_editing_schema.py tests/test_verify_harness.py tests/test_schema.py`; exigir PASS/sem drift.
- [ ] Commit `feat: expose editing CLI and versioned JSON schemas`.

### Task 5: Verificação completa, leitura do MP4 disponível e handoff

**Files:** Modify `README.md`, `docs/PROJECT-MEMORY.md`, `docs/GOAL-ROADMAP.md`,
`docs/PRD-MVP-MASS-VIDEO-AUTOMATION.md`; create `docs/superpowers/2026-10-01-d3a-verification.md`.
Corrigir comportamento somente com teste RED correspondente e commit estreito.

- [ ] Documentar CLI/caminhos, coexistência v1/v2 e ausência de render/UI; atualizar
  capability entregue apenas após verificação. D3B continua planejado.
- [ ] Rodar `rtk uv run python scripts/verify.py full`; exigir todos os steps PASS,
  registrar contagem real de testes/skips, sem usar números previstos como evidência.
- [ ] Fazer teste local em diretório temporário isolado com o MP4 de D2C existente:
  obter seu caminho dos metadados operacionais sem copiá-lo para Git;
  medir SHA/duração, criar profile com overlays/music desabilitados e request correspondente,
  executar resolve duas vezes e conferir mesmo hash e fonte inalterada.
  Não escrever no workspace do canário, banco, Voice Master ou HeyGen; não aprovar vídeo.
  Se fonte não estiver disponível, registrar limitação e pedir direção, sem substituí-la silenciosamente.
- [ ] Executar revisão independente do diff completo para contrato/compatibilidade,
  paths/publicação e requisitos da spec; corrigir findings importantes com TDD e rerodar gate.
- [ ] Revisar staged diff por secrets, mídia, paths privados e scope creep;
  `rtk git diff --check`; commit `docs: record D3A editing verification and usage`.
- [ ] Handoff com `IMPLEMENTED`/`LOCAL_VERIFIED` sustentados pela evidência real;
  nenhuma nova claim PROVIDER_VERIFIED. Merge/push somente quando solicitado.

## Execução e revisão do plano

Método Nativo previamente escolhido pelo usuário: implementar sequencialmente nesta sessão
com `superpowers:executing-plans`, TDD e commits por tarefa, depois um reviewer independente.
Criar/reutilizar worktree isolada pelo skill `using-git-worktrees` somente após aprovação
do plano; levar os dois documentos commitados, sem alterar main durante implementação.

Self-review: requisitos da spec mapeados às Tasks 1–5; cinco Review Focus têm testes
na tarefa proprietária; assinaturas e paths iguais em produtores/consumidores;
sem etapas de UI/render/batch ou SQL. Este plano aguarda aprovação antes de execução.
