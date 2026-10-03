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
- campanhas com uma ou mais cenas, permitindo canário de uma imagem/um render sem migration;
- fila local retomável com idempotência, leases, retries e recuperação;
- Voice Master automatizado pela API oficial da ElevenLabs, com processamento, QC, review e
  aprovação humana;
- importação local de MP3/WAV externo como Voice Master, com origem `imported`, QC independente
  e aprovação humana separada (sem chamada ElevenLabs);
- WAV processado em mono/48 kHz, normalizado e com silêncio removido nas duas bordas;
- validação HeyGen do WAV PCM inteiro via `ffprobe`, inclusive PCM24 extensível do FFmpeg;
- domínio de imagens, candidatas, review e histórico persistente;
- importação batch manual com manifest explícito, dry-run, provenance, aprovação opcional e
  replay idempotente;
- conexão HeyGen MCP/OAuth local, preflight, upload batch e reuso de imagens/WAV por conta, tipo
  e hash, com checkpoints e reconciliação explícita;
- CLI `heygen connect|disconnect|status|preflight|prepare-assets|reconcile`;
- D2B implementado: batch local de vídeo por variante, reserva de budget, polling/retomada,
  download HTTPS, QC H.264/AAC e publicação sem overwrite (`LOCAL_VERIFIED`);
- D2C: canário real de um vídeo concluído com MCP/OAuth, vínculo manual confirmado, download/QC
  e replay sem nova geração (`PROVIDER_VERIFIED` para este caso; revisão visual ainda humana);
- runtime Google Flow/Playwright, geração, correlação de downloads e recuperação implementados e
  verificados com fixtures locais;
- CLI JSON e harness de verificação para as capacidades acima.
- D3A: profiles locais versionados, manifest v2, resolver tipado com hash/provenance,
  persistência exclusiva e CLI `edit`, sem renderer ou chamadas de provider.
- D3B: planejamento A/B em lote, captions ligadas à copy/voz aprovadas, sidecar de timing
  validado quando fornecido e CLI `edit plan|plan-get`, sem gerar mídia.
- D4A.1 implementado: API FastAPI de consultas locais, status factual por campanha,
  profiles/plans verificados e OpenAPI; verificação integral pendente nesta entrega.

### Não entregue ainda

- render final com headline, captions, música e framing configuráveis;
- renderização das variações A/B já planejadas, reutilizando voz, imagem e HeyGen;
- ações operacionais da API (D4A.2) e interface React local (D4B);
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

Após esgotar tentativas locais, reconcile vincula um novo job de recuperação ao mesmo render/ID
remoto; preserva os jobs anteriores e sua auditoria, sem nova reserva ou geração paga.

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

## Importar áudio externo

Com a Copy Master já aprovada e o arquivo dentro do project root confiável:

```powershell
uv run python -m auraly_pipeline.cli voice import CAMPAIGN_ID --source caminho/para/hook.mp3
uv run python -m auraly_pipeline.cli voice run-import CAMPAIGN_ID --worker-id Rafael
uv run python -m auraly_pipeline.cli voice get VOICE_MASTER_ID
uv run python -m auraly_pipeline.cli voice approve VOICE_MASTER_ID --approved-by Rafael
uv run python -m auraly_pipeline.cli heygen prepare-assets CAMPAIGN_ID
```

Import aceita MP3/WAV de até 100 MiB e apenas submete um job local. O worker específico processa
somente `voice.import` da campanha indicada. Original preservado; WAV mono/48 kHz/24-bit com
normalização e trim nas bordas. Um MP3 CBR válido sem Xing pode ser importado sem mudar o gate
da geração ElevenLabs. Replay idêntico reutiliza IDs e verifica os hashes; outra entrada é
recusada enquanto houver uma voz ativa/aprovada. Não há retry automático ou auto-approve.

O resultado é `review_required`, não uma aprovação. Whisper indisponível (inclusive bloqueio
do Application Control no Windows) produz falha sanitizada, preservando os arquivos locais.
Transcrição `mismatched` ou headline falada impede aprovação; não substituir por texto manual.
Para voz `imported` com comparação `review_required` e somente o achado de revisão da
transcrição, `voice approve --review-reason "Motivo da aceitação humana" --approved-by Rafael`
registra a exceção auditável sem mudar WAV, ASR, QC ou hashes. Não permite contornar outros achados.
Confira `job.status`: `success: true` do worker significa que a consulta/execução retornou,
não que um job `failed` passou no QC.

