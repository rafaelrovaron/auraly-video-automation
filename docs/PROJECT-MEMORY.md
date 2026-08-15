# Auraly Mass Video Pipeline — Memória do Projeto

**Status:** documento vivo
**Última consolidação:** 2026-08-15
**Projeto:** `<AURALY_ROOT>/pipeline`
**Responsável de produto:** Rafael Rovaron
**Uso:** contexto permanente para humanos, agentes de IA e futuras sessões de implementação.

> Este documento registra decisões duráveis, convenções e aprendizados. Estados operacionais de campanhas, IDs remotos, tokens, URLs assinadas e progresso temporário pertencem ao banco de dados e aos manifests de cada job, não a este arquivo.

---

## 1. Visão

Construir uma aplicação local para produção em massa de Reels orgânicos do Auraly. A unidade central será uma **campanha** composta por:

- uma copy aprovada;
- uma headline visual;
- uma voz master gerada e tratada uma única vez;
- um personagem/perfil social;
- várias cenas em locais diferentes;
- uma imagem aprovada por cena;
- um talking avatar HeyGen Avatar III por imagem;
- uma edição determinística por variante;
- QC técnico e revisão visual antes da publicação.

Exemplo de fan-out:

```text
Copy aprovada + Voice Master
  ├── Lavanderia 24 horas → imagem → HeyGen → edição → QC
  ├── Restaurante vazio   → imagem → HeyGen → edição → QC
  ├── Estação de metrô    → imagem → HeyGen → edição → QC
  └── Supermercado        → imagem → HeyGen → edição → QC
```

O objetivo é que a IA decida parâmetros criativos, chame a aplicação e interprete os relatórios. A aplicação deve executar as operações mecânicas de maneira repetível, retomável e auditável.

---

## 2. Princípio de separação de responsabilidades

### 2.1 Responsabilidades da IA

- pesquisar referências e padrões já validados;
- definir ângulo, carta, cenário, personagem e headline;
- escrever e revisar a copy;
- criar a matriz de variantes e os prompts do Google Flow;
- avaliar fidelidade visual, anatomia, semântica e identidade;
- recomendar aprovação, rejeição ou regeneração;
- avaliar interpretação emocional da voz quando necessário;
- analisar exceções que não podem ser resolvidas por regras;
- chamar a aplicação local e interpretar JSONs de resultado.

### 2.2 Responsabilidades da aplicação

- criar e versionar campanhas, variantes e jobs;
- validar inputs, paths, formatos, dimensões e hashes;
- gerar voz pela API oficial do ElevenLabs;
- tratar e verificar áudio;
- operar o Google Flow por Playwright;
- operar o HeyGen pelo MCP oficial com OAuth;
- criar/pollar/baixar talking avatars Avatar III;
- criar captions, headline, zooms e mixagem;
- renderizar por FFmpeg/renderer adapter;
- executar QC técnico;
- gerar proxies, crops, contact sheets e relatórios;
- entregar para pasta sincronizada;
- persistir estado e retomar após falhas.

### 2.3 Gates humanos obrigatórios

1. aprovação da copy e headline;
2. aprovação da Voice Master;
3. aprovação individual das imagens;
4. aprovação do primeiro vídeo canário da campanha;
5. aprovação final das variantes destinadas à publicação.

O QC técnico pode ser automático; decisões de marca e qualidade visual permanecem humanas/IA.

---

## 3. Decisões tecnológicas

### 3.1 Arquitetura

- monólito modular orientado a jobs;
- arquitetura hexagonal/ports and adapters;
- state machine persistente por campanha e variante;
- pipeline modelada como DAG com fan-out e fan-in;
- backend, workers, CLI e interface usando a mesma camada de aplicação;
- nada de microserviços no MVP.

### 3.2 Stack recomendada

#### Backend

- Python `>=3.11,<3.12`;
- FastAPI;
- Pydantic;
- SQLAlchemy 2;
- Alembic;
- Typer;
- HTTPX;
- pytest, Ruff e mypy;
- logging estruturado em JSON.

