# Auraly Video Pipeline

Pipeline local, determinística, retomável e auditável para produção em massa de Reels do Auraly.

## Estado atual

### Implementado hoje

- contrato Pydantic do `edit.json`;
- JSON Schema versionado;
- parser das copies canônicas;
- inspeção de mídia com `ffprobe` JSON;
- ingestão que copia, mas nunca move ou sobrescreve, os arquivos originais;
- base de conhecimento local pesquisável;
- CLI `auraly` e testes unitários/smoke;
- domínio `Campaign`, `CopyMaster` versionado e `SceneVariant` com invariantes Pydantic;
- persistência local SQLite em WAL via SQLAlchemy 2 e migrações Alembic;
- camada repository/application service e CLI JSON `campaign create/get/list`;
- proteção de imutabilidade para CopyMaster aprovado e persistência após restart;
- orquestração local durável com `Job`, tentativas imutáveis após finalização e eventos append-only;
- fila SQLite, idempotência, claim atômico, leases renováveis, retries e recuperação auditável;
- CLI JSON `job submit/get/list/worker-once/cancel/resume/recover` e handlers fake determinísticos;
- Voice Master persistente com API oficial ElevenLabs, processamento/QC local, aprovação humana,
  budget gate, reconciliação e proteção contra geração paga duplicada;
- contratos Google Flow v1.1 e schema de manifesto;
- trusted project/download roots, validação canônica de contexto e paths;
- correlação segura de downloads, partial-download handling, finalização não destrutiva,
  manifests e diagnósticos sanitizados.

### Parcialmente implementado

Os comandos `image-*` preparam e finalizam mecanicamente jobs de imagem. O runtime Playwright
que abre o Flow, verifica a UI, gera/preserva candidatas, seleciona o download 2K e captura trace
ainda não foi implementado nem validado. Os contratos registram explicitamente
`browserRuntimeStatus=not_implemented` e `imageQcStatus=not_implemented`; eles não afirmam que
uma imagem foi gerada automaticamente ou que o QC 2K foi concluído.

### Planejado

Runtime Google Flow, HeyGen MCP/OAuth, edição final,
canário end-to-end e API/UI local estão sequenciados em `docs/GOAL-ROADMAP.md`. Captions, B-roll,
música e render editorial também permanecem planejados; a integração oficial ElevenLabs, o
processamento de Voice Master e transcript/QC já pertencem ao Goal 3 implementado. Nenhuma
capacidade futura deve ser inferida apenas por constar no PRD.

### Estado de verificação dos milestones

O projeto usa termos separados:

- `IMPLEMENTED`: produção e testes requeridos existem;
- `LOCAL_VERIFIED`: o baseline determinístico/local requerido foi executado com sucesso, sem
  implicar provider real;
- `PROVIDER_VERIFIED`: um canário real explicitamente aprovado foi concluído.

Goals 0–3 estão `IMPLEMENTED` e `LOCAL_VERIFIED`. O provider canary de ElevenLabs não foi
demonstrado e permanece pendente como `Goal 3C`; portanto Goal 3 não é declarado
`PROVIDER_VERIFIED`. Nomes históricos de commit com `[verified]` não são evidência independente
de CI ou de provider.

## Arquitetura oficial de geração de imagens

O único caminho suportado é:

```text
Prompt criado pela IA/Hermes
→ Google Flow
→ Playwright Python
→ candidatas
→ download 2K
→ QC
→ review
→ approve/reject/regenerate
```

A IA/Hermes cria os prompts e toma decisões criativas. A aplicação executará o workflow de
forma mecânica, auditável e retomável. O browser usará perfil Chromium persistente dedicado,
concorrência 1, seletores verificáveis por roles/labels/texto/DOM, screenshots e trace em
falhas relevantes. Se a UI não puder ser confirmada, o worker deverá parar com segurança,
sem cliques cegos por coordenadas. A grade visível terá evidência por screenshot; toda candidata
intencionalmente baixada e toda versão baixada rejeitada serão preservadas sem overwrite. Baixar
toda candidata visível não é requisito P0.

Google Flow + Playwright é o único provider/browser workflow ativo. Não há provider alternativo
de geração de imagens. A dependência Playwright Python está declarada, mas o browser runtime
continua planejado no PRD e não deve ser confundido com funcionalidade pronta.

## Documentação da automação em massa

