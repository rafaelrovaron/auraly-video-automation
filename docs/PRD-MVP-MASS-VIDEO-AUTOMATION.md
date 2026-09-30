# PRD — Auraly Delivery-First Video Automation MVP

**Versão:** 2.0
**Data:** 2026-09-27
**Status:** aprovado para planejamento incremental

## 1. Resumo executivo

O MVP transforma uma Copy Master em múltiplos vídeos verticais com o mínimo de trabalho manual
repetitivo. Ele reutiliza a Voice Master já automatizada, recebe imagens geradas manualmente,
automatiza HeyGen em lote e permite criar variações editoriais — especialmente testes A/B de
headline — sem pagar novamente por voz, imagem ou vídeo.

A operação acontece em uma interface local simples. Ela gerencia campanhas, assets, HeyGen,
profiles, variants e renders e oferece preview aproximado do layout. Não é um editor de vídeo
genérico e não terá timeline estilo CapCut ou preview frame-perfect.

## 2. Separação entre presente e alvo

### 2.1 Capacidade entregue

- Campaign, CopyMaster e SceneVariant persistentes;
- Jobs locais retomáveis e auditáveis;
- Voice Master via ElevenLabs API;
- WAV processado com trim de silêncio nas bordas, normalização e QC;
- domínio/review de imagens;
- importação batch de imagens manuais, dry-run, provenance e CLI JSON;
- D2A upload/reuso de assets via MCP/OAuth e D2B geração/polling/download via CLI implementados;
  D2B `LOCAL_VERIFIED` (gate Windows 13/13 e revisão independente); HeyGen real não é PROVIDER_VERIFIED;
- automação Google Flow implementada e localmente verificada;
- contrato `edit.json` legado, ingestão e inspeção de mídia;
- CLI e harness de verificação.

### 2.2 Capacidade alvo deste PRD

- uploads e geração HeyGen em batch;
- polling retomável e download dos MP4;
- EditProfile + EditManifest + overrides;
- headline/captions/music/framing configuráveis;
- A/B de headline downstream;
- API FastAPI e UI React local;
- preview aproximado;
- render, QC, review e entrega local.

### 2.3 Capacidade preservada, mas fora do caminho crítico

Google Flow/Playwright permanece no código, sem expansão e sem canário no MVP delivery-first.
Imagens manuais são o único caminho obrigatório de aceitação.

## 3. Problema

O fluxo atual exige muitas operações repetidas e desconectadas:

- associar imagens a variações;
- subir as mesmas classes de assets;
- criar vários vídeos no HeyGen;
- acompanhar status e baixar resultados;
- repetir configurações de headline, legenda, música e enquadramento;
- produzir variações simples de texto sem refazer etapas caras;
- lembrar em qual estágio cada campanha está.

Automatizar geração de imagem antes de fechar esse caminho não resolve o maior gargalo. O MVP
precisa primeiro entregar vídeos utilizáveis de forma rápida e repetível.

## 4. Usuário e contexto

### Operador primário

Rafael, em uma máquina Windows local, operando campanhas Auraly pessoalmente.

### Agente assistivo

Codex/Hermes pode preparar manifests, consultar status e recomendar ações, mas decisões criativas
e aprovações continuam explícitas.

### Contexto operacional

- uma pessoa;
- localhost;
- SQLite + filesystem;
- providers oficiais;
- volume inicial pequeno/moderado;
- prioridade em throughput operacional, não disponibilidade 24/7.

## 5. Objetivos do MVP

### O1 — Reutilizar o que é caro

Uma Copy Master e uma Voice Master por campanha. Cada imagem gera no máximo um source MP4 por
configuração HeyGen, e várias versões editoriais reutilizam esse source.

### O2 — Automatizar HeyGen em escala

Preparar uploads, gerar lote, acompanhar status, retomar após restart e baixar outputs sem repetir
ações pagas concluídas.

### O3 — Tornar edição configurável

Separar defaults reutilizáveis de overrides por campanha, vídeo e output variant.

