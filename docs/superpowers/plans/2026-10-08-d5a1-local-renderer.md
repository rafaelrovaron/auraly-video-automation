# D5A.1 Local Renderer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (Native, recommended here) or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Renderizar um plano salvo em masters locais, preservando seus inputs e reutilizando outputs íntegros.

**Architecture:** Serviço sequencial consome EditBatchPlan imutável. FFmpeg/ASS executa composição, fontes locais e cues; recibos identificados por outputHash/runtime permitem reuso sem Jobs, API ou UI novos.

**Tech Stack:** Python 3.11, Pydantic, Typer, FFmpeg/ffprobe/libass, pysubs2 e Pillow já instalados.

**Spec:** `docs/superpowers/specs/2026-10-08-d5a1-local-renderer-design.md` — aprovada em 2026-10-08; base de código `42beda7`.

## Global Constraints

- Master: somente 1080 × 1920, 30 FPS, MP4 H.264/AAC, yuv420p, faststart.
- Pesos 400/700; highlightEnabled=true em captions habilitadas é rejeitado. Não validar propriedades de overlays desabilitados como se estivessem ativos.
- Captions habilitadas exigem timing fornecido, source_mp4 e cues resolvidos; não gerar timing.
- Voz sincronizada vem do source MP4, nunca reinserir/trimar o WAV. Música usa volumeDb + duckUnderVoiceDb, voz ganho 1, amix normalize=0 e limiter sem auto-gain.
- Não alterar manifest/plan hashes, approvals, mídia original, AGENTS.md ou sources/.
- Sem providers, dependências novas, migrations, Jobs, endpoints, UI, proxy, alinhamento ou aprovação/delivery final.
- Validar roots/paths/hash nas duas plataformas; staging próprio, sem shell, protocolos locais, timeout finito, publicação sem overwrite.
- A conclusão desta entrega não significa D5A inteiro entregue ou PROVIDER_VERIFIED.

## Review Focus

1. Mesmo output em plano diferente: reutilizar master e manter planHash produtor no recibo (Task 4).
2. Asset muda entre preflight e publicação: falhar sem publicar sucesso nem alterar irmãos (Task 4).
3. Cues com pontuação, braces/backslashes e fonte sem glyph: texto literal; impedir fallback silencioso (Task 2).
4. Música não loopada termina antes do vídeo: manter voz e duração, completando apenas música com silêncio (Task 3).
5. Frame rate/source duração e precisão ASS/AAC diferem: manter timing e tolerância explícita, sem truncar voz (Tasks 2/3).

## Arquivos e fronteiras

Criar `src/auraly_pipeline/editing/render_domain.py` (contratos/identidade),
`render_runtime.py` (capacidades/subprocessos), `render_text.py` (ASS/fit),
`render_media.py` (framing/mix/encode/probe) e `render_service.py` (lote/publicação/reuso).
Modificar apenas helpers públicos necessários em `editing/service.py`, registro
CLI em `editing/cli.py`, export em `editing/schema.py` e gate/schema tracking em
`scripts/verify.py`. Não ampliar modelos de edição já publicados.

Testes novos: `tests/test_render_domain.py`, `test_render_text.py`,
`test_render_media.py`, `test_render_service.py`, `test_render_cli.py` e
`tests/render_helpers.py`. Reutilizar `editing_helpers.py` e `editing_batch_helpers.py`.
Adicionar somente os novos testes ao job Windows de `.github/workflows/verify.yml`;
Linux já executa pytest completo. Docs atuais só recebem capacidade entregue ao final.

## Task 1 — Contratos e runtime local

**Files:** Create `editing/render_domain.py`, `editing/render_runtime.py`, `tests/test_render_domain.py`, `tests/render_helpers.py`; modify `editing/schema.py`, `scripts/verify.py`, `tests/test_verify_harness.py`; generate `schemas/render-receipt.schema.json`, `schemas/render-batch-result.schema.json`. Paths Python relativos a `src/auraly_pipeline/`.

