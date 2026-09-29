# Goal D2B — HeyGen Generation, Polling & Download Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produzir um source MP4 validado por SceneVariant, reutilizando imagens e WAV já enviados em D2A, sem duplicar ações pagas em retomadas.

**Architecture:** Batch local, um job durável por variante, usando SQLite e JobService existentes. Estender o adapter MCP/OAuth e o fake de D2A; persistir checkpoints de geração e publicar arquivos sem overwrite. Nenhuma fila externa ou serviço permanente.

**Tech Stack:** Python 3.11+, Pydantic, SQLAlchemy/Alembic, SQLite, MCP/OAuth, httpx, Typer, FFmpeg/ffprobe e pytest existentes; stdlib para threads e publicação.

**Spec:** `docs/superpowers/specs/2026-09-29-goal-d2b-heygen-generation-download-design.md` (aprovado).

**Execução preservada:** Native/inline, conforme escolha anterior. Plano aguardando revisão do usuário; nenhuma chamada paga nesta implementação.

## Global Constraints

- Não editar `AGENTS.md` nem arquivos sincronizados em `sources/`; não adicionar dependências.
- D2B é local/fake; canário real com créditos só em D2C, mediante autorização específica.
- MCP oficial `https://mcp.heygen.com/mcp/v1/`; ferramentas `create_video_from_image` e `get_video`; nenhuma automação do website.
- Config fechado/versionado: image, provider_default, 9:16, 1080p (720p permitido), mp4, cover, medium; motion_prompt opcional.
- Concorrência default 2, intervalo 1..2; polling 10 → 20 → 40 → 60 segundos, timeout 1800 segundos.
- `max_paid_renders` inteiro positivo e `approved_by` obrigatório; `--yes` nunca ignora aprovação, budget ou integridade.
- Reserva de todos os novos jobs/renders em uma transação `BEGIN IMMEDIATE`; teto por campanha inclui falhas e ambiguidades.
- Marcar dispatch antes de create; persistir video ID antes de polling; callback é correlação, não idempotência.
- URLs assinadas/tokens somente em memória; erros e metadata sanitizados; conta, hashes e assets revalidados antes do dispatch.
- MP4 H.264/AAC, canvas vertical da resolução escolhida, rotação coerente; tolerância de duração `max(2s, 5% do WAV)` e full decode.
- Publicação sem overwrite e containment Windows/POSIX; retomada nunca cria outro vídeo para resolver ambiguidade.
- JSON estável em stdout; progresso em stderr. UI, editor e variantes editoriais ficam fora deste slice.

## Review Focus

1. Dois submits concorrentes com planos obsoletos: só uma reserva por identidade e nenhum estouro de budget (Task 2).
2. Tool remoto muda schema ou devolve sucesso malformado: bloquear antes de pagar, ou reconciliar se dispatch ocorreu (Tasks 3/5).
3. Runner reclama job de outra campanha/tipo, ou segundo runner excede o limite: seleção e limite atômicos, sem interferência (Task 5).
4. Signed URL expira/interrompe download: consultar o mesmo ID novamente, nunca repetir create ou vazar URL (Tasks 4/6).
5. Crash após publicar MP4, antes do commit/manifest: recuperar o mesmo arquivo; conflito/junction nunca sobrescreve source (Task 4).

## File Map