### O4 — Tornar testes A/B baratos

Trocar texto de headline e outros campos editoriais sem regenerar Voice Master, imagem ou HeyGen.

### O5 — Centralizar a operação

Oferecer uma interface local simples com status, ações e preview aproximado.

### O6 — Entregar outputs confiáveis

Renderizar, medir, revisar e copiar masters sem sobrescrever sources ou versões anteriores.

## 6. Não objetivos

- timeline ou NLE completo;
- preview frame-perfect;
- edição frame a frame ou keyframes livres;
- automação ativa do Google Flow;
- multiusuário, autenticação, RBAC ou acesso LAN;
- cloud hosting ou sync engine próprio;
- publicação automática em redes sociais;
- geração automática de copy;
- decisão criativa totalmente autônoma;
- microservices, Redis, broker externo ou storage remoto obrigatório;
- suporte especulativo a múltiplos providers.

## 7. Métricas de sucesso

O MVP é aceito quando:

- uma campanha real com três imagens manuais produz três MP4 HeyGen;
- cada MP4 gera três headlines, totalizando nove renders locais;
- criar as nove versões editoriais não dispara nenhuma nova ação paga HeyGen;
- restart durante polling não duplica geração;
- Rafael completa o fluxo principal pela UI;
- o preview é suficiente para escolher posição/estilo antes do render;
- sources e versões anteriores permanecem intactos;
- cada output pode ser rastreado aos seus inputs e configuração;
- três masters escolhidos passam QC e são entregues com hash verificado.

Métricas secundárias a registrar no piloto:

- minutos de operação humana por campanha;
- taxa de reuso de assets remotos;
- número de ações pagas evitadas por idempotência;
- tempo do submit ao download;
- quantidade de renders editoriais por source MP4;
- falhas recuperadas sem intervenção manual.

## 8. Jornada principal

### 8.1 Criar campanha e aprovar copy

O operador cria ou abre uma campanha, confere Copy Master e variantes. A headline é visual-only;
hook, body e CTA formam o texto falado.

### 8.2 Gerar e aprovar Voice Master

O sistema usa ElevenLabs, preserva o raw, produz WAV mono/48 kHz, remove silêncio nas bordas,
normaliza e executa QC. A versão aprovada vira o único áudio upstream da campanha.

### 8.3 Gerar imagens manualmente

O operador usa a ferramenta que preferir fora do sistema. A pipeline não depende de como a imagem
foi criada.

### 8.4 Importar imagens em batch

O operador escolhe um manifest/pasta, confere dry-run e importa. Cada arquivo é validado, copiado
e associado explicitamente a uma variante.

### 8.5 Preparar HeyGen

O sistema faz preflight, reutiliza remote assets por hash e sobe apenas imagens/WAV ausentes.

### 8.6 Gerar lote HeyGen

O operador confere quantidade/custo, aprova o batch e acompanha submissão, polling e download. O
progresso de cada variante é independente.

### 8.7 Configurar edição

O operador seleciona um EditProfile, ajusta headline, captions, music e framing e cria output
variants. O preview mostra composição aproximada.

### 8.8 Renderizar e revisar

O sistema resolve um EditManifest por output, renderiza, executa QC e apresenta os arquivos para
approve/reject.

### 8.9 Entregar

Masters aprovados são copiados para uma pasta configurada e verificados por hash.

## 9. Requisitos funcionais

### FR-001 — Preservar Voice Master automatizada

**Prioridade:** P0

- API oficial ElevenLabs;
- texto sem headline;
- raw imutável;
- WAV processado mono/48 kHz;
- trim de silêncio no início e no fim;
- loudness/transcript/QC;
- aprovação humana;
- um asset de áudio reutilizável por campanha.

### FR-002 — Importar imagens em batch

**Prioridade:** P0

