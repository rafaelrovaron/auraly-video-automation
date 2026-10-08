# Auraly Video Automation

Pipeline local para transformar uma Copy Master em vários vídeos verticais com a mesma voz,
imagens diferentes, geração HeyGen em lote e edição configurável.

O projeto agora segue um MVP **delivery-first**: chegar rapidamente a vídeos utilizáveis para o
Rafael, aproveitando o que já está pronto e evitando que automações não essenciais bloqueiem a
entrega.

## Estado do produto

### Entregue hoje

- D5A.1 `IMPLEMENTED`, `LOCAL_VERIFIED` (Windows): renderer local sequencial
  FFmpeg/ASS e CLI `edit render` integrado em `main` no código `8be3fab`. Gate pós-merge 19/19,
  350 frontend e 1.877 Python / 27 skips; três achados da revisão corrigidos
  com RED→GREEN. Actions Linux/Windows desta integração ainda pendentes.
  Consome plano salvo, gera masters 1080×1920/30 FPS H.264/AAC com headline,
  captions com timing existente, música e framing. A/B reutiliza o MP4 original;
  recibo por outputHash/runtime permite replay sem encode. Sem novos Jobs/API/UI.
  [Evidência D5A.1](docs/superpowers/2026-10-08-d5a1-verification.md).
- D4B.3c `IMPLEMENTED`, `LOCAL_VERIFIED`, integrado em `main` por fast-forward `fdfb3cb`:
  gate Windows 19/19, 350 frontend e 1.823 Python / 26 skips; preview estático aproximado
  do MP4 selecionado, headline/legenda/framing e seleção de variante, sem render
  ou regeneração. Gate completo repetido em `main` com sucesso. Verificação final e limites registrados na
  [evidência D4B.3c](docs/superpowers/2026-10-08-d4b3c-verification.md).
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
  persistência exclusiva e CLI `edit`; neste checkpoint não havia renderer ou chamadas de provider.
- D3B: planejamento A/B em lote, captions ligadas à copy/voz aprovadas, sidecar de timing
  validado quando fornecido e CLI `edit plan|plan-get`, sem gerar mídia.
- D4A.1 implementado: API FastAPI de consultas locais, status factual por campanha,
  profiles/plans verificados e OpenAPI (`IMPLEMENTED`, `LOCAL_VERIFIED`);
  gate Windows 15/15, 1.627 testes aprovados e 23 skips; sem novo canário de provider.
- D4A.2 implementado: 19 ações POST, fila tipada e worker local explícito. Gate Windows
  15/15 no código `73ca358`, 1.701 testes aprovados / 24 skips; E2E fake até MP4/plano A/B.
  Integrado em `main` no código `9cd8731`, com Actions Linux/Windows aprovados.
  Revisão final independente é registrada na memória. Sem novo canário pago.
- D4B.1 implementado: painel React local de campanhas, assets e Jobs, polling e controle
  explícito start/stop do worker; integração com API/proxy reais e providers fake.
  Gate Windows 19/19, 56 testes frontend e 1.711 Python aprovados / 24 skips;
  `LOCAL_VERIFIED`, sem nova execução de provider. Actions desta branch ainda não executados.
- D4B.2a `IMPLEMENTED`, `LOCAL_VERIFIED`: criação de campanha/copy aprovada, novas versões de copy,
  associações manuais imutáveis, dry-run diagnóstico, importação ligada ao snapshot das
  fontes e review de imagens pela UI. Evidência atual no PROJECT-MEMORY;
  sem novas chamadas pagas. Actions deste slice pendentes de publicação.
- D4B.2b.1 `IMPLEMENTED`, `LOCAL_VERIFIED`: formulários de orçamento inicial, geração/importação e review de Voice Master;
  workers explícitos e acompanhamento separado de operação local/Job filho. Verificação
  deste slice registrada no PROJECT-MEMORY; sem nova chamada paga.
- D4B.2b.2 `IMPLEMENTED`, `LOCAL_VERIFIED`: preparação de assets, plano/autorizações
  HeyGen, reservas, reconciliação e metadados dos MP4s pela UI local. Gate 19/19;
  revisão independente concluída e três achados corrigidos; gate pós-revisão 19/19,
  220 frontend e 1.793 Python / 25 skips. CI pendente. Evidência no PROJECT-MEMORY;
  sem novo canário ou uso de créditos.

