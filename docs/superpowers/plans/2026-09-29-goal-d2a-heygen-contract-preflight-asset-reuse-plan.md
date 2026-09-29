# Goal D2A HeyGen Contract, Preflight & Asset Reuse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Conectar a aplicação local ao Remote MCP oficial do HeyGen por OAuth e preparar, deduplicar e persistir as imagens aprovadas e a Voice Master como assets remotos reutilizáveis.

**Architecture:** Um pacote `auraly_pipeline.heygen` concentra contratos, OAuth, adapter MCP, fake, persistência, planejamento e handler do job. O application service resolve inputs aprovados e submete um único job batch; o handler persiste IDs remotos antes do PUT, finaliza o batch e reconcilia sempre pela mesma idempotency key. CLI e futuro FastAPI reutilizam esse serviço síncrono; o SDK MCP assíncrono fica encapsulado no adapter.

**Tech Stack:** Python 3.11, Pydantic 2, SQLAlchemy 2, Alembic, Typer, httpx, MCP Python SDK 2.x, keyring, SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-09-29-goal-d2a-heygen-contract-preflight-asset-reuse-design.md`

## Global Constraints

- Suportar Python `>=3.11,<3.12`, Windows 10/11 x64 e Linux.
- Usar somente `https://mcp.heygen.com/mcp/v1/`; não automatizar o website HeyGen.
- Usar OAuth/MCP sem API key neste Goal; o volume é o MVP pessoal. O canário D2C decide se os limites exigem outro adapter para escala maior.
- Adicionar apenas `mcp>=2,<3` e `keyring>=25,<26`; reutilizar `httpx` para os PUTs assinados.
- Não persistir ou registrar tokens, refresh tokens, client registration secreta, authorization URLs, presigned URLs, upload headers ou payloads de mídia.
- Guardar tokens e client registration no cofre nativo; não fazer fallback para arquivo em texto puro.
- Limitar cada batch a `1..100` itens e fornecer `checksum_sha256` em todos os slots.
- Revalidar path, tamanho e SHA-256 imediatamente antes de qualquer mutação remota.
- Persistir `batch_id` e `asset_id` antes do primeiro PUT.
- Jobs `heygen.asset.upload` usam `RetrySafety.RECONCILE_BEFORE_RETRY`; nenhum retry automático cria nova ação remota.
- Não implementar criação/polling/download de vídeo, FastAPI, React, webhooks, API-key auth, múltiplos providers ou deleção remota.
- Testes e CI nunca autenticam no HeyGen nem executam ações remotas.
- Não alterar `AGENTS.md` nem arquivos sob `sources/`.

## Review Focus

- Callback OAuth duplicado, atrasado ou com parâmetros incompletos deve ser aceito no máximo uma vez; validação criptográfica de `state`/`iss` permanece no SDK. Cobrir em Task 3.
- Path adulterado, symlink escapando do work root ou hash/tamanho divergente deve bloquear antes de alocar batch. Cobrir em Tasks 4 e 5.
- Troca de conta HeyGen deve impedir reuso de IDs pertencentes à conta anterior. Cobrir em Tasks 2 e 5.
- Cardinalidade ou schema inválido na resposta de alocação deve falhar sem persistir linhas e sem enviar bytes. Cobrir em Task 3; a associação por ordem segue a garantia explícita do HeyGen.
- Crash após alocação e slot expirado devem reutilizar a mesma idempotency key; sem URL utilizável, o job fica bloqueado para reconciliação. Cobrir em Task 4.

---

### Task 1: Dependências e contratos de domínio HeyGen

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `src/auraly_pipeline/heygen/__init__.py`
- Create: `src/auraly_pipeline/heygen/domain.py`
- Create: `tests/test_heygen_domain.py`

**Interfaces:**
- Consumes: `ContractModel`, `validate_safe_identifier`, `validate_safe_error_message` e o formato workspace-relative já usado por images/voices.
- Produces: `RemoteAssetKind`, `RemoteAssetStatus`, `ProviderAssetStatus`, `AssetSource`, `RemoteAsset`, `HeyGenPreflight`, `AssetPreparationPlan`, `AssetPreparationSubmission`, `AssetUploadSlot`, `AssetBatchAllocation`, `AssetBatchState`, `AssetUploadJobInput` e `asset_batch_idempotency_key(account_ref: str, sources: Sequence[AssetSource]) -> str`.