#### Persistência

- SQLite em modo WAL no MVP;
- PostgreSQL somente se houver necessidade multiusuário/multimáquina;
- mídia no filesystem, nunca como BLOB no banco;
- banco armazena paths, IDs, estados, hashes e metadados.

#### Frontend

- React + TypeScript;
- Vite;
- TanStack Query;
- React Router;
- componentes acessíveis, preferencialmente shadcn/ui ou equivalentes;
- Server-Sent Events no MVP para atualizações em tempo real.

#### Mídia

- FFmpeg e ffprobe como base mecânica;
- faster-whisper small.en CPU INT8 para ASR local quando necessário;
- ASS/libass ou renderer adapter para captions;
- HyperFrames permanece atrás de um adapter substituível.

#### Distribuição

- web app local em `127.0.0.1` primeiro;
- Tauri poderá empacotar a aplicação futuramente;
- não iniciar com Electron ou microserviços.

---

## 4. Integrações obrigatórias

### 4.1 HeyGen

**Obrigatório:** MCP oficial do HeyGen com OAuth gerenciado pelo Hermes.

Fluxo esperado:

```text
OAuth/MCP preflight
→ create_asset_upload
→ PUT com headers assinados exatos
→ complete_asset_upload
→ create_photo_avatar
→ poll get_avatar_look
→ validar supported_api_engines
→ create_video_from_avatar
→ poll get_video
→ download e QC
```

Regras duras:

- usar explicitamente `engine.type = avatar_iii`;
- nunca depender do engine default;
- bloquear se o look não listar `avatar_iii` em `supported_api_engines`;
- persistir IDs remotos antes de avançar;
- não repetir POST pago em estado ambíguo;
- nunca armazenar token OAuth, cookies ou URLs assinadas no job;
- o OAuth fica no armazenamento MCP/Hermes;
- não usar automação web no HeyGen quando o MCP oficial estiver disponível.

Aprendizado de upload S3:

- usar os headers retornados pelo HeyGen como fonte principal;
- tratar nomes de headers sem diferenciar maiúsculas/minúsculas;
- não duplicar `Content-Type`/`content-type`;
- não adicionar headers `x-amz-*` que não estejam assinados;
- verificar `complete_asset_upload` antes de criar look ou vídeo.

### 4.2 ElevenLabs

**Obrigatório:** API oficial do ElevenLabs, nunca automação web na pipeline de produção.

Responsabilidades do adapter:

- TTS com voice ID e model ID explícitos;
- múltiplas alternativas configuráveis;
- captura de metadados e consumo quando disponível;
- download direto;
- timestamps/alinhamento quando disponíveis;
- STT opcional para validar o áudio final;
- tratamento de rate limit e retry seguro.

Para a mesma copy, gerar uma Voice Master e reutilizá-la em todas as variantes. O audio asset do HeyGen também deve ser reutilizado quando possível.

### 4.3 Google Flow

**Decisão definitiva:** Google Flow + Playwright Python é o único caminho ativo de geração de
imagens. Não existe provider alternativo em paralelo. Google AI Studio não faz parte da
arquitetura ativa e não deve aparecer como provider, URL, skill ou workflow suportado.

Decisões:

- Playwright Python no MVP;
- worker dedicado;
- perfil Chromium persistente e isolado;
- login manual inicial;
- não usar o perfil pessoal principal do Chrome;
- download sempre em 2K;
- concorrência inicial igual a 1;
- capturar screenshot/checkpoints;
- gerar trace em falhas;
- centralizar seletores em módulo versionado;
- parar com `human_intervention_required` quando a UI mudar em vez de clicar por coordenadas incertas.

Fluxo canônico:

```text
AI/Hermes prompt
→ Google Flow
→ Playwright
→ candidatas
→ download 2K
→ QC
→ review
→ approve/reject/regenerate
```