**Interfaces:**
- `RenderRuntime`: modelo com `fingerprint: str`, `ffmpeg_version: str`, `libass_version: str`, `encoding: dict[str, str]`; fingerprint inclui versões/build e configuração de encoding.
- `RenderReceipt`: EditingModel, schemaVersion 1.0, campaign/video/variant IDs, planHash produtor, manifestHash, outputHash, renderKey, rendererVersion 1.0, runtime, inputs map de SHAs, mixPolicy, path relativo, sizeBytes, sha256, probe: MediaProbe, fullDecodePassed=true.
- `RenderOutputResult`: key, outputVariantId, status rendered/reused/failed/planned, renderKey, fitMeasured: bool, path relativo opcional, erro de campo/mensagem sanitizada opcional. `RenderBatchResult`: planHash, dryRun, outputs e propriedade hasFailures. fitMeasured=false em dry-run planejado; reuso traz validação registrada do master existente.
- `render_key(output_hash: str, runtime: RenderRuntime) -> str`: content_hash de outputHash + rendererVersion + runtime.fingerprint.
- `validate_supported(manifest: EditManifestV2, caption: CaptionInput) -> None`: limites fixos e captions; levanta EditingError com campo conhecido.
- `detect_runtime() -> RenderRuntime` e `run_ffmpeg(args: list[str], *, cwd: Path | None = None, timeout_sec: float = 600) -> bytes`: args não incluem executável; runner adiciona no-stdin, restringe protocolos por input e recolhe subprocesso em timeout. Erros públicos nunca incluem stderr bruto.
- Helper `make_render_plan(project_root: Path) -> EditBatchPlan`: source sintético H.264/AAC de 2 s, três headlines habilitadas com fonte local verificada, metadados sintéticos aprovados, hashes/duração reais, sem rede. `publish_test_plan(plan, work_root) -> None` grava snapshot no layout existente apenas no tmp_path.

- [ ] **1. RED:** criar `test_render_key_changes_with_output_or_runtime`, `test_supported_limits`, `test_receipt_relative_paths` e `test_runtime_capability_failure_is_sanitized`. Assert hash estável/64 chars; runtime/timing outputHash distintos geram keys distintas; master/peso/highlight inválidos falham no campo correto; path absoluto/traversal/reparse rejeitado; erro não expõe stderr.
- [ ] **2. Confirmar RED:** `rtk uv run pytest tests/test_render_domain.py -q`; falha por interfaces ausentes, não por ferramenta/fixture quebrada.
- [ ] **3. Implementar:** contratos, fingerprint, runner e limites. Encoder fixo libx264, CRF 18, preset medium; áudio mixado AAC 192k/48kHz estéreo; sem música stream copy. Esses defaults entram no fingerprint. Runtime verifica libass/encoders/filtros e falha se indisponíveis; não usar skips para esconder isso em CI. Source sintético reutiliza geração existente; localizar fonte instalada válida e registrar seleção, sem download em teste. Exportar schemas e incluir ambos no controle de drift do harness.
- [ ] **4. GREEN:** `rtk uv run python scripts/verify.py fast --pytest tests/test_render_domain.py tests/test_editing_schema.py tests/test_verify_harness.py`; todos passam. Conferir schemas regenerados e diff.
- [ ] **5. Commit:** arquivos desta task somente; mensagem `feat: add local render contracts and runtime preflight`.

## Task 2 — Texto literal, ASS e fit real

**Files:** Create `editing/render_text.py`, `tests/test_render_text.py`; extend `tests/render_helpers.py`.

**Interfaces:**
- Consome manifest, CaptionInput e RenderRuntime da Task 1.
- `write_ass(manifest: EditManifestV2, caption: CaptionInput, *, font_paths: dict[str, Path], staging: Path, runtime: RenderRuntime) -> Path`: chaves headline/captions; retorna ASS sob staging, escreve apenas nessa área.
- `escape_ass_text(text: str) -> str`: preserva conteúdo literal/linebreak, não interpreta tags.
- `measure_ass(script: str, *, staging: Path, runtime: RenderRuntime) -> tuple[int, int, int, int]`: rasterização local libass e bbox não recortado com Pillow; canvas de medição deve incluir margens para detectar overflow e efeitos fora do canvas final. Memória/cache só nesta chamada de lote, sem cache em disco global.