- [ ] **Step 1: Escrever testes falhando para contratos fechados e chave determinística**

```python
def test_asset_batch_key_is_order_independent_and_account_scoped() -> None:
    first = asset_batch_idempotency_key("account-a", [IMAGE, AUDIO])
    assert first == asset_batch_idempotency_key("account-a", [AUDIO, IMAGE])
    assert first != asset_batch_idempotency_key("account-b", [IMAGE, AUDIO])
    assert first.startswith("heygen.asset.upload:")

def test_asset_source_rejects_absolute_path_and_bad_hash() -> None:
    with pytest.raises(ValidationError):
        AssetSource(
            source_id="00000000-0000-4000-8000-000000000001",
            kind="image",
            local_path="C:/secret.png",
            sha256="bad",
            mime_type="image/png",
            size_bytes=1,
        )

def test_job_input_rejects_more_than_one_hundred_sources() -> None:
    with pytest.raises(ValidationError):
        AssetUploadJobInput(
            account_ref="account-a",
            idempotency_key="heygen.asset.upload:" + "0" * 64,
            sources=[IMAGE] * 101,
        )
```

- [ ] **Step 2: Executar os testes para confirmar RED**

Run: `rtk uv run pytest tests/test_heygen_domain.py -q`

Expected: FAIL durante import porque `auraly_pipeline.heygen.domain` ainda não existe.

- [ ] **Step 3: Adicionar SDK MCP/keyring e criar os modelos mínimos**

Implementar os tipos listados no bloco Interfaces. Regras exatas:

- `kind`: `image|audio`;
- `AssetSource`: `source_id`, `kind`, `local_path`, `sha256`, `mime_type`, `size_bytes`;
- status persistido: `allocated|processing|ready|failed|reconciliation_required`;
- status do provider: `queued|processing|completed|failed|not_found`;
- SHA-256: 64 caracteres hex minúsculos;
- path: workspace-relative sem `..`, drive ou NUL;
- `AssetUploadJobInput.sources`: `min_length=1`, `max_length=100`;
- chave: `heygen.asset.upload:` + SHA-256 do JSON canônico de `accountRef` e pares ordenados `kind:sha256`;
- slots mantêm `upload_url` e `upload_headers` em dataclass `repr=False`, nunca em `model_dump`.

- [ ] **Step 4: Atualizar o lock e executar testes/typecheck focados**

Run: `rtk uv lock`

Run: `rtk uv run pytest tests/test_heygen_domain.py -q`

Run: `rtk uv run ruff check src/auraly_pipeline/heygen tests/test_heygen_domain.py`

Run: `rtk uv run mypy src`

Expected: todos PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add pyproject.toml uv.lock src/auraly_pipeline/heygen tests/test_heygen_domain.py
rtk git commit -m "feat: define HeyGen asset contracts"
```

---

### Task 2: Persistência de RemoteAsset e migration 0007

**Files:**
- Create: `src/auraly_pipeline/heygen/db_models.py`
- Create: `src/auraly_pipeline/heygen/repository.py`
- Modify: `src/auraly_pipeline/campaigns/migrations/env.py`
- Create: `src/auraly_pipeline/campaigns/migrations/versions/0007_heygen_remote_assets.py`
- Create: `tests/test_heygen_migrations.py`
- Create: `tests/test_heygen_repository.py`

**Interfaces:**
- Consumes: `RemoteAsset`, `AssetSource`, `AssetUploadSlot`, `AssetBatchState` da Task 1 e o `Base` SQLAlchemy existente.
- Produces: `RemoteAssetRow`; `RemoteAssetRepository(session_factory)` com `find_by_keys(account_ref, sources)`, `record_allocation(account_ref, batch_id, sources, slots, now)`, `list_by_batch(batch_id)`, `apply_batch_state(batch_state, now)` e `mark_reconciliation_required(batch_id, code, message, now)`.

- [ ] **Step 1: Escrever testes falhando da migration e constraints**

Testes devem subir um banco em `0006`, migrar a `head` e afirmar:

```python
assert columns == {
    "id", "provider", "provider_account_ref", "kind", "sha256",
    "mime_type", "size_bytes", "remote_asset_id", "remote_batch_id",
    "status", "last_error_code", "last_error_message", "created_at", "updated_at",
}
```

Também testar rejeição de status/kind inválidos, `size_bytes <= 0`, duplicata de
`(provider, provider_account_ref, kind, sha256)` e duplicata de
`(provider, provider_account_ref, remote_asset_id)`.

- [ ] **Step 2: Executar migration tests para confirmar RED**

Run: `rtk uv run pytest tests/test_heygen_migrations.py -q`

Expected: FAIL porque revision `0007_heygen_remote_assets` não existe.

- [ ] **Step 3: Criar row, migration e registro de metadata**

`RemoteAssetRow` deve espelhar exatamente o modelo persistente da spec. `provider` recebe constraint
`provider = 'heygen'`; `remote_asset_id` e `remote_batch_id` são obrigatórios porque a linha só nasce
depois de uma alocação respondida. A migration revisa `0006_manual_image_import`.

- [ ] **Step 4: Escrever testes falhando do repositório e atomicidade da alocação**

```python
def test_record_allocation_persists_ids_in_submitted_order() -> None:
    rows = repository.record_allocation("account-a", "batch-1", [IMAGE, AUDIO], SLOTS, NOW)
    assert [(row.sha256, row.remote_asset_id) for row in rows] == [
        (IMAGE.sha256, "asset-image"),
        (AUDIO.sha256, "asset-audio"),
    ]