- manifest versionado com campaign ID e items;
- associação explícita variant ID → path;
- dry-run sem mutação;
- validação de path, mídia real, dimensão e orientação;
- hash e deduplicação;
- cópia não destrutiva para path versionado;
- falha atômica por default em batch inválido;
- provenance de importação;
- aprovação/seleção explícita;
- replay idempotente.

### FR-003 — Executar preflight HeyGen

**Prioridade:** P0

- integração oficial MCP/OAuth ou API disponível;
- status de conexão e capabilities;
- engine/configuração suportada;
- erro sanitizado e ação recomendada;
- sem automação web.

### FR-004 — Fazer upload/reuso de assets HeyGen

**Prioridade:** P0

- imagens selecionadas e Voice Master WAV;
- upload batch;
- dedupe por provider + kind + hash;
- persistir remote ID;
- reutilizar uma Voice Master entre variantes;
- não persistir signed URLs ou tokens;
- reconciliar estado ambíguo antes de retry.

### FR-005 — Planejar geração HeyGen

**Prioridade:** P0

- dry-run com variantes, assets, reuso e paid actions;
- configuração de avatar/engine explícita;
- limite de renders pagos por campanha;
- confirmação antes do primeiro batch real;
- um logical render por variante/configuração.

### FR-006 — Gerar vídeos em batch

**Prioridade:** P0

- submissão por variante;
- concorrência configurável, default 2;
- persistir video ID antes do polling;
- falha isolada por variante;
- sem retry cego de create-video.

### FR-007 — Poll e retomar

**Prioridade:** P0

- backoff e timeout configuráveis;
- polling pelo video ID persistido;
- resume após restart;
- status claro para queued/processing/completed/failed/blocked;
- reconciliation para resultado ambíguo.

### FR-008 — Baixar e validar MP4

**Prioridade:** P0

- download atômico `.part` → final;
- output versionado sem overwrite;
- hash;
- `ffprobe` e full decode;
- vídeo vertical, streams H.264/AAC aceitos e duração plausível;
- signed URL apenas em memória.

### FR-009 — Gerenciar EditProfile

**Prioridade:** P0

- create/list/get/update-as-new-version;
- defaults de output, headline, captions, music, framing e safe zones;
- nome/versão imutáveis após uso;
- sem texto específico de campanha ou source path.

### FR-010 — Resolver EditManifest

**Prioridade:** P0

- inputs e hashes;
- profile version;
- campaign defaults;
- video override;
- output variant override;
- precedência determinística;
- provenance por seção/campo;
- manifest resolvido imutável e hashável;
- somente campos tipados conhecidos.

### FR-011 — Configurar headline

**Prioridade:** P0

- text;
- style/preset;
- font, weight, size e line height;
- color, stroke/shadow/background;
- anchor/position e safe zone;
- max lines e fit policy;
- start/end;
- sempre visual-only.

### FR-012 — Configurar captions

**Prioridade:** P0

- texto da Copy Master aprovada;
- timing com provenance;
- font/style/color/highlight;
- position/safe zone;
- máximo de linhas;
- headline excluída.

### FR-013 — Configurar music

**Prioridade:** P0

- asset local aprovado;
- volume;
- loop/trim;
- fade-in/fade-out;
- voz como referência de mix.

### FR-014 — Configurar framing

**Prioridade:** P0

- fit/crop;
- scale;
- x/y position;
- zoom sutil opcional;
- sem timeline ou keyframes livres.

### FR-015 — Criar A/B de headline

**Prioridade:** P0

- múltiplas output variants para um source MP4;
- headline text independente por output;
- IDs/filenames determinísticos;
- contador e limite de combinações;
- nenhum job de Voice, image ou HeyGen;
- dry-run lista todos os outputs.

### FR-016 — Renderizar

**Prioridade:** P0

- renderer inicial FFmpeg/ASS;
- master 1080×1920, H.264/AAC, yuv420p, faststart;
- headline, captions, music e framing;
- `amix normalize=0` ou compensação equivalente documentada;
- limiter;
- output versionado;
- idempotência por manifest hash.

### FR-017 — Executar QC final

**Prioridade:** P0