- `docs/PROJECT-MEMORY.md` — visão consolidada, decisões duráveis, integrações, convenções e aprendizados do projeto;
- `docs/PRD-MVP-MASS-VIDEO-AUTOMATION.md` — PRD completo do MVP end-to-end com ElevenLabs API, Google Flow por Playwright, HeyGen MCP/OAuth, pós-produção, QC e interface local.
- `docs/GOAL-ROADMAP.md` — sequência de Goals estreitos e verificáveis para implementação com Codex;
- `AGENTS.md` — limites, fontes de verdade, regras de engenharia e checks obrigatórios para agentes.

O PRD amplia o escopo futuro da pipeline para campanhas com uma Copy/Voice Master e múltiplas variantes visuais. As capacidades descritas ali são planejamento de produto e não devem ser confundidas com funcionalidades já implementadas.

## Preparação

Defina `<AURALY_ROOT>` como a pasta raiz local do Auraly. O código usa
`~/Documents/Auraly` por padrão; outro local pode ser definido com
`AURALY_PROJECT_ROOT` ou `--project-root` em cada comando de geração de
imagem. Todos os artefatos de campanha (incluindo Voice Master e preparação de
imagem) usam `<AURALY_ROOT>/pipeline/work`; o SQLite permanece independente em
`~/.auraly/auraly.db` por padrão. O diretório confiável de downloads usa
`~/Downloads` por padrão e
pode ser alterado com `AURALY_DOWNLOADS_DIR` ou com `--downloads-dir` em cada
comando de geração de imagem. Os comandos de continuação rejeitam contextos
que não correspondam a esses dois roots confiáveis.

```bash
cd "<AURALY_ROOT>/pipeline"
uv sync --all-groups
npm ci
```

## Campaign Foundation

O banco de metadados usa SQLite em modo WAL. Por padrão ele fica fora do repositório em
`~/.auraly/auraly.db`; defina `AURALY_DATABASE_PATH` ou passe `--database` para escolher outro
arquivo SQLite. Cada comando aplica as migrações Alembic pendentes antes de acessar os dados.
Arquivos SQLite, WAL e SHM são ignorados pelo Git.

Criar, consultar e listar uma campanha:

```bash
uv run auraly campaign create \
  --input examples/campaign.request.json \
  --database ~/.auraly/auraly.db

uv run auraly campaign get eight-of-cups-pilot \
  --database ~/.auraly/auraly.db

uv run auraly campaign list \
  --database ~/.auraly/auraly.db
```

As respostas são JSON estruturado. IDs duplicados falham sem overwrite; um `CopyMaster`
aprovado não pode ser atualizado nem removido no banco e qualquer revisão é persistida como uma
nova versão. As variantes exigem ao menos três locais distintos. A headline permanece visual-only
e `spokenText` é derivado somente de hook, body e CTA. O banco armazena apenas estado e metadados
— nunca mídia, cookies, tokens, profiles ou URLs assinadas.

Para criar ou atualizar explicitamente um banco por Alembic:

```bash
AURALY_DATABASE_PATH=~/.auraly/auraly.db uv run alembic upgrade head
```

## Voice Master

Goal 3 adds a campaign-level `VoiceMaster` linked to one approved `CopyMaster` version. A logical voice request creates one durable `voice.generate` Job with `reconcile_before_retry`; every SceneVariant reuses the approved processed artifact.

```bash
uv run auraly voice generate CAMPAIGN_ID --voice-id VOICE_ID --model-id eleven_multilingual_v2 --approve-paid-request --paid-request-approved-by OPERATOR --approved-budget-cents 1000
uv run auraly job worker-once --worker-id voice-worker
uv run auraly voice get VOICE_MASTER_ID
uv run auraly voice list --campaign-id CAMPAIGN_ID
uv run auraly voice approve VOICE_MASTER_ID --approved-by OPERATOR
uv run auraly voice reject VOICE_MASTER_ID --rejected-by OPERATOR --reason "Pacing requires regeneration"
uv run auraly voice resolve-no-artifact VOICE_MASTER_ID --resolved-by OPERATOR --reason "Provider history confirms no artifact"
```

`voice generate` requires a positive Campaign `budget.limitCents`, a valid uppercase three-letter
Campaign `budget.currency`, explicit `--approve-paid-request`, an explicit operator, and
`--approved-budget-cents` no greater than the Campaign limit. The append-only authorization event
records operator, timestamp, approved ceiling, Campaign limit, and authoritative currency. Ambiguous
or `dispatching` outcomes without a raw artifact whose digest was already persisted remain blocked
until `resolve-no-artifact` records an operator-confirmed reconciliation; the same Job is then resumed
without bypassing its attempt or fencing history. Provider MP3 is accepted only when Xing/Info/VBRI
metadata supplies an independently checkable frame count; formats without declared frame count fail
closed rather than treating a clean frame boundary as proof of completeness. Uma Campaign com VoiceMaster
já aprovada rejeita qualquer nova logical generation antes de criar VoiceMaster, Job ou autorização
paga; criação de VoiceMaster + Job + evento pago e a decisão concorrente com approval são serializadas
em uma única transação SQLite. Replay exato da logical key existente continua idempotente. Replacement/supersede não faz
parte deste Goal.

