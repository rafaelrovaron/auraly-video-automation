# External Voice Master Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for native execution, or superpowers:subagent-driven-development if explicitly selected. Execute task-by-task with TDD and small commits.

**Goal:** Importar MP3/WAV externo como Voice Master revisável, sem regenerar voz nem consumir créditos.

**Architecture:** Reutilizar Jobs, aprovação Voice Master, processamento FFmpeg e transcritor existentes. Um módulo de importação separado controla intake e processamento; uma migração aditiva registra a origem importada sem mudar a geração ElevenLabs. O HeyGen continua consumindo somente o WAV aprovado.

**Tech Stack:** Python 3.11, Pydantic, SQLAlchemy/Alembic, SQLite, Typer, FFmpeg/ffprobe e faster-whisper existentes; nenhuma dependência nova.

**Spec:** `docs/superpowers/specs/2026-09-30-external-voice-import-design.md`, aprovado pelo usuário em 2026-09-30.

## Global Constraints

- Aceitar inicialmente MP3 e WAV, com limite de 100 MiB.
- WAV mono 48 kHz, normalização e trim apenas nas bordas, preservando pausas internas.
- A origem deve estar dentro do project root confiável configurado.
- Não registrar caminhos absolutos privados em contratos ou logs.
- A importação termina em `review_required`, nunca em `approved`.
- Não usar o próprio texto esperado como transcrição reconhecida.
- Não contornar Application Control nem substituir transcrição por texto manual.
- Não enfraquecer o gate MP3 da geração ElevenLabs.
- Falhas ficam explícitas, com artefatos de diagnóstico preservados; nenhuma aprovação parcial.
- Não modificar AGENTS.md; não adicionar UI, batch de áudio ou serviços de transcrição.
- No primeiro canário, uma campanha com uma SceneVariant e no máximo uma geração paga.
- Shell via RTK; edições via apply_patch. Nenhum arquivo real de mídia entra no Git.

## Review Focus

1. MP3 CBR válido sem cabeçalho Xing: decodificar integralmente, sem exigir metadados próprios dos exports ElevenLabs (Task 2).
2. Origem alterada entre planejamento e cópia: recusar bytes divergentes, sem publicar Voice Master aprovável (Task 2).
3. SQLite rebuild com triggers referenciados por outras tabelas: preservar registros, vínculos e invariantes (Task 1).
4. Whisper bloqueado pelo Windows: falha sanitizada, arquivos preservados e zero chamadas pagas (Task 2).
5. Dois imports simultâneos na campanha: no máximo um Voice Master ativo; repetição não duplica Jobs (Task 2).

## Arquivos e interfaces

- `voices/domain.py`: origem `imported`; novo contrato `VoiceImportRequest`.
- `voices/db_models.py` e migração `0009_external_voice_import.py`: constraints e triggers compatíveis.
- `voices/import_audio.py`: `VoiceImportService` e `VoiceImportHandler`, intake e Job local.
- `voices/audio.py`: helper público mínimo para decodificar entrada externa para WAV intermediário.
- `voices/service.py`: reutilização da aprovação existente, sem ampliar o arquivo com intake.
- `jobs/service.py`: registrar handler `voice.import` na fábrica padrão de workers.
- `cli.py`: comando `voice import`; manter os comandos atuais.
- `voices/schema.py`, schemas e harness: exportar contratos de importação/Voice Master e detectar drift.
- Tests focados, README e PROJECT-MEMORY: evidência e uso da capacidade realmente entregue.

---

### Task 1: Contrato e migração compatível

**Files:** Modify `src/auraly_pipeline/voices/domain.py`, `voices/db_models.py`; create `src/auraly_pipeline/campaigns/migrations/versions/0009_external_voice_import.py`, `src/auraly_pipeline/voices/schema.py`, `schemas/voice-import.schema.json`, `schemas/voice-master.schema.json`; modify `scripts/verify.py`; test `tests/test_voice_domain.py`, `tests/test_voice_migrations.py`, `tests/test_voice_migrations_direct_insert.py`, `tests/test_verify_harness.py`.

**Interfaces:**
- Consumes: `VoiceMaster`, `VoiceMasterRow` e triggers da migração 0003; head atual 0008.
- Produces: `VoiceImportRequest(schema_version: Literal[1] = 1, campaign_id: str, copy_master_version: int | None = None)`, herdando `ContractModel` e validações de identificação já existentes.
- Produces: `VoiceMaster.provider: Literal["elevenlabs", "imported"]`; versão de importação/processing em settings e manifesto, sem fingir procedência ElevenLabs.
- Produces: estado `local_ready` exclusivo de `imported`; sentinelas `voice_id="imported"`, `model_id="external-audio-v1"`. `provider_request_id` permanece nulo.
- Produces: exportação `rtk proxy uv run python -m auraly_pipeline.voices.schema`.

