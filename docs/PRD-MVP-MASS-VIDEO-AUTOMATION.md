# PRD — MVP Auraly Mass Video Pipeline

**Versão:** 1.0
**Status:** pronto para planejamento técnico e implementação
**Data:** 2026-08-09
**Responsável de produto:** Rafael Rovaron
**Repositório:** `C:/Users/Rovaron/Documents/Auraly/pipeline`
**Documento de contexto:** `docs/PROJECT-MEMORY.md`

---

## 1. Resumo executivo

O MVP ampliará o Auraly Video Pipeline existente de uma ferramenta de ingestão/pós-produção para uma aplicação local capaz de produzir, de forma resumível e auditável, múltiplas variantes de Reels a partir de uma única copy aprovada.

O fluxo-alvo do piloto será:

```text
1 Copy Master aprovada
→ 1 Voice Master via ElevenLabs API
→ 3 prompts/cenas
→ 3 imagens 2K via Google Flow + Playwright
→ revisão e aprovação das imagens
→ 3 looks HeyGen com suporte a Avatar III
→ 3 vídeos via HeyGen MCP/OAuth
→ 3 edições determinísticas
→ QC técnico + revisão humana
→ entrega local versionada
```

O MVP terá backend e workers em Python, persistência SQLite, CLI, API FastAPI e uma interface web local mínima para gestão da campanha, acompanhamento e gates de aprovação.

---

## 2. Problema

A produção atual exige que a IA ou um operador execute manualmente dezenas de operações:

- navegar no Google Flow;
- inserir prompts;
- escolher e baixar imagens 2K;
- gerar e baixar voz;
- tratar áudio;
- descobrir/chamar ferramentas HeyGen;
- fazer uploads assinados;
- criar look e vídeo;
- acompanhar polling;
- baixar MP4;
- gerar captions;
- renderizar;
- executar ffprobe, hashes e QC;
- copiar para entrega.

Isso cria:

- alto consumo de tempo e tokens;
- dependência de contexto conversacional;
- risco de repetir operações pagas;
- dificuldade para retomar após falhas;
- inconsistência de configurações;
- baixa capacidade de produzir muitas variantes;
- pouca visibilidade consolidada do estado da campanha.

---

## 3. Hipótese de produto

Se transformarmos as operações mecânicas em uma aplicação local orientada a campanhas e variantes, então será possível produzir três ou mais vídeos a partir de uma única copy/voz com menos intervenção manual, sem repetir ações pagas, mantendo controle humano sobre qualidade visual e publicação.

---

## 4. Objetivos do MVP

### O1 — Campanha com fan-out

Criar uma campanha com uma copy, uma Voice Master e três Scene Variants independentes.

### O2 — Voice Master programática

Gerar voz exclusivamente pela API oficial do ElevenLabs, tratar, validar e aprovar um áudio master reutilizável.

### O3 — Imagens automatizadas

Gerar e baixar imagens do Google Flow por Playwright, sempre em 2K, com evidências e retomada.

### O4 — HeyGen oficial e verificável

Gerar talking avatars exclusivamente pelo MCP oficial do HeyGen com OAuth, usando explicitamente Avatar III.

### O5 — Pós-produção determinística

Renderizar três vídeos com headline, captions, movimento e música usando manifests versionados.

### O6 — Qualidade e auditabilidade

Executar QC técnico, manter hashes, IDs, logs e status persistentes e impedir entrega quando checks P0 falharem.

### O7 — Gestão local

Oferecer uma interface web mínima para visualizar campanhas, aprovar imagens/voz/vídeos e acompanhar a fila.

---

## 5. Métricas de sucesso

O MVP será aceito quando o piloto real comprovar:

| Métrica | Critério |
|---|---:|
| Copy Masters | 1 aprovada |
| Voice Masters | 1 gerada pela API e aprovada |
| Variantes | 3 locais distintos |
| Imagens Flow | 3 imagens 2K aprovadas |
| HeyGen | 3 vídeos concluídos com Avatar III verificado |
| Renders finais | 3 masters 1080×1920 com QC P0 aprovado |
| Duplicação paga | 0 retries pagos cegos |
| Retomada | pelo menos 1 interrupção simulada retomada com sucesso |
| Reprodutibilidade | mesmo manifest e inputs produzem output funcionalmente equivalente |
| Secrets em logs/manifests | 0 |
| Operações ElevenLabs via web | 0 |
| Operações HeyGen via web | 0 |
| Downloads Flow | 100% em 2K |