A IA/Hermes escreve os prompts e toma as decisões criativas. A aplicação executa os prompts e
as transições de estado mecanicamente, com evidências, auditoria e retomada. Roles, labels,
texto e atributos DOM verificáveis têm preferência; mudanças inesperadas na UI exigem parada
segura, screenshot e trace, nunca continuidade por cliques cegos em coordenadas.

O MVP precisa detectar slots/estado das candidatas e preservar screenshot/evidência da grade.
Não precisa baixar todas as candidatas visíveis se isso tornar o browser frágil. Cada candidata
intencionalmente baixada deve ser preservada sem overwrite, receber seu próprio registro e manter
o histórico de aprovação/rejeição. Baixar todas as candidatas permanece enhancement, salvo se
vier a ser mecanicamente necessário.

### 4.4 Entrega

No MVP, copiar para a pasta local sincronizada com Google Drive, verificar tamanho e SHA-256 do destino e declarar apenas “copiado para a pasta sincronizada”. Não afirmar upload na nuvem sem evidência do sync ou da API.

---

## 5. Modelo de domínio

### Campaign

Agrupa decisões e recursos compartilhados:

- conta/personagem;
- Copy Master;
- Voice Master;
- carta/objeto de prova;
- headline;
- presets de edição;
- música;
- orçamento;
- conjunto de Scene Variants.

### CopyMaster

- texto aprovado e imutável;
- headline visual separada da narração;
- hook, body e CTA;
- hash e versão;
- status de aprovação.

### VoiceMaster

- áudio bruto e processado;
- voice ID, model ID e parâmetros;
- transcript reconhecido;
- duração, WPM, LUFS, true peak e hash;
- aprovação humana.

### SceneVariant

Uma ramificação visual da campanha:

- local;
- horário;
- ação;
- objeto/carta;
- prompt;
- candidatas do Flow;
- imagem aprovada;
- look HeyGen;
- source MP4;
- edit manifest;
- renders e QC.

### ImageGeneration

Representa uma operação lógica de geração no Google Flow, mesmo quando nenhum arquivo foi
persistido. Deve vincular Campaign, SceneVariant e Job e preservar generation number, snapshot/hash
do prompt, referência/hash, provider/executor, provider state, dispatch timestamp e timestamps de
auditoria. Esta entidade permite distinguir uma geração iniciada de uma candidata baixada e evita
regeneração cega após falha ambígua do browser.

### ImageCandidate

- pertence a uma `ImageGeneration` e representa um arquivo/artefato resultante;
- arquivo original preservado e índice da candidata;
- dimensões, hash e metadados;
- crops/contact sheet;
- status de revisão;
- desvios conhecidos;
- nunca sobrescrever versões rejeitadas.

### RemoteAsset / AvatarLook / HeyGenRender

Recursos remotos duráveis com IDs persistidos. IDs não devem ser inferidos por nome de arquivo.

### EditManifest

Fonte de verdade editorial renderer-neutral:

- headline;
- captions;
- música;
- zoom/punch-ins;
- safe zones;
- duração;
- render settings;
- hashes dos inputs;
- status de revisão.

---

## 6. Estados

Cada classe de estado tem um único owner:

```text
Job.status
= estado de execução/orquestração

ImageGeneration.provider_state
= estado da operação no Google Flow

ImageCandidate.review_status
= estado do artefato/revisão
```

`SceneVariant.status` não deve duplicar detalhadamente essas fontes de verdade. Quando um estado
global de progresso for necessário, ele deve preferencialmente ser derivado das entidades
persistidas. A função derivadora e sua taxonomia pertencem ao design do Goal que precisar delas.

Vocabulário mínimo distribuído entre os owners acima (não uma enumeração única de
`SceneVariant.status`):

```text
not_started
queued
running
completed
failed
blocked
retry_scheduled
human_review_required
human_approved
human_approved_with_known_deviations
rejected
superseded
cancelled
```

Projeção derivada recomendada de progresso da variante (não persistida como segunda state
machine):

