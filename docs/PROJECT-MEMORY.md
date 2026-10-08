# Auraly Mass Video Pipeline — Memória do Projeto

**Atualizado em:** 2026-10-08
**Decisão vigente:** MVP delivery-first para uso local e pessoal

Este documento guarda decisões duráveis e fatos verificados. O estado entregue aparece separado
do produto planejado para impedir que roadmap seja confundido com capacidade existente.

## Entrega atual: D5A.1 — Renderer local (2026-10-08)

Integração autorizada pelo usuário: fast-forward de `main` `42beda7` → `8be3fab`.
Gate completo fresco na cópia principal após merge: 19/19, 350 frontend,
1.877 Python / 27 skips (pytest 726,17 s), exit 0. Checkpoint abaixo preserva
o histórico da branch; Actions desta integração ainda pendentes.

`IMPLEMENTED`, `LOCAL_VERIFIED` (Windows) na branch `feat/d5a1-renderer`,
código corrigido `f08fbdd`; gate final sobre `7f0b7e6`: 19/19, 350 frontend,
1.877 Python / 27 skips (pytest 721,38 s). Ruff/mypy/build/schemas aprovados;
cinco moderate transitivos preexistentes permanecem no audit abaixo do limiar high.
Revisão independente: zero Critical, três Important corrigidos com RED→GREEN
em um passe, zero Minor; suite focada 54 passed / 1 skip. Sem merge/push ou
Actions desta branch. Linux local indisponível (WSL não instalado); CI pendente.
Evidência: `docs/superpowers/2026-10-08-d5a1-verification.md`.

CLI `edit render` consome o EditBatchPlan salvo e processa variantes em ordem.
Master fixo 1080×1920, 30 FPS, H.264/AAC/yuv420p/faststart, probe e full decode.
Fonte local exata; texto ASS literal, fit e safe zones medidos por libass.
Captions exigem timing existente/source_mp4; pesos 400/700, sem word highlight.
Voz permanece no MP4 original: AAC copiado sem música; mix com ducking fixo,
amix normalize=0 e limiter sem auto-gain. Nunca reinserir/trimar WAV nesta fase.

Recibos imutáveis por outputHash/runtime, planHash como provenance do produtor;
outro plano pode reutilizar o mesmo output sem modificar recibo. Rehash de inputs
antes de publicar; falha de variante isolada, sem overwrite/adotar órfãos.
Dry-run não grava nem mede fit. No Windows, work root usa caminhos longos nativos.
Nenhum novo Job/API/UI/migration/provider/dependência. D5A.2 Jobs/UI e D5B
QC/review/delivery permanecem PLANNED; Flow continua pausado/não bloqueante.

## Histórico: D4B.3c — Preview aproximado (2026-10-08)

Integração autorizada pelo usuário: fast-forward de `main` `dd7203c` → `fdfb3cb`.
Gate completo fresco na cópia principal após merge: 19/19, 350 frontend e
1.823 Python / 26 skips (677,47 s), exit 0. Os checkpoints da branch abaixo
preservam o histórico anterior à integração; Actions desta publicação pendentes.

`IMPLEMENTED`, `LOCAL_VERIFIED` na branch `feat/d4b3c-preview`, código `b8164a5`;
gate fresco Windows 19/19, 350 frontend e 1.823 Python / 26 skips (669,34 s).
Ruff/mypy/build/schemas aprovados; cinco moderate transitivos preexistentes.
Revisão independente: nenhum Critical, dois Important corrigidos com RED→GREEN
em uma rodada; cobertura específica de caption readonly adiada como Minor.
Um gate anterior passou nos testes mas parou em tipagem do teste novo; ajuste
mínimo e repetição completa passaram. Sem merge/push desta etapa.
Evidência, revisão independente, decisões e estado do gate final em
`docs/superpowers/2026-10-08-d4b3c-verification.md`.

GET local PNG em memória limitado a 720 px por eixo/4 MiB/10 s, validando
campanha, ready, path confiável e hash real do MP4. Sem arquivo de poster,
Job, cache persistente, migration ou dependência nova. Frame ligado à identidade
campanha/render/hash, com abort/cleanup; overlays não disparam POST ou novo GET.
Rascunho visual usa as três camadas existentes sem duplicar planner/hash.
Plano validado/salvo usa manifest e captionInput exatos; consulta readonly
preserva draft, validação e worker existentes. Fonte fallback e limites
estáticos explícitos; avisos de overflow/safe zones fora do canvas, proporção
preservada dentro de 360 × 640 px. Sem player/timeline/render ou chamada paga.
Neste checkpoint, D5 renderer era o próximo recorte planejado; Flow permanece pausado.