Intake aceita `--project-root`, `--work-root` e `--database`; o worker aceita os dois últimos.
Revisão/aprovação usam o work root configurado por `AURALY_PROJECT_ROOT`; se usar override no
intake, mantenha a mesma configuração para aprovar e preparar assets. Não usar o worker genérico
em uma fila real que também contenha jobs pagos.

## API local de consultas (D4A.1)

Requer um banco existente e atualizado pelo fluxo CLI habitual. A API abre SQLite em modo
somente leitura: não cria banco, não aplica migrations e não executa workers/providers.

```powershell
uv run auraly api serve --port 8000
# Overrides opcionais: --project-root PATH --work-root PATH --database PATH
Invoke-RestMethod http://127.0.0.1:8000/api/v1/campaigns
Invoke-RestMethod http://127.0.0.1:8000/api/v1/campaigns/CAMPAIGN_ID/status
```

Defaults preservam `AURALY_PROJECT_ROOT` e `AURALY_DATABASE_PATH`. Work root deve estar dentro
do project root; o banco pode ficar fora. O servidor escuta exclusivamente em `127.0.0.1`,
com um processo, sem reload, CORS ou logs de acesso. Documentação em
`http://127.0.0.1:8000/docs`; contrato em `/openapi.json`; saúde em `/health`.

As consultas cobrem campanhas, imagens, vozes, renders HeyGen, jobs, profiles e planos
editoriais. Coleções retornam `{"items": [...]}`; erros usam `{error: {code, message, field}}`.
Não aceita query parameters. Diretório editorial ausente significa lista vazia; artefato
presente e corrompido retorna erro, sem esconder a falha. Status é informação para o operador,
não autorização de geração paga. Não há media serving, ações POST ou render final nesta etapa.

## Próximo slice de desenvolvimento

O próximo slice é **D4A.2 — ações operacionais e integração de workers**, com design separado.
D4A só estará completo após as duas partes; D4B adicionará React/preview e D5 o renderer.
Revisão visual final do vídeo do canário continua humana; configurar um manifest não aprova o vídeo.

A execução de 2026-09-30 aprovou o WAV existente com motivo auditável, completou upload de
uma imagem e um áudio e despachou uma única geração, com concorrência 1. A resposta não
forneceu ID verificável; o pipeline bloqueou, sem repetir a geração. A consulta MCP encontrou
um vídeo concluído no horário do envio com duração 7,21733 s, mas sem callback/asset IDs.
Rafael confirmou o vínculo manual: download e QC concluíram, MP4 H.264/AAC 1080×1920 de
7,224 s. Replay reutilizou o mesmo render sem outro dispatch/reserva. Este canário é
`PROVIDER_VERIFIED`, mas não comprova recuperação automática do ID nem escala em lote real.
Detalhes em [evidência D2C](docs/superpowers/2026-09-30-d2c-canary-verification.md).

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

## Configurar edição local (D3A)

`EditProfile` v1 guarda defaults reutilizáveis de estilo, sem texto de campanha/MP4.
`EditManifest` v2 registra um snapshot hashável da configuração resolvida; não é um render.
O `edit.json` legado v1, ingest e seu schema permanecem intactos. Não há conversão
automática de cuts, punch-ins ou b-roll para o novo contrato.

Precedência:

```text
EditProfile < campaign defaults < video override < output variant override
```

Overrides são restritos a campos conhecidos. Omitir herda; false/zero são explícitos;
null só vale para referências/end nullable. A origem fica registrada por campo.
Trocar `headline.text` muda o hash downstream, sem alterar fonte/voz/imagem/HeyGen.
O D3B acrescenta planejamento em lote e inputs de captions, separados do manifest v2.

```powershell
uv run python -m auraly_pipeline.cli edit profile-create --request examples/edit-profile.json
uv run python -m auraly_pipeline.cli edit profile-list
uv run python -m auraly_pipeline.cli edit profile-get --profile-id plain --version 1
uv run python -m auraly_pipeline.cli edit resolve --request minha-edicao.json --dry-run
uv run python -m auraly_pipeline.cli edit resolve --request minha-edicao.json
uv run python -m auraly_pipeline.cli edit validate --manifest meu-manifest-v2.json
```