```text
not_started
→ prompt_ready
→ flow_queued
→ flow_generating
→ image_downloaded
→ image_qc_pending
→ human_review_required
→ image_approved
→ heygen_look_queued
→ heygen_look_processing
→ heygen_video_queued
→ heygen_video_processing
→ source_video_ready
→ edit_queued
→ rendering
→ technical_qc
→ final_review_required
→ approved
→ delivered
```

Os rótulos de execução (`queued`, `running`, `retry_scheduled`, `failed`, `blocked`) derivam de
`Job`; os rótulos Flow/HeyGen derivam do estado persistido da operação provider; os rótulos de
QC/review/approval derivam dos artefatos correspondentes. `SceneVariant.status`, enquanto existir,
é no máximo metadado agregado/coarse e nunca autoritativo para esses detalhes. A taxonomia e a
função de projeção exatas só serão introduzidas pelo Goal que delas precisar.

Toda transição deve:

1. validar precondições;
2. registrar início;
3. executar uma operação idempotente ou com proteção contra duplicidade;
4. persistir IDs/resultados imediatamente;
5. verificar output real;
6. registrar evento;
7. avançar estado somente após verificação.

---

## 7. Produção em massa

O pipeline deve compartilhar recursos por campanha:

- copy: uma vez;
- voz: uma vez;
- processamento de áudio: uma vez;
- upload do áudio para HeyGen: uma vez quando reutilizável;
- fonts, música e presets: compartilhados;
- imagem, look, vídeo e render: por variante.

Concorrência inicial:

```yaml
google_flow: 1
elevenlabs: 1
heygen: 2
local_render: 2
```

Ações pagas precisam de budget gate. Um resultado remoto ambíguo nunca autoriza retry cego.

---

## 8. Convenções editoriais Auraly

### Gerais

- conteúdo orgânico; não vender/nomear diretamente o app no Reel;
- objetivo é levar ao quiz/leitura gratuita de um minuto;
- headline visual não é falada;
- uma imagem por Reel;
- movimento apenas por talking avatar e zooms discretos;
- música sutil;
- captions centralizadas na faixa âmbar do canvas;
- headlines somente:
  - fundo branco + texto vermelho;
  - fundo branco + texto preto;
  - fundo vermelho + texto branco.

### Soul Constellation

- única conta masculina;
- identidade canônica: Avatar 003;
- voz oficial: Michael C. Vincent;
- entrega firme/dinâmica, não calma/lenta;
- sem southern-auntie slang;
- CTA aponta para Stories e primeiro comentário fixado;
- uma prova/objeto de autoridade ligado ao roteiro, sem clutter místico aleatório.

### Google Flow

- imagens sociais sempre 9:16;
- downloads sempre 2K, nunca 1K;
- preservar todas as versões intencionalmente baixadas, sem exigir download de toda candidata
  visível;
- registrar rejeições e desvios conhecidos.

---

## 9. QC obrigatório

### Imagem

Automático:

- formato e corrupção;
- resolução e proporção;
- hash;
- OCR indicativo;
- faces extras indicativas;
- crops de rosto/mãos/objeto;
- contact sheet.

Semântico por IA/humano:

- identidade;
- fidelidade da carta/objeto;
- anatomia;
- cenário;
- pessoas extras;
- marcas/textos;
- safe zones;
- coerência com a copy.

### Voz

- duração;
- WPM;
- silêncios;
- LUFS;
- true peak;
- clipping;
- transcript versus copy aprovada;
- headline ausente da narração;
- audição humana da Voice Master.

### HeyGen source

- engine Avatar III confirmado;
- MP4 íntegro;
- resolução/FPS/codec;
- áudio presente;
- full decode;
- estabilidade visual por frames;
- rosto, boca, mãos e objeto;
- sem drift relevante de duração.

### Render final