D4B.3b — overrides e variantes de headline pela UI está `IMPLEMENTED`, `LOCAL_VERIFIED`
na branch `feat/d4b3b-editing`: gate Windows 19/19, 341 frontend e 1.807 Python / 25 skips.
Valida/salva planos sobre um MP4 existente, sem regenerar assets. Revisão independente e
correções concluídas; sem merge/push ou Actions desta branch. D4B.3a está integrado em `main`.
Evidência: [verificação D4B.3b](docs/superpowers/2026-10-07-d4b3b-verification.md).

### Não entregue ainda

- D5A.2: disparar e acompanhar o render por Jobs/API/UI;
- D5B: QC/review humano e entrega dos masters;
- piloto end-to-end operável pela interface (o preview aproximado já foi entregue).

### Render local de um plano salvo (D5A.1)

```powershell
uv run auraly edit render --campaign-id <campaign> --video-id <video> --plan-hash <hash> --project-root <project> --work-root <work>
```

`--dry-run` valida assets/suporte/reuso sem gravar arquivos ou medir fit.
O comando retorna JSON; exit 1 indica erro global ou variante failed, sem interromper
irmãs válidas. Texto literal com fonte local exata, pesos 400/700 e fit wrap/error/shrink;
captions habilitadas exigem cues aceitos em `source_mp4`, sem highlight por palavra.
Sem música, copia AAC; com música, aplica volumeDb + duckUnderVoiceDb fixo e limiter
sem auto-gain, preservando a voz do MP4 (não reinsere WAV).

Masters ficam sob `campaigns/<campaign>/editing/renders/<variant>/<renderKey>/`,
com `render.json` publicado por último. Reuso exige hash/probe/recibo íntegros;
planHash do recibo identifica o plano produtor, não precisa ser o plano consumidor.
Órfão/corrupção nunca é sobrescrito ou adotado: reparar manualmente o artefato
identificado, preservando backup se necessário, e executar novamente.
Windows usa caminhos longos nativos; ferramentas legadas podem precisar de work root curto.

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

## API local operacional (D4A.1 + D4A.2)

Requer um banco existente e atualizado pelo fluxo CLI habitual. As consultas usam SQLite
somente leitura; ações usam um engine separado sobre o mesmo banco existente. Startup não
cria banco, não aplica migrations e não inicia workers/providers automaticamente.

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
Logs rotineiros do Uvicorn ficam desativados para não expor paths em falhas de startup;
o comando apresenta uma mensagem estática e sai com erro.

As consultas cobrem campanhas, imagens, vozes, renders HeyGen, jobs, profiles e planos
editoriais. Coleções retornam `{"items": [...]}`; erros usam `{error: {code, message, field}}`.
Não aceita query parameters. Diretório editorial ausente significa lista vazia; artefato
presente e corrompido retorna erro, sem esconder a falha. Status é informação para o operador,
não autorização de geração paga. Não há media serving ou render final nesta etapa.

As 19 ações POST tipadas cobrem campanhas/copy, import/review de imagens, voz, HeyGen,
profiles/planos, cancel/resume e worker. Ações longas retornam `202` com `jobId`; enfileirar
não executa. Até dry-run persiste seu Job de operação, mas não importa candidatos/persiste planos.
POST exige JSON e Origin ausente ou exatamente igual à origem loopback da request.

```powershell
$base = 'http://127.0.0.1:8000/api/v1/campaigns/CAMPAIGN_ID'
$operation = Invoke-RestMethod "$base/images/import/prepare" -Method Post -ContentType 'application/json' -Body '{"campaignId":"CAMPAIGN_ID","outputPath":"inbox/batch-01"}'
Invoke-RestMethod "$base/worker/start" -Method Post -ContentType 'application/json' -Body '{"campaignId":"CAMPAIGN_ID","kind":"local_operations"}'
Invoke-RestMethod "$base/operations/$($operation.jobId)"
Invoke-RestMethod "$base/worker"
Invoke-RestMethod "$base/worker/stop" -Method Post -ContentType 'application/json' -Body '{"campaignId":"CAMPAIGN_ID"}'
```

Após prepare concluir, coloque imagens em `images/` da pasta retornada e preencha cada
`items[].path` no `image-import.json` (por exemplo `images/scene-01.png`). Não há watcher:
envie `/images/import` com `manifestPath` e `mode: "dry_run"` ou `"execute"`, depois inicie
`local_operations`. Caminhos relativos de entrada partem do project root; `outputPath` do
prepare parte do work root. Não altere o manifest depois de enfileirar: seu hash fica fixado.
O planner verifica e interpreta o mesmo snapshot, inclusive a campanha do plano.
No Windows, escolha um work root curto para não atingir limites legados de caminhos.