def test_mismatched_slot_count_rolls_back_every_row() -> None:
    with pytest.raises(RemoteAssetPersistenceError):
        repository.record_allocation("account-a", "batch-1", [IMAGE, AUDIO], [ONE_SLOT], NOW)
    assert repository.find_by_keys("account-a", [IMAGE, AUDIO]) == []
```

Adicionar testes de isolamento entre contas, replay da mesma alocação, transições para `ready` e
`failed`, e recusa de transição `ready -> processing`.

- [ ] **Step 5: Implementar o repositório com transações `BEGIN IMMEDIATE`**

`record_allocation` valida cardinalidade antes da transação, associa por índice e faz flush de todas
as linhas antes do commit. Replay idêntico retorna as linhas existentes; conflito entre hash e
remote ID lança erro sanitizado. `apply_batch_state` preserva itens `ready` e atualiza falhas por
índice/asset ID.

- [ ] **Step 6: Executar testes focados e migrations regressivas**

Run: `rtk uv run pytest tests/test_heygen_migrations.py tests/test_heygen_repository.py tests/test_migrations.py tests/test_image_migrations.py tests/test_voice_migrations.py -q`

Expected: todos PASS.

- [ ] **Step 7: Commit**

```bash
rtk git add src/auraly_pipeline/heygen src/auraly_pipeline/campaigns/migrations tests/test_heygen_migrations.py tests/test_heygen_repository.py
rtk git commit -m "feat: persist reusable HeyGen assets"
```

---

### Task 3: OAuth local, adapter MCP e fake determinístico

**Files:**
- Create: `src/auraly_pipeline/heygen/auth.py`
- Create: `src/auraly_pipeline/heygen/provider.py`
- Create: `src/auraly_pipeline/heygen/fake_provider.py`
- Create: `tests/test_heygen_auth.py`
- Create: `tests/test_heygen_provider.py`

**Interfaces:**
- Consumes: modelos da Task 1; MCP SDK `OAuthClientProvider`, `TokenStorage`, `streamable_http_client`, `ClientSession`; `keyring`; `httpx.Client`.
- Produces: `HeyGenProvider` Protocol; `HeyGenProviderFailure(kind, public_message, request_dispatched)`; `KeyringTokenStorage`; `LoopbackOAuthCallback`; `HeyGenMcpAdapter`; `FakeHeyGenProvider` configurável por cenários.

- [ ] **Step 1: Escrever testes falhando do storage e callback OAuth**

Testar com keyring em memória/monkeypatch:

```python
async def test_storage_round_trips_tokens_and_client_registration() -> None:
    await storage.set_tokens(TOKENS)
    await storage.set_client_info(CLIENT_INFO)
    assert await storage.get_tokens() == TOKENS
    assert await storage.get_client_info() == CLIENT_INFO
    assert "access-token" not in caplog.text