- `heygen/video_domain.py`: config, render/plan/status/provider-result contracts e identidade material.
- `heygen/db_models.py`, `heygen/video_repository.py`, migration `0008_heygen_renders.py`: registros, constraints e checkpoints.
- `jobs/service.py`, `jobs/repository.py`: extensão pública de submissão batch e claim filtrado/limitado; comportamento atual permanece default.
- `heygen/provider.py`, `heygen/fake_provider.py`: preflight de vídeo separado, create/get e fake determinístico.
- `heygen/video_media.py`: streaming, QC, publicação e recuperação local.
- `heygen/video_handler.py`: máquina de estados do job `heygen.video.generate`.
- `heygen/video_service.py`: plano, reserva, runner e reconciliação; reutilizável pela UI futura.
- `heygen/video_cli.py`, `cli.py`: cinco comandos, sem crescer o CLI raiz com a implementação.
- `schemas/heygen-video-config.schema.json`, `examples/heygen-video-config.json`: contrato e exemplo default.
- `tests/test_heygen_video_*.py`, `tests/heygen_support.py`: testes por responsabilidade, fixtures compartilhadas locais.
- Paths Python acima são relativos a `src/auraly_pipeline/`; migration em `campaigns/migrations/versions/`.

## Task 1: Contratos de vídeo e identidade material

**Files:** criar `src/auraly_pipeline/heygen/video_domain.py`, schema e exemplo; criar `tests/test_heygen_video_domain.py`.

**Interfaces:**

- `HeyGenVideoConfig`: `schema_version=1` e campos/valores de Global Constraints; extras proibidos. Motion prompt opcional limitado a 2000 caracteres.
- `VideoPlanItem`: campaign/scene/image/voice UUIDs, account ref, hashes, asset IDs, duração positiva, config, schema fingerprint e logical key.
- `VideoPlan`: items, new_count, reused_count, reserved_count, max_paid_renders, total_audio_seconds.
- `HeyGenRenderStatus`: enum planned/submitting/processing/download_pending/ready/failed/reconciliation_required. `HeyGenRender`: campos da seção 5 do spec; `render_id` UUID; status `HeyGenRenderStatus`; provenance de binding manual opcional.
- `VideoPreflight`: account ref, tool name, schema fingerprint; `ProviderVideo`: video ID, status queued/processing/completed/failed, download URL transitória opcional, callback/input facts opcionais. URL não entra em contratos persistidos.
- `VideoSource`: path relativo, sha256, size_bytes, `MediaProbe`; `VideoRunSummary`: renders e contagens ready/failed/blocked.
- `material_config_sha256(config: HeyGenVideoConfig) -> str`; `video_logical_key(item: VideoPlanItem) -> str`: JSON canônico/SHA-256, excluindo campos operacionais de polling/concurrency; incluir conta, campanha, variante, materiais e config de geração. Fingerprint remoto é evidência, não mudança material automática.

- [ ] Escrever `test_config_defaults_and_closed_schema`, `test_material_identity`: fixtures de item válido e config default; assertions:
  ```python
  assert config.concurrency == 2
  assert (config.poll_initial_seconds, config.poll_max_seconds, config.poll_timeout_seconds) == (10, 60, 1800)
  assert video_logical_key(item) == video_logical_key(item_with_other_poll_interval)
  assert video_logical_key(item) != video_logical_key(item_with_other_scene)
  ```
  Rejeitar extra, resolução 4k, concurrency 3, duração zero e identificador inseguro; alteração de motion_prompt muda identidade.
- [ ] Rodar `rtk proxy uv run pytest tests/test_heygen_video_domain.py -q`; esperado RED por contracts ausentes.
- [ ] Implementar os contratos/signed-URL boundary, hashes e exemplo default; usar `ContractModel`, validadores de identificadores/paths existentes. Schema gerado pelo Pydantic, sem novo framework de schemas.
- [ ] Repetir comando; esperado PASS, incluindo igualdade do schema checked-in com `model_json_schema()`.
- [ ] Revisar diff e commitar arquivos deste task: `feat: define HeyGen video contracts`.

## Task 2: Persistência e reserva batch atômica

**Files:** modificar `heygen/db_models.py`, `campaigns/migrations/env.py`, `jobs/service.py`, `jobs/repository.py`; criar migration `campaigns/migrations/versions/0008_heygen_renders.py`, `heygen/video_repository.py`, `tests/test_heygen_video_repository.py`; ampliar `tests/test_job_concurrency.py`.

