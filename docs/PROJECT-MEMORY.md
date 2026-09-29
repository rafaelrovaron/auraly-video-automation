# Auraly Mass Video Pipeline — Memória do Projeto

**Atualizado em:** 2026-09-29
**Decisão vigente:** MVP delivery-first para uso local e pessoal

Este documento guarda decisões duráveis e fatos verificados. O estado entregue aparece separado
do produto planejado para impedir que roadmap seja confundido com capacidade existente.

## 1. Resultado desejado

Produzir várias peças verticais a partir de uma única campanha:

- uma Copy Master aprovada;
- uma Voice Master aprovada e reutilizada;
- uma imagem diferente por variante;
- um MP4 HeyGen por imagem;
- uma ou mais versões editoriais por MP4;
- outputs rastreáveis, reproduzíveis e revisáveis.

O operador principal é o Rafael. O sistema roda localmente e precisa reduzir trabalho repetitivo,
não se comportar como um SaaS multiusuário.

## 2. Estado entregue no repositório

### 2.1 Foundations

- Python 3.11, Pydantic, Typer, SQLAlchemy 2, Alembic e SQLite WAL;
- contratos versionados, JSON Schemas e validação de paths;
- Campaign, CopyMaster e SceneVariant persistentes;
- Copy Master aprovada imutável e headline explicitamente visual-only;
- ingestão que copia, nunca move ou sobrescreve sources;
- `ffprobe` JSON, hashing e escrita atômica onde já implementados.

### 2.2 Orquestração

- Jobs persistentes com attempts e eventos append-only;
- idempotency keys, claim atômico, leases, heartbeat, retry e recovery;
- policies `idempotent`, `manual_only` e `reconcile_before_retry`;
- CLI JSON para submeter, consultar, cancelar, retomar e recuperar jobs;
- handlers fake e harness determinístico de verificação.

### 2.3 Voice Master

- integração oficial ElevenLabs via API;
- uma Voice Master ligada a uma versão aprovada da Copy Master;
- texto enviado ao provider exclui a headline;
- artefato bruto preservado sem overwrite;
- WAV processado mono, 48 kHz, PCM;
- trim de silêncio no início e no fim com `silenceremove` + `areverse`;
- loudness, true peak, silêncio, duração, WPM, hashes e transcript diff persistidos;
- aprovação humana obrigatória;
- budget gate, reconciliação e proteção contra geração paga duplicada.

Artefato canônico reutilizável pelo HeyGen:

```text
campaigns/<campaign-id>/voice/<voice-master-id>/processed/voice-master.wav
```

O filtro vigente inclui normalização `I=-16:TP=-1.5:LRA=11` e remoção de bordas abaixo de
`-50dB` com duração mínima de `0.1s`. Alterações futuras devem preservar um knob de calibração,
porque pacing e silêncio são características perceptuais.

### 2.4 Imagens e Google Flow

- ImageGeneration e ImageCandidate persistentes;
- review, approve/reject/replace e histórico de candidatas;
- handler local determinístico;
- runtime Playwright com perfil persistente, lock global e locators semânticos;
- geração Google Flow, dois slots, correlação de downloads 2K e recovery por checkpoints;
- diagnósticos sanitizados, screenshot/trace e bloqueio em estado ambíguo;
- verificação local por fixtures.

Não existe evidência de canário real Google Flow. Portanto:

```text
IMPLEMENTED       YES
LOCAL_VERIFIED    YES
PROVIDER_VERIFIED NO
```

## 3. Decisão delivery-first de 2026-09-27

### 3.1 O que mudou

A automação de imagens pelo Google Flow deixou de ser dependência do MVP. As imagens serão
geradas manualmente e importadas em batch. O código Flow permanece preservado, mas congelado e
opt-in.

O esforço ativo passa a ser:

1. HeyGen em escala;
2. edição configurável e A/B de headline;
3. interface local simples;
4. render e piloto end-to-end.

### 3.2 Por que

- automação de UI de provider exige manutenção alta;
- imagens manuais não bloqueiam entrega nem aprendizado criativo;
- HeyGen e edição removem mais trabalho repetitivo por unidade de esforço;
- UI operacional simples reduz atrito antes de existir um renderer completo;
- o objetivo é uso pessoal rápido, não hardening de produto público.