def test_loopback_callback_accepts_one_complete_callback_only() -> None:
    callback.receive({"code": "code", "state": "state", "iss": "issuer"})
    assert callback.wait(timeout_seconds=1).state == "state"
    with pytest.raises(OAuthCallbackError):
        callback.wait(timeout_seconds=1)
```

Adicionar timeout, parâmetro ausente e bind exclusivo em `127.0.0.1`; não testar comparação de
`state`/`iss`, pois ela pertence ao SDK.

- [ ] **Step 2: Executar auth tests para confirmar RED**

Run: `rtk uv run pytest tests/test_heygen_auth.py -q`

Expected: FAIL durante import.

- [ ] **Step 3: Implementar storage no cofre e callback loopback stdlib**

`KeyringTokenStorage` implementa os quatro métodos async do `TokenStorage` e usa service name
`auraly.heygen.oauth` com entradas `tokens` e `client-info`. Serializar/deserializar pelos modelos
do SDK. `clear()` remove ambas. `has_session()` não retorna conteúdo.

`LoopbackOAuthCallback` usa `ThreadingHTTPServer(("127.0.0.1", 0), handler)`, aceita uma única
requisição, entrega `AuthorizationCodeResult` e fecha sempre em sucesso, timeout ou erro.

- [ ] **Step 4: Escrever testes falhando do adapter e schema boundary**

Com uma sessão MCP fake injetada, testar:

- `preflight()` exige exatamente as seis tools da spec e produz account fingerprint sem e-mail;
- o preflight exige os campos mínimos: `files`/`idempotency_key` em
  `create_asset_upload_batch`, `batch_id` em `complete_asset_batch` e `get_asset_batch`,
  `asset_ids|batch_ids` em `bulk_asset_statuses` e `asset_id` em `get_asset`;
- `allocate_asset_batch()` envia `filename`, `content_type`, `size_bytes`, `checksum_sha256` e a
  chave idempotente;
- resposta com cardinalidade incompatível, URL não HTTPS ou campos ausentes falha antes do PUT;
- `upload_file()` envia bytes/headers verbatim, revalida `size_bytes` e nunca inclui URL no erro;
- erro antes do dispatch é `retryable`; timeout após tool call/PUT é `ambiguous`;
- parser aceita `structuredContent` e, como fallback compatível, um único bloco JSON textual;
- fake cobre success, terminal failure, timeout e ambiguous.

- [ ] **Step 5: Implementar Protocol, adapter MCP síncrono e fake**

Assinaturas:

```python
class HeyGenProvider(Protocol):
    def connect(self) -> HeyGenPreflight: ...
    def preflight(self) -> HeyGenPreflight: ...
    def allocate_asset_batch(self, sources: Sequence[AssetSource], idempotency_key: str) -> AssetBatchAllocation: ...
    def upload_file(self, slot: AssetUploadSlot, local_path: Path) -> None: ...
    def complete_asset_batch(self, batch_id: str, idempotency_key: str) -> None: ...
    def get_asset_batch(self, batch_id: str) -> AssetBatchState: ...
    def get_assets(self, asset_ids: Sequence[str]) -> AssetBatchState: ...
```

Cada método MCP abre uma sessão curta por `asyncio.run`; não introduzir async no domínio existente.
`connect()` habilita os handlers de navegador/callback; os demais usam a sessão persistida e falham
com `configuration` quando não há credencial. O upload direto usa o `httpx.Client` já instalado.

- [ ] **Step 6: Executar testes e análise estática focados**

Run: `rtk uv run pytest tests/test_heygen_auth.py tests/test_heygen_provider.py -q`

Run: `rtk uv run ruff check src/auraly_pipeline/heygen tests/test_heygen_auth.py tests/test_heygen_provider.py`

Run: `rtk uv run mypy src`

Expected: todos PASS; nenhum teste abre navegador ou rede real.

- [ ] **Step 7: Commit**

```bash
rtk git add src/auraly_pipeline/heygen tests/test_heygen_auth.py tests/test_heygen_provider.py
rtk git commit -m "feat: connect HeyGen through OAuth MCP"
```

---

### Task 4: Handler batch, checkpoints e reconciliação

**Files:**
- Create: `src/auraly_pipeline/heygen/handler.py`
- Modify: `src/auraly_pipeline/jobs/repository.py`
- Modify: `src/auraly_pipeline/jobs/service.py`
- Create: `tests/test_heygen_handler.py`
- Modify: `tests/test_job_service.py`

**Interfaces:**
- Consumes: `HeyGenProvider`, `RemoteAssetRepository`, `AssetUploadJobInput`, `JobHandler`, `JobExecutionResult` e `RetrySafety.RECONCILE_BEFORE_RETRY`.
- Produces: `HeyGenAssetUploadHandler(session_factory, provider, work_root, clock=None, sleeper=None, poll_interval_seconds=2.0, poll_timeout_seconds=300.0)`; nova `ReconciliationReason` literal `remote_asset_batch_reconciled`; registro default de `heygen.asset.upload` em `JobService.for_database`.

- [ ] **Step 1: Escrever testes falhando do happy path e ordem dos checkpoints**

```python
def test_handler_persists_remote_ids_before_first_put() -> None:
    result = handler.execute(CONTEXT)
    assert result.outcome == "success"
    assert fake_provider.events == [
        "preflight", "allocate", "first_persisted_check", "put:0", "put:1",
        "complete", "poll",
    ]
    assert [asset.status for asset in repository.list_by_batch("batch-1")] == ["ready", "ready"]
