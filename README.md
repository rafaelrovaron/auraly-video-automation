# Auraly Video Pipeline

Pipeline local, determinística e não destrutiva para transformar MP4s do HeyGen em Reels verticais do Auraly.

## Estado atual

A Fase 2 está implementada:

- contrato Pydantic do `edit.json`;
- JSON Schema versionado;
- parser das copies canônicas;
- inspeção de mídia com `ffprobe` JSON;
- ingestão que copia, mas nunca move ou sobrescreve, os arquivos originais;
- CLI `auraly`;
- testes unitários e smoke test com MP4 sintético.

Ainda não estão implementados cortes, transcrição, captions, B-roll, música ou render editorial.

## Documentação da automação em massa

- `docs/PROJECT-MEMORY.md` — visão consolidada, decisões duráveis, integrações, convenções e aprendizados do projeto;
- `docs/PRD-MVP-MASS-VIDEO-AUTOMATION.md` — PRD completo do MVP end-to-end com ElevenLabs API, Google Flow por Playwright, HeyGen MCP/OAuth, pós-produção, QC e interface local.

O PRD amplia o escopo futuro da pipeline para campanhas com uma Copy/Voice Master e múltiplas variantes visuais. As capacidades descritas ali são planejamento de produto e não devem ser confundidas com funcionalidades já implementadas.

## Preparação

```bash
cd "C:/Users/Rovaron/Documents/Auraly/pipeline"
uv sync --all-groups
npm ci
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
C:/Users/Rovaron/Documents/Auraly/07 Validated Ads Knowledge/Top Ads - Auraly
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

## Próxima fase

A próxima fase implementará cortes conservadores e revisáveis:

1. detecção de silêncio com FFmpeg;
2. geração de `cuts.plan.json`;
3. comparação opcional com Auto-Editor;
4. rough cut CFR de 30 fps;
5. teste de sincronização de áudio e vídeo.