**Interfaces:**

- `JobService.submit_linked_batch(requests: Sequence[JobSubmit], create_linked: Callable[[Session, JobRow], T], load_existing: Callable[[Job], T], *, before_commit: Callable[[Session], None]) -> list[LinkedJobSubmission[T]]`. Validar handlers/references/fingerprints; uma única transação. `before_commit` verifica o total de reservas dentro da mesma transação. Não usar internals de jobs em HeyGen.
- `JobRepository.create_linked_batch(requests: Sequence[JobSubmit], now: datetime, create_linked: Callable[[Session, JobRow], T], validate: Callable[[JobRow], None], before_commit: Callable[[Session], None]) -> list[_LinkedJobCreateResult[T]]`: reutilizar criação/reload já existente; conflito de fingerprint aborta batch inteiro antes de commit.
- `HeyGenVideoRepository(session_factory: sessionmaker[Session])`: `get(render_id: str) -> HeyGenRender`, `list_campaign(campaign_id: str) -> list[HeyGenRender]`, `find(logical_key: str) -> HeyGenRender | None`, `create_in_session(session: Session, job: JobRow, item: VideoPlanItem, *, max_paid_renders: int, approved_by: str) -> HeyGenRender`, `check_budget_in_session(session: Session, campaign_id: str, max_paid_renders: int) -> None`.
- Checkpoint methods: `mark_dispatch(render_id: str) -> HeyGenRender`, `record_video(render_id: str, video_id: str, *, manual_binding: bool = False) -> HeyGenRender`, `set_status(render_id: str, status: HeyGenRenderStatus, *, error_code: str | None = None, error_message: str | None = None) -> HeyGenRender`, `record_source(render_id: str, source: VideoSource) -> HeyGenRender`.

- [ ] Escrever testes `test_batch_budget_race`, `test_batch_rolls_back_on_conflict`, `test_remote_id_unique_per_account`, `test_migration_roundtrip`. Duas conexões/threads, três items e teto 3:
  ```python
  assert reserved_count == 3
  assert job_count == 3
  assert all(job.retry_safety == RetrySafety.RECONCILE_BEFORE_RETRY for job in jobs)
  assert replay_new_count == 0
  ```
  Teto 2 deve deixar zero novos renders/jobs; falha ou ambiguidade continua contada. Fingerprint conflitante não deixa batch parcial.
- [ ] Rodar `rtk proxy uv run pytest tests/test_heygen_video_repository.py tests/test_job_concurrency.py -q`; RED nos novos comportamentos.
- [ ] Implementar migration com FKs, identidade única e `(provider_account_ref, remote_video_id)` único; guardar config e aprovação segura. Usar API batch pública para reserva; reaprovação explícita pode elevar teto, nunca liberar reservas automaticamente. Checkpoints condicionais impedem trocar ID já vinculado.
- [ ] Repetir testes, mais `tests/test_job_service.py` e `tests/test_heygen_migrations.py`; PASS sem regressões D2A.
- [ ] Commit: `feat: reserve HeyGen video batches atomically`.

## Task 3: Adapter MCP de vídeo e fake

**Files:** modificar `heygen/provider.py`, `heygen/fake_provider.py`, `tests/heygen_support.py`; criar `tests/test_heygen_video_provider.py`.

**Interfaces:** estender `HeyGenMcpAdapter` e fake com `preflight_video(config: HeyGenVideoConfig) -> VideoPreflight`, `create_video(item: VideoPlanItem, *, callback_id: str) -> str`, `get_video(video_id: str) -> ProviderVideo`. Manter preflight de assets independente. Reutilizar exceptions de provider, diferenciando falha anterior ao dispatch de resposta ambígua.