- [ ] **1. RED:** adicionar testes que permitem registro importado vinculado a `voice.import`, impedem vínculo cruzado com `voice.generate`, impedem aprovação incompleta e aceitam somente origem/estado compatíveis. Validar `VoiceImportRequest` inválido e origem importada serializada.
- [ ] **2. RED:** adicionar upgrade com registros ElevenLabs existentes, conferindo IDs/hashes e rejeição de delete, replace e mutação de Job/registro final. Testar downgrade com importados: deve falhar antes de alterar o banco; sem importados deve preservar os registros.
- [ ] **3. Run:** `rtk proxy uv run python -m pytest tests/test_voice_domain.py tests/test_voice_migrations.py tests/test_voice_migrations_direct_insert.py -q`; confirmar falha por contrato/migração ausentes.
- [ ] **4. GREEN:** implementar migração Alembic batch compatível com FK e recriar triggers/índice parcial. Manter Copy Master aprovado, estado inicial pending, Job correspondente, exclusividade e histórico imutável. Acrescentar checks de origem/estado; não editar migrações antigas. Atualizar a conversão de origem em `VoiceMasterService._to_domain` em `voices/service.py`, preservando o retorno tipado.
- [ ] **5. GREEN:** exportar os dois schemas com IDs versionados; adicionar drift ao harness e teste comportamental de detecção. Regenerar apenas os artefatos afetados.
- [ ] **6. Verify/commit:** rodar testes acima mais `tests/test_verify_harness.py`, Ruff/mypy aplicáveis e diff check; revisar o diff e commit `feat: support imported Voice Master provenance`.

### Task 2: Importação local, processamento e QC

**Files:** Create `src/auraly_pipeline/voices/import_audio.py`, `tests/test_voice_external_import.py`; modify `src/auraly_pipeline/voices/audio.py`, `tests/test_voice_audio.py`. Reutilizar fixtures de campanha/Copy Master em `tests/test_voice_service.py`, sem importar funções privadas de produção.

**Interfaces:**
- Consumes: Task 1; `process_voice_audio(raw_path: Path, output_path: Path) -> AudioProcessingReport`; `TranscriptProvider.transcribe(audio_path: Path) -> str`; `transcript_comparison`; `JobService.submit_linked_job`.
- Produces: `decode_external_audio(source: Path, output: Path) -> None` em `voices/audio.py`: probe de formato, MP3/WAV somente, decode integral FFmpeg com error/explode, saída WAV exclusiva.
- Produces: `VoiceImportService.for_database(database_path: Path, *, project_root: Path | None = None, work_root: Path | None = None, transcriber: TranscriptProvider | None = None) -> VoiceImportService`.
- Produces: `VoiceImportService.import_audio(request: VoiceImportRequest, *, source: Path) -> VoiceGenerationSubmission`; DTO já existente contendo `voice_master` e `job`.
- Produces: `VoiceImportService.worker_once(worker_id: str, *, campaign_id: str) -> Job | None`, filtrado por campanha e `voice.import`; `close() -> None`.
- Produces: `VoiceImportHandler(session_factory: sessionmaker[Session], *, work_root: Path, transcriber: TranscriptProvider | None = None)`, implementando o protocolo JobHandler existente.

- [ ] **1. RED:** escrever happy path com áudio sintetizado por FFmpeg, Copy Master aprovado e transcritor de teste específico. Assertar `provider == "imported"`, `status == "review_required"`, `sample_rate == 48000`, `channels == 1`, original byte-idêntico e duração preservando uma pausa interna conhecida. Provider de fala que explode em qualquer chamada garante ausência de geração paga.
- [ ] **2. RED:** parametrizar MP3 CBR sem Xing, WAV, formato falso, arquivo truncado detectável, vazio, mais de 100 MiB, saída existente, origem fora do root e links/junctions quando o SO permitir. Expectativa: entrada válida processada; inválida nunca publicada/aprovada; nenhum original removido.
- [ ] **3. RED:** testar mudança de bytes entre validação/cópia, repetição idêntica, alteração de hash, conflito com Voice Master ativo/aprovado e duas submissões concorrentes. Assertar um Job/registro para replay e no máximo um import ativo por campanha.
- [ ] **4. RED:** testar transcrição divergente/headline falada, transcritor indisponível e artefato adulterado. Assertar bloqueio de aprovação existente, falha sanitizada sem caminhos privados e preservação dos diagnósticos.
- [ ] **5. Run:** `rtk proxy uv run python -m pytest tests/test_voice_external_import.py tests/test_voice_audio.py -q`; confirmar RED por funcionalidade ausente.
- [ ] **6. GREEN:** validar project root e cada componente da origem/destino; reaproveitar validação de caminhos existente em `flow/artifacts.py` onde aplicável, sem contornar checks. Copiar raw para diretório UUID novo com criação exclusiva e hash conferido; Job guarda somente paths workspace-relative seguros.
- [ ] **7. GREEN:** criar Job/registro ligados atomicamente via API pública; identidade por campanha, Copy Master/version, SHA-256 da origem e processing version 1. Usar `RetrySafety.MANUAL_ONLY`, `max_attempts=1`: sem prometer recuperação automática. Falha permanece explícita; replay retorna o mesmo estado, não mascara sucesso nem cria tentativa nova.
- [ ] **8. GREEN:** handler lê cópia raw, verifica hash, decodifica WAV intermediário e chama processamento existente. Transcrição independente, comparação e manifesto/inspeção com hashes e métricas; estado local_ready somente após publicação completa. Exceptions não viram aprovação e não removem diagnósticos.
- [ ] **9. Verify/commit:** testes acima e regressões `tests/test_voice_service.py tests/test_voice_retry_safety.py`; Ruff/mypy, diff check, revisão; commit `feat: import external audio into Voice Master review`.