```

Adicionar caso três imagens + um WAV, uma única ocorrência do hash do WAV e success parcial que
mantém três `ready` e um `failed`.

- [ ] **Step 2: Executar handler tests para confirmar RED**

Run: `rtk uv run pytest tests/test_heygen_handler.py -q`

Expected: FAIL durante import.

- [ ] **Step 3: Implementar handler mínimo e polling limitado**

Fluxo exato:

1. validar job input e account do preflight;
2. resolver cada path sob `work_root`, recusando escape/symlink;
3. conferir tamanho/hash;
4. remover hashes já `ready` da mesma conta;
5. chamar allocate com a chave persistida no input;
6. `record_allocation` e só então fazer PUTs;
7. complete com chave `<job-key>:complete`;
8. poll a cada `2s` por no máximo `300s`;
9. persistir status por item e retornar resultado sanitizado sem paths/URLs.

- [ ] **Step 4: Escrever testes falhando das fronteiras de crash/ambiguidade**

Cobrir:

- arquivo mudou antes da alocação: terminal, zero provider mutations;
- allocation timeout após dispatch: blocked, zero linhas, mesma chave disponível para reconcile;
- crash depois de persistir e antes do PUT: restart repete allocate com a mesma chave;
- URL expirada/PUT ambíguo: rows viram `reconciliation_required`, sem novo batch;
- preflight em outra account: blocked e nenhum reuso;
- polling timeout com batch conhecido: blocked; assets `processing` preservados;
- `ready` nunca regride;
- `job resume` continua recusando job RECONCILE_BEFORE_RETRY.

- [ ] **Step 5: Implementar mapeamento de outcomes e registro do handler**

Mapear `configuration -> BLOCKED`, `retryable -> RETRYABLE_FAILURE`, `terminal ->
TERMINAL_FAILURE`, `ambiguous -> BLOCKED`. Adicionar `remote_asset_batch_reconciled` às razões
aceitas pelo repositório de jobs e seus testes. Registrar o handler real somente quando
`handlers is None`; injeções de teste permanecem intactas.

- [ ] **Step 6: Executar regressões de jobs e handler**

Run: `rtk uv run pytest tests/test_heygen_handler.py tests/test_job_service.py tests/test_job_handlers.py tests/test_job_concurrency.py -q`

Expected: todos PASS.

- [ ] **Step 7: Commit**

```bash
rtk git add src/auraly_pipeline/heygen/handler.py src/auraly_pipeline/jobs tests/test_heygen_handler.py tests/test_job_service.py
rtk git commit -m "feat: upload HeyGen assets with durable checkpoints"
```

---

### Task 5: Planejamento por campanha e submissão idempotente

**Files:**
- Create: `src/auraly_pipeline/heygen/service.py`
- Create: `tests/heygen_support.py`
- Create: `tests/test_heygen_service.py`

**Interfaces:**
- Consumes: rows de Campaign/SceneVariant, `VoiceMasterRepository.approved_for_campaign`, `ImageRepository.approved_candidate_for_scene_in_session`, `RemoteAssetRepository`, `HeyGenProvider`, `JobService`, `HeyGenAssetUploadHandler`.
- Produces: `HeyGenService.for_database(database_path, work_root, provider=None)`; `connect()`, `disconnect()`, `connection_status()`, `preflight()`, `plan_assets(campaign_id)`, `submit_assets(plan)` e `reconcile_upload(job_id)`.

- [ ] **Step 1: Criar fixture local mínima e testes falhando de planejamento**

`tests/heygen_support.py` cria campanha com três SceneVariants, Voice Master aprovada e quatro
artefatos pequenos sob o work root, sem ffmpeg ou rede.

```python
def test_plan_has_three_images_and_one_shared_voice(tmp_path: Path) -> None:
    plan = service.plan_assets("campaign-one")
    assert len(plan.sources) == 4
    assert [item.kind for item in plan.sources].count("audio") == 1
    assert len({item.sha256 for item in plan.sources if item.kind == "audio"}) == 1