- [ ] Escrever `test_video_schema_requires_imported_audio`, `test_video_payload_exact`, `test_malformed_create_is_ambiguous`, `test_asset_preflight_stays_independent`. Fake MCP schemas derivados do contrato documentado, não de uma conta real:
  ```python
  assert payload['image'] == {'type': 'asset_id', 'asset_id': item.image_asset_id}
  assert payload['audio_asset_id'] == item.audio_asset_id
  assert payload['aspect_ratio'] == '9:16'
  assert 'idempotency_key' not in payload
  assert 'engine' not in payload
  ```
  Remover áudio, alterar enum/required ou composição de schema ⇒ zero create. Sucesso sem ID e ID inconsistente ⇒ ambíguo/bloqueado. Schemas não compreendidos bloqueiam, nunca assumem compatibilidade.
- [ ] Rodar `rtk proxy uv run pytest tests/test_heygen_video_provider.py -q`; RED.
- [ ] Implementar discovery e validação do payload contra schema relevante, fingerprint canônico, extração estrita de status/ID. Engine exigido/selecionável bloqueia provider_default até decisão explícita; não inventar valor. Fake registra chamadas e simula queued→processing→completed, erros e MP4 sintético via transporte local de testes.
- [ ] Rodar testes novos mais `tests/test_heygen_provider.py tests/test_heygen_auth.py`; PASS sem autenticar provider.
- [ ] Commit: `feat: add HeyGen MCP video generation adapter`.

## Task 4: Download, QC e publicação recuperável

**Files:** criar `heygen/video_media.py`, `tests/test_heygen_video_media.py`; modificar `probe.py` somente para acrescentar timeout opcional compatível ao subprocess de probe.

**Interfaces:** `download_source(render: HeyGenRender, url: str, *, work_root: Path, client: httpx.Client) -> VideoSource`; `recover_source(render: HeyGenRender, *, work_root: Path) -> VideoSource | None`. Probe preserva callers: `probe_media(path: Path, ffprobe_bin: str = 'ffprobe', *, timeout_seconds: float | None = None) -> MediaProbe`.

- [ ] Escrever `test_qc_rejects_invalid_video`, `test_https_stream_limits`, `test_publication_crash_recovery`, `test_final_conflict_and_path_escape`. Fixtures geram MP4 720×1280 H.264/AAC com FFmpeg, HTTPX MockTransport e staging temporário:
  ```python
  assert recovered.sha256 == published.sha256
  assert download_calls_after_recovery == 0
  assert final_bytes_after_conflict == original_final_bytes
  assert source.probe.video.width == 720
  assert source.probe.video.height == 1280
  ```
  Testar sem áudio, codec/container errado, truncado, duração fora da tolerância, redirect HTTP, tamanho excessivo, timeout, junction/symlink e manifest adulterado. Crash sem manifest só recupera após QC e correspondência de fatos já persistidos quando existentes; arquivo divergente bloqueia.
- [ ] Rodar `rtk proxy uv run pytest tests/test_heygen_video_media.py -q`; RED.
- [ ] Implementar streaming HTTPS limitado a 512 MiB, timeout connect 10s/read 60s; probe 30s/full decode 120s. Root/path validado antes de escrita; `.part` pertence apenas ao render. Publicar com `os.link` sem overwrite, remover só staging próprio; sem fallback com rename sobrescrevente. Reutilizar padrões de containment/publicação existentes sem importar automação Flow. Manifest seguro `source.json` identifica render/material/hash/probe, sem URL; ready só após manifest. Final existente deve passar QC e identidade, jamais ser substituído.
- [ ] Repetir testes e `tests/test_probe.py`; PASS. FFmpeg ausente deve gerar erro operacional claro, não ready.
- [ ] Commit: `feat: download and validate HeyGen source videos`.

## Task 5: Handler, polling e claims limitados

**Files:** criar `heygen/video_handler.py`, `tests/test_heygen_video_handler.py`; modificar `jobs/service.py`, `jobs/repository.py`; ampliar `tests/test_job_concurrency.py`.