`examples/edit-resolve.json` e `edit-manifest.v2.json` são contratos sintéticos:
substitua IDs, path, SHA-256 e duração pelo MP4 local real e o hash do profile publicado.
Headline/captions/music vêm desabilitadas, sem presumir fontes instaladas.
Para habilitar texto, forneça fonte TTF/OTF local com path/hash; música exige asset
local e `musicAccepted=true`. Paths de assets são POSIX relativos ao project root.

Comandos de serviço aceitam `--project-root`/`--work-root`. Profiles ficam em
`editing/profiles/<id>/<version>/profile.json` sob work root, com checksum de conteúdo;
manifests em `campaigns/<campaign>/editing/<video>/<output-variant>/<hash>/manifest.json`.
Replay íntegro é reutilizado; conflito/corrupção não sobrescreve. Alterar profile usa
`edit profile-new-version --profile-id plain --base-version 1 --request novo-profile.json`
com versão 2 no documento. Nenhuma tabela SQL ou Job é criado.

Evidência e limites: [D3A verification](docs/superpowers/2026-10-01-d3a-verification.md).

## Planejar variantes A/B (D3B)

```powershell
uv run python -m auraly_pipeline.cli edit plan --request meu-batch.json --database caminho/auraly.db --project-root ROOT --work-root WORK --dry-run
uv run python -m auraly_pipeline.cli edit plan --request meu-batch.json --database caminho/auraly.db --project-root ROOT --work-root WORK
uv run python -m auraly_pipeline.cli edit plan-get --campaign-id CAMPAIGN --video-id VIDEO --plan-hash HASH --project-root ROOT --work-root WORK
```

O banco existente precisa estar sob `ROOT`, assim como `WORK` e os assets. A consulta é
somente leitura, sem migrations. `renderId` é o ID local de `heygen_renders`, não scene ID
nem ID remoto. `videoId` é uma chave editorial segura escolhida pelo operador.
Use `examples/edit-batch-request.json` como contrato sintético: substitua IDs e profileRef
pelos reais. Os exemplos de plano/timing não são sidecars prontos para seu áudio.

A lista explícita tem limite `maxOutputs=3` por padrão, sem produto cartesiano.
Cada output embute um manifest v2 intacto, refs de origem, hashes e filename determinísticos.
Todos são validados antes de publicar um único
`campaigns/<campaign>/editing/plans/<video>/<planHash>/plan.json` sob WORK.
Dry-run não publica planos/manifests; replay não sobrescreve corrupção nem cria jobs HeyGen.

O texto de captions vem da copy aprovada vinculada ao WAV do render, nunca da headline
ou da versão mais nova da campanha. `timingStatus=missing` preserva a pendência.
Um `timingRef` opcional aponta para JSON local por path/hash: origem `manual` ou
`external_alignment`, operador `acceptedBy`, timebase `source_mp4`, hashes exatos de
MP4/copy/WAV e cues cobrindo os tokens `split()` em intervalos inicial inclusivo/final exclusivo.
Não há ASR/alinhamento automático. Por output: `disabled`, `timing_missing` ou
`timing_provided`; texto habilitado exige fonte local válida. Planejar não renderiza MP4.

Evidência: [D3B verification](docs/superpowers/2026-10-02-d3b-verification.md).

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

Goals 0–3, 4A–4C, D1, D2A, D2B, D3A e D3B estão implementados e verificados localmente.
HeyGen tem somente o canário D2C com vínculo manual; ElevenLabs e Google Flow não têm canário
real registrado. D3A/D3B são locais, sem nova verificação de provider. Nenhuma capacidade do roadmap deve ser
tratada como entregue antes de código, testes e evidência correspondente.

Gate local completo:

```bash
uv run python scripts/verify.py full
```

Evidência D2B em 2026-09-30: gate Windows 13/13, 1357 testes aprovados e 18 skips, revisão
independente e correções com regressões. Ver o [relatório de verificação](docs/superpowers/2026-09-30-goal-d2b-verification.md).

## Documentação

- [`docs/PROJECT-MEMORY.md`](docs/PROJECT-MEMORY.md) — decisões duráveis, estado entregue e
  histórico técnico relevante;
- [`docs/PRD-MVP-MASS-VIDEO-AUTOMATION.md`](docs/PRD-MVP-MASS-VIDEO-AUTOMATION.md) — produto
  delivery-first e requisitos do MVP;
- [`docs/GOAL-ROADMAP.md`](docs/GOAL-ROADMAP.md) — ordem executável dos próximos Goals;
- [`AGENTS.md`](AGENTS.md) — regras de engenharia do repositório.