- existência/tamanho/hash;
- `ffprobe` e full decode;
- duração, resolução, FPS e streams;
- loudness, true peak e clipping;
- voz audível;
- bounds de headline/captions;
- contact sheet/proxy.

### FR-018 — Revisar e entregar

**Prioridade:** P0

- approve/reject + comentário;
- master aprovado imutável;
- nova revisão em vez de overwrite;
- delivery para pasta local configurada;
- hash origem/destino;
- não chamar cópia local de upload cloud.

### FR-019 — Operar pela API e UI

**Prioridade:** P0

- API e CLI compartilham application services;
- UI nunca chama provider/FFmpeg ou banco diretamente;
- operações longas criam Jobs;
- status agregado e próximo bloqueio;
- polling HTTP simples suficiente para o MVP.

### FR-020 — Oferecer preview aproximado

**Prioridade:** P0

- canvas 9:16;
- frame/poster do source;
- overlays HTML/CSS para headline/captions;
- aproximação de framing, font, size, color, wrap e position;
- atualização rápida ao editar;
- label permanente “preview aproximado”;
- render final é a referência autoritativa.

## 10. Interface local

### 10.1 Campaigns

- lista de campanhas;
- progresso agregado;
- próximo bloqueio;
- jobs/falhas/reviews pendentes.

### 10.2 Campaign Detail

- Copy Master e Voice Master;
- variantes;
- assets locais/remotos;
- status HeyGen;
- edit variants e renders.

### 10.3 Image Import

- selecionar manifest/pasta;
- dry-run;
- mostrar cobertura e erros por variante;
- confirmar importação.

### 10.4 HeyGen

- preflight;
- assets reused/uploaded;
- batch planned/submitted;
- status por variante;
- resume e download.

### 10.5 Editing

- escolher EditProfile;
- editar overrides;
- criar/reordenar/remover output variants;
- duplicar headline variant;
- preview aproximado;
- contador de renders planejados.

### 10.6 Renders

- queue/progress;
- player/proxy;
- QC;
- approve/reject;
- path e delivery.

## 11. Arquitetura

```text
React/TypeScript
      │ HTTP polling
      ▼
FastAPI routes
      │
      ▼
Application services
      │
      ├── Domain/Pydantic contracts
      ├── SQLite repositories
      ├── persistent Jobs
      └── filesystem workspaces
             │
             ├── ElevenLabs adapter (existing)
             ├── HeyGen official adapter
             └── FFmpeg/ffprobe renderer
```

Google Flow existe como adapter legado pausado e não aparece no happy path obrigatório.

### Regras arquiteturais

- modular monolith;
- routes/components finos;
- provider calls somente em workers/handlers;
- filesystem contém mídia, SQLite contém metadados;
- nenhum framework novo sem necessidade demonstrada;
- reuse dos helpers e domínios existentes antes de criar novos módulos;
- fakes no limite de provider;
- contratos versionados e schemas gerados.

## 12. Modelo de dados alvo

### Já existente e reutilizado

- Campaign;
- CopyMaster;
- SceneVariant;
- VoiceMaster;
- ImageGeneration/ImageCandidate;
- Job/Attempt/Event;
- approval/audit metadata já disponível.

### A introduzir

#### ImageImportBatch

- id/schema_version/campaign_id;
- source manifest hash;
- status;
- items e errors;
- created_at/completed_at.

#### RemoteAsset

- provider/kind/local hash;
- remote ID;
- status;
- sanitized metadata.

#### HeyGenRender

- campaign/variant IDs;
- image/audio remote asset IDs;
- engine/config hash;
- remote video ID;
- source MP4 path/hash;
- status and timestamps.

#### EditProfile

- id/name/version;
- typed defaults;
- schema version;
- created_at.

#### EditVariant

- id/source video ID;
- label;
- typed override sections;
- planned manifest hash;
- status.

#### EditRender

- edit variant ID;
- resolved manifest path/hash;
- renderer version;
- proxy/master path/hash;
- QC/review/delivery status.