Meta adicional: reduzir significativamente o número de interações manuais em comparação com o processo Eight of Cups/Laundromat.

---

## 6. Usuários

### U1 — Rafael / operador de produção

Precisa:

- criar campanhas;
- revisar copy e voz;
- revisar imagens em lote;
- acompanhar custos/status;
- aprovar vídeos;
- entregar masters.

### U2 — Agente de IA/Hermes

Precisa:

- criar parâmetros e prompts;
- chamar a aplicação por contrato estável;
- consultar status compacto;
- receber paths de artifacts para análise;
- registrar recomendações/aprovações;
- tratar exceções.

### U3 — Worker local

Executa jobs sem contexto conversacional, respeitando estado, locks, retries e limites.

---

## 7. Escopo do MVP

### 7.1 Incluído — P0

- Campaign, Copy Master, Voice Master e Scene Variant;
- SQLite + migrações;
- fila local persistente;
- state machine;
- CLI Typer;
- FastAPI local;
- UI web mínima;
- ElevenLabs TTS por API;
- tratamento/QC de áudio;
- Google Flow por Playwright;
- perfil persistente separado;
- download 2K;
- ingest/QC técnico de imagens;
- gate de aprovação de imagens;
- HeyGen MCP/OAuth preflight;
- upload de imagem e áudio;
- criação/polling de photo avatar look;
- validação de `avatar_iii`;
- criação/polling/download de vídeo;
- ingest/QC do MP4-fonte;
- captions usando copy aprovada e timing disponível;
- headline visual;
- música e zoom sutil;
- render 1080×1920;
- QC final;
- revisão/aprovação final;
- entrega em pasta local configurada;
- logs sanitizados;
- dry-run;
- retomada por etapa.

### 7.2 Incluído — P1, se não comprometer P0

- geração de duas candidatas por local;
- galeria comparativa;
- geração de duas vozes e comparação;
- proxy 540×960;
- contact sheet acessível pela UI;
- reprocessamento automático de erro de mixagem;
- ferramenta Hermes via HTTP local.

### 7.3 Fora do MVP

- publicação automática em redes sociais;
- coleta de performance do Reel;
- múltiplos usuários;
- acesso remoto à interface;
- PostgreSQL/Redis;
- execução distribuída;
- aplicativo móvel;
- timeline NLE completa;
- seleção visual totalmente autônoma;
- microserviços;
- troca automática para Avatar IV/V;
- automação web do ElevenLabs;
- automação web do HeyGen;
- uso de Google Flow por coordenadas rígidas sem seletor verificável.

---

## 8. Jornada principal

### Etapa 1 — Criar campanha

O operador/IA informa:

- identificador;
- personagem;
- copy;
- headline;
- carta/objeto;
- lista de três locais;
- preset de voz;
- preset de edição;
- orçamento.

Resultado: campanha `draft` com três variantes `not_started`.

### Etapa 2 — Aprovar Copy Master

O sistema valida formato e separa:

- headline visual;
- hook;
- body;
- CTA;
- spoken text.

Resultado: Copy Master imutável com versão e hash.

### Etapa 3 — Gerar Voice Master

- chama ElevenLabs API;
- salva raw;
- trata áudio;
- mede duração, WPM, silêncios, LUFS e true peak;
- transcreve/compara;
- apresenta player/relatório;
- aguarda aprovação.

### Etapa 4 — Gerar imagens

Para cada variante:

- monta job com prompt já fornecido pela IA;
- Playwright abre Flow;
- gera candidatas;
- captura grid;
- seleciona/download 2K;
- salva artifacts;
- executa image preflight;
- envia para revisão.

### Etapa 5 — Aprovar imagens

Operador/IA visualiza imagem e crops, registra:

- aprovada;
- aprovada com desvios;
- rejeitada;
- regenerar.

### Etapa 6 — Gerar HeyGen

Para imagens aprovadas:

- autenticação MCP/OAuth;
- reutiliza audio asset da Voice Master;
- envia imagem;
- cria look;
- verifica Avatar III;
- solicita vídeo explicitamente com Avatar III;
- acompanha status;
- baixa MP4;
- executa QC.

### Etapa 7 — Renderizar

- produz captions;
- gera headline;
- aplica preset;
- adiciona música;
- renderiza proxy/master;
- executa QC.

### Etapa 8 — Revisar e entregar

- player e relatório;
- aprovar/rejeitar;
- copiar master aprovado para destino;
- comparar SHA-256;
- registrar Delivery.