### Task 3: CLI, worker e integração HeyGen

**Files:** Modify `src/auraly_pipeline/cli.py`, `src/auraly_pipeline/jobs/service.py`, `README.md`, `docs/PROJECT-MEMORY.md`; create `tests/test_voice_external_import_cli.py`; modify `tests/test_heygen_service.py`, `tests/test_heygen_video_service.py` e `tests/test_job_service.py` conforme integração.

**Interfaces:**
- Consumes: Tasks 1–2; aprovação `VoiceMasterService.approve(voice_master_id: str, *, approved_by: str) -> VoiceMaster`; serviços HeyGen existentes.
- Produces: `voice import CAMPAIGN_ID --source FILE [--copy-master-version N] [--database PATH] [--project-root PATH] [--work-root PATH]`, apenas intake/submissão local.
- Produces: worker padrão reconhece `voice.import`; novo comando `voice run-import CAMPAIGN_ID --worker-id ID [--database PATH] [--work-root PATH]` chama o worker scoped da Task 2. O comando genérico `job worker-once` atual não expõe filtros de campanha/tipo; não usá-lo para processar a campanha real junto de Jobs pagos.
- Produces: JSON sanitizado com `success`, `voiceMaster` e `job`; erros `voice_import_failed` sem traceback/caminho privado. Nenhuma opção auto-approve.

- [ ] **1. RED:** CLI submete import em banco temporário, retorna IDs e origem importada; worker scoped processa só o import escolhido. Testar com e sem FORCE_COLOR e Whisper indisponível: help/import intake não carregam runtime nativo; falha ocorre no processamento real.
- [ ] **2. RED:** campanha com uma imagem aprovada e Voice Master importado revisado/aprovado produz plano HeyGen com hash/asset de áudio esperado. Antes de aprovação, upload/geração são recusados. Usar provider HeyGen determinístico; nenhuma credencial real.
- [ ] **3. Run:** `rtk proxy uv run python -m pytest tests/test_voice_external_import_cli.py tests/test_job_service.py tests/test_heygen_service.py tests/test_heygen_video_service.py -q`; confirmar RED nas novas integrações.
- [ ] **4. GREEN:** registrar comando e handler na fábrica padrão, sem alterar geração de voz nem criar acesso novo a Job internals. Reusar aprovação/listagem atuais. Documentar sequência importar → worker → revisar → aprovar → prepare-assets e falhas conhecidas.
- [ ] **5. Verify/commit:** testes acima mais `tests/test_voice_cli.py tests/test_voice_imports.py`, Ruff/mypy e diff check; revisar; commit `feat: expose external voice import through CLI`.

### Encerramento e handoff operacional

- [ ] Rodar `rtk proxy uv run python scripts/verify.py full`. Qualquer falha identificada deve ser reportada; não declarar LOCAL_VERIFIED apenas com testes focados.
- [ ] Revisão independente do diff inteiro conforme workflow do repositório; corrigir achados com RED/GREEN e repetir gates afetados. Não incluir mídia, credenciais, paths privados ou alterações a AGENTS.md.
- [ ] Registrar evidência sanitizada em `docs/superpowers/2026-09-30-external-voice-import-verification.md`; commit documental pequeno. Manter PROVIDER_VERIFIED pendente até o canário real.
- [ ] Não fazer merge/push automaticamente neste plano: apresentar branch, commits, verificações e pedir decisão de integração.
- [ ] Antes do canário, validar disponibilidade real do transcritor no Windows. Se Application Control bloquear, parar e explicar a dependência, sem bypass ou transcrição inventada.
- [ ] Preparar campanha de uma SceneVariant com os dois arquivos escolhidos, sem alterar originais. Texto esperado deve ser uma copy real revisada, não a própria transcrição tratada como prova.
- [ ] Obter as aprovações de copy/imagem/voz e conexão OAuth que faltarem. Somente então executar o D2C autorizado, com `max-paid-renders=1`, `concurrency=1`; resultado ambíguo exige reconciliar o mesmo ID, não gerar outro vídeo.