D4B.3b e correção mínima do teste CI foram integrados em main `dd7203c`.
Actions Windows passou; Linux foi cancelado na preparação antes dos testes.
O texto abaixo preserva o checkpoint anterior à integração, não o estado atual.

## Histórico: D4B.3b — Overrides e variantes UI (2026-10-07)

`IMPLEMENTED`, `LOCAL_VERIFIED` na branch `feat/d4b3b-editing`, base `12823be`.
Gate fresco Windows após revisão/correções no código `e33d9df`: 19/19 etapas,
341 frontend e 1.807 Python / 25 skips (pytest 660,75 s). Ruff, mypy, build e
schemas sem drift; audits no limiar high aprovados, cinco moderate transitivos
preexistentes permanecem. Doctor retorna sucesso com avisos de memória/recursos
opcionais; isto não certifica render. Sem merge/push ou Actions desta branch.
Evidência e decisões: `docs/superpowers/2026-10-07-d4b3b-verification.md`.

Painel por campanha seleciona um MP4 HeyGen ready e profile exato por ID/versão/hash.
Overrides completos em campanha/vídeo/variante e headlines A/B explícitas,
limite inicial três; herdar omite, limpar nullable envia null, false/zero/RGBA
preservados. Fonte/música/timing por path/hash, música com aceite separado.
Captions seguem copy/voz aprovadas; timing ausente fica pendente, sem editor/ASR.
Validar persist=false cria Job/audit, não artefatos; Salvar persist=true revalida
o mesmo request/plano e exige GET exato. Worker local_operations manual.
Unknown congela sem repost; plano encontrado sem Job não prova autoria do POST.
Draft somente em memória, com descarte confirmado inclusive Back; consulta
readonly não reconstrói draft. Assets upstream/profile e budget/copy não mudam.
Sem backend/schema/migration/dependências novos, provider pago, preview ou render.

Revisão independente: nenhum Critical, três Important corrigidos em uma rodada
RED→GREEN (guarda de múltiplos drafts, colisão de key, foco do campo inválido),
sem re-review. Duas Minor adiadas: guard cliente de trim invertido (backend
rejeita) e loading preso ao limpar seleção durante GET. Primeiro gate passou
todos os testes, mas parou no mypy de JSON nullable do teste; narrowing mínimo
passou no mypy/browser e gate completo foi repetido com sucesso.
Sete browser E2E editoriais reais com providers fake passaram no gate final.
Não amplia PROVIDER_VERIFIED D2C. Próximo recorte: D4B.3c preview aproximado;
D5 renderer continua planejado, Flow pausado/não bloqueante.

## Histórico: D4B.3a — Profiles UI (2026-10-07)

`IMPLEMENTED`, `LOCAL_VERIFIED` na branch `feat/d4b3a-profiles`.
Gate final Windows pós-correções: 19/19, 275 frontend e 1.800 Python / 25 skips
(602,27 s). Revisão independente: nenhum Critical, três Important corrigidos
com regressões RED→GREEN; browser Back específico adiado como Minor.
Cinco moderate transitivos preexistentes permanecem; audits no limiar high passam.
Estado no fechamento daquele checkpoint: sem merge/push ou Actions da branch.
Integração posterior em `main`: `f3ac38a`. Evidência em
`docs/superpowers/2026-10-07-d4b3a-verification.md`.
Design e plano aprovados, execução Nativa. UI global `#/profiles`, formulário
completo, consulta readonly, criação e base + 1 imutável sobre quatro endpoints
existentes. Sem backend/migration/dependência nova, worker, provider ou mídia.
Fonte/música referenciadas por path relativo + SHA-256 explícito; publicação
continua `validate_assets=False`, não aprova disponibilidade ou uso dos assets.
Unknown congela payload e exige GET exato; nenhuma repetição automática do POST.
Rascunhos em memória com confirmação de descarte; seleção antiga não troca dados atuais.
Browser/API/arquivos reais de teste: seis testes passaram, incluindo conflito,
resposta perdida, reload, teclado e viewport 320/390; zero provider/Jobs novos.
D4B.3b/c e D5 eram planejados neste checkpoint; o estado vigente é registrado acima. Flow pausado.

## Histórico: D4B.2b.2 — HeyGen Operations UI (2026-10-05)

Branch `codex/d4b2b2`, execução Nativa do design/plano aprovados. `IMPLEMENTED`,
`LOCAL_VERIFIED` pré-revisão em 2026-10-05: gate Windows 19/19, 214 frontend e
1.793 Python aprovados / 25 skips (pytest: 582,02 s). Revisão independente concluída
em 2026-10-07: três Important reproduzidos e corrigidos em uma rodada RED→GREEN;
nenhum Critical/Minor, sem re-review. Fechamento pós-revisão `LOCAL_VERIFIED`:
gate Windows 19/19, 220 frontend e 1.793 Python / 25 skips (569,68 s).
Bloqueio transitório da auditoria resolvido com os patches autorizados abaixo;
sem merge/push, Actions desta branch ou chamada paga.