def test_ready_hashes_are_reused_without_job(tmp_path: Path) -> None:
    plan = service.plan_assets("campaign-one")
    submission = service.submit_assets(plan)
    assert submission.job is None
    assert submission.upload_count == 0
```

Adicionar campanhas sem Voice Master, variante sem imagem aprovada, path fora do root, symlink de
escape, tamanho/hash adulterado e account diferente.

- [ ] **Step 2: Executar service tests para confirmar RED**

Run: `rtk uv run pytest tests/test_heygen_service.py -q`

Expected: FAIL durante import.

- [ ] **Step 3: Implementar resolução de inputs e plano read-only**

`plan_assets` chama preflight, carrega todas as variantes em ordem determinística, exige exatamente
um approved candidate por variante e uma Voice Master aprovada, resolve paths sob `work_root`,
recalcula facts e consulta assets `ready` pela account. Ele não cria Job nem RemoteAsset.

- [ ] **Step 4: Escrever testes falhando de submissão/replay**

```python
def test_submit_creates_one_reconcile_before_retry_batch_job() -> None:
    submitted = service.submit_assets(service.plan_assets("campaign-one"))
    assert submitted.job.job_type == "heygen.asset.upload"
    assert submitted.job.retry_safety == RetrySafety.RECONCILE_BEFORE_RETRY
    assert len(submitted.job.input["sources"]) == 4

def test_replay_reuses_same_job() -> None:
    first = service.submit_assets(service.plan_assets("campaign-one"))
    second = service.submit_assets(service.plan_assets("campaign-one"))
    assert second.job.job_id == first.job.job_id
```

O job input não pode conter token, URL assinada, upload headers, e-mail ou path absoluto.

- [ ] **Step 5: Implementar submissão e reconciliação explícita**

`submit_assets` revalida o plano e usa `asset_batch_idempotency_key`. `reconcile_upload(job_id)`:

- exige job `heygen.asset.upload` bloqueado;
- consulta rows/batch conhecidos;
- quando allocation foi ambígua antes de persistir, repete allocate com a mesma chave;
- atualiza status remoto;
- deixa processando/expirado bloqueado;
- chama `resume_reconciled_job(..., reason="remote_asset_batch_reconciled")` somente depois de
  provar batch/IDs coerentes e retomáveis.

- [ ] **Step 6: Executar testes de service e regressões de domínio upstream**

Run: `rtk uv run pytest tests/test_heygen_service.py tests/test_campaigns.py tests/test_image_review.py tests/test_voice_service.py -q`

Expected: todos PASS.

- [ ] **Step 7: Commit**

```bash
rtk git add src/auraly_pipeline/heygen/service.py tests/heygen_support.py tests/test_heygen_service.py
rtk git commit -m "feat: plan reusable HeyGen campaign assets"
```

---

### Task 6: CLI operacional HeyGen

**Files:**
- Modify: `src/auraly_pipeline/cli.py`
- Create: `tests/test_heygen_cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `HeyGenService` da Task 5 e `_json_echo`/failure boundaries existentes.
- Produces: `auraly heygen connect|disconnect|status|preflight|prepare-assets|reconcile`.

- [ ] **Step 1: Escrever testes CLI falhando para os seis comandos**

Cobrir payloads JSON estáveis:

```python
result = runner.invoke(app, ["heygen", "status"])
assert json.loads(result.stdout) == {
    "success": True,
    "connected": False,
}
```