### 3.3 O que não mudou

- Voice Master continua automatizada;
- o WAV processado continua sendo o áudio mestre compartilhado;
- sources e finals continuam não destrutivos;
- ações pagas continuam idempotentes, reconciliáveis e limitadas por budget;
- secrets e paths continuam protegidos;
- UI, CLI e workers continuam usando application services comuns.

## 4. Fluxo ativo do MVP

```text
Campaign + Copy Master
  → Voice Master automatizada e aprovada
  → imagens criadas fora do sistema
  → import batch e associação às variantes
  → upload/reuso de assets no HeyGen
  → geração batch
  → polling retomável
  → download e inspeção dos MP4
  → resolução EditProfile + overrides
  → render de uma ou mais edit variants
  → review e entrega
```

Falha ou atraso na automação Flow não bloqueia nenhum estágio desse caminho.

### 4.1 Manual Image Batch Intake entregue

D1 está `IMPLEMENTED` e `LOCAL_VERIFIED`. O fluxo explícito é:

```text
prepare-import → adicionar imagens/preencher paths → import-batch --dry-run → import-batch
```

O manifest versionado associa `variantId` a path relativo. A importação valida cobertura, mídia e
orientação, preserva os sources, publica por hash e persiste `ImageCandidate` manual em uma única
transação. Reruns idênticos reutilizam arquivos e candidatos.

### 4.2 HeyGen asset preparation entregue

D2A está `IMPLEMENTED` e `LOCAL_VERIFIED`. A aplicação conecta ao Remote MCP oficial por OAuth,
guarda a sessão no cofre do sistema e valida as seis tools necessárias. Imagens aprovadas e o WAV
processado são planejados em um batch de até 100 itens; `remote_assets` deduplica por provider,
conta, tipo e hash.

IDs de batch/asset são persistidos antes do primeiro PUT. URLs e headers temporários permanecem
somente em memória. Outcomes ambíguos bloqueiam o job `reconcile_before_retry`, e `heygen
reconcile` prova o estado remoto antes de retomar. O fake local cobre o fluxo; HeyGen ainda não
está `PROVIDER_VERIFIED`.

### 4.3 D2B — geração/polling/download implementados

Em 2026-09-29, o slice local D2B implementa um job `heygen.video.generate` por SceneVariant,
batch por campanha, tabela `heygen_renders` (migration 0008), reserva atômica com teto explícito
e concorrência 1..2. Validação final/revisão em andamento; não marcar PROVIDER_VERIFIED.

MCP `create_video_from_image` recebe image asset + audio asset compartilhado. Não há engine
ou idempotency key inventados: `provider_default` é validado contra schema remoto; callback
serve apenas de correlação. `submitting` é persistido antes da chamada e o ID antes de polling.
Resultado ambíguo bloqueia sem paid retry; ID conhecido retoma leitura/download. Sem ID, binding
manual exige confirmação e recusa identidade contraditória/duplicada.

Config material é separado de polling/concurrency na identidade. Jobs/reservas são reutilizados
em replay; falha não devolve budget automaticamente. O WAV aprovado é validado por hash/duração,
assim como imagem, copy, conta e assets ready antes de dispatch. Assinaturas/URLs não persistem.

Source MP4 H.264/AAC vertical passa SHA-256, ffprobe, tolerância `max(2s,5% do WAV)` e full decode.
Hardlink publica sem overwrite; publication.json fixa identidade/hash antes do MP4, source.json
fecha o manifest antes de ready. Crash entre publicação e commit recupera sem novo download.

Interface: `heygen plan-videos|generate-videos|run-videos|videos|reconcile-video`. Generate reserva;
run pode consumir créditos. Canário D2C segue pendente de autorização; editor/UI não entregues.

## 5. Contrato de importação batch de imagens

O batch deve ser explícito e determinístico. Não inferir associação apenas pela ordem retornada
pelo filesystem.

Formato planejado mínimo:

```json
{
  "schemaVersion": "1.0",
  "campaignId": "campaign-id",
  "items": [
    {"variantId": "laundromat", "path": "images/laundromat.png"},
    {"variantId": "hotel", "path": "images/hotel.png"}
  ]
}
```

Regras:

- dry-run antes da cópia;
- paths sob roots confiáveis;
- extensões e mídia real validadas;
- orientação vertical e dimensões registradas;
- hash para deduplicação e idempotência;
- cópia para path versionado, nunca move;
- batch parcial falha antes de mutar estado por default;
- associação persistida a SceneVariant;
- imagem importada pode ser marcada como selecionada/aprovada no mesmo comando quando o manifest
  declarar essa intenção.

## 6. Integração HeyGen

### 6.1 Provider boundary

O boundary MCP/OAuth oficial e o fake determinístico estão entregues. Não automatizar o website.
MCP/OAuth atende ao MVP pessoal; eventual API key para escala maior exige um Goal separado.

### 6.2 Assets

- upload/reuso de imagens e `processed/voice-master.wav`: entregue localmente;
- persistência de `remote_asset_id` antes dos bytes: entregue localmente;
- dedupe por conta/tipo/hash e secrets fora do SQLite: entregue localmente;
- validação real do provider: pendente em D2C.

### 6.3 Batch generation

Cada variante produz uma requisição lógica independente, mas um comando pode preparar/submeter o
lote inteiro. O lote deve:

- mostrar dry-run e custo/limite antes da primeira ação paga;
- usar engine/configuração HeyGen explicitamente registrada;
- persistir o video ID antes do polling;
- limitar concorrência;
- reconciliar estado ambíguo antes de retry;
- permitir retomar sem recriar renders já submetidos.

### 6.4 Poll e download

- polling com backoff e timeout configuráveis;
- retomada pelo video ID;
- download para `.part` e rename atômico;
- hash e `ffprobe` do MP4;
- validação de vídeo vertical, streams e duração plausível;
- URL assinada somente em memória/log sanitizado;
- source MP4 nunca sobrescrito.

## 7. Modelo de edição alvo

### 7.1 EditProfile

Objeto reutilizável e versionado com defaults visuais:

- canvas/output;
- headline style;
- caption style;
- music defaults;
- framing defaults;
- fonte e safe zones.

Um profile não contém texto específico de uma campanha nem paths de source MP4.

### 7.2 EditManifest

Snapshot resolvido e imutável de um render:

- IDs e hashes dos inputs;
- versão do EditProfile;
- configuração final de headline, captions, music e framing;
- output/version ID;
- parâmetros e versão do renderer;
- provenance dos overrides.

O `edit.json` legado é ponto de partida, não prova de que esse sistema já esteja implementado.

### 7.3 Precedência de configuração

```text
EditProfile
  < campaign defaults
  < source-video override
  < output-variant override
```

Merge profundo arbitrário não faz parte do MVP. Cada seção aceita apenas campos tipados e
conhecidos, evitando resultados silenciosos por typo.

### 7.4 Headline

Configurável por:

- `text`;
- preset/style;
- font, weight, size e line height;
- color, stroke/shadow e background;
- anchor/position e safe zone;
- max lines e fit policy;
- start/end.

A headline permanece visual-only.

### 7.5 Captions

Configuráveis por preset, fonte, cor, posição, max lines e highlight. O texto vem da Copy Master
aprovada; timing vem do melhor sidecar confiável disponível ou de alinhamento posterior. Headline
não entra nas captions.

### 7.6 Music

Asset local aprovado, volume, loop/trim, fade-in e fade-out. Voz é a referência; clipping e voz
inaudível são blockers de QC.

### 7.7 Framing

Crop/fit, escala, posição e zoom sutil. O MVP não inclui keyframes livres nem timeline.

### 7.8 A/B de headline

Uma A/B variant altera somente campos editoriais, normalmente `headline.text`:

```text
1 source MP4 HeyGen
  ├── edit variant A → headline A → output A
  ├── edit variant B → headline B → output B
  └── edit variant C → headline C → output C
```

Ela nunca dispara nova voz, imagem, upload de avatar ou geração HeyGen. O sistema deve calcular e
exibir o número de outputs antes de renderizar para evitar explosão combinatória.

## 8. Interface local alvo

Stack: React/TypeScript + FastAPI, servidos em loopback.

Telas mínimas:

1. Campaigns — lista, progresso e próximo bloqueio;
2. Campaign Detail — copy, Voice Master, variantes e assets;
3. Image Import — seleção de pasta/manifest, dry-run e cobertura;
4. HeyGen — uploads, gerações, polling, falhas e downloads;
5. Editing — profile, overrides e output variants;
6. Preview — composição 9:16 aproximada;
7. Renders — fila, player, review e path final.