---

## 9. Requisitos funcionais

### FR-001 — Criar campanha

**Prioridade:** P0

O sistema deve criar campanha a partir da UI, CLI ou API.

**Campos mínimos:**

- `campaign_id`;
- conta/personagem;
- copy/headline;
- card/proof object;
- voice preset;
- edit preset;
- budget;
- três ou mais scene variants.

**Aceitação:**

- rejeita ID duplicado;
- valida slug e paths;
- persiste no SQLite;
- cria diretório sem sobrescrever;
- registra evento `campaign.created`.

### FR-002 — Versionar e aprovar Copy Master

**Prioridade:** P0

- preservar source;
- gerar spoken text sem headline;
- calcular SHA-256;
- bloquear edição de versão aprovada;
- criar nova versão para mudanças.

**Aceitação:** headline nunca aparece em `spoken_text` por default.

### FR-003 — Gerenciar variantes

**Prioridade:** P0

Cada variante contém local, horário, ação, prompt, objeto e estado independente.

**Aceitação:** falha em uma variante não bloqueia as demais.

### FR-004 — Gerar voz por ElevenLabs API

**Prioridade:** P0

- usar API oficial;
- voice ID/model ID explícitos;
- secrets fora do projeto;
- salvar raw e metadata sanitizada;
- permitir pelo menos uma geração;
- nunca usar Playwright/computer-use no ElevenLabs.

### FR-005 — Processar áudio

**Prioridade:** P0

- trim inicial/final;
- clamp conservador de pausas longas;
- tempo máximo configurável;
- loudness e true peak;
- 48 kHz;
- hash e ffprobe;
- preservar raw.

**Aceitação:** output não substitui raw e possui relatório JSON.

### FR-006 — Verificar narração

**Prioridade:** P0

- gerar transcript/timestamps via API ou faster-whisper;
- alinhar com copy;
- listar diferenças;
- bloquear quando a divergência exceder limite configurado;
- bloquear se headline/direções forem faladas.

### FR-007 — Aprovar Voice Master

**Prioridade:** P0

- player;
- métricas;
- transcript diff;
- approve/reject;
- versão aprovada imutável.

### FR-008 — Operar Google Flow por Playwright

**Prioridade:** P0

- perfil persistente isolado;
- login manual inicial;
- browser worker separado;
- preencher prompt;
- iniciar geração;
- esperar resultado;
- capturar screenshots;
- selecionar candidata configurada;
- aprimorar/download em 2K;
- capturar download via Playwright;
- timeout e erro estruturado.

**Aceitação:** não aceitar arquivo 1K quando `require_2k=true`.

### FR-009 — Detectar mudança da UI do Flow

**Prioridade:** P0

Se elementos necessários não forem encontrados, o sistema deve:

- parar a variante;
- salvar screenshot;
- salvar trace;
- retornar `human_intervention_required`;
- não clicar por coordenadas incertas.

### FR-010 — Ingest/QC técnico da imagem

**Prioridade:** P0

- validar JPEG/PNG e decodificação;
- dimensões/proporção;
- hash;
- crops de face, mãos e proof object quando configurados;
- contact sheet;
- OCR/face count como indicadores, sem aprovação automática semântica.

### FR-011 — Revisar imagem

**Prioridade:** P0

- approve;
- approve with known deviations;
- reject;
- regenerate;
- registrar operador, timestamp e comentário;
- não excluir rejeitadas.

### FR-012 — HeyGen MCP/OAuth preflight

**Prioridade:** P0

Antes do lote:

- verificar servidor MCP;
- verificar OAuth sem expor token;
- verificar ferramentas obrigatórias;
- retornar relatório sanitizado.

### FR-013 — Upload de assets HeyGen

**Prioridade:** P0

- criar upload;
- fazer PUT com headers exatos;
- completar upload;
- verificar asset;
- persistir asset ID e hash;
- reutilizar audio asset por campanha;
- não persistir URL assinada.

### FR-014 — Criar e verificar Photo Avatar Look

**Prioridade:** P0

- criar/reutilizar avatar group;
- criar look por imagem aprovada;
- poll com timeout;
- persistir look ID;
- bloquear se `supported_api_engines` não incluir `avatar_iii`.

### FR-015 — Criar vídeo Avatar III

**Prioridade:** P0

Request deve conter explicitamente:

```json
{"engine":{"type":"avatar_iii"}}
```