`ELEVENLABS_API_KEY` is loaded only by the worker from the environment. It is never accepted as CLI/job input or persisted. The official `POST /v1/text-to-speech/{voice_id}/with-timestamps` API is the only TTS path. The exact persisted `CopyMaster.spoken_text` is sent; `headline` remains visual-only.

Artifacts are non-destructive under the configured work root:

```text
campaigns/<campaign-id>/voice/<voice-master-id>/
  raw/provider.mp3
  processed/voice-master.wav
  inspection/transcript.json
  manifest/voice-master.json
```

The raw response is created exclusively and never overwritten. FFmpeg produces separate mono 48 kHz PCM WAV using `silenceremove=start_periods=1:start_duration=0.1:start_threshold=-50dB,areverse,silenceremove=start_periods=1:start_duration=0.1:start_threshold=-50dB,areverse,loudnorm=I=-16:TP=-1.5:LRA=11`. Final LUFS, true peak, silence, duration, hashes, WPM and transcript comparison are persisted. Human approval is mandatory.

## Persistent Job Orchestration

Goal 2 persiste toda a informação necessária para entender e retomar trabalho local sem contexto
conversacional. Jobs podem ser globais, vinculados a uma campanha ou vinculados a uma
`SceneVariant`. A mesma `idempotencyKey` retorna o job existente quando o contrato é idêntico e
falha com conflito quando é reutilizada para outra operação.

```bash
uv run auraly job submit \
  --input examples/job.request.json \
  --database ~/.auraly/auraly.db

uv run auraly job list --status queued --database ~/.auraly/auraly.db
uv run auraly job get <job-id> --database ~/.auraly/auraly.db
uv run auraly job worker-once --worker-id local-worker-1 \
  --database ~/.auraly/auraly.db
uv run auraly job cancel <job-id> --database ~/.auraly/auraly.db
uv run auraly job resume <job-id> --database ~/.auraly/auraly.db
uv run auraly job recover --database ~/.auraly/auraly.db
```

`worker-once` é deliberadamente limitado: recupera leases expirados, promove retries vencidos,
faz claim atômico de no máximo um job e executa um handler local registrado. Enquanto o handler
está ativo, um heartbeat interno renova aproximadamente a cada terço do lease e encerra antes da
finalização; perda de worker/attempt fencing bloqueia completion com erro sanitizado. O número da
tentativa é o fencing token: completion e renewal exigem o mesmo worker, a mesma tentativa e um
lease ainda válido. Os handlers fake disponíveis são `fake.success`, `fake.retry-once`, `fake.retry-always`,
`fake.permanent-failure`, `fake.blocked` e `fake.crash`; Goal 3 também registra o handler real
`voice.generate`, cuja única chamada externa é a API oficial da ElevenLabs e cujo dispatch exige
autorização paga persistida.

Estados suportados:

```text
queued -> running | cancelled
running -> completed | retry_scheduled | failed | blocked
retry_scheduled -> queued | cancelled
blocked -> queued | cancelled
completed | failed | cancelled -> terminal
```

O contrato persiste `retrySafety`: `idempotent` autoriza retry automático, `manual_only` bloqueia
até `job resume` explícito, e `reconcile_before_retry` permanece bloqueado até reconciliação humana.
Para `voice.generate`, `voice resolve-no-artifact` registra operador e razão em evento append-only e
retoma o mesmo Job somente após confirmação de que nenhum artifact foi criado. A capability declarada
pelo handler precisa coincidir com a policy do job.

O cancelamento de job `running` é rejeitado, pois este Goal não finge interromper uma operação em
execução. O lease pode ser renovado pela camada de aplicação e, quando expira, a tentativa ativa é
finalizada como `interrupted`; o job recebe `job.recovered` e vai para retry automático apenas se a
policy for idempotente, fica blocked quando requer autorização/reconciliação, ou falha se o budget
de tentativas foi esgotado. Migrations de startup usam lock de arquivo entre processos. A associação
Campaign/SceneVariant também é validada por triggers SQLite. Inputs, outputs e eventos aceitam apenas metadados JSON seguros:
secrets, cookies, profiles, URLs assinadas, data URLs e mídia/BLOB são rejeitados.