Worker kinds: `local_operations`, `voice_generate`, `voice_import`, `heygen_assets` e
`heygen_videos`. Há um runner ativo por processo, limitado à campanha e ao tipo escolhido;
start concorrente retorna 409. Stop impede novas claims, deixando o trabalho ativo terminar
e salvar checkpoints. Retries futuros exigem outro start quando estiverem disponíveis.
Shutdown drena o worker antes de fechar engines; restart não executa nada automaticamente.
Após stale recovery, resume de um wrapper de voz/HeyGen com checkpoint durável validado
pode retornar `completed` diretamente, sem nova tentativa/reserva/dispatch. Wrappers sem
checkpoint continuam single-attempt; a API não força retries de mutações parcialmente feitas.

Upload/reserva e geração HeyGen são fases explícitas separadas. Aprovações, cap de renders,
budget e reconciliação continuam obrigatórios; OAuth permanece no CLI. `requestId` de voz/
HeyGen deve ser mantido em retries e renovado para uma nova ação. Profiles são metadados
imutáveis (`201`), sem probe/hash de mídia na request; o worker de plano valida fontes,
música e source antes de produzir o plano. Consulte `/docs` para os bodies de cada ação.

## Painel local (D4B.1 + D4B.2a)

Use Node 22 (>=22.12) e instale o frontend com `npm --prefix web ci`.
Com o banco da campanha já preparado pelos comandos CLI existentes, abra dois terminais:

```powershell
uv run python -m auraly_pipeline.cli api serve --port 8000
```

```powershell
npm run ui:dev
```

Abra `http://127.0.0.1:5173`. Configure `--database`, `--project-root` e `--work-root`
no comando da API quando necessário; o painel consulta o mesmo storage da CLI.
As portas 8000/5173 são fixas nesta etapa. O proxy preserva Host/Origin loopback;
não é necessário habilitar CORS nem expor a API na rede.

O painel mostra metadados, status, pendências, erros e Jobs; não serve arquivos de mídia,
thumbnails ou players. Leituras não executam providers. Start exige confirmação e pode
consumir créditos se houver Jobs pagos já aprovados na fila. Stop impede novas claims e
deixa o Job ativo terminar; não cancela a geração nem devolve créditos. Um worker de
outra campanha aparece como desconhecido, não como idle. POSTs não são repetidos
automaticamente; falhas de resposta exigem reconciliação pelas leituras de status.
Campanhas/copy e import/review de imagens já usam os formulários abaixo.
OAuth, voz/HeyGen e configuração editorial continuam pela CLI/API.

### Campanha, copy e batch manual de imagens

1. Na lista, abra **Criar campanha**. Preencha copy e cenas iniciais, informe o ator e
   confirme a aprovação. A headline é visual; somente hook/body/CTA formam a narração.
   Novas versões de copy preservam o histórico. Presets são referências, não autorização de gasto.
2. Na campanha, em **Batch manual de imagens**, informe uma pasta relativa ao work root
   (por exemplo `imports/campanha-01`) e clique **Preparar pasta**.
3. Inicie explicitamente o worker **Operações locais** e confirme. Espere a pasta preparada.
   Copie as imagens pelo Explorer para o path `.../images` exibido, relativo ao project root
   da API. O work root padrão é `pipeline/work`; roots personalizados mudam esse prefixo.
4. Preencha **Arquivo de VARIANTE** para cada cena, por exemplo `images/foto 01.png`.
   Os nomes podem ter espaços/Unicode. Não edite `image-import.json` e não há associação
   inferida pelo nome. Clique **Salvar associações**; o worker publica outro manifest por hash.
5. Clique **Validar batch** e execute o worker quando parado. O dry-run mostra fatos reais
   ou erros; job concluído não significa batch válido. Corrija os arquivos/associações e
   valide de novo explicitamente. Cada nova validação lê novamente os arquivos.
6. Com **Batch válido**, confirme e clique **Importar batch validado**. Inicie o worker
   se necessário. Alterar associações invalida a validação; alterar bytes da imagem bloqueia
   a execução até nova validação. Sources nunca são movidos ou sobrescritos.
7. Após importar, selecione cada candidato, ator e ação de review. Aprovar/substituir exige
   confirmação; rejeitar exige motivo. Importar não aprova automaticamente.

O worker pode terminar ao esvaziar a fila: repita seu início explícito entre etapas.
Adicionar arquivos à pasta **não** dispara automação. Sem watch folder, upload, file picker,
thumbnail ou player. Depois de reload, consulte um job de publicação e **Usar associações
salvas**; uma nova validação é obrigatória. Resposta perdida bloqueia novas escritas até
inspeção/reconciliação por GET, sem repetir POST. A consulta de imagens confirma estado,
não autoria da revisão. Rascunhos não salvos pedem confirmação ao sair e não são persistidos.