- usar look concluído;
- usar audio asset aprovado;
- solicitar 1080p 9:16;
- solicitar SRT sidecar quando suportado;
- persistir video ID imediatamente;
- nunca repetir POST pago de estado ambíguo.

### FR-016 — Poll e download HeyGen

**Prioridade:** P0

- polling com backoff;
- timeout configurável;
- retomada pelo video ID;
- download atômico `.part` → final;
- hash;
- nunca registrar URL assinada.

### FR-017 — QC do source MP4

**Prioridade:** P0

- full decode;
- 1080×1920;
- H.264/AAC aceitos;
- áudio presente;
- duração compatível;
- contact sheet;
- relatório.

### FR-018 — Construir captions

**Prioridade:** P0

Preferência de timing:

1. sidecar do HeyGen;
2. timing ElevenLabs validado contra source;
3. ASR do MP4 final;
4. forced-alignment fallback.

Texto final sempre vem da Copy Master aprovada.

- máximo duas linhas;
- safe zone;
- sem headline;
- validar sequência de palavras;
- sem eventos fora da duração.

### FR-019 — Gerar headline

**Prioridade:** P0

- visual-only;
- presets permitidos;
- wrap e font fit;
- position/duration no manifest;
- style-frame de verificação.

### FR-020 — Render editorial

**Prioridade:** P0

- manifest renderer-neutral;
- zoom sutil;
- captions;
- headline;
- música;
- 1080×1920;
- H.264/AAC;
- faststart;
- output versionado;
- nunca sobrescrever final.

### FR-021 — Mixagem segura

**Prioridade:** P0

- voz preservada;
- música em nível configurável;
- `amix normalize=0` ou compensação documentada;
- limiter;
- loudness medido no output final;
- falhar quando voz estiver baixa ou clipping ocorrer.

### FR-022 — QC final

**Prioridade:** P0

Checks:

- existência/tamanho;
- hash;
- ffprobe;
- full decode;
- duração;
- resolução/FPS;
- áudio;
- LUFS/true peak;
- black/freeze indicators;
- captions/headline bounds;
- source/input hashes;
- contact sheet;
- proxy.

### FR-023 — Aprovar render

**Prioridade:** P0

- approve/reject;
- comentário;
- master aprovado imutável;
- rejeição pode criar nova versão de edit manifest.

### FR-024 — Entregar

**Prioridade:** P0

- copiar para pasta configurada;
- filename determinístico/versionado;
- verificar hash origem/destino;
- registrar Delivery;
- distinguir pasta sincronizada de cloud upload confirmado.

### FR-025 — Fila persistente

**Prioridade:** P0

- queued/running/completed/failed/cancelled;
- attempts;
- lock e heartbeat;
- timeout;
- retry policy;
- priority;
- concurrency por adapter;
- retomada após restart.

### FR-026 — Dry-run

**Prioridade:** P0

Dry-run deve mostrar:

- jobs planejados;
- ações pagas;
- recursos reutilizados;
- arquivos a criar;
- bloqueios;
- estimativa de variantes;
- sem executar geração/download pago.

### FR-027 — Budget gate

**Prioridade:** P0

- limite de renders HeyGen;
- aprovação antes do primeiro paid render do piloto;
- bloquear acima do limite;
- registrar decisão.

### FR-028 — API e CLI compartilhadas

**Prioridade:** P0

CLI e UI devem chamar os mesmos application services. Nenhuma regra de negócio exclusiva na UI.

### FR-029 — Eventos em tempo real

**Prioridade:** P0

SSE deve transmitir:

- job queued/started/progress/completed/failed;
- state changed;
- review required;
- campaign summary changed.

### FR-030 — Ferramenta Hermes

**Prioridade:** P1

Oferecer endpoint/wrapper com ações compactas:

- create_campaign;
- run_campaign;
- get_status;
- approve/reject;
- resume/pause/cancel.

---

## 10. Requisitos da interface MVP

### UI-001 — Dashboard

- campanhas;
- progresso agregado;
- jobs ativos;
- reviews pendentes;
- falhas;
- filtros simples.

### UI-002 — Campaign Detail

- Copy Master;
- Voice Master;
- lista de variantes;
- estado por estágio;
- budget;
- ações run/pause/resume.

### UI-003 — Voice Review

- player;
- duração/WPM/LUFS/peak;
- transcript diff;
- approve/reject.

### UI-004 — Image Gallery

- candidatas por variante;
- preview/crops;
- prompt;
- metadata;
- approve/reject/regenerate;
- comentário de desvios.