- [ ] **1. RED:** `test_literal_ass_text` assert texto `"{\\pos(1,2)}\\N"` não move/suprime texto; `test_fit_and_safe_zones` cobre wrap sem shrink, error sem autoquebra, shrink passos 1 px até mínimo 1, maxLines, stroke/sombra/padding e posições fora das safe zones; `test_caption_intervals_use_saved_cues` assert texto exato/cues source_mp4 sem headline, ativação/desativação dentro de um frame; `test_font_selection_has_no_silent_fallback` usa glyph não disponível e face diferente como casos rejeitados.
- [ ] **2. Confirmar RED:** `rtk uv run pytest tests/test_render_text.py -q`; falhas esperadas antes do módulo existir.
- [ ] **3. Implementar:** pysubs2 para eventos/estilos; libass para rasterização e font selection verificável, sem métricas CSS nem fallback. Eventos separados por linha dão lineHeight; retângulo ASS em layer de fundo dá padding; posicionar bloco centralizado em x, anchor vertical em y. Cores RGBA convertidas para alpha ASS invertido. Medir bloco completo com efeitos, preservar quebras explícitas, reutilizar medidas de textos iguais. Headline/captions são blocos estáticos, sem karaoke. Se libass não puder selecionar fonte ou representar propriedade aprovada, parar e reportar restrição antes de mudar tecnologia.
- [ ] **4. GREEN:** `rtk uv run python scripts/verify.py fast --pytest tests/test_render_domain.py tests/test_render_text.py`; assert imagens reais de libass, não só strings ASS. Guardar PNGs só em tmp/ignored e inspecionar com view_image.
- [ ] **5. Commit:** arquivos desta task; `feat: render bounded headline and caption ASS overlays`.

## Task 3 — Framing, mix e integridade do master

**Files:** Create `editing/render_media.py`, `tests/test_render_media.py`; extend `tests/render_helpers.py`.

**Interfaces:**
- Consome Task 1 runtime/runner e ASS da Task 2.
- `video_filter(manifest: EditManifestV2, source: MediaProbe) -> str`.
- `audio_filter(manifest: EditManifestV2, *, music_duration_sec: float) -> str | None`: None se música desabilitada.
- `encode_master(manifest: EditManifestV2, *, source_path: Path, music_path: Path | None, ass_path: Path | None, output_path: Path, runtime: RenderRuntime) -> MediaProbe`: path staging exclusivo; resultado só depois de probe/full decode/validação.

- [ ] **1. RED:** `test_cover_contain_and_zoom_geometry` usa source com quadrantes coloridos e verifica crop/padding/posição/scale e zoomStart/zoomEnd sem deformar overlays; `test_music_shorter_than_voice_keeps_duration` assert vídeo+voz completos, silêncio só na música; `test_mix_preserves_voice_gain` usa voz 440 Hz e música 880 Hz, compara projeção da voz ao source decodificado com tolerância 0.5 dB fora de limiter/fades; assert ganho música volumeDb+duckUnderVoiceDb com tolerância 1 dB; `test_limiter_no_auto_gain` sinal baixo não amplificado e mix com picos não ultrapassa limite de amostras antes de AAC; `test_no_music_copies_aac` hashes de packets AAC iguais.
- [ ] **2. Confirmar RED:** `rtk uv run pytest tests/test_render_media.py -q`.
- [ ] **3. Implementar:** cover/contain preservam razão; scale × zoom linear sobre duração real, preto fora do vídeo, x/y no excesso/espaço livre. Normalizar CFR/PTS; compor ASS depois do framing. Trim antes de loop de segmento, pad silêncio sem loop, fades na trilha efetiva, mix voz ganho 1/amix normalize=0/limiter auto-gain off com compensação de latência. Manter áudio AAC sem música. FFmpeg publica apenas staging com faststart; usar probe_media e probe pontual para pix_fmt/streams, mais decode `-xerror` e validação moov antes de mdat. FPS 30 e duração tolerância 1/30 + 1024/sampleRate s, sem cortar fala para esconder mismatch.
- [ ] **4. GREEN:** `rtk uv run python scripts/verify.py fast --pytest tests/test_render_domain.py tests/test_render_text.py tests/test_render_media.py`; incluir teste de full decode, output inválido/sem áudio/rotação source não zero e tempo ASS com bordas em centésimos. Inspecionar frames extraídos.
- [ ] **5. Commit:** arquivos desta task; `feat: encode voice-preserving vertical masters locally`.