Para `prepare-assets`, testar cancelamento sem Job, confirmação por prompt em stderr e `--yes` para
execução não interativa. Para erros, stdout contém um único JSON com `success=false`; tokens, URLs e
paths absolutos não aparecem em stdout/stderr.

- [ ] **Step 2: Executar CLI tests para confirmar RED**

Run: `rtk uv run pytest tests/test_heygen_cli.py -q`

Expected: FAIL porque grupo `heygen` não existe.

- [ ] **Step 3: Implementar grupo Typer e boundaries sanitizados**

Opções comuns: `--database` e `--work-root`. `connect` pode abrir navegador; testes injetam service.
`prepare-assets` sempre imprime o plano antes da confirmação; cancelamento retorna JSON de sucesso
com `submitted=false`. `--yes` confirma explicitamente. `reconcile` recebe `JOB_ID` posicional.

- [ ] **Step 4: Executar CLI e suíte focused D2A**

Run: `rtk uv run pytest tests/test_heygen_domain.py tests/test_heygen_migrations.py tests/test_heygen_repository.py tests/test_heygen_auth.py tests/test_heygen_provider.py tests/test_heygen_handler.py tests/test_heygen_service.py tests/test_heygen_cli.py tests/test_cli.py -q`

Expected: todos PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add src/auraly_pipeline/cli.py tests/test_heygen_cli.py tests/test_cli.py
rtk git commit -m "feat: add HeyGen asset preparation CLI"
```

---

### Task 7: Documentação, verificação cross-platform e handoff para D2B

**Files:**
- Modify: `README.md`
- Modify: `docs/PROJECT-MEMORY.md`
- Modify: `docs/GOAL-ROADMAP.md`
- Modify: `docs/PRD-MVP-MASS-VIDEO-AUTOMATION.md`
- Modify: `scripts/verify.py` only if focused test routing requires it
- Modify: `.github/workflows/verify.yml` only if dependency/bootstrap commands require it

**Interfaces:**
- Consumes: todos os comandos e estados entregues nas Tasks 1–6.
- Produces: documentação que separa `IMPLEMENTED/LOCAL_VERIFIED` de `PROVIDER_VERIFIED`; D2B marcado como próximo Goal.

- [ ] **Step 1: Atualizar documentação sem declarar canário real**

Registrar:

- D2A entregue localmente;
- fluxo `connect -> preflight -> prepare-assets -> worker-once -> reconcile`;
- OAuth/MCP apropriado ao MVP pessoal, com ceiling oficial para escala maior;
- `remote_assets` e dedupe por account/kind/hash;
- D2C ainda obrigatório para `PROVIDER_VERIFIED`;
- D2B como próximo Goal.

- [ ] **Step 2: Executar verificação focused Windows**

Run: `rtk uv run python scripts/verify.py fast --pytest tests/test_heygen_domain.py tests/test_heygen_migrations.py tests/test_heygen_repository.py tests/test_heygen_auth.py tests/test_heygen_provider.py tests/test_heygen_handler.py tests/test_heygen_service.py tests/test_heygen_cli.py`

Expected: PASS em todos os steps.

- [ ] **Step 3: Executar verificação completa**

Run: `rtk uv run python scripts/verify.py full`

Expected: PASS em todos os steps, sem rede HeyGen.

- [ ] **Step 4: Confirmar migrations e diff final**

Run: `uv run alembic -c /dev/null heads` is not portable and must not be used. Instead run:

```powershell
rtk uv run pytest tests/test_migrations.py tests/test_heygen_migrations.py -q
rtk git diff --check
rtk git status --short
```

Expected: uma única head `0007_heygen_remote_assets` exercitada pelos testes; diff sem whitespace errors; somente arquivos D2A/documentação modificados.

- [ ] **Step 5: Commit**

```bash
rtk git add README.md docs/PROJECT-MEMORY.md docs/GOAL-ROADMAP.md docs/PRD-MVP-MASS-VIDEO-AUTOMATION.md scripts/verify.py .github/workflows/verify.yml
rtk git commit -m "docs: mark HeyGen asset preparation locally verified"
```

- [ ] **Step 6: Solicitar revisão final antes de integração**

Usar `superpowers:requesting-code-review`, corrigir findings confirmados e repetir `verify.py full`
antes de merge/push.