### UI-005 — HeyGen Queue

- MCP/OAuth status sanitizado;
- look/video status;
- engine;
- IDs remotos não sensíveis;
- retry/resume seguro;
- paid action gate.

### UI-006 — Video Review

- player;
- contact sheet;
- technical QC;
- approve/reject;
- path do master.

### UI-007 — Logs e erros

- eventos filtrados por campanha/variante;
- mensagem humana;
- detalhes técnicos expansíveis;
- paths de screenshot/trace;
- secrets redigidos.

---

## 11. Arquitetura técnica

```text
React/TypeScript UI
        │ HTTP + SSE
        ▼
FastAPI
        │
        ▼
Application Services
        │
        ├── Domain + State Machine
        ├── Job Queue (SQLite)
        └── Approval/Cost Policies
                │
                ▼
Workers
  ├── FlowPlaywrightWorker
  ├── ElevenLabsWorker
  ├── HeyGenMcpWorker
  ├── MediaWorker
  ├── RenderWorker
  └── DeliveryWorker
                │
                ▼
Adapters
  ├── Google Flow / Playwright
  ├── ElevenLabs API
  ├── HeyGen MCP/OAuth
  ├── FFmpeg/ffprobe
  ├── Filesystem
  └── Drive Desktop folder
```

### Regra arquitetural

UI, CLI e Hermes adapter nunca executam FFmpeg, Playwright ou APIs diretamente. Eles solicitam application services que criam jobs persistentes.

---

## 12. Estrutura de código alvo

```text
src/auraly_pipeline/
  api/
    app.py
    routes/
    sse.py

  cli/
    campaign.py
    jobs.py
    providers.py

  domain/
    campaign.py
    copy_master.py
    voice_master.py
    scene_variant.py
    assets.py
    states.py
    events.py

  application/
    commands/
    queries/
    services/
    policies/

  orchestration/
    scheduler.py
    worker.py
    state_machine.py
    retry.py
    locks.py

  adapters/
    elevenlabs/
    google_flow/
    heygen_mcp/
    media/
    delivery/
    persistence/

  qc/
    audio.py
    image.py
    video.py
    render.py

  manifests/
    models.py
    schema.py

frontend/
  src/
    api/
    components/
    pages/
    features/
```

---

## 13. Modelo de dados inicial

### campaigns

- id;
- slug;
- character;
- status;
- copy_master_id;
- voice_master_id;
- edit_preset;
- budget_json;
- created_at/updated_at.

### copy_masters

- id/version;
- campaign_id;
- source_path;
- spoken_text_path;
- headline;
- sha256;
- approval status/by/at.

### voice_masters

- id/version;
- campaign_id;
- provider;
- voice_id/model_id;
- raw_path/processed_path;
- transcript_path;
- duration/wpm/lufs/peak;
- sha256;
- status.

### scene_variants

- id;
- campaign_id;
- slug;
- location/time/action/object;
- prompt_path;
- status;
- selected_image_id;
- look_id;
- video_render_id.

### image_candidates

- id/version;
- variant_id;
- source_path;
- prompt_hash;
- width/height;
- sha256;
- review status/comment;
- deviations_json.

### remote_assets

- id;
- provider;
- kind;
- local_sha256;
- remote_id;
- status;
- metadata_json sanitizado.

### avatar_looks

- id;
- variant_id;
- remote_id;
- group_id;
- status;
- supported_engines_json.

### video_renders

- id;
- variant_id;
- provider;
- remote_id;
- engine;
- source_path;
- sha256;
- status.

### edit_renders

- id/version;
- variant_id;
- manifest_path/hash;
- proxy_path/master_path;
- status.

### qc_reports

- id;
- subject_type/id;
- report_path;
- result;
- failures_json.

### jobs / job_attempts / job_events

- tipo, payload, status, priority;
- lock/heartbeat;
- attempts;
- error code/message;
- timestamps.

### approvals

- subject;
- decision;
- actor;
- comment;
- known deviations;
- timestamp.

### deliveries

- render_id;
- source/destination;
- hashes;
- status;
- timestamp.

---

## 14. Contrato de diretórios

```text
campaigns/<campaign-id>/
  campaign.yaml
  shared/
    copy/
    voice/
    music/
    fonts/
  variants/<variant-id>/
    flow/
    source/
    heygen/
    edit/
    render/
    qc/
    manifests/
  reports/
  delivery/
  logs/
```

Regras:

- sources imutáveis;
- outputs versionados;
- writes atômicos;
- paths relativos no manifest;
- nenhum secret;
- nada é sobrescrito por default.

---

## 15. API mínima

### Campaigns

```text
POST /api/campaigns
GET  /api/campaigns
GET  /api/campaigns/{id}
POST /api/campaigns/{id}/run
POST /api/campaigns/{id}/pause
POST /api/campaigns/{id}/resume
POST /api/campaigns/{id}/dry-run
```

### Voice

```text
POST /api/campaigns/{id}/voice/generate
GET  /api/campaigns/{id}/voice
POST /api/voice/{id}/approve
POST /api/voice/{id}/reject
```

### Variants/images

```text
POST /api/campaigns/{id}/variants
POST /api/variants/{id}/flow/generate
GET  /api/variants/{id}/images
POST /api/images/{id}/approve
POST /api/images/{id}/reject
POST /api/variants/{id}/flow/regenerate
```

### HeyGen

```text
GET  /api/providers/heygen/status
POST /api/variants/{id}/heygen/submit
GET  /api/variants/{id}/heygen
POST /api/variants/{id}/heygen/resume
```

### Render/QC/delivery

```text
POST /api/variants/{id}/render
GET  /api/variants/{id}/qc
POST /api/renders/{id}/approve
POST /api/renders/{id}/reject
POST /api/renders/{id}/deliver
```

### Jobs/events

```text
GET  /api/jobs
GET  /api/jobs/{id}
POST /api/jobs/{id}/cancel
GET  /api/events/stream
```

---

## 16. Segurança e privacidade

### Secrets

- ElevenLabs API key em secret store local ou ambiente seguro fora do repo;
- OAuth HeyGen no Hermes/MCP;
- cookies Flow no perfil Playwright ignorado pelo Git;
- nenhum secret no SQLite, campaign YAML, manifests ou logs;
- sanitizer obrigatório para payloads e erros.

### Rede

- API e UI em `127.0.0.1`;
- sem exposição LAN no MVP;
- CORS apenas para o frontend local;
- downloads remotos somente dos providers configurados.

### Filesystem

- validar paths sob roots aprovados;
- prevenir path traversal;
- finals imutáveis;
- download atômico;
- verificar hash antes/depois de delivery.

### Ações pagas

- budget gate;
- IDs remotos persistidos;
- sem retry cego;
- dry-run;
- confirmação antes do primeiro lote pago.

---

## 17. Requisitos não funcionais

### NFR-001 — Retomada

Após restart do processo ou PC, jobs `running` sem heartbeat tornam-se `recoverable` e retomam a partir de IDs/artifacts persistidos.

### NFR-002 — Idempotência

Reexecutar comando não deve:

- duplicar campanhas;
- reenviar asset com mesmo hash quando reutilizável;
- recriar look concluído;
- repetir vídeo pago existente;
- sobrescrever render final.

### NFR-003 — Observabilidade

Todo job registra:

- correlation ID;
- campaign/variant IDs;
- stage;
- tentativa;
- timestamps;
- duration;
- status;
- erro sanitizado;
- paths de relatório.

### NFR-004 — Performance

- UI deve responder em menos de 500 ms para consultas locais comuns;
- operações longas sempre em worker;
- progresso visível em até 2 segundos após evento persistido.

### NFR-005 — Concorrência

Defaults:

```text
Flow: 1
ElevenLabs: 1
HeyGen: 2
Render: 2
```

Configuráveis sem alterar código.

### NFR-006 — Reprodutibilidade

Manifest deve registrar:

- hashes dos inputs;
- versões de presets;
- parâmetros;
- versões de FFmpeg/renderer;
- timestamps e approval hashes.

### NFR-007 — Compatibilidade

- Windows 10 x64;
- Python 3.11.15;
- FFmpeg 8.1.1 ou versão validada;
- paths Windows e MSYS tratados corretamente.

### NFR-008 — Testabilidade

Adapters devem ser substituíveis por fakes. Testes de unidade não fazem chamadas pagas.

---

## 18. Políticas de retry

### Seguro para retry automático

- GET/status;
- download incompleto com mesma URL ainda válida;
- polling;
- ffprobe/QC local;
- render local para novo path versionado;
- Flow antes da confirmação de geração somente quando não houver evidência de submissão.

### Não repetir automaticamente em estado ambíguo

- ElevenLabs TTS POST;
- Flow generate click;
- HeyGen create look;
- HeyGen create video;
- qualquer paid action.

