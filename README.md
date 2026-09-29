# Auraly Video Automation

Pipeline local para transformar uma Copy Master em vários vídeos verticais com a mesma voz,
imagens diferentes, geração HeyGen em lote e edição configurável.

O projeto agora segue um MVP **delivery-first**: chegar rapidamente a vídeos utilizáveis para o
Rafael, aproveitando o que já está pronto e evitando que automações não essenciais bloqueiem a
entrega.

## Estado do produto

### Entregue hoje

- contratos Pydantic e JSON Schema do `edit.json` legado;
- parser de Copy Master que mantém a headline fora da narração;
- inspeção de mídia por `ffprobe`, ingestão não destrutiva e base de conhecimento local;
- domínio e persistência SQLite para Campaign, CopyMaster, SceneVariant, Jobs e eventos;
- fila local retomável com idempotência, leases, retries e recuperação;
- Voice Master automatizado pela API oficial da ElevenLabs, com processamento, QC, review e
  aprovação humana;
- WAV processado em mono/48 kHz, normalizado e com silêncio removido nas duas bordas;
- domínio de imagens, candidatas, review e histórico persistente;
- importação batch manual com manifest explícito, dry-run, provenance, aprovação opcional e
  replay idempotente;
- runtime Google Flow/Playwright, geração, correlação de downloads e recuperação implementados e
  verificados com fixtures locais;
- CLI JSON e harness de verificação para as capacidades acima.

### Não entregue ainda

- integração HeyGen para upload batch de imagens e do Voice Master, geração, polling e download;
- `EditProfile` reutilizável e resolução de overrides por vídeo/variante;
- render final com headline, captions, música e framing configuráveis;
- variações A/B de headline sem regenerar voz, imagem ou HeyGen;
- API FastAPI e interface React local;
- preview aproximado e fluxo end-to-end operável pela interface.

### Google Flow: preservado, mas pausado

A automação do Google Flow não será removida. Ela representa trabalho técnico relevante e pode
voltar a ser usada como caminho opcional. Porém:

- nunca foi validada contra uma execução real do provider;
- não está no caminho crítico do novo MVP;
- não deve receber novos Goals antes do piloto delivery-first;
- imagens serão geradas manualmente e importadas em batch no fluxo principal.

Esse corte elimina o maior risco de manutenção de UI externa sem bloquear a produção.

## Fluxo alvo do MVP

```text
Copy Master aprovada
        ↓
Voice Master automatizado → processed/voice-master.wav
        ↓
imagens geradas manualmente → import batch → uma imagem por variante
        ↓
HeyGen: upload/reuso de imagem + Voice Master → generate batch
        ↓
polling retomável → download dos MP4
        ↓
EditProfile + overrides → EditManifest resolvido
        ↓
headline/captions/music/framing → render A/B
        ↓
review e entrega local
```

Uma variante A/B de headline começa no estágio de edição. Ela reutiliza o mesmo MP4 do HeyGen e
os mesmos assets upstream.

## Importar imagens manuais

```powershell
uv run auraly image prepare-import --campaign <campaign-id> --output imports/<campaign-id>
# coloque as imagens em imports/<campaign-id>/images e preencha image-import.json
uv run auraly image import-batch --input imports/<campaign-id>/image-import.json --dry-run
uv run auraly image import-batch --input imports/<campaign-id>/image-import.json
```

O comando só processa o lote quando chamado. Os arquivos originais permanecem intactos; um
manifest inválido não publica nem persiste nada, e repetir o mesmo lote reutiliza os candidatos.

## Próximo slice de desenvolvimento

O próximo Goal é **D2A — HeyGen Contract, Preflight & Asset Reuse**: definir a fronteira oficial
do provider e reutilizar por hash as imagens importadas e o Voice Master aprovado.

O escopo completo e os critérios de saída estão em
[`docs/GOAL-ROADMAP.md`](docs/GOAL-ROADMAP.md).

## Princípios do MVP

- uso local e pessoal primeiro;
- modular monolith, SQLite e filesystem local;
- UI e CLI chamam os mesmos application services;
- ações pagas precisam de budget gate e proteção contra duplicação;
- arquivos de origem nunca são alterados ou sobrescritos;
- segurança de secrets e paths permanece obrigatória;
- sem multiusuário, cloud sync próprio, publicação social ou infraestrutura distribuída;
- sem timeline, drag-and-drop livre ou preview frame-perfect.

## Contratos de edição planejados

`EditProfile` guarda defaults reutilizáveis de estilo. `EditManifest` registra a configuração
resolvida e imutável de cada render.

Precedência:

```text
EditProfile < campaign defaults < video override < output variant override
```

Os overrides serão restritos a campos editoriais conhecidos. Uma variante de headline altera
somente `headline.text` e recebe outro output/version ID; não cria nova Voice Master, imagem ou
geração HeyGen.

## Interface local planejada

React + FastAPI em `127.0.0.1`, com telas simples para:

- campanhas e assets;
- importação de imagens;
- Voice Master e status HeyGen;
- profiles e variantes de edição;
- preview 9:16 aproximado;
- disparo e acompanhamento de renders;
- revisão dos outputs.

O preview serve para validar composição, posição, fonte, cor e hierarquia. O arquivo renderizado
continua sendo a referência final.

## Preparação atual

Requisitos do repositório existente:

- Python 3.11;
- `uv`;
- Node/npm para o ambiente de renderer existente;
- FFmpeg/ffprobe;
- SQLite local.

```bash
uv sync --all-groups
npm ci
uv run python scripts/verify.py fast
```

O banco fica em `~/.auraly/auraly.db` por padrão. O work root usa
`~/Documents/Auraly/pipeline/work`, ou os valores de `AURALY_DATABASE_PATH` e
`AURALY_PROJECT_ROOT`.

## Verificação

O projeto distingue:

- `IMPLEMENTED`: código e testes existem;
- `LOCAL_VERIFIED`: o baseline determinístico passou;
- `PROVIDER_VERIFIED`: um canário real autorizado passou.

Goals 0–3, 4A–4C e D1 estão implementados e verificados localmente. ElevenLabs e Google Flow ainda
não têm canário real registrado neste repositório. Nenhuma capacidade do novo roadmap deve ser
tratada como entregue antes de código, testes e evidência correspondente.

Gate local completo:

```bash
uv run python scripts/verify.py full
```

## Documentação

- [`docs/PROJECT-MEMORY.md`](docs/PROJECT-MEMORY.md) — decisões duráveis, estado entregue e
  histórico técnico relevante;
- [`docs/PRD-MVP-MASS-VIDEO-AUTOMATION.md`](docs/PRD-MVP-MASS-VIDEO-AUTOMATION.md) — produto
  delivery-first e requisitos do MVP;
- [`docs/GOAL-ROADMAP.md`](docs/GOAL-ROADMAP.md) — ordem executável dos próximos Goals;
- [`AGENTS.md`](AGENTS.md) — regras de engenharia do repositório.