O preview pode usar poster/frame amostrado e overlays HTML/CSS. Ele precisa representar posição,
quebra de linha, fonte, cor e framing com utilidade operacional, mas não precisa reproduzir pixels,
timing ou mixagem do renderer. Um label deve deixar isso explícito.

Sem timeline tipo CapCut, canvas livre, colaboração, login, permissões, cloud hosting ou edição
frame a frame.

## 9. Limites de engenharia do MVP

### Mantidos

- validação em trust boundaries;
- secrets fora do banco, manifests e logs;
- trusted roots e prevenção de traversal/symlink escape;
- proteção contra paid-action duplicada;
- outputs versionados e hashes;
- testes com fakes, sem chamadas pagas por default;
- full verification antes de declarar `LOCAL_VERIFIED`.

### Deliberadamente simplificados

- uma pessoa e uma máquina;
- bind em `127.0.0.1`;
- sem autenticação local no primeiro MVP;
- SQLite e filesystem local;
- polling em vez de infraestrutura distribuída;
- no máximo um frontend e um processo local de worker por default;
- observabilidade por estados/jobs/logs locais, sem stack externa;
- revisão humana simples, sem RBAC ou workflow configurável.

Adicionar hardening apenas quando houver exposição de rede, múltiplos usuários ou evidência de
falha operacional real.

## 10. Histórico técnico preservado

### Goals concluídos

- Goal 0 — Repository Alignment;
- Goal 1 — Campaign Foundation;
- Goal 2 — Persistent Job Orchestration;
- Goal 3 — Voice Master;
- Goal 4A — Image Domain & Persistence;
- Goal 4B — Google Flow Browser Runtime;
- Goal 4C — Flow Generation, Download & Recovery.

Todos estão `IMPLEMENTED` e `LOCAL_VERIFIED`. Canary de provider não pode ser inferido de testes
mockados ou do nome de commits.

### Trabalho Flow pausado

O antigo Goal 4D (QC/review/canary real) está congelado. Só deve ser reaberto se:

- imagens manuais se tornarem gargalo medido;
- o caminho delivery-first já estiver gerando vídeos end-to-end;
- houver disponibilidade para manter automação de UI externa;
- um novo design for aprovado.

### Lições que permanecem válidas

- a headline é visual e não deve contaminar áudio/captions;
- uma Voice Master por campanha reduz custo e variação indesejada;
- correlacionar downloads por evidência é melhor que confiar em ordem/nome;
- ações externas ambíguas precisam de reconciliação, não retry cego;
- IDs remotos devem ser persistidos antes de polling;
- source e final nunca devem ser sobrescritos;
- interface operacional vale mais que editor genérico para este caso de uso;
- variações baratas devem acontecer downstream, depois dos assets caros.

## 11. Fora do MVP atual

- automação ativa do Google Flow;
- canário Flow;
- editor com timeline;
- preview frame-perfect;
- keyframes livres;
- publicação automática em redes sociais;
- geração de copy pela aplicação;
- múltiplos usuários, RBAC ou autenticação remota;
- cloud hosting, storage próprio ou sync engine;
- mobile app;
- otimização autônoma baseada em performance de anúncios.

## 12. Questões que podem ser decididas no momento do Goal

Defaults atuais, para não bloquear desenvolvimento:

```yaml
pilot:
  campaign_variants: 3
  heygen_paid_render_limit: 3
  heygen_concurrency: 2
  renderer: ffmpeg_ass
  ui_host: 127.0.0.1
  ui_port: 8742
  image_approval_on_import: true
  headline_variants_per_video: 3
  preview_mode: approximate
```

Configurações reais de avatar/engine HeyGen, música default e pasta final podem ser selecionadas
no primeiro canário sem redesenhar a arquitetura.

## 13. Fontes de verdade

1. tarefa explícita atual;
2. este `PROJECT-MEMORY.md` para decisões duráveis e estado real;
3. `PRD-MVP-MASS-VIDEO-AUTOMATION.md` para o produto-alvo;
4. contratos e testes para comportamento implementado;
5. `GOAL-ROADMAP.md` para sequência;
6. `README.md` para orientação operacional resumida.
