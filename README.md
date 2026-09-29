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
- conexão HeyGen MCP/OAuth local, preflight, upload batch e reuso de imagens/WAV por conta, tipo
  e hash, com checkpoints e reconciliação explícita;
- CLI `heygen connect|disconnect|status|preflight|prepare-assets|reconcile`;
- D2B implementado: batch local de vídeo por variante, reserva de budget, polling/retomada,
  download HTTPS, QC H.264/AAC e publicação sem overwrite (validação final em andamento);
- runtime Google Flow/Playwright, geração, correlação de downloads e recuperação implementados e
  verificados com fixtures locais;
- CLI JSON e harness de verificação para as capacidades acima.

### Não entregue ainda

- canário HeyGen real (D2C), com autorização específica de consumo de créditos;
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

### Operação HeyGen por campanha (D2B)

Após aprovar copy/imagens/voz e preparar os assets D2A:

```powershell
uv run python -m auraly_pipeline.cli heygen plan-videos CAMPAIGN_ID --config examples/heygen-video-config.json --max-paid-renders 3
uv run python -m auraly_pipeline.cli heygen generate-videos CAMPAIGN_ID --config examples/heygen-video-config.json --max-paid-renders 3 --approved-by Rafael --yes
uv run python -m auraly_pipeline.cli heygen run-videos CAMPAIGN_ID
uv run python -m auraly_pipeline.cli heygen videos CAMPAIGN_ID
uv run python -m auraly_pipeline.cli heygen reconcile-video RENDER_ID
```

Os comandos aceitam `--database` e `--work-root`. `plan-videos` só consulta; `generate-videos`
reserva jobs e não gera mídia; **`run-videos` despacha gerações pagas** usando essa aprovação.
Não executar geração real antes do canário D2C autorizado. Não há estimativa monetária inventada:
o limite é por quantidade de renders da campanha, incluindo falhas/ambiguidades.

Config default: imagem + áudio de assets, engine escolhido pelo provider, 9:16/1080p/MP4,
cover, expressiveness medium, concorrência 2 e polling 10→20→40→60s até 1800s. O schema remoto
deve aceitar esses inputs; engine selecionável/exigido bloqueia até decisão explícita.

Sources ficam em `campaigns/<campaign>/heygen/<scene-uuid>/<logical-key>/source.mp4`, com
`publication.json` e `source.json`. São validados por hash, ffprobe e full decode. Repetir
submit/run reutiliza os registros. Após bloqueio com ID conhecido, reconcile consulta o mesmo
vídeo e libera a retomada; não chama create. Sem ID após dispatch ambíguo, informar o ID exato
com `--video-id ID --confirm-manual-binding`; nunca criar outro vídeo para resolver ambiguidade.

Testes locais usam fake MCP e MP4/WAV sintéticos. Eles não provam interoperabilidade real,
custo ou engine do provider. UI/editor/A/B permanecem no roadmap, sem afetar a identidade HeyGen.

### Pipeline alvo

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

O próximo Goal é **D2B — HeyGen Batch Generation, Polling & Download**: usar os assets remotos já
preparados para gerar e baixar um MP4 por variante com retomada segura.

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

Goals 0–3, 4A–4C, D1 e D2A estão implementados e verificados localmente. ElevenLabs, Google Flow e
HeyGen ainda não têm canário real registrado neste repositório. Nenhuma capacidade do roadmap deve ser
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