## Task 4 — Serviço sequencial, recibos e reuso

**Files:** Create `editing/render_service.py`, `tests/test_render_service.py`; modify `editing/service.py` somente para wrappers públicos dos helpers existentes de asset/font/music/path/publicação JSON.

**Interfaces:**
- Consome EditBatchService.get_plan, Task 1 identidade/contratos, Task 2 write_ass e Task 3 encode_master.
- `RenderService(*, project_root: Path, work_root: Path)`.
- `RenderService.render(campaign_id: str, video_id: str, plan_hash: str, *, dry_run: bool = False) -> RenderBatchResult`.
- Helpers públicos: `EditingService.validate_asset(asset: AssetRef, field: str) -> Path`, `validate_font(asset: AssetRef, field: str) -> Path`, `audio_duration(asset: AssetRef) -> float`; `publish_editing_json(root: Path, path: Path, payload: dict[str, Any]) -> None`. Wrappers reaproveitam implementação existente sem mudar comportamento de seus callers.

- [ ] **1. RED:** `test_three_headlines_publish_distinct_masters` assert três SHAs distintos/recibos decode=true e todos inputs com hashes inalterados; `test_replay_reuses_without_encode` spy encode_master chamado só na primeira execução; `test_other_plan_reuses_producer_receipt` plano muda label/irmão, outputHash igual mantém master/planHash produtor; `test_corrupt_or_orphan_output_never_overwrites` assert bytes intactos/failed; `test_changed_input_prevents_publication` altera asset após encode, assert sem recibo sucesso; `test_variant_failure_preserves_siblings` captions sem timing e outra desabilitada rendem failed/rendered.
- [ ] **2. Confirmar RED:** `rtk uv run pytest tests/test_render_service.py -q`.
- [ ] **3. Implementar:** plano salvo verificado, preflight global source/runtime e preflight por variante. Path final conforme spec/renderKey/filename original; staging via tempfile sob root seguro. Identidade exige outputHash/runtime, não planHash consumidor. Reuso verifica recibo/schema/path/tamanho/hash/probe e decode flag; sem encode/republicação. Revalidar hashes antes de publicação. Hardlink/no-overwrite do master e recibo por último; conflito nunca adota órfão. Remover somente staging próprio em finally, recolher subprocessos. Resultado ordered igual ao plano; um failed não interrompe irmãos.
- [ ] **4. GREEN:** `rtk uv run python scripts/verify.py fast --pytest tests/test_render_service.py tests/test_editing_service.py tests/test_editing_batch_service.py`; incluir timeout/failure sem subprocesso órfão, path POSIX/Windows/traversal/symlink/junction e conflito na publicação. Helpers públicos não mudam regressões existentes.
- [ ] **5. Commit:** arquivos desta task; `feat: publish immutable render receipts and reuse intact masters`.

## Task 5 — CLI e cobertura Windows

**Files:** Modify `editing/cli.py`, `.github/workflows/verify.yml`; create `tests/test_render_cli.py`; extend `tests/test_verify_harness.py` se necessária checagem da lista CI.

**Interfaces:** Consome RenderService.render; comando `auraly edit render --campaign-id --video-id --plan-hash`, roots existentes, flag `--dry-run`.