- 1080×1920;
- H.264 + AAC;
- FPS constante;
- faststart;
- full decode;
- captions dentro da safe zone;
- headline correta e não narrada;
- música sem mascarar voz;
- loudness e true peak medidos no render final;
- contact sheet e proxy;
- hash do master.

Regra FFmpeg crítica:

```text
amix=inputs=2:duration=first:dropout_transition=0:normalize=0
```

O `amix` normalizado por default pode reduzir a narração em aproximadamente 6 dB. Um render com peak/loudness inesperadamente baixo deve falhar no QC.

---

## 10. Interface de gestão desejada

Web app local em `127.0.0.1`, com:

- dashboard de campanhas;
- wizard de campanha;
- editor de copy/headline;
- Voice Studio;
- matriz de locais/variantes;
- galeria de candidatas do Flow;
- aprovação/rejeição/regeneração em lote;
- fila do HeyGen;
- visualização de jobs e logs;
- editor de presets;
- revisão de vídeo;
- relatórios de QC;
- entrega e histórico.

CLI, frontend e ferramenta Hermes devem chamar a mesma camada de aplicação:

```text
CLI ──────────────┐
React/FastAPI ────┼── Application Services → Orchestrator → Workers
Hermes Tool ──────┘
```

A lógica não pode ser duplicada na interface.

---

## 11. Integração com Hermes

A aplicação deve oferecer um contrato local estável, inicialmente por CLI JSON e depois por HTTP/tool wrapper.

Ações desejadas:

```text
create_campaign
run_campaign
get_status
approve_assets
reject_assets
resume
pause
cancel
render_batch
deliver_batch
```

O Hermes/IA deve receber respostas compactas, estruturadas e sem secrets. Saídas grandes ficam em relatórios locais referenciados por path.

---

## 12. Estado atual do repositório

O repositório já existe e não deve ser recriado:

```text
<AURALY_ROOT>/pipeline
```

Implementado atualmente:

- projeto Python `auraly-video-pipeline` versão `0.1.0`;
- Python 3.11 com uv;
- Pydantic e Typer;
- contrato inicial de `edit.json` e JSON Schema;
- parser da copy canônica;
- ingestão não destrutiva;
- ffprobe JSON;
- CLI `auraly`;
- Campaign Foundation: contratos Pydantic para `Campaign`, `CopyMaster` e `SceneVariant`;
- SQLite em WAL com SQLAlchemy 2, migração Alembic, repository e application service;
- CLI JSON `campaign create/get/list`, sem lógica de negócio nos comandos Typer;
- IDs, timestamps UTC, uniqueness, restart persistence e três ou mais variantes por campanha;
- `CopyMaster` com source preservado, SHA-256 canônico, versão e aprovação;
- headline excluída de `spokenText` e triggers SQLite que impedem update/delete de copy aprovada;
- metadados de budget/config rejeitam chaves de secrets, tokens, cookies e URLs assinadas;
- Persistent Job Orchestration: `Job`, `JobAttempt` e `JobEvent` persistidos em SQLite;
- state machine explícita `queued/running/completed/failed/blocked/retry_scheduled/cancelled`;
- fila local com claim atômico, leases renováveis, heartbeat automático durante handlers, fencing por attempt number, recovery de lease expirado e eventos append-only;
- idempotency key global única com fingerprint do contrato e conflito para reuse incompatível;
- retry safety persistida (`idempotent`, `manual_only`, `reconcile_before_retry`) e validada contra a capability do handler;
- retries lineares determinísticos, max attempts, falhas retryable/terminal e cancelamento seguro;
- CLI JSON `job submit/get/list/worker-once/cancel/resume/recover` e handlers fake mais o handler real `voice.generate`;
- Voice Master campaign-level ligado a uma versão aprovada de CopyMaster, com lifecycle `pending/generating/processing/review_required/approved/rejected/failed`;
- migration `0003_voice_master`, um VoiceMaster aprovado por Campaign, finais imutáveis e regenerações históricas sem overwrite;
- ElevenLabs somente pela API oficial `/v1/text-to-speech/{voice_id}/with-timestamps`, com voice/model/output explícitos e `ELEVENLABS_API_KEY` somente no ambiente do worker;
- `voice.generate` usa `reconcile_before_retry`, chave lógica determinística e nunca recebe texto arbitrário ou headline no Job;
- raw MP3 preservado por criação exclusiva e aceito somente com contagem de frames Xing/Info/VBRI verificável; WAV PCM 24-bit mono 48 kHz separado, processado com FFmpeg e medido para duração, LUFS, true peak e silêncios;
- transcript prefere alignment ElevenLabs e cai para faster-whisper small.en CPU INT8; QC compara tokens e detecta headline falada;
- CLI JSON `voice generate/get/list/approve/reject/resolve-no-artifact`, com aprovação humana obrigatória e flag explícita para ação paga;
- o request pago exige `budget.limitCents > 0`, `budget.currency` autoritativa em três letras maiúsculas, `--approve-paid-request`, operador explícito e `--approved-budget-cents` não superior ao limite da Campaign; o evento append-only registra operador, timestamp, teto aprovado, limite e moeda;
- VoiceMaster aprovada bloqueia nova logical generation paga antes de VoiceMaster/Job/evento; criação do trio e decisão concorrente com approval são serializadas em uma transação SQLite, replay lógico exato permanece idempotente e replacement continua fora de escopo;
- testes unitários/smoke;
- faster-whisper small.en local;
- FFmpeg/ffprobe;
- HyperFrames `0.7.104`, versão stable validada e fixada no lockfile, atrás de adapter;
- Auto-Editor instalado e verificado.