O design de cada Goal decide se uma entidade requer tabela própria. Não criar tabelas apenas por
constarem neste modelo conceitual.

## 13. Diretórios alvo

```text
campaigns/<campaign-id>/
  shared/
    copy/
    voice/
    music/
    fonts/
  variants/<variant-id>/
    images/imported/
    heygen/assets/
    heygen/source/
    edit/manifests/
    edit/renders/
    qc/
  reports/
  delivery/
```

Regras: paths relativos em manifests, files versionados, writes atômicos, sources imutáveis e
nenhum secret.

## 14. Requisitos não funcionais

### NFR-001 — Uso local

FastAPI escuta apenas em `127.0.0.1`. Sem autenticação no MVP enquanto não houver exposição de
rede.

### NFR-002 — Retomada

Restart não duplica uploads, gerações pagas, downloads íntegros ou renders pelo mesmo manifest.

### NFR-003 — Idempotência

Keys incluem os inputs que materialmente mudam a operação. Configuração HeyGen diferente cria um
logical render diferente; headline diferente cria apenas outro EditVariant.

### NFR-004 — Não destrutivo

Nunca mover source do usuário, sobrescrever media existente ou reutilizar filename como identidade
sem hash/ID persistido.

### NFR-005 — Segurança mínima obrigatória

- secrets fora de Git/SQLite/manifests/logs;
- trusted roots e path containment;
- signed URLs efêmeras;
- sanitização de erros;
- budget gate;
- no blind retry para ação paga ambígua.

### NFR-006 — Performance percebida

- consultas locais comuns abaixo de 500 ms como alvo;
- operação longa vira Job;
- UI atualiza por polling em intervalo curto;
- preview responde sem render real.

### NFR-007 — Testabilidade

- unit tests para contratos e resolução;
- integration tests com fake HeyGen e mídia sintética;
- nenhuma chamada paga em CI;
- canaries separados e aprovados.

### NFR-008 — Compatibilidade

- Windows 10/11 x64;
- Python 3.11;
- versões travadas do ambiente existente;
- paths Windows tratados explicitamente.

## 15. Políticas de retry

### Retry automático permitido

- GET/status;
- polling;
- download incompleto quando a mesma referência ainda é válida;
- QC local;
- render local para novo path versionado;
- upload somente quando o provider garante idempotência ou a ausência foi reconciliada.

### Retry automático proibido em estado ambíguo

- ElevenLabs generation;
- HeyGen asset creation;
- HeyGen create-video;
- qualquer paid action;
- Google Flow Generate legado.

## 16. Estratégia de testes

### Unitários

- image batch validation/idempotency;
- asset dedupe;
- budget e retry policy;
- EditProfile/Manifest validation;
- override precedence;
- A/B planning sem upstream jobs;
- filename/hash/versioning;
- log redaction.

### Integração local

- fake HeyGen;
- simulated upload endpoint;
- polling/restart;
- synthetic MP4/WAV/images;
- FFmpeg render + full decode;
- FastAPI test client;
- React tests para principais estados e preview controls.

### Canary real

1. ElevenLabs curto, caso ainda não esteja provider-verified;
2. HeyGen com uma imagem/Voice Master;
3. piloto de três variantes;
4. nove renders locais e três deliveries.

## 17. Critérios de aceite

### Produto

- [ ] batch de três imagens importado e associado corretamente;
- [ ] Voice Master WAV reutilizada como asset HeyGen;
- [ ] assets remotos deduplicados por hash;
- [ ] três gerações HeyGen reais concluídas e baixadas;
- [ ] três headlines planejadas para cada MP4;
- [ ] nove manifests/renders sem paid actions extras;
- [ ] headline/captions/music/framing configuráveis;
- [ ] preview aproximado útil e claramente rotulado;
- [ ] três masters aprovados e entregues.

### Operação

