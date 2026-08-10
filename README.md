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

Orquestração durável, ElevenLabs API, runtime Google Flow, HeyGen MCP/OAuth, edição final,
canário end-to-end e API/UI local estão sequenciados em `docs/GOAL-ROADMAP.md`. Cortes,
transcrição, captions, B-roll, música e render editorial também permanecem planejados; nenhuma
dessas capacidades deve ser inferida apenas por constar no PRD.

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
sem cliques cegos por coordenadas. As candidatas e versões rejeitadas serão preservadas.

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
imagem. O diretório confiável de downloads usa `~/Downloads` por padrão e
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
uv run mypy src
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

## Próximo Goal

O próximo trabalho é `Goal 2 — Persistent Job Orchestration`, conforme
`docs/GOAL-ROADMAP.md`. Ele adicionará jobs retomáveis, estado explícito, tentativas, eventos e
idempotência usando handlers fake, sem providers externos ou operações pagas.