**Interfaces:** acrescentar a `claim_next_job`, `worker_once` e `JobRepository.claim_next` os kwargs compatíveis `campaign_id: str | None = None`, `job_type: str | None = None`, `max_running: int | None = None`. Contar running no mesmo escopo e aplicar limite junto ao claim em transação curta `BEGIN IMMEDIATE`; defaults preservam callers atuais. `HeyGenVideoHandler.execute(context: JobExecutionContext) -> JobExecutionResult`; constructor recebe repository, adapter/fake, work_root e `sleep: Callable[[float], None]`/`monotonic: Callable[[], float]` injetáveis para testes.

- [ ] Escrever `test_crash_after_dispatch_never_recreates`, `test_resume_known_id`, `test_poll_backoff_and_deadline`, `test_scoped_claim_two_runners`. Fixtures crasham cada fronteira e usam relógio fake:
  ```python
  assert create_calls_after_restart == 1
  assert sleeps[:4] == [10, 20, 40, 60]
  assert blocked_render.remote_video_id == original_video_id
  assert peak_running <= 2
  assert unrelated_job.status == JobStatus.QUEUED
  ```
  Testar falha pré-dispatch comprovada, resposta sem ID, rate limit, status desconhecido, deadline e lease perdida. Nunca repetir create após marcador sem ID. Mismatch de conta/material bloqueia antes de create.
- [ ] Rodar `rtk proxy uv run pytest tests/test_heygen_video_handler.py tests/test_job_concurrency.py -q`; RED.
- [ ] Implementar validação→dispatch marker→create→ID persistido→poll→download→ready. Timeout/ambiguidade retorna BLOCKED com ID preservado; falha remota terminal isolada. Backoff inclui falhas de leitura e não ultrapassa deadline. Usar heartbeat/leases existentes; stale reconcile-before-retry continua bloqueado. Registrar handler no JobService default, respeitando registry injetado. Claim público filtra antes de selecionar e aplica limite entre processos; não criar lock/fila externa.
- [ ] Repetir testes e `tests/test_job_service.py tests/test_heygen_handler.py`; PASS.
- [ ] Commit: `feat: run resumable HeyGen video jobs`.

## Task 6: Plano de campanha, runner e reconciliação

**Files:** criar `heygen/video_service.py`, `tests/test_heygen_video_service.py`.

**Interfaces:** `HeyGenVideoService.for_database(database_path: Path, *, work_root: Path | None = None, provider: HeyGenMcpAdapter | FakeHeyGenProvider | None = None) -> HeyGenVideoService`; `plan_videos(campaign_id: str, config: HeyGenVideoConfig, *, max_paid_renders: int) -> VideoPlan`; `submit_videos(campaign_id: str, config: HeyGenVideoConfig, *, max_paid_renders: int, approved_by: str) -> list[HeyGenRender]`; `run_videos(campaign_id: str) -> VideoRunSummary`; `list_videos(campaign_id: str) -> list[HeyGenRender]`; `reconcile_video(render_id: str, *, video_id: str | None = None, confirm_manual_binding: bool = False) -> HeyGenRender`.

- [ ] Escrever `test_three_variants_share_voice`, `test_expired_url_resumes_same_id`, `test_manual_binding_validation`, `test_changed_account_or_inputs_block`:
  ```python
  assert summary.ready_count == 3
  assert len(set(audio_asset_ids)) == 1
  assert len(set(remote_video_ids)) == 3
  assert replay_new_count == 0
  assert create_count_after_download_retry == 3
  ```
  Mesma imagem em duas variantes compartilha asset, não vídeo. Rejeitar ID duplicado, identidade remota contraditória e binding sem confirmação; sem facts suficientes exige provenance manual. Ausência de resultado em listagem nunca autoriza create.