Parcialmente implementado para imagens:

- contratos e schema Google Flow v1.1;
- preparação mecânica de requests e diretórios de inspeção;
- trusted project root e trusted downloads root;
- validação de paths/contexto, detecção de downloads e partial downloads;
- finalização não destrutiva, manifests e erros públicos sanitizados.

Ainda planejado/não validado: runtime Playwright que abre o Flow, usa o perfil Chromium
dedicado, verifica seletores, gera candidatas, confirma o download 2K, produz trace e executa o
gate de QC/review. A existência dos contratos não deve ser interpretada como automação Flow
funcional.

Configuração operacional da Campaign Foundation e da Persistent Job Orchestration:

- banco padrão fora do repositório: `~/.auraly/auraly.db`;
- artefatos de Campaign usam o root canônico `<AURALY_PROJECT_ROOT>/pipeline/work`, compartilhado por Voice Master e preparação de imagem, sem mover o SQLite;
- override por `AURALY_DATABASE_PATH` ou `--database`;
- migrations executadas antes do acesso pela application service e serializadas por lock de arquivo entre processos;
- banco guarda somente estado/metadados, nunca mídia BLOB, secrets, cookies, profiles ou URLs assinadas;
- revisões de CopyMaster aprovado são novos inserts versionados; versões aprovadas não aceitam update/delete;
- jobs guardam apenas JSON seguro e metadados pequenos; mídia/BLOB, data URLs, credentials e URLs assinadas são rejeitados;
- jobs podem referenciar Campaign e, opcionalmente, SceneVariant, sem exigir referência para jobs globais; triggers rejeitam pares incompatíveis;
- uma tentativa `running` pode ser finalizada uma vez e então se torna imutável; tentativas finalizadas e eventos não aceitam update, delete ou replace;
- `worker-once` recupera leases expirados, ativa retries vencidos e executa no máximo um handler local;
- durante o handler, heartbeat renova o lease com o mesmo worker/attempt e encerra em `finally`; perda de fencing impede completion normal;
- cancelamento de job running é rejeitado; este Goal não simula interrupção arbitrária de operação ativa;
- completion/renewal exigem worker, attempt number e lease ainda válido; attempt number funciona como fencing token;
- recovery registra `job.recovered` e finaliza a tentativa interrompida antes de retry, bloqueio por safety ou falha terminal;
- os Goals 0, 1, 2 e 3 têm produção/testes implementados e baseline local executado; isso não é
  evidência independente de CI nem de provider canary;