Verificação do frontend: `uv run python scripts/verify.py ui`.
O gate `full` instala/testa/builda/audita o frontend antes dos testes Python.
Pare os servidores de desenvolvimento antes do gate: os testes de integração usam essas
portas e falham se estiverem ocupadas, sem encerrar processos de terceiros.

## Operação pela interface

### Voice Master na interface (D4B.2b.1)

Dentro da campanha, em **Voice Masters**:

1. Selecione explicitamente uma versão aprovada da copy. A headline é visual;
   somente hook/body/CTA compõem a narração.
2. Para gerar: configure o orçamento inicial (moeda de três letras maiúsculas e
   limite em centavos), informe Voice ID/Model ID do ElevenLabs, responsável e teto
   da geração. Confirme o gasto e enfileire. Inicie `voice_generate` no controle de worker.
3. Para importar: copie o MP3/WAV (até 100 MiB) para dentro do project root pelo Explorer
   e informe o caminho relativo, por exemplo `imports/voice.wav`. Não exige budget.
   Enfileire, inicie `local_operations`, observe o Job filho e inicie `voice_import`.
4. Consulte os fatos persistidos. Ouça o WAV processado fora do painel, usando o
   caminho relativo e o work root configurado no servidor; não há player/upload.
5. Selecione a voz, responsável e confirmação de escuta/revisão. Enfileire aprovação
   ou rejeição (motivo obrigatório) e inicie `local_operations`. Aprovação só é fato
   após execução e nova consulta. A exceção de transcrição importada exige motivo e
   não permite ignorar headline falada, mismatch ou outros findings de QC.

Budget é configuração inicial, não saldo/estimativa ou autorização automática; limite/moeda
já definidos não são editáveis aqui. Cada start processa todos os Jobs elegíveis daquele
tipo na campanha. Nada inicia ao abrir/recarregar. A mesma identidade pode reutilizar
voz/Job: não há regeneração forçada. Resultado perdido/inválido fica desconhecido e
não é reenviado; consulte Jobs e operações conhecidos explicitamente. Coincidência de
copy/provider não identifica uma request perdida e consulta não prova autoria.

### HeyGen na interface (D4B.2b.2)

Na seção **HeyGen**, use todas as cenas com uma imagem aprovada por cena e uma única
voz aprovada. O backend resolve o material e a conta conectada pelo MCP/OAuth da CLI.

1. **Preparar assets HeyGen** cria um wrapper; inicie `local_operations`. Se houver
   Job de upload, inicie `heygen_assets` e confira o filho, não apenas o wrapper.
2. Informe o **limite total de renders reservados da campanha**, incluindo históricos,
   failed e blocked. É quantidade, não moeda/saldo. Planeje e inicie `local_operations`.
3. Confira novos/reuso/histórico. Informe responsável e marque autorização paga.
   Enfileire a geração; inicie `local_operations` para criar reservas, depois
   `heygen_videos` para despachar/pollar/baixar. Cada start processa os Jobs elegíveis
   daquele tipo na campanha; não é limitado ao wrapper recém-criado.
4. Confira os renders pelo GET: caminho relativo ao work root, hashes, bytes e probe.
   Abra o MP4 no Explorer. Não há player, media serving ou download no browser.
5. Para reconciliar, selecione um render com Job blocked. ID conhecido é readonly;
   ID novo exige vínculo manual explícito. Sem ID, somente o backend prova no-dispatch
   ou recusa a retomada. Inicie `local_operations`, confira render/Job atual (pode ser
   Job de recuperação) e inicie `heygen_videos` explicitamente quando necessário.

Config fixa: image/provider_default, 9:16/1080p/MP4, cover, medium, motionPrompt null,
concorrência 2, polling 10→60 s até 1800 s. O plano é informativo, não token/snapshot
atômico: a submissão recalcula no backend. Mudanças de material/limite invalidam a
confirmação; timestamps de polling não. Unknown preserva intenção/draft e bloqueia
repost; inspecionar Job conhecido não prova autoria de uma resposta perdida.
Nada inicia ao abrir/recarregar, nem há retry automático de POST ou autenticação na UI.

### Profiles de edição pela UI — D4B.3a