- [ ] **1. RED:** `test_cli_render_returns_json_and_exit_code` assert rendered/reused em stdout JSON e exit 0, failed por variante exit 1 preservando JSON; `test_dry_run_does_not_write` compara árvore/hashes antes/depois, statuses planned/reused/failed e sem staging; `test_cli_global_failure_is_sanitized` exit 1 com campo conhecido, sem stack/stderr/private paths. `test_windows_ci_includes_render_tests` assert todos os cinco novos arquivos presentes no gate Windows.
- [ ] **2. Confirmar RED:** `rtk uv run pytest tests/test_render_cli.py -q`.
- [ ] **3. Implementar:** registrar comando no Typer existente e ajustar help “no render” para capacidade atual. Usar `_errors` e nomes conhecidos dos modelos novos na sanitização de ValidationError. dry-run verifica suporte/assets/reuso sem rasterização/encode/mkdir, explicita fit não medido no resumo. Adicionar novos arquivos ao focused Windows sem relaxar checks/timeouts ou instalar bibliotecas.
- [ ] **4. GREEN:** `rtk uv run python scripts/verify.py fast --pytest tests/test_render_cli.py tests/test_editing_cli.py tests/test_editing_batch_cli.py tests/test_verify_harness.py`; confirmar `edit render --help` e ausência de acesso a provider/DB write. CLI real sobre fixture temporária gera lote e replay.
- [ ] **5. Commit:** arquivos desta task; `feat: expose local batch render CLI and cross-platform checks`.

## Task 6 — Gate final, revisão e documentação de entrega

**Files:** Modify `README.md`, `docs/PROJECT-MEMORY.md`, `docs/GOAL-ROADMAP.md`, `docs/PRD-MVP-MASS-VIDEO-AUTOMATION.md`; create `docs/superpowers/2026-10-08-d5a1-verification.md`.

- [ ] **1. Gate:** `rtk uv run python scripts/verify.py full` — todas as etapas aplicáveis passam; registrar contagens/tempo/commit/skips. Schema export/drift deve incluir os contratos de render. Não usar fixtures privadas nem paid calls.
- [ ] **2. Inspeção:** gerar source sintético de 2 s + três variants; probe/decode e extrair frames em início/meio/fim e bordas de cues; abrir PNGs via view_image, conferir tipografia/fit/crop. Guardar mídia só em ignored/tmp. Reexecutar, provar reuso/inputs iguais e variante inválida isolada. Registrar comandos/resultados sem paths privados.
- [ ] **3. Revisão independente:** uma revisão fresca da branch completa contra spec/plano, foco em Review Focus, integridade/preservação da voz e compatibilidade Windows/Linux. Corrigir Critical/Important com RED→GREEN e repetir gate afetado/full após mudança de produção. Não spawnar implementadores para cada task no método Native.
- [ ] **4. Documentar:** D5A.1 IMPLEMENTED/LOCAL_VERIFIED somente com evidência correspondente; D5A.2 Jobs/UI e D5B ainda PLANNED. Descrever CLI, formato master, timing exigido, pesos/highlight, ducking estático, reuso e recuperação manual de órfãos. Preservar histórico shipped/roadmap e aprovação humana final.
- [ ] **5. Commit:** docs somente; `docs: record D5A.1 local render capability and verification` depois de `rtk git diff --check` e revisão de arquivos/secrets/mídia.

## Handoff e integração

Plano aguarda aprovação; nenhuma task implementada. Recomenda-se manter execução
**Native**: tarefas dependem dos mesmos contratos/fixtures, sem benefício de
implementadores paralelos. Ler spec/plano, usar executing-plans, criar/reusar
worktree isolado via using-git-worktrees e fazer commits pequenos por task.

Não fazer merge/push como consequência automática da aprovação deste plano.
Ao concluir, apresentar resultados e pedir direção de integração. Depois de
publicação autorizada, verificar Actions Linux/Windows; falhas são investigadas,
não ignoradas nem chamadas de verdes por inferência. CI só confirma a versão
que realmente executou. Só depois começar design de D5A.2.