- o canário real ElevenLabs permanece pendente como `Goal 3C -- ElevenLabs Provider Canary`;
- o Verification Harness determinístico foi implementado antes de Goal 4A;
- próximo milestone de produto: `Goal 4A -- Image Domain & Persistence`.

Terminologia obrigatória de milestone:

- `IMPLEMENTED`: produção e testes requeridos existem;
- `LOCAL_VERIFIED`: baseline determinístico/local requerido foi executado com sucesso, sem inferir
  execução de provider real;
- `PROVIDER_VERIFIED`: canário real explicitamente aprovado foi concluído com sucesso.

O histórico de commits com `[verified]` não prova por si só verificação independente, CI ou
provider. Estado atual: Goals 0–3 estão `IMPLEMENTED` e `LOCAL_VERIFIED`; Goal 3 não está
`PROVIDER_VERIFIED`; Goal 3C está `PENDING`.

O README antigo descreve a pipeline principalmente como pós-produção de um MP4 do HeyGen. O novo escopo amplia a aplicação para geração em massa end-to-end. A implementação deve preservar compatibilidade com ingest/render existentes sempre que possível.

---

## 13. Lições da produção Eight of Cups / Laundromat

- a imagem precisa de gate semântico mesmo quando o QC técnico passa;
- numeral correto não garante iconografia fiel da carta;
- aprovações com desvios devem registrar exatamente o que foi aceito;
- browser automation manual consome muitas interações; Flow precisa de Playwright;
- ElevenLabs web não serve para escala; usar API;
- MCP HeyGen funciona com OAuth e deve ser encapsulado;
- schemas MCP devem ser cacheados e limitados às ferramentas necessárias;
- upload S3 precisa preservar headers assinados exatamente;
- o áudio processado deve ser validado por transcrição;
- captions devem usar copy aprovada para texto e timing verificado para sincronização;
- solicitar SRT sidecar do HeyGen quando disponível;
- sempre medir loudness do render final, não apenas dos stems;
- `amix normalize=0` deve ser regra do renderer;
- resultados de tentativas antigas não podem sobrescrever o estado atual;
- cada job precisa persistir IDs e tentativas para notificações atrasadas não confundirem a operação.

---

## 14. Princípios de segurança

- nenhum secret no Git, YAML de campanha, banco de job ou logs;
- ElevenLabs API key em secret store local/Windows Credential Manager ou configuração segura fora do projeto;
- OAuth do HeyGen permanece no Hermes/MCP;
- cookies/sessão do Flow ficam no perfil Playwright isolado e ignorado pelo Git;
- URLs assinadas somente em memória durante a operação;
- logs devem ter redaction;
- serviços web do MVP devem escutar apenas em `127.0.0.1`;
- paid actions exigem budget gate;
- finals são imutáveis e nunca sobrescritos.

---

## 15. Escopo do piloto MVP

O piloto de aceitação será:

```text
1 copy aprovada
1 Voice Master
3 locais
3 imagens aprovadas
3 looks com Avatar III
3 vídeos HeyGen
3 renders finais
```

Locais sugeridos:

1. lavanderia;
2. restaurante;
3. estação de metrô.

O piloto deve demonstrar geração Flow por Playwright, ElevenLabs por API, HeyGen por MCP/OAuth, retomada, edição determinística e QC.

---

## 16. Itens deliberadamente fora do MVP

- publicação automática em Facebook/Instagram;
- otimização de performance baseada em métricas reais;
- múltiplos usuários;
- execução distribuída em várias máquinas;
- mobile app;
- timeline NLE completa;
- microserviços;
- troca automática para outro engine HeyGen;
- decisão visual totalmente autônoma;
- automação por web do ElevenLabs ou HeyGen.

---

## 17. Questões abertas para decisão durante o MVP