- [ ] Rodar `rtk proxy uv run pytest tests/test_heygen_video_service.py -q`; RED.
- [ ] Implementar leitura de aprovações/paths/hashes com helpers D2A, plano sem writes e nova validação no submit. Usar Task 2 para jobs/reservas. Runner stdlib até duas threads, cada uma com sessão própria, scoped claims e max_running; configs operacionais distintas no mesmo batch usam o menor limite ativo. Reconcile conhecido consulta mesmo ID; sem ID só aceita correlação inequívoca realmente exposta pelo provider ou binding confirmado. Usar `JobService.resume_reconciled_job`, sem acesso ao repo privado. Refresh de URL faz get, não create. Falha isolada não cancela outros itens.
- [ ] Repetir testes e todos `tests/test_heygen_*.py`; PASS com zero rede real/créditos.
- [ ] Commit: `feat: orchestrate HeyGen campaign video batches`.

## Task 7: CLI e evidência de entrega

**Files:** criar `heygen/video_cli.py`, `tests/test_heygen_video_cli.py`; modificar `cli.py`, `README.md`, `docs/PROJECT-MEMORY.md`, `docs/GOAL-ROADMAP.md`, `docs/PRD-MVP-MASS-VIDEO-AUTOMATION.md`.

**Interfaces:** `register_video_commands(app: typer.Typer) -> None` registra no grupo heygen existente: `plan-videos`, `generate-videos`, `run-videos`, `videos`, `reconcile-video`. Argumentos exatamente como seção 9 do spec; database/work-root seguem opções existentes. Generate reserva, run executa. JSON usa contratos Task 1, sem signed URLs.

- [ ] Escrever `test_video_commands_json`, `test_submit_requires_budget_and_approver`, `test_cli_three_video_flow`, `test_outputs_contain_no_secrets` usando CliRunner/serviço fake e fixtures Task 6:
  ```python
  assert json.loads(result.stdout)['ready_count'] == 3
  assert 'https://signed.example/' not in result.stdout + result.stderr
  assert create_calls_when_budget_missing == 0
  ```
  Testar `--yes`, erro por config inválido, reconcile flags e stdout parseável mesmo com progresso.
- [ ] Rodar `rtk proxy uv run pytest tests/test_heygen_video_cli.py -q`; RED.
- [ ] Implementar comandos delegando ao serviço, aprovação explícita e cancelamento sem submit. Atualizar docs distinguindo shipped D2A/D2B local de roadmap D2C/UI/editor; preservar histórico técnico. Documentar comandos, configuração, retomada e erros acionáveis sem afirmar interoperabilidade real.
- [ ] Rodar testes CLI e `rtk proxy uv run python scripts/verify.py full`; registrar resultado exato. Inspecionar diff, secrets, media e escopo antes de commit. Se gate falhar, diagnosticar causa antes de alterar código.
- [ ] Solicitar revisão independente whole-branch conforme skill de review, corrigir achados e repetir somente verificações afetadas (full se alteração transversal). D2B só recebe LOCAL_VERIFIED após evidência; PROVIDER_VERIFIED continua pendente D2C.
- [ ] Commit: `feat: expose HeyGen video campaign commands`; handoff com commits, testes e próximos passos. Merge/push só com autorização aplicável; este plano não autoriza canário pago.

## Self-review e execução

Cobertura: seções 1–4 do spec → Tasks 1/3/6; persistência/budget → Task 2; dispatch/polling/runner → Tasks 3/5/6; reconciliação → Task 6; QC/publicação → Task 4; CLI/evidência → Task 7. Cinco Review Focus possuem testes nomeados nos tasks indicados. Novos contratos são definidos antes dos consumidores; nenhum componente depende de UI ou batch remoto.

Preparar isolamento com `superpowers:using-git-worktrees` após aprovação do plano. Implementar Native com `superpowers:executing-plans`, TDD e commits por task; manter design e plano juntos. Registrar adaptações pequenas baseadas no código real, sem expandir escopo nem realizar ação paga.