Nesses casos, reconciliar provider/state antes de retry.

---

## 19. Estratégia de captions

Ordem de preferência:

1. SRT sidecar do HeyGen;
2. timestamps ElevenLabs, após medir drift do MP4 HeyGen;
3. faster-whisper no source MP4;
4. forced alignment.

Regras:

- copy aprovada fornece texto e pontuação;
- ASR fornece timing;
- headline não entra nas captions;
- validar token sequence;
- máximo 2 linhas;
- estilo/preset versionado;
- style-frame obrigatório para um novo preset.

---

## 20. Estratégia de render

### Input

- source MP4 HeyGen;
- EditManifest;
- captions;
- headline;
- música aprovada;
- fonts job-local.

### Output

- proxy opcional 540×960;
- master 1080×1920;
- H.264 high profile;
- yuv420p;
- AAC 48 kHz;
- CFR;
- faststart.

### Mixagem

- voz como referência;
- música inicialmente 16–20 dB abaixo da narração, ajustada por medição;
- `amix normalize=0`;
- limiter;
- medir final.

---

## 21. Estratégia de testes

### Unitários

- state transitions;
- manifest validation;
- header dedup case-insensitive;
- path safety;
- copy/headline separation;
- budget policy;
- retry policy;
- caption token alignment;
- filename/versioning;
- log redaction.

### Integração sem custo

- fake ElevenLabs server;
- fake HeyGen MCP responses;
- local HTTP presigned-upload simulator;
- Playwright em página mock do Flow;
- FFmpeg synthetic media;
- SQLite restart/recovery.

### Canary real

1. ElevenLabs: geração curta aprovada;
2. Flow: uma imagem 2K;
3. HeyGen: uma variante real Avatar III;
4. render/QC/delivery local.

### E2E do piloto

- três variantes reais;
- pausa/restart simulada;
- uma rejeição/regeneração de imagem;
- uma falha de worker recuperada;
- três renders aprovados.

---

## 22. Plano de implementação

### Milestone 0 — Baseline e ADRs

- validar testes atuais;
- documentar arquitetura em ADRs;
- mapear código existente;
- criar schema de Campaign/Variant;
- definir migração sem quebrar ingest atual.

**Saída:** baseline verde e ADRs aprovados.

### Milestone 1 — Fundação

- SQLite/SQLAlchemy/Alembic;
- domain models;
- state machine;
- job queue;
- CLI campaign/status/jobs;
- logs/redaction;
- dry-run;
- testes de restart/idempotência.

**Gate:** jobs locais retomáveis.

### Milestone 2 — ElevenLabs

- API adapter;
- secret loading;
- geração;
- processamento/QC;
- ASR/diff;
- aprovação Voice Master;
- fake server e canary.

**Gate:** Voice Master real aprovada sem browser.

### Milestone 3 — Google Flow Playwright

- perfil persistente;
- login setup;
- selectors versionados;
- geração/download 2K;
- screenshot/trace;
- image ingest/QC;
- review gate.

**Gate:** três imagens 2K obtidas e versionadas.

### Milestone 4 — HeyGen MCP/OAuth

- preflight;
- MCP adapter;
- upload helper testado;
- audio asset reuse;
- look creation/poll;
- Avatar III gate;
- video creation/poll/download;
- sanitização e cost policy.

**Gate:** um canário Avatar III completo e reconciliável.

### Milestone 5 — Render/QC

- captions;
- headline;
- edit manifest derivado;
- música/zoom;
- proxy/master;
- QC técnico;
- review/delivery.

**Gate:** master canário com todos checks P0.

### Milestone 6 — Interface web mínima

- FastAPI routes;
- React app;
- dashboard;
- campaign detail;
- voice/image/video review;
- SSE;
- job/error views.

**Gate:** operador consegue gerir o piloto sem CLI para tarefas normais.

### Milestone 7 — Piloto de três variantes

- executar campanha real;
- medir tempo/intervenções;
- corrigir falhas;
- documentar operação;
- aprovar/reprovar MVP.

---

## 23. Riscos e mitigação

### R1 — Mudança na UI do Google Flow

**Probabilidade:** alta.
**Mitigação:** selectors versionados, roles/labels, screenshots, trace, stop-safe e canary diário/manual.

### R2 — OAuth/MCP indisponível

**Mitigação:** preflight antes do lote, status claro e bloqueio; não usar fallback web silencioso.

### R3 — Duplicação de ações pagas