Checkpoint D4B.3a: `IMPLEMENTED`, `LOCAL_VERIFIED`, gate 19/19, 275 frontend
e 1.800 Python / 25 skips. Integrado em `main` no commit `f3ac38a`.
Essas contagens são históricas; a evidência D4B.3b registra a verificação da nova branch.
Abra **Profiles de edição** na navegação global (`#/profiles`). Use **Novo profile**
para criar a versão 1; selecione uma versão publicada e **Criar nova versão**
para editar sua cópia e publicar base + 1. Versões anteriores não são alteradas.
O formulário cobre output, headline, legendas, música e enquadramento; opções
avançadas ficam recolhidas. Salvar exige ação explícita e confirmação via API.

Fontes/música usam caminho relativo ao project root e SHA-256 conhecido. Não há
upload ou catálogo neste slice. Publicar um profile valida seus metadados, não
a existência dos arquivos: disponibilidade/hash/tipo são verificados na preparação
da edição. Música ainda exige aceitação por edição; nenhum timing é inventado.
Profile não contém texto de headline de campanha nem source MP4.

Resultado desconhecido oferece **Consultar versão enviada**, sem reenviar POST.
Só ausência consultada permite tentativa explícita do mesmo payload, com outra
consulta prévia. Conteúdo conflitante não é sobrescrito ou renumerado. Rascunhos
ficam em memória, com aviso antes de descartar; não sobrevivem a reload.
Versões publicadas podem ser consultadas novamente após reload.

### Edição e variantes pela UI — D4B.3b

Na campanha, abra **Edição e variantes**:

1. Selecione um MP4 HeyGen `ready` e uma versão publicada do profile. O ID editorial
   começa com o ID do render; informe a headline base manualmente.
2. Configure overrides da campanha, do vídeo e de cada variante explícita.
   **Herdar** omite o campo; **Substituir** envia o valor; **Limpar** envia null
   somente quando permitido. A precedência é profile → campanha → vídeo → variante.
3. A variante inicial é `a`/A; adicione B/C e altere o texto da headline.
   O limite inicial é três saídas, ajustável; não há combinação cartesiana.
4. Clique **Validar plano** (`persist=false`) e inicie o worker `local_operations`
   nos controles existentes. A validação cria/reutiliza Job e audit locais,
   mas não publica plano ou mídia. O backend valida referências e configuração.
5. Depois de validar, clique **Salvar plano** (`persist=true`) e inicie o worker
   novamente. Sucesso exige revalidação do mesmo request/plano e GET exato do arquivo.
   Editar o rascunho invalida a validação anterior.

Fonte/música/timing usam caminhos relativos ao project root e SHA-256 explícitos;
não há upload, catálogo ou cálculo de hash pelo browser. Música exige aceite
separado, limpo ao trocar fonte/profile/referência de música. Legendas seguem a copy
vinculada à voz aprovada; sem timing, ficam pendentes, não sincronizadas.
Espaços nas bordas dos campos editoriais são normalizados antes da submissão,
alinhando o payload ao armazenamento dos Jobs locais.

Rascunhos ficam somente em memória, com confirmação de descarte. Planos salvos
podem ser consultados readonly após reload, sem preencher automaticamente o draft.
Resposta perdida mantém o envio congelado: consultar Job conhecido/artefato não
reenvia POST, e um arquivo existente não prova autoria do POST sem Job confirmado.
Abandonar acompanhamento não cancela o Job no backend.

Um plano reutiliza MP4/WAV/imagem/profile, não regenera assets nem renderiza vídeo.
D4B.3c mostra um frame do MP4 com overlays aproximados, em Rascunho, Plano validado
ou Plano salvo readonly. Escolha a origem e a variante no preview; o rascunho não
habilita Salvar e a consulta não sobrescreve seus campos. A/B troca texto sem
regenerar voz, imagem ou HeyGen. O frame é buscado por campanha/render/hash,
apenas ao trocar a fonte ou usar Recarregar frame; falha mantém fundo neutro avisado.
Fonte de sistema é fallback; caption de rascunho é demonstrativa, enquanto planos
usam a primeira cue ou as primeiras doze palavras do captionInput armazenado.
Avisos de overflow/safe zones permanecem fora do canvas; preview cabe em até
360 × 640 px preservando a proporção configurada. Não simula shrink/error,
highlight, zoomEnd, timing ou áudio. D5 continua responsável pelo render final.
Sem timeline CapCut ou preview frame-perfect.
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

## Interface local — alvo completo

D4B.1 entrega o painel descrito acima. O alvo completo React + FastAPI em `127.0.0.1`
inclui telas simples para:

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
- Node 22 (>=22.12)/npm para o painel e o ambiente de renderer existente;
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