Checkpoint pós-revisão (2026-10-07): bloqueio após GET inválido preservando plano e
confirmação; resultado de submit só adotado com Job filho de campanha/tipo/cena
compatíveis, ausência temporária aguarda leitura sem repost; reconciliação exige
read IDs posteriores e estados/contexto compatíveis, não apenas IDs já conhecidos.
Gate final UI 4/4, 220/220 testes, build/tipos/audit limpos. Python completo:
1.793 aprovados / 25 skips (576,80 s), incluindo browser; Ruff/mypy/schemas passaram.
`verify.py full` parou na etapa 18/19: audit de produção raiz reporta dois high
em `sharp@0.35.4` e `source-map-js@1.2.1`, além de cinco moderate transitivos.
Advisories: [sharp](https://github.com/advisories/GHSA-wq5f-xc86-pv6w) e
[source-map-js](https://github.com/advisories/GHSA-68fv-2mgg-jv7q).
Após aprovação explícita, lockfile atualizado para `sharp@0.35.5` (incluindo seus
binários/libvips) e `source-map-js@1.2.2`, dentro dos ranges existentes. Comparação
semântica confirmou que só essa família mudou; HyperFrames 0.7.104 e package.json
intactos. Reinstalação locked e smoke PNG nativo passaram. Gate completo repetido:
19/19, 220 frontend e 1.793 Python / 25 skips (569,68 s), Ruff/mypy/build/schemas
e audit no threshold original aprovados. Permanecem cinco alertas moderate na cadeia
sprintf-js/roarr/global-agent/onnxruntime-node/HyperFrames; não foram suprimidos nem
motivaram upgrade forçado. Esta evidência local não prova Linux/Actions ou provider.
Reusa React/FastAPI/SQLite, MCP OAuth e workers existentes, sem contratos backend,
migrations, novas dependências diretas ou mudança no engine; apenas os patches
transitivos autorizados acima.

- preparação all-scenes: wrapper `local_operations` → Job `heygen.asset.upload`
  (`heygen_assets`); reuso sem filho não é novo upload;
- plano informativo e confirmação paga separados; defaults fixos 1080p, limite total
  quantitativo incluindo todo histórico; backend recalcula no submit;
- wrapper de submit cria reservas/Jobs, não MP4s. Start `heygen_videos` explícito;
- reconciliação só de render cujo Job está blocked: ID conhecido readonly, vínculo
  manual confirmado para novo ID; sem ID, prova de no-dispatch apenas no backend;
- recovery Job observado por leituras atuais; sem resume/start automático;
- MP4: caminho relativo ao work root, hashes, probe e binding; abrir fora da UI;
- guards de DTO/campanha/cena/IDs únicos, stale preserva último fato; unknown não
  reenvia nem identifica por coincidência; drafts/lifecycle independentes.

Browser/proxy/API/SQLite/workers reais: 4/4 testes novos passaram isoladamente e
novamente na seleção focada (21/21, incluindo 4 de serviço) e no gate completo.
Seed mantém três renders 720p históricos; conta fake nova/default1080 cria mais três
sob limite total 6. Dois uploads de imagem + um WAV, três creates, MP4/hash/probe reais,
reuso/reload sem nova mutação; dispatch ambíguo reconciliado por ID exato sem outro
create. Resposta POST persistida perdida não reenvia. Viewport 390×844 sem overflow,
sem novo CSS. Download sintético 1080p separado para preservar QC e seed 720p.

D4B.3 (profiles/variants/preview) e D5 (renderer) estavam planejados neste checkpoint. Flow permanece
pausado e não bloqueante. Esta entrega local não amplia o `PROVIDER_VERIFIED` D2C.

### Histórico D4B.2b.1 — Voice Master UI (2026-10-04)

Implementação na branch `codex/d4b2b1`, integrada em `4a06c4d`. Reusa React,
FastAPI, SQLite e workers existentes. `IMPLEMENTED`, `LOCAL_VERIFIED`.
Gate completo Windows pós-revisão: 19/19 etapas, 130 testes
frontend e 1.789 Python aprovados / 25 skips. Browser/proxy/API/SQLite/workers reais
validaram geração fake sem auto-start, import em duas fases/review e budget gravado
com resposta 503 sem repost; 13 testes de navegador aprovados.
Revisão independente sobre `eda1d93` concluída: dois Important e o aviso de drafts
(reclassificado de Minor por risco de perda sem aviso) corrigidos em uma rodada
RED→GREEN, nove regressões frontend e gate completo; sem re-review. Agora wrapper
stale preserva fase/filho, coleções de voz/Jobs validam campanha antes de substituir
snapshot e submissões não descartam o aviso de outros rascunhos.
Minor adiado: budget legado acima do inteiro seguro do browser aparece como erro
genérico/stale, não como projeção invalid; frontend rejeita, sem autorizar gasto arredondado.
Sem nova execução real de provider ou autorização paga usada nos testes.

- orçamento inicial de campanha por GET/POST focado, projeção missing/configured/invalid,
  confirmação própria, transação serializada, metadata preservada e replay idêntico no-op;
  não permite editar/reparar budget estabelecido, não é saldo/custo nem autorização de gasto;
- geração fixa copy aprovada e defaults atuais, responsável/teto/checkbox pagos explícitos,
  Job `voice.generate` direto e start manual `voice_generate`, sem force-regenerate;
- import relativo ao project root (Explorer, MP3/WAV até 100 MiB): operação local →
  Job `voice.import`, starts separados e original preservado; não exige budget;
- review pela UI após ouvir WAV fora dela: ator e confirmação, motivo na rejeição e
  na exceção exata de transcript importado; gates/backend intactos;
- paths/hash/duração/QC e fatos históricos de review persistidos, snapshot stale mantido,
  guards de DTO/campanha/lifecycle, submit único e aviso de draft; unknown faz GET, não repost;
  adoção explícita de Job só inspeciona fatos, não prova identidade/autoria de request perdida.

Actions do Voice slice: Linux passou; Windows inicialmente deu timeout ao aguardar
“Criar campanha”. Dois runs locais 13/13 não reproduziram (o segundo com diagnóstico
de erros JS/requests). Um único rerun do Job falho passou; run 37282794254, attempt 2,
Linux/Windows success. Causa não confirmada: nenhum código corrigido, retry ou timeout
maior acrescentado. Histórico técnico abaixo preservado.

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
- campanhas aceitam uma ou mais cenas; lista vazia, IDs repetidos e locações repetidas
  continuam inválidos. O canário pode usar uma cena sem alterar o banco;
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

Extensão de importação externa: CLI `voice import` submete um job local `voice.import`, e
`voice run-import` processa apenas essa campanha/tipo. MP3/WAV até 100 MiB sob project root
confiável, original preservado, cópia exclusiva com hash e origem `imported`, sem provider ID
ElevenLabs. Decodificação integral para WAV intermediário reutiliza o processamento existente;
trim somente nas bordas, mono/48 kHz/24-bit. `local_ready` somente após publicação completa.
QC usa transcrição independente faster-whisper e aprovação humana existente; jamais auto-approve.
Replay verifica artefatos e reutiliza job/voz; falha é explícita e sem retry automático.

O runtime nativo de transcrição já apresentou bloqueio pelo Windows Application Control.
Não contornar nem usar a copy esperada como transcrição: sem ASR real disponível, o canário para.
Integração com HeyGen usa o mesmo WAV aprovado por hash; testes fake não provam o provider real.
Validação compartilhada do WAV usa `ffprobe` existente: aceita PCM inteiro, incluindo o PCM24
extensível produzido pelo FFmpeg que `wave` do Python 3.11 não lê. Formato, duração finita
positiva e tolerância de 50 ms continuam obrigatórios, além de hashes e aprovações existentes.
Probe operacional em 2026-09-30: import nativo e modelo `small.en` já em cache carregaram;
transcrição local do MP3 selecionado reconheceu fala sem download/créditos. O bloqueio anterior
não se reproduziu. Isso não aprova a copy nem comprova o canário pago.

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
e concorrência 1..2. Em 2026-09-30, `IMPLEMENTED` e `LOCAL_VERIFIED`: gate Windows 13/13,
1357 testes aprovados/18 skips e revisão independente com correções verificadas.
`PROVIDER_VERIFIED` continua pendente D2C.

MCP `create_video_from_image` recebe image asset + audio asset compartilhado. Não há engine
ou idempotency key inventados: `provider_default` é validado contra schema remoto; callback
serve apenas de correlação. `submitting` é persistido antes da chamada e o ID antes de polling.
Resultado ambíguo bloqueia sem paid retry; ID conhecido retoma leitura/download. Sem ID, binding
manual exige confirmação e recusa identidade contraditória/duplicada.

Após esgotar tentativas locais, reconciliação explícita vincula atomicamente um job de recuperação
ao mesmo render/ID remoto. Jobs anteriores e auditoria são preservados; nenhuma reserva/geração
paga adicional. Há um job atual por variante, com jobs históricos adicionais nesse caso.

Config material é separado de polling/concurrency na identidade. Jobs/reservas são reutilizados
em replay; falha não devolve budget automaticamente. O WAV aprovado é validado por hash/duração,
assim como imagem, copy, conta e assets ready antes de dispatch. Assinaturas/URLs não persistem.

Source MP4 H.264/AAC vertical passa SHA-256, ffprobe, tolerância `max(2s,5% do WAV)` e full decode.
Hardlink publica sem overwrite; publication.json fixa identidade/hash antes do MP4, source.json
fecha o manifest antes de ready. Crash entre publicação e commit recupera sem novo download.

Interface: `heygen plan-videos|generate-videos|run-videos|videos|reconcile-video`. Generate reserva;
run pode consumir créditos. Canário D2C tem autorização de consumo limitado, mas não foi executado;
editor/UI não entregues. O primeiro canário planejado tem teto de um render e concorrência um.
O conflito histórico (CampaignCreate exigia três cenas) foi resolvido com ajuste separado
aprovado para uma ou mais cenas; o planner continua incluindo todas, sem elevar o teto autorizado.

### Checkpoint D2C — 2026-09-30

Estado atual, supersedendo as notas de preparação acima: aprovação humana do WAV importado
registrada com motivo, preservando `review_required`, ASR, QC e hashes. Exceção aceita apenas
origem `imported`, headline não falada e o único achado de revisão da transcrição; jamais
`mismatched` ou falha técnica. Migration 0010 preserva histórico e imutabilidade.

Contrato MCP real usa camelCase nos argumentos de ferramentas, snake_case no objeto image,
identidade de usuário hasheada quando workspace/id não são expostos e respostas `items`.
Paginação inesperada bloqueia; erro pós-dispatch nunca autoriza repetir criação.
Gate completo: 14/14 etapas, 1430 testes aprovados/19 skips, revisão sem achados críticos/importantes.

Upload real da imagem e WAV concluído. Uma reserva e um envio de geração foram executados;
sem ID verificável na resposta, render ficou `reconciliation_required`. Consulta somente leitura
encontrou um vídeo concluído no mesmo horário e com duração do WAV, sem correlação material
exposta pelo MCP. Rafael confirmou o vínculo manual; download/QC concluíram e o render está
`ready`: H.264/AAC, 1080×1920, 25 fps, 7,224 s, 3.603.735 bytes, full decode sem erros.
Replay de plan/submit/run preservou o mesmo render, sem nova reserva ou chamada paga.
`PROVIDER_VERIFIED` somente para este canário com reconciliação manual; a resposta automática
de criação sem ID segue limitação conhecida, não resolvida por gerar outro vídeo.
Revisão visual final continua humana; este checkpoint precede o D3A descrito abaixo.
Evidência: `docs/superpowers/2026-09-30-d2c-canary-verification.md`.

Alinhamento SQL em 2026-10-01: migration 0011 adiciona gatilhos INSERT/UPDATE que recusam motivos
vazios com whitespace Unicode, caracteres NUL e mais de 512 caracteres antes de trim, sem reconstruir a tabela
ou modificar aprovações existentes. O CHECK do modelo aplica a mesma regra estrutural.
Sanitização de dados sensíveis permanece responsabilidade do serviço/domínio existente; SQL
direto não substitui esse validador. Não duplicar regexes ou registrar funções Python no SQLite.

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

## 7. Modelo de edição: D3A entregue e capacidades alvo

D3A implementou contratos `EditProfile` v1 e `EditManifestV2` v2 separados do legado,
resolver puro, overrides tipados, provenance por campo e hash determinístico.
Profiles são arquivos versionados com envelope/checksum, não tabelas SQL; alterações
criam nova versão. Manifests são snapshots sem timestamps ou renderer version inventada.
Precedência profile/campaign/video/outputVariant; omissão herda, false/zero são valores,
null explícito só em campos nullable. Headline continua visual-only.

CLI `edit` cria/lista/consulta/versiona profiles, resolve com dry-run, consulta e valida
manifests v2. Paths relativos ao project root, assets verificados, writes exclusivos,
replay íntegro e recusa de corrupção. V1 continua em ingest/validate legado sem conversão
que descarte cuts/punch-ins/b-roll. Sem provider, DB mutation ou auto-approval.
Naquele checkpoint, texto/timing de captions e planejamento A/B em lote ficaram no D3B.
O checkpoint D3B abaixo supersede essa pendência; UI/preview/render continuam futuros.
Evidência local em `docs/superpowers/2026-10-01-d3a-verification.md`.
`IMPLEMENTED`, `LOCAL_VERIFIED`: gate pós-correções 15/15, 1491 passed/20 skipped.
Revisão independente encontrou três Important corrigidos com RED→GREEN; nenhum Minor adiado.

### Checkpoint D3B — 2026-10-02

Planner de lista explícita com `maxOutputs` estrito (default 3), keys únicas, overrides
e IDs/hashes/filenames determinísticos. `EditBatchPlan` separado embute manifests v2 intactos;
label afeta planHash, não outputHash. Caption input também participa do outputHash.
CLI `edit plan|plan-get`, schemas/exemplos e publicação JSON exclusiva por planHash.
Todos os outputs validam antes da publicação; dry-run não escreve planos/manifests.
Sem novas tabelas, migrations, Jobs, providers ou dependências.

SQLite existente é aberto somente leitura: render ready, copy exata vinculada ao WAV
aprovado, refs de imagem/voz e conteúdo local verificados. Captions usam spoken_text
da copy, nunca headline. Timing opcional validado por hashes, operador, origem,
timebase source_mp4, intervalos e cobertura completa dos tokens; ausência fica missing.
Não implementa alinhamento automático, UI ou renderer.

Smoke real D2C: três headlines, replay estável, SQL/source/WAV intactos e zero paid calls.
Profile local de teste publicado fora da campanha: `editing/profiles/d3b-smoke/1/profile.json`.
Timing real ausente; captions desabilitadas no smoke, sem fingir legendas sincronizadas.
Evidência e gate em `docs/superpowers/2026-10-02-d3b-verification.md`.

O restante desta seção descreve o modelo alvo, não capacidades adicionais entregues.

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

D4A foi dividido em duas entregas. D4A.1 está `IMPLEMENTED` e `LOCAL_VERIFIED`:
API de consultas/status, `auraly api serve --port 8000`, OpenAPI e DTOs allowlisted sobre
serviços existentes. SQLite abre somente leitura, exige revision/tabelas/colunas atuais e não
migra/cria storage. Consultas preservam renders e planos históricos por ID/hash; Flow pausado
não bloqueia o status. Leituras não executam provider, ASR, probes ou workers.

D4A.2 implementa 19 POSTs tipados sobre os serviços existentes e um worker local explícito.
GET preserva o engine readonly; ações usam outro engine existente RW, sem migrations/DDL.
Operações longas apenas enfileiram Jobs: import de imagens/voz, review de voz, preparação/
reserva/reconciliação HeyGen e planejamento editorial. O runner tem escopo campanha/tipo,
start/status/stop e shutdown que drena a execução antes de fechar engines. Nenhum startup
faz dispatch. Até dry-run persiste um Job, não seus efeitos de domínio. Profiles publicam
metadados imutáveis sem mídia na request; o resolver no Job verifica os assets antes do plano.
Bodies JSON/Origin loopback, aprovações, budget, idempotência e checkpoints são preservados.
Uso enqueue/start/poll/stop e import manual está no README. O painel React D4B.1 está
implementado; D4B.2a acrescenta campanhas/copy/import/review de imagens. Voz/HeyGen
pela UI (D4B.2b), preview (D4B.3) e renderer (D5) continuam planejados;
sem timeline ou preview frame-perfect.
O comando aceita roots/DB locais confiáveis, bind fixo 127.0.0.1, um processo e sem access logs.
Banco fora do project root é permitido; work root fora não. Nenhum canário pago é necessário
para D4A.1, e a evidência histórica PROVIDER_VERIFIED não foi ampliada.

Evidência local de 2026-10-03: `uv run python scripts/verify.py full`, 15/15 etapas;
1.627 testes aprovados / 23 skips em 356,29 s no Windows, código em
`678d661e4386114abd0c8e56ba20199d20b2348a`. Revisão independente da branch encontrou três
problemas relevantes; testes RED→GREEN e o gate final confirmaram as correções de IDs
existentes de campanha, status de jobs `voice.import` e traceback de startup do Uvicorn.
Logs rotineiros ficam suprimidos; falhas de startup usam mensagem CLI estática.
Essa evidência histórica D4A.1 não incluiu nova execução Linux/Actions ou real de provider.

Em D4A.2, Actions 37125176378 confirmou a correção de filenames SQLite no Linux no SHA
`0693674c9e6caf9783201a721a04e92eb2c85052`: 1.677 passed/6 skipped, harness 15/15.
Windows focado nesse SHA: 951 passed/12 skipped, 3/3. Isso cobre Tasks 1–4, não o código
posterior ainda local naquele checkpoint. PR #2 estava draft, sem merge. O teste HTTP ponta a ponta usa
mídia local real e provider/transcriber fake: import manual, WAV/review, uploads, três MP4s
e dois manifests A/B com source/voz/copy fixados, sem chamadas adicionais ao provider.
Não houve nova chamada paga nem ampliação de `PROVIDER_VERIFIED`.

Verificação D4A.2 local no código `21b9d5d` em 2026-10-03: gate Windows 15/15,
1.697 passed/24 skipped em 393,13 s; Ruff, mypy source/tests, quatro schemas e audit
production aprovados. Doctor sinaliza Docker indisponível, sem impedir o harness;
nenhum runner Linux local está disponível. Esse foi o gate anterior à revisão final.

Revisão independente única em `1ae3eec..ca00f47`: três achados Important, sem Critical
ou Minor. Corrigidos em uma rodada com regressões RED→GREEN: resume público recupera
somente checkpoint durável validado (sem executar novamente a mutação), planner aceita
o DB externo explicitamente configurado, e import verifica/interpreta o mesmo snapshot
do manifest e confere a campanha antes de executar. Auditoria de tentativas falhas é
preservada; wrappers single-attempt sem checkpoint não ganham force-resume. Contratos
públicos mínimos foram registrados no spec; CLI padrão e roots de mídia não mudaram.

Gate pós-fix no código `73ca3587ebfd94aa8d3f42a6b9b0744b61b07168`: Windows 15/15,
1.701 passed/24 skipped em 396,42 s; quatro regressões finais passaram, inclusive a
negação de resume sem checkpoint. Sem re-review: testes e gate verificam as correções.
D4A completo `IMPLEMENTED`, `LOCAL_VERIFIED`; D4B React/preview é o próximo passo.
Naquele checkpoint não havia push/merge dessa atualização ou execução Linux/Actions posterior a `0693674`.

D4A.2 integrado em `main` no SHA `9cd8731`, com Actions 37146845609 (push) e
37146846960 (PR) aprovados em Linux/Windows. Essa evidência não amplia `PROVIDER_VERIFIED`.

### D4B.1 — painel local de campanhas

React/TypeScript/Vite em `web/`, sem router ou nova biblioteca de estado. Hash navigation,
lista/detalhe de campanhas, metadados de copy/cenas/imagens/voz/renders, Jobs e operações.
API é a única fonte de verdade; não há acesso direto ao banco/filesystem/provider nem
serving de mídia. Proxy loopback 5173→8000 preserva Host/Origin, sem novo CORS.
Lista consulta a cada 5 s e status/Jobs/worker a cada 2 s, sem overlap; pausa com a aba
oculta e descarta respostas de navegação anterior. Falhas preservam dados anteriores
com timestamp e aviso stale, sem inventar progresso.

Únicos POSTs do painel: worker start/stop, com confirmação de início, bloqueio contra clique duplicado,
sem retry automático e reconciliação por GET após resposta incerta. 404 scoped do worker
significa desconhecido, não idle; 409 preserva a autoridade do backend. Stop drena o Job
ativo e não cancela provider/reembolsa créditos. OAuth segue na CLI.

Frontend tem 56 testes, incluindo regressão A→B→A com POST atrasado. Integração tem sete
testes com navegador/proxy/API reais, provider fake e mídia sintética: leitura sem mutação,
start/stop/draining, Origin estrangeiro, JSON malformado, responsividade, porta ocupada e
bloqueio de acesso a arquivos da raiz por alias de dependência ou `/@fs`.
Harness inclui modo `ui` e quatro etapas UI no `full`; Actions provisiona Node 22/cache
dos dois lockfiles e FFmpeg no Windows. CI dessa nova branch depende de publicação.
Compatibilidade das referências Windows nos fixtures foi verificada com mypy Linux.
Gate local Windows pré-revisão de 2026-10-03: `uv run python scripts/verify.py full`, 19/19 etapas;
41 testes frontend e 1.710 Python aprovados / 24 skips. Ruff, mypy source/tests, schemas,
build/typecheck e audits production aprovados. D4B.1 `IMPLEMENTED`, `LOCAL_VERIFIED`;
sem nova evidência de provider ou execução runtime Linux da nova branch.

Revisão independente única de `ec9496b..cfa0ce0`: três Important, sem Critical/Minor e
sem itens deixados fora de julgamento. Uma rodada de correção RED→GREEN removeu a dependência
parent `file:..` e seu link para a raiz; reconciliação do worker passou a exigir GET iniciado
após o POST terminar (sequência de request, não timestamp); validação dos campos consumidos
rejeita DTOs aninhados incompletos antes de substituir o último snapshot válido. Labels
desconhecidos também permanecem texto, sem usar propriedades herdadas do mapa.
Lock regenerado a partir do diretório `web/`, sem parent package. Gate pós-fix Windows:
19/19 etapas, 56 testes frontend e 1.711 Python aprovados / 24 skips em 426,47 s.
Sem re-review; regressões e gate completo verificam as correções. Nenhuma chamada paga.

Spec e plano: `docs/superpowers/specs/2026-10-03-d4b1-campaign-panel-design.md` e
`docs/superpowers/plans/2026-10-03-d4b1-campaign-panel.md`. D4B.2 adicionará formulários
operacionais/import; D4B.3 adicionará profiles, variants e preview aproximado.
Runtime por dois terminais neste slice; launcher/distribuição ficam para depois.

### D4B.2a — campanhas/copy e import manual pela UI (2026-10-04)

`IMPLEMENTED`, `LOCAL_VERIFIED`; revisão independente e correções concluídas. CI pendente de publicação,
nenhum novo `PROVIDER_VERIFIED` ou chamada paga.

Gate Windows pré-revisão `verify.py full`: 19/19, 86 testes frontend, 1.753 Python aprovados /
25 skips em 464,31 s. Ruff, mypy src/tests, schemas sem drift, build/typecheck e audits
production passaram. Nove testes browser/proxy reais passaram. HyperFrames doctor
continua reportando Docker/opcionais indisponíveis com exit 0, como no baseline; isso
não comprova renderer. Código-base das Tasks 1–4: `7c654c8`, `783e0ba`, `3c65433`, `b0ef949`.

React reutiliza os endpoints e o worker atuais; API acrescenta POST de publicação de
associações e diagnósticos/snapshot opt-in compatíveis com jobs legados. Sem tabelas,
migrations, bibliotecas ou segundo motor de jobs. Copy aprovada exige ator/checkbox;
sourceText canônico e histórico versionado, headline fora da narração.

Prepare retorna paths relativos ao projeto; Explorer recebe arquivos na pasta images.
Associação por variante é explícita, sem watch/inferência/upload. Worker publica manifest
por hash dos bytes canônicos, sem sobrescrever o template. Validação inspeciona cobertura,
paths, orientação e mídia; valid false é completed com erros, nunca importável. Fatos reais
e ID novo por validação; execute compara snapshot de hashes antes da cópia e preserva
as rechecagens do serviço existente. Provenance manual admite nomes contidos com
espaços/Unicode; paths internos gerados continuam restritos.

POST único, sem auto-retry/auto-start; polling somente para job selecionado. Respostas
tardias ou associações alteradas não restauram validação antiga. Dados bons são preservados
mas stale bloqueia ações. Reload recupera publicação persistida, nunca validação antiga.
Perda de POST exige GET pós-submissão/match verificável; resultado ambíguo fica unknown.
Review approve/replace exige ator/confirmação, reject exige motivo. GET de imagens prova
apenas estado solicitado, não autoria. Rascunhos não persistidos têm aviso de saída.

Testes browser usam Vite/proxy/FastAPI/SQLite/worker reais e imagens Pillow em tmp_path:
três importações/reviews, copy versionada, mudança de source bloqueada, nova validação,
reload sem POST e largura 390px. Providers fake devem registrar zero chamadas.
Spec/plano: `docs/superpowers/specs/2026-10-04-d4b2a-campaign-import-design.md` e
`docs/superpowers/plans/2026-10-04-d4b2a-campaign-import.md`.

Checkpoint pós-revisão: um reviewer independente avaliou `e792064`, sem Critical,
dois Important e três apontamentos inicialmente Minor. Erro HTTP 5xx após escrita
commitada passa a unknown (GET após submissão, nunca segundo POST automático);
erros genéricos sem prova de rejeição seguem a mesma regra. Teste browser reproduz
copy realmente gravada e falha 503 na consulta que monta a resposta, preservando
uma única versão nova. Recuperação de prepare não usa sufixo como identidade:
campo aditivo opcional outputPath fixa o output work-root-relative exato.
Resultados legados identificados pelo job continuam aceitos; resposta perdida
legada sem essa evidência permanece unknown.

Dois apontamentos reclassificados pelo efeito funcional também foram corrigidos:
I/O real EIO/EACCES na abertura de imagem vira job failed, não diagnóstico de mídia;
atmosfera/objeto de prova opcionais da cena entram no DTO/matcher de criação,
inclusive null, para não confirmar conteúdo diferente. Regressões RED→GREEN na
mesma rodada; sem re-review. Gate final Windows: 19/19, 96 frontend, 1.757 Python
aprovados / 25 skips em 472,17 s; typing/build/schemas/audits aprovados.

Minor adiado: exibir ação create/reuse e resumo de cobertura no dry-run; fatos e
gates continuam corretos. A revisão não estabelece garantias contra substituição
hostil concorrente de diretórios além das verificações determinísticas já cobertas;
não foi ampliado hardening para esse cenário. Actions no SHA atual e provedores
reais não foram exercitados ou declarados verificados. Nenhum merge/push nesta etapa.

Telas mínimas do alvo completo (não todas entregues em D4B.1):

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