**Mitigação:** persistência de IDs antes de polling, reconciliation, budget gate e sem retry cego.

### R4 — Inconsistência visual do personagem/carta

**Mitigação:** gate por imagem, crops e revisão semântica; preservar versões rejeitadas.

### R5 — Drift de timing do HeyGen

**Mitigação:** timing do MP4 final ou medir drift antes de reutilizar timestamps.

### R6 — Mixagem baixa/inaudível

**Mitigação:** `amix normalize=0`, final loudness QC e blocker automático.

### R7 — Application Control do Windows

**Mitigação:** usar Python 3.11 validado, executáveis aprovados, comandos isolados e testes de instalação.

### R8 — Escopo grande demais

**Mitigação:** piloto de três variantes, P0 rigoroso, sem publicação automática/timeline completa.

### R9 — HyperFrames vulnerabilities

**Mitigação:** localhost, inputs aprovados, versão fixada e adapter substituível; FFmpeg pode ser renderer inicial.

---

## 24. Critérios de aceite do MVP

### Produto

- [ ] campanha criada por UI/API;
- [ ] Copy Master aprovada e imutável;
- [ ] Voice Master via ElevenLabs API;
- [ ] três variantes configuradas;
- [ ] três imagens Flow 2K aprovadas;
- [ ] três looks com suporte Avatar III;
- [ ] três vídeos gerados via MCP/OAuth;
- [ ] três masters renderizados;
- [ ] três QCs P0 aprovados;
- [ ] gates e histórico visíveis na UI;
- [ ] entrega com hash verificado.

### Técnico

- [ ] `pytest`, Ruff e mypy aprovados;
- [ ] migrações do banco testadas;
- [ ] restart/recovery testado;
- [ ] duplicate-paid-action testado;
- [ ] secrets ausentes de logs/manifests;
- [ ] Flow UI failure produz screenshot/trace;
- [ ] Avatar III explícito e verificado;
- [ ] render final full-decode;
- [ ] loudness QC bloqueia mixagem baixa;
- [ ] sources/finals não são sobrescritos.

### Operacional

- [ ] Rafael consegue iniciar, pausar e retomar campanha;
- [ ] consegue aprovar/rejeitar voz, imagens e vídeos;
- [ ] erros apresentam ação recomendada;
- [ ] IA consegue consultar status em JSON compacto;
- [ ] documentação de instalação/operação atualizada.

---

## 25. Definition of Done por feature

Uma feature só está concluída quando:

1. código implementado;
2. schema/migração criados quando necessário;
3. unit tests;
4. integration test com fake;
5. log sanitizado;
6. erro e retry documentados;
7. API/CLI atualizadas;
8. UI atualizada quando aplicável;
9. docs atualizados;
10. canary real executado quando envolver provider;
11. artifact real verificado, não apenas mock.

---

## 26. Decisões que precisam de Rafael antes/durante a implementação

Não bloqueiam Milestone 0–1, mas devem ser decididas antes dos respectivos adapters:

1. limite de renders pagos HeyGen no piloto;
2. número de candidatas Flow por local;
3. settings iniciais oficiais do Michael C. Vincent;
4. renderer MVP: FFmpeg/ASS como default ou HyperFrames;
5. pasta final de entrega;
6. porta local da interface;
7. retenção de imagens rejeitadas e traces;
8. música default por conta/campanha;
9. se a IA pode aprovar imagens sozinha ou apenas recomendar;
10. se o primeiro wrapper Hermes será CLI JSON ou endpoint HTTP.

Defaults propostos, caso não haja decisão:

```yaml
pilot:
  heygen_render_limit: 3
  flow_candidates_per_location: 2
  renderer: ffmpeg_ass
  ui_port: 8742
  flow_trace_retention_days: 30
  rejected_image_retention: permanent
  image_approval: human_required
  hermes_integration: http_local
```

---

## 27. Próximo passo de implementação

Começar pelo **Milestone 0**, produzindo:

1. inventário do código atual;
2. ADR-001: monólito modular + ports/adapters;
3. ADR-002: SQLite job queue/state machine;
4. ADR-003: provider contracts e secret boundaries;
5. modelos Pydantic de Campaign, VoiceMaster e SceneVariant;
6. primeira migração Alembic;
7. comando `auraly campaign create`;
8. comando `auraly campaign status`;
9. testes de criação, duplicidade, paths e restart.

Nenhuma chamada paga é necessária para iniciar o Milestone 0–1.