- limites de budget/créditos por campanha no HeyGen;
- parâmetros ElevenLabs que devem ser editáveis versus fixos por personagem;
- se o primeiro MVP deve usar ASS/FFmpeg ou HyperFrames para captions/headline;
- pasta final exata de entrega sincronizada;
- política de retenção de traces e candidatos rejeitados;
- número de candidatas por local;
- se aprovação da imagem será exclusivamente humana ou IA recomenda + humano confirma;
- regra de seleção da música por campanha;
- porta local definitiva da interface;
- formato do wrapper Hermes: plugin tool HTTP ou CLI JSON na primeira versão.

---

## 18. Documentos relacionados

- `README.md` — capacidades atuais do repositório;
- `INSTALLATION-REPORT.md` — ambiente instalado e validado;
- `docs/PRD-MVP-MASS-VIDEO-AUTOMATION.md` — requisitos do MVP;
- `docs/GOAL-ROADMAP.md` — sequência operacional dos Codex Goals;
- `AGENTS.md` — regras, limites, fontes de verdade e checks para agentes;
- `schemas/edit.schema.json` — contrato editorial existente;
- manifests em `work/<job>/manifest/` — estado e evidência por job.

---

## 19. Execução incremental a partir do Goal 4

Cada feature/subgoal significativo segue:

```text
design/spec
→ revisão do usuário
→ plano de implementação
→ tarefas pequenas e testáveis
→ TDD
→ commits por entrega revisável
→ verificação completa
→ revisão independente
```

Specs vivem em `docs/superpowers/specs/`; planos vivem em `docs/superpowers/plans/`. A sequência
decomposta preserva o produto e a arquitetura, mudando somente a granularidade de execução:

```text
Goal 3C  ElevenLabs Provider Canary
Goal 4A  Image Domain & Persistence
Goal 4B  Google Flow Browser Runtime
Goal 4C  Flow Generation, Download & Recovery
Goal 4D  Image QC, Review & Provider Canary
Goal 5A  HeyGen Preflight & Asset Upload
Goal 5B  Avatar Look & Avatar III Verification
Goal 5C  Video Generation, Polling & Source QC
Goal 5D  HeyGen Provider Canary
Goal 6A  Edit Manifest & Captions
Goal 6B  Deterministic Rendering
Goal 6C  Final QC & Delivery
Goal 6.5 Approval Lifecycle Hardening
Goal 7   End-to-End Canary
Goal 8   Local API/UI
```

O Goal 3C pode ocorrer antes do Goal 5 ou em outro checkpoint adequado, mas deve ocorrer antes de
um pipeline end-to-end depender de Voice Master real. Todo canário de provider continua exigindo
aprovação explícita.

Goal 4 introduzirá gradualmente módulos focados em `images/` e `flow/`, reutilizando o código
compatível de `image_generation.py` sem big-bang rewrite. Quando Goal 4A precisar persistir
atomicamente entidade de domínio + Job + evento, deverá introduzir/reusar um contrato público de
orquestração/transação; não deve multiplicar acesso direto a internals privados do Job como
`self._jobs._repository`. O contrato exato pertence ao design de Goal 4A.

O lifecycle atual de CopyMaster, que efetivamente inicia aprovado, é dívida deliberadamente
deferida. Goal 6.5 deve estabelecer `draft -> human review -> approved` antes do Goal 7, sem levar
essa mudança para Goal 4.

O Verification Harness implementado expõe `scripts/verify.py fast|full`, detecta drift dos schemas
gerados e alimenta CI determinístico em `.github/workflows/verify.yml` com jobs Linux full e Windows
focused. Ele não executa ElevenLabs, Google Flow, HeyGen ou qualquer chamada paga. Uma execução
local `full` bem-sucedida estabelece `LOCAL_VERIFIED`; somente uma execução bem-sucedida no GitHub
estabelece evidência independente de CI. Nenhuma delas estabelece `PROVIDER_VERIFIED`.