## Ingestão

```bash
uv run auraly ingest \
  --video "../05 HeyGen Inputs/Inbox/susan-sign.mp4" \
  --copy "../01 Copies/susan-sign.md" \
  --character susan-smith \
  --work-root work \
  --reel-id susan-sign-001
```

Personagens aceitos:

- `susan-smith` → template `susan-hard-truth-v1`;
- `soul-constellation` → template `soul-constellation-v1`.

A ingestão cria:

```text
work/<reel-id>/
├── source/
│   ├── heygen.mp4
│   └── copy.md
├── manifest/
│   └── edit.json
└── probe.json
```

O vídeo e a copy de origem permanecem intactos. Um workspace existente nunca é sobrescrito.

## Formato obrigatório da copy

```markdown
## Headline para tela
**HEADLINE VISUAL**

## Hook
Texto falado do hook.

## Body
Texto falado do body.

## CTA
Texto falado do CTA.
```

A headline é excluída de `spokenText` por design e o contrato proíbe `headline.spoken=true`.

## Validar um manifesto

```bash
uv run auraly validate work/<reel-id>/manifest/edit.json
```

## Regenerar o JSON Schema

```bash
uv run auraly export-schema
```

Arquivo gerado:

```text
schemas/edit.schema.json
```

## Base de vídeos validados

A biblioteca somente leitura fica fora do repositório:

```text
<AURALY_ROOT>/07 Validated Ads Knowledge/Top Ads - Auraly
```

Verifique integridade e processamento:

```bash
uv run auraly knowledge-status
```

Pesquise hooks, CTAs, ângulos, claims, nomes de arquivos e documentos em texto integral:

```bash
uv run auraly knowledge-search "face reveal" --collection validated-ad --limit 5
uv run auraly knowledge-search "horóscopo visual" --limit 5
uv run auraly knowledge-search "hidden feelings return" --collection validated-ad
```

Guias:

- `knowledge/guides/copy-playbook.md`;
- `knowledge/guides/hook-cta-patterns.md`;
- `knowledge/guides/editing-playbook.md`.

Fluxo antes de criar um novo `edit.json`:

1. pesquisar um ângulo e consultar de 3 a 5 referências validadas;
2. selecionar um padrão narrativo, sem copiar frases literalmente;
3. escolher um único objeto de prova conectado ao roteiro;
4. aplicar beat map, ritmo e safe zones do playbook editorial;
5. remover ou aprovar todos os claims sinalizados;
6. ingerir no job somente assets novos/licenciados — nunca vídeos concorrentes da biblioteca;
7. renderizar e executar o QC habitual.

`validated-ad` identifica um anúncio usado por perfis de terceiros que vendeu muito bem — uma referência de desempenho comercial comprovado em outros perfis, não mera aprovação editorial. Isso não garante o mesmo resultado no perfil atual e não implica direito de publicação, aprovação jurídica ou licença do arquivo. `competitor-reference` serve somente como benchmark/inspiração.

## Gates de qualidade

```bash
uv run pytest --cov=auraly_pipeline --cov-report=term-missing
uv run ruff check src tests
uv run python -m mypy src
uv run python -m auraly_pipeline.schema
```

## Garantias atuais do manifesto

- `schemaVersion` deve ser `1.0`;
- paths internos devem ser relativos ao workspace;
- a headline não pode ser narrada;
- eventos precisam ter `end > start`;
- eventos não podem ultrapassar a duração da fonte;
- todo B-roll precisa declarar licença;
- `approved` e `rendered` exigem aprovação humana;
- somente personagens e formatos suportados são aceitos;
- campos desconhecidos são rejeitados.

## Próximos passos

A sequência imediata é:

```text
roadmap/process alignment
→ Verification Harness
→ Goal 4A design spec
→ human review
→ Goal 4A implementation plan
→ small TDD/task commits
→ full verification
→ independent review
→ Goal 4B design
```

Goal 4 foi decomposto em `4A Image Domain & Persistence`, `4B Google Flow Browser Runtime`,
`4C Flow Generation, Download & Recovery` e `4D Image QC, Review & Provider Canary`. Nenhum
desses subgoals está implementado. Specs e planos futuros vivem respectivamente em
`docs/superpowers/specs/` e `docs/superpowers/plans/`; qualquer canário real continua exigindo
aprovação explícita.