- [ ] happy path operável pela UI;
- [ ] restart durante polling recuperado;
- [ ] erro isolado por variante;
- [ ] próximo bloqueio visível;
- [ ] CLI suficiente para diagnóstico;
- [ ] nenhum JSON precisa ser editado manualmente no happy path.

### Técnico

- [ ] full verification green;
- [ ] schema drift check green;
- [ ] frontend lint/typecheck/test/build green;
- [ ] secrets ausentes dos artifacts;
- [ ] duplicate-paid-action coberto;
- [ ] sources/finals não sobrescritos;
- [ ] render full-decode e loudness QC;
- [ ] provider canary separado de testes determinísticos.

## 18. Riscos e respostas

### R1 — Integração oficial HeyGen difere do contrato esperado

Começar por preflight/adapter estreito e validar uma geração real antes de expandir batch.

### R2 — Ações pagas duplicadas

Persistir remote IDs cedo, usar logical keys completas e reconciliar antes de retry.

### R3 — Timing de captions não acompanha MP4

Registrar provenance; preferir sidecar confiável e medir drift antes de reutilizar timing da voz.

### R4 — Preview diverge do render

Declarar preview aproximado, manter controles equivalentes e usar proxy renderizado para review
final.

### R5 — Explosão de variantes

Mostrar cardinalidade, impor limite configurável e exigir confirmação acima do default.

### R6 — Escopo de UI cresce para editor genérico

Manter telas orientadas ao workflow, inputs tipados e ausência deliberada de timeline/canvas livre.

### R7 — Google Flow volta a consumir o roadmap

Manter Goals 4B–4D pausados até o piloto demonstrar que geração manual é gargalo real.

## 19. Defaults do piloto

```yaml
campaign_variants: 3
headline_variants_per_video: 3
heygen_paid_render_limit: 3
heygen_concurrency: 2
renderer: ffmpeg_ass
ui_host: 127.0.0.1
ui_port: 8742
preview: approximate
image_approval_on_import: true
```

Esses valores destravam desenvolvimento. Podem virar configuração no Goal responsável, sem criar
uma camada genérica antes de existir necessidade.

## 20. Ordem de entrega

A sequência executável é mantida exclusivamente em `docs/GOAL-ROADMAP.md`:

1. D1 Manual Image Batch Intake — entregue;
2. D2A HeyGen Contract, Preflight & Asset Reuse — entregue localmente;
3. D2B HeyGen generation — entregue localmente; D2C canário — concluído com vínculo manual;
4. D3A/D3B edição e A/B;
5. D4A/D4B API/UI e preview;
6. D5A/D5B render/QC/delivery;
7. D6 piloto end-to-end.

D2B implementa batch local (um job por variante), geração por image/audio assets, polling e
download com QC. D2A/D2B estão `IMPLEMENTED` e `LOCAL_VERIFIED`; D2B passou no gate Windows
13/13 em 2026-09-30 (1357 testes aprovados/18 skips) e revisão independente com correções.
Reconciliação após esgotar tentativas locais pode adicionar um job histórico de recuperação,
vinculado ao mesmo render/ID remoto, sem nova geração paga.
D2C concluiu um canário: em 2026-09-30, preflight e upload reais concluíram e uma geração autorizada
foi enviada, sem retry pago. Resposta sem ID verificável bloqueou para reconciliação; candidato
concluído foi localizado por consulta MCP e Rafael confirmou o vínculo manual.
Download/QC passaram (1080×1920, H.264/AAC, 7,224 s, full decode); replay reutilizou o render
sem nova reserva/dispatch. `PROVIDER_VERIFIED` para este canário com reconciliação manual,
não para recuperação automática do ID nem escala real. Review visual continua humano.
Gate atual 14/14 (1430 testes/19 skips).
Voz importada permite aceitação humana auditável somente de `review_required` isolado, preservando
ASR/QC/WAV/hashes; divergência grave, headline falada e outros achados continuam bloqueantes.
UI/editor/A/B continuam capacidades alvo. Evidência em
`docs/superpowers/2026-09-30-d2c-canary-verification.md`.
