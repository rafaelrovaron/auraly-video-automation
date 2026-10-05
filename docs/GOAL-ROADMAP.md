# Auraly Delivery-First Goal Roadmap

**Roadmap vigente:** 2026-10-05

Este documento é a ordem operacional dos próximos Goals. O PRD define o produto; este roadmap
define como chegar a ele sem transformar cada Goal em um projeto grande demais.

## 1. Regra de execução

Cada Goal significativo segue:

```text
design aprovado
→ plano de implementação
→ TDD em tarefas pequenas
→ commits coerentes
→ verificação local
→ revisão independente
```

Não misturar provider, edição e UI no mesmo Goal. Nenhuma chamada paga é necessária para testes
locais. Canary real exige autorização explícita.

## 2. Terminologia de status

- `IMPLEMENTED`: código e testes requeridos existem;
- `LOCAL_VERIFIED`: o harness determinístico aplicável passou;
- `PROVIDER_VERIFIED`: um canário real autorizado passou;
- `PAUSED`: trabalho preservado, fora do caminho crítico;
- `PLANNED`: ainda não implementado.

## 3. Baseline já entregue

| Goal | Capacidade | Estado |
|---|---|---|
| 0 | Repository Alignment | `IMPLEMENTED`, `LOCAL_VERIFIED` |
| 1 | Campaign Foundation | `IMPLEMENTED`, `LOCAL_VERIFIED` |
| 2 | Persistent Job Orchestration | `IMPLEMENTED`, `LOCAL_VERIFIED` |
| 3 | Voice Master | `IMPLEMENTED`, `LOCAL_VERIFIED` |
| 3C | ElevenLabs Provider Canary | `PLANNED`, não bloqueia desenvolvimento local |
| 4A | Image Domain & Persistence | `IMPLEMENTED`, `LOCAL_VERIFIED` |
| 4B | Google Flow Browser Runtime | `IMPLEMENTED`, `LOCAL_VERIFIED`, `PAUSED` |
| 4C | Flow Generation, Download & Recovery | `IMPLEMENTED`, `LOCAL_VERIFIED`, `PAUSED` |
| 4D | Flow QC/Review/Provider Canary | `PAUSED` |
| D1 | Manual Image Batch Intake | `IMPLEMENTED`, `LOCAL_VERIFIED` |
| D2A | HeyGen Contract, Preflight & Asset Reuse | `IMPLEMENTED`, `LOCAL_VERIFIED` |
| D2B | HeyGen Batch Generation, Polling & Download | `IMPLEMENTED`, `LOCAL_VERIFIED` |

O código Flow permanece no repositório. Não removê-lo, reescrevê-lo ou expandi-lo durante o novo
MVP sem um Goal específico aprovado.

## 4. Sequência delivery-first

```text
D0  Documentation Alignment                    DONE
D1  Manual Image Batch Intake                  DONE
D2A HeyGen Contract, Preflight & Asset Reuse   DONE
D2B HeyGen Batch Generation, Polling & Download LOCAL_VERIFIED
D2C HeyGen Real Canary                         PROVIDER_VERIFIED (vínculo manual)
D3A EditProfile, EditManifest & Override Resolution LOCAL_VERIFIED
D3B Headline A/B Planning & Caption Inputs LOCAL_VERIFIED
D4A.1 Local Query API & Campaign Status LOCAL_VERIFIED
D4A.2 Operational Actions & Worker Integration LOCAL_VERIFIED
D4B.1 Local Campaign Panel                    LOCAL_VERIFIED
D4B.2a Campaign/Copy & Manual Image Import    LOCAL_VERIFIED
D4B.2b.1 Voice Master Forms                  IMPLEMENTED, LOCAL_VERIFIED
D4B.2b.2 HeyGen Forms                        IMPLEMENTED, LOCAL_VERIFIED
D4B.3 Editing UI & Approximate Preview        PLANNED
D5A Deterministic Renderer
D5B Render QC, Review & Delivery
D6  End-to-End Personal Pilot
```

UI entra antes do renderer final. D4 pode disparar fakes e exibir dados persistidos de D1–D3;
quando D5 chegar, a tela apenas conecta o novo job de render.

## D0 — Documentation Alignment

**Status:** documentação implementada neste replanejamento.

### Objetivo

Separar o estado entregue do novo roadmap e tornar o próximo slice inequívoco.

### Incluído

- README, PROJECT-MEMORY, PRD e roadmap alinhados;
- Google Flow marcado como preservado/pausado;
- Voice Master confirmada como capability reaproveitada;
- ordem delivery-first e limites da UI definidos.

### Saída

- os quatro documentos não contradizem o estado do código;
- D1 pode receber design e plano sem decisão arquitetural pendente.

## D1 — Manual Image Batch Intake

**Status:** `IMPLEMENTED`, `LOCAL_VERIFIED`.

### Objetivo

Transformar uma pasta de imagens criadas manualmente em assets de campanha prontos para HeyGen.

### Incluído

- contrato Pydantic `ImageImportBatch` versionado;
- manifest explícito `variantId → relative image path`;
- dry-run com cobertura, duplicidade, dimensões e erros;
- validação de extensão, mídia real, orientação e trusted roots;
- hash e cópia não destrutiva para paths versionados;
- associação a SceneVariant e reutilização do domínio ImageCandidate existente;
- seleção/aprovação explícita na importação;
- idempotência por campaign + variant + content hash;
- CLI JSON `image import-batch` e consultas existentes atualizadas;
- migration de provenance manual e ownership direto da SceneVariant;
- comandos JSON `image prepare-import`, `image import-batch` e
  `export-image-import-schema` entregues.

### Explicitamente excluído

- gerar imagens;
- Google Flow;
- UI React;
- HeyGen;
- crop ou edição da imagem;
- heurística de associação por ordem de arquivos.

### Dependências

- Campaign/SceneVariant existentes;
- ImageCandidate existente;
- helpers atuais de trusted roots, hash e paths.

### Critérios de saída

- um batch válido com três imagens deixa três variantes prontas para HeyGen;
- rerun idêntico não duplica candidatos nem arquivos;
- batch incompleto ou ambíguo falha antes de copiar/persistir;
- source externo permanece intacto;
- restart preserva associações e provenance;
- schema gerado e docs do comando estão atualizados.

### Verificação

```bash
uv run python scripts/verify.py fast --pytest tests/test_image_import_batch.py
uv run python scripts/verify.py full
```

## D2A — HeyGen Contract, Preflight & Asset Reuse

**Status:** `IMPLEMENTED`, `LOCAL_VERIFIED`.

### Objetivo

Criar a fronteira oficial HeyGen e garantir upload/reuso seguro de imagens e Voice Master.

### Incluído

- adapter protocol pequeno para provider oficial MCP/OAuth ou API;
- fake determinístico;
- preflight sanitizado de conexão/capabilities;
- `RemoteAsset` persistente para image e audio;
- upload por hash;
- uma Voice Master WAV reutilizada por todas as variantes;
- persistência do remote ID antes de avançar;
- idempotência e reconciliação de upload;
- jobs `heygen.asset.upload`;
- CLI batch para preparar assets de uma campanha.

### Explicitamente excluído

- automação do website HeyGen;
- criação de vídeo;
- polling/download;
- UI;
- retry cego de POST ambíguo.

### Dependências

- D1;
- Voice Master aprovada;
- Job orchestration existente.

### Critérios de saída

- três imagens e um WAV resultam em quatro remote assets lógicos;
- replay pelo mesmo hash reutiliza o remote asset;
- secrets e URLs assinadas não entram no banco/logs;
- provider fake cobre sucesso, falha, timeout e resultado ambíguo.

### Operação entregue

```powershell
uv run auraly heygen connect
uv run auraly heygen preflight
uv run auraly heygen prepare-assets CAMPAIGN_ID
uv run auraly job worker-once --worker-id local-worker
uv run auraly heygen reconcile JOB_ID
```

OAuth/MCP é o caminho aprovado para o MVP pessoal e pequenos volumes. Os testes locais não
estabelecem `PROVIDER_VERIFIED`; a evidência real limitada está na seção D2C abaixo.

## D2B — HeyGen Batch Generation, Polling & Download

**Status:** `IMPLEMENTED`, `LOCAL_VERIFIED` — gate Windows 13/13 em 2026-09-30,
1357 testes aprovados/18 skips; revisão independente e correções verificadas.
Evidência real posterior: D2C abaixo, um vídeo com reconciliação manual, não escala batch real.

### Objetivo

Gerar e baixar um MP4 HeyGen por variante, em lote retomável.

### Incluído

- geração por imagem + áudio importado; engine `provider_default`, sem campos MCP inventados;
- dry-run com quantidade de paid renders e assets reutilizados;
- budget gate antes da primeira submissão;
- job lógico por variante e comando batch por campanha;
- persistência de video ID antes do polling;
- polling com backoff, timeout e resume;
- limite de concorrência default 2;
- download `.part` → final, hash e `ffprobe`;
- validação de streams, orientação, resolução e duração plausível;
- source MP4 versionado e imutável;
- reconciliação antes de repetir create-video ambíguo.

### Explicitamente excluído

- captions/render final;
- UI React;
- publicação social;
- download por browser.

### Dependências

- D2A.

### Critérios de saída

- o fake executa lote de três variantes end-to-end;
- restart durante polling retoma pelos video IDs existentes;
- MP4 concluído não é gerado ou baixado novamente;
- falha de uma variante não apaga progresso das demais;
- budget acima do limite bloqueia antes de paid action.

## D2C — HeyGen Real Canary

**Status:** `PROVIDER_VERIFIED` para um vídeo com vínculo manual confirmado; review visual humano pendente.

### Objetivo

Validar o adapter e as suposições de payload contra um render real pequeno.

### Incluído

- preflight real;
- upload/reuso de uma imagem e uma Voice Master aprovada;
- uma geração real;
- polling, download e QC do MP4;
- registro sanitizado de IDs, custo e evidência.

Preparação local: campanha com uma única cena, importação de imagem/Voice Master e validação
do WAV PCM24 extensível. O teste integrado usa provider fake e reserva somente um render
(`max_paid_renders=1`, concorrência 1), sem geração paga. Aprovações reais de copy, imagem e
voz e preflight OAuth continuam necessários antes do canário; isto não é `PROVIDER_VERIFIED`.

Checkpoint 2026-09-30: voz aprovada com motivo auditável sem alterar evidência; OAuth/preflight
reais e upload de imagem/WAV concluídos. Uma reserva, concorrência 1 e um dispatch de geração.
Resposta sem ID verificável bloqueou corretamente, sem nova cobrança automática. Consulta MCP
encontrou candidato concluído com duração 7,21733 s. Rafael confirmou o vínculo manual; download/QC
passaram (H.264/AAC, 1080×1920, 7,224 s, full decode). Replay sem novo dispatch/reserva.
Resposta automática sem ID permanece limitação, não recuperação automática verificada.
Gate daquele checkpoint: 14/14; 1430 testes aprovados/19 skips, antes do D3A.
Ver `docs/superpowers/2026-09-30-d2c-canary-verification.md`.

### Saída

- `PROVIDER_VERIFIED` somente após MP4 real íntegro;
- diferenças de schema/capability voltam para um Goal corretivo estreito.

## D3A — EditProfile, EditManifest & Override Resolution

**Status:** `IMPLEMENTED`, `LOCAL_VERIFIED`; revisão independente e correções concluídas.

Contratos/schemas/CLI novos coexistem com v1 sem migração automática. Persistência
JSON versionada, resolver puro, precedence/provenance e validação de assets locais.
Teste somente leitura com MP4 real D2C passou, replay com mesmo hash e fonte intacta,
sem paid calls. Sem UI, renderer, Jobs ou tabelas adicionais.
Evidência: `docs/superpowers/2026-10-01-d3a-verification.md`.
Gate Windows pós-correções 15/15, 1491 testes aprovados/20 skips; não há nova evidência de provider.

### Objetivo

Separar estilo reutilizável de configuração resolvida por render.

### Incluído

- `EditProfile` versionado;
- evolução compatível ou migração explícita do `EditManifest` legado;
- seções tipadas para headline, captions, music e framing;
- precedência `profile < campaign < video < output variant`;
- resolver puro/determinístico com provenance;
- validação de fonts/assets/paths;
- JSON Schemas e exemplos;
- persistência de profile e manifest resolvido.

### Explicitamente excluído

- renderer;
- UI;
- merge profundo arbitrário;
- keyframes/timeline.

### Critérios de saída

- a mesma entrada sempre gera o mesmo manifest/hash;
- override inválido falha com campo exato;
- o manifest registra profile version e origem dos overrides;
- configs legadas suportadas têm comportamento definido por teste.

## D3B — Headline A/B Planning & Caption Inputs

**Status:** `IMPLEMENTED`, `LOCAL_VERIFIED`.

Lista explícita limitada, IDs/hashes/filenames determinísticos, plano separado dos manifests
v2 e CLI `edit plan|plan-get`. SQLite somente leitura; copy exata vinculada à voz do render.
Timing opcional validado por hashes/cobertura/provenance, ausência explicitamente missing;
sem ASR/alinhamento, UI/render, novas tabelas ou Jobs. Dry-run real D2C de três headlines
passou com replay estável, SQL/MP4/WAV intactos, zero paid calls; captions desabilitadas
e timing ausente neste smoke. Evidência: `docs/superpowers/2026-10-02-d3b-verification.md`.

### Objetivo

Criar output variants baratos sem refazer assets upstream.

### Incluído

- coleção de `EditVariant` por source video;
- override de `headline.text` e demais campos editoriais permitidos;
- cálculo/limite de combinações antes do render;
- IDs e filenames determinísticos;
- captions usando texto da Copy Master aprovada;
- escolha de timing disponível com provenance;
- dry-run mostrando outputs planejados e assets reutilizados.

### Critérios de saída

- 1 MP4 + 3 headlines planeja 3 outputs e zero jobs HeyGen;
- headline continua fora da voz e das captions;
- rerun do mesmo plano mantém IDs/hashes;
- limite configurado bloqueia explosão combinatória.

## D4A — FastAPI Operational API

**Status:** D4A.1 e D4A.2 `IMPLEMENTED`, `LOCAL_VERIFIED`.

### Slices aprovados

- D4A.1: consultas/status, readonly SQLite, DTOs públicos, profiles/plans verificados,
  OpenAPI e CLI loopback `IMPLEMENTED`, `LOCAL_VERIFIED`.
- D4A.2: ações operacionais e integração de workers `IMPLEMENTED`, `LOCAL_VERIFIED`.

D4A.1 preserva consultas readonly. D4A.2 adiciona 19 POSTs tipados, Jobs de operação e
runner explícito scoped por campanha/tipo, sem migrations ou auto-dispatch no startup.
O fluxo fake HTTP D1–D3 cobre import manual, voz/review, HeyGen/download e A/B editorial.
Media serving, React/preview e renderer continuam fora de D4A, em D4B/D5.

Gate local Windows de 2026-10-03: 15/15 etapas, 1.627 testes aprovados / 23 skips,
código `678d661`. Revisão independente concluída; os três achados relevantes foram
reproduzidos e corrigidos, com gate completo pós-fix. Sem chamada paga ou novo
`PROVIDER_VERIFIED`; essa é a evidência histórica D4A.1.

Actions D4A.2 run 37125176378, SHA `0693674`: Linux 1.677 passed/6 skipped, harness
15/15; Windows focado 951 passed/12 skipped, 3/3. Confirma Task 1 no Linux, mas não
Tasks 5–8 ainda locais naquele checkpoint. PR #2 estava draft, sem merge. Uso operacional enqueue/start/poll/stop
documentado no README; dry-run cria Job sem efeitos de import/plano. Profiles salvam
metadados imutáveis; o Job valida assets. D4B é o próximo slice, D5 permanece posterior.

Gate final local D4A.2 de 2026-10-03 no código `21b9d5d`: Windows 15/15 etapas,
1.697 passed/24 skipped em 393,13 s; Ruff, mypy source/tests, quatro schemas e audit
production aprovados. Docker indisponível no diagnóstico opcional do doctor; não há
runner Linux local. Esse foi o gate anterior à revisão final.

Revisão independente única da branch encontrou três achados relevantes. Uma rodada de
correção RED→GREEN resolveu recuperação pública de checkpoint sem novo dispatch, DB externo
no plano editorial e race do manifest entre verificação/leitura. Gate pós-fix no código
`73ca3587ebfd94aa8d3f42a6b9b0744b61b07168`: Windows 15/15, 1.701 passed/24 skipped
em 396,42 s. Sem minors pendentes. Não houve re-review; regressões e gate pós-fix são
a evidência das correções. Naquele checkpoint, CI ainda dependia de publicação autorizada.

D4A.2 foi integrado em `main` no SHA `9cd8731`: Actions 37146845609 (push) e
37146846960 (PR) concluídos com sucesso, incluindo Linux e Windows. Sem novo canário pago.

### Objetivo

Expor os application services existentes e planejados para uma UI local sem duplicar regra de
negócio.

### Incluído

- FastAPI em `127.0.0.1`;
- endpoints para campaigns, assets, voice, HeyGen, profiles, variants, jobs e renders;
- ações de import, submit, resume, approve/reject e dry-run;
- status agregado e próximo bloqueio por campanha;
- OpenAPI gerado;
- polling HTTP simples para progresso no primeiro MVP.

### Explicitamente excluído

- autenticação, RBAC e acesso LAN;
- WebSockets/SSE obrigatório;
- regras de negócio nas routes;
- execução direta de provider/FFmpeg nas requests.

### Critérios de saída

- todas as mutações delegam para application services;
- operações longas apenas criam/retomam Jobs;
- API pode operar o fluxo fake D1–D3;
- erros têm mensagem humana e código estável.

## D4B — React Operations UI & Approximate Preview

**Status:** D4B.1 e D4B.2a `IMPLEMENTED`, `LOCAL_VERIFIED`;
D4B.2b.1 Voice Master `IMPLEMENTED`, `LOCAL_VERIFIED`: gate pós-revisão Windows
19/19, 130 frontend e 1.789 Python / 25 skips; evidência no PROJECT-MEMORY.
D4B.2b.2 HeyGen `IMPLEMENTED`, `LOCAL_VERIFIED`: gate Windows 19/19, 214 frontend e
1.793 Python / 25 skips; revisão independente/Actions deste slice ainda pendentes.
Evidência no PROJECT-MEMORY, sem nova chamada paga.
D4B.3 permanece `PLANNED` e é o próximo slice; D5 ainda não tem renderer.

Gate final D4B.2a Windows de 2026-10-04: 19/19 etapas; 96 testes frontend e 1.757 Python
aprovados / 25 skips (472,17 s). Browser/proxy/API/worker reais cobrem import, review e
copy gravada com resposta 503. Revisão independente concluída sobre `e792064`;
achados funcionais corrigidos em uma rodada RED→GREEN e gate completo, sem re-review.
CI Linux/Windows pendente de publicação. Nenhuma chamada paga.

### Slices e capacidade atual

- D4B.1: painel React/TypeScript/Vite, lista/detalhe, metadados de assets/voz/HeyGen,
  Jobs/operações, polling com dados anteriores marcados quando stale e start confirmado/stop explícito.
  API é a única fonte de verdade. Sem criação/import/review, mídia ou preview nesta etapa.
- D4B.2a: campanhas/copy aprovada e versões, pasta preparada/Explorer, associação explícita,
  publicação imutável pelo worker, dry-run diagnóstico, snapshot dos sources e review manual.
  Sem auto-start/watch/upload, provider ou migration. Fluxo operador sem CLI/JSON manual.
- D4B.2b.1: orçamento inicial GET/POST sem Jobs; gerar ElevenLabs ou importar MP3/WAV,
  starts explícitos `voice_generate` ou `local_operations` → `voice_import`, review auditável
  após escuta externa. Backend/QC preservados, sem player/upload/novas dependências/migrations.
  Unknown não dispara repost; wrapper concluído não equivale a filho concluído ou voz aprovada.
- D4B.2b.2 HeyGen: preparação/reuso de imagens e WAV aprovados; plano informativo,
  limite total de reservas históricas e autorização paga separados; wrappers/filhos,
  starts explícitos `local_operations` → `heygen_assets` ou `heygen_videos`;
  reconciliação de Job blocked com binding confirmado e metadados de MP4 por GET.
  Defaults fixos 9:16/1080p/MP4, concurrency 2, polling 10→60/1800s. Sem auto-start,
  POST retry, player, OAuth na UI, media serving ou mudança no engine.
- D4B.3: profiles, overrides, variantes A/B e preview aproximado.

D4B.1 usa dois processos locais (API 8000 e Vite 5173); distribuição estática/launcher
fica para depois. Worker 404 fora do escopo significa desconhecido; start/stop não
ganham retry automático. Stop drena o Job ativo, sem cancelar provider ou reembolsar créditos.
Testes de integração usam navegador/proxy/API reais e mídia sintética, sem créditos.
Gate Windows pós-revisão de 2026-10-03: `verify.py full`, 19/19 etapas; 56 testes frontend e
1.711 Python aprovados / 24 skips. Três achados Important corrigidos com regressões
RED→GREEN em uma rodada; sem Critical/Minor e sem re-review. Tipos dos três arquivos novos de testes verificados
também para Linux. Isso não substitui execução Linux/Actions da nova branch, ainda não publicada.

### Objetivo

Permitir que Rafael gerencie a pipeline sem editar JSON ou usar múltiplos comandos.

### Incluído

- Vite + React + TypeScript;
- Campaign list/detail;
- import batch e cobertura de variantes;
- Voice Master e HeyGen status/actions;
- editor simples de profile e output variants;
- preview 9:16 aproximado com frame/poster + overlays CSS;
- controles para headline, captions, music e framing;
- fila/status por polling;
- acessibilidade básica e estados de loading/error/empty.

### Explicitamente excluído

- timeline;
- drag-and-drop livre;
- preview frame-perfect;
- edição frame a frame;
- design system próprio;
- login e multiusuário.

### Critérios de saída

- o fluxo D1–D3 pode ser configurado e acompanhado pela UI;
- preview mostra texto, quebra, fonte, cor, posição e framing de forma útil;
- label informa que o preview é aproximado;
- alterações persistem via API e sobrevivem a reload;
- UI não acessa banco, filesystem ou provider diretamente.

## D5A — Deterministic Renderer

**Status:** `PLANNED`.

### Objetivo

Renderizar cada EditManifest resolvido em um MP4 vertical reproduzível.

### Incluído

- renderer inicial FFmpeg/ASS, salvo restrição técnica demonstrada;
- headline, captions, music e framing;
- voice-preserving mix com `amix normalize=0` ou equivalente documentado;
- proxy opcional e master 1080×1920 H.264/AAC;
- outputs versionados, faststart e hashes;
- job local idempotente por manifest hash;
- renderer version registrada.

### Explicitamente excluído

- compositor genérico;
- timeline/keyframes livres;
- plugins de efeitos;
- render distribuído.

### Critérios de saída

- fixtures sintéticas cobrem todas as seções;
- 1 MP4 + 3 headline variants produz 3 masters distintos;
- nenhum input é alterado;
- mesmo manifest reaproveita output íntegro existente;
- output passa full decode e inspeção.

## D5B — Render QC, Review & Delivery

**Status:** `PLANNED`.

### Objetivo

Fechar o ciclo local do render até entrega.

### Incluído

- QC de streams, duração, resolução, FPS, loudness e clipping;
- bounds de headline/captions;
- proxy/contact sheet;
- approve/reject com comentário;
- master aprovado imutável;
- cópia para pasta de entrega com hash origem/destino.

### Critérios de saída

- mix com voz baixa ou clipping bloqueia aprovação;
- rejeição cria nova revisão, não sobrescreve master;
- delivery é distinguido de upload cloud;
- UI exibe QC e decisão.

## D6 — End-to-End Personal Pilot

**Status:** `PLANNED`, requer paid-action approval.

### Objetivo

Provar o fluxo real mais curto para uso pessoal.

### Piloto

```text
1 Copy Master aprovada
1 Voice Master real aprovada
3 imagens criadas manualmente
3 variantes importadas
3 MP4 HeyGen reais
3 headlines por MP4
9 renders locais
3 masters escolhidos e entregues
```

### Cenários obrigatórios

- restart durante polling HeyGen;
- replay idempotente do batch;
- uma variante HeyGen falha sem invalidar as outras;
- alteração somente de headline não dispara paid action;
- um render rejeitado gera nova revisão;
- operação completa pela UI, com CLI disponível para diagnóstico.

### Saída

- Rafael consegue repetir a operação a partir da documentação;
- tempos, custos e principais atritos são registrados;
- somente problemas observados geram novos Goals.

## 5. Baseline comum de verificação

Durante TDD:

```bash
uv run python scripts/verify.py fast
uv run python scripts/verify.py fast --pytest <focused-test>
```

Antes de `LOCAL_VERIFIED`:

```bash
uv run python scripts/verify.py full
```

Quando frontend existir, o harness deve incorporar lint, typecheck, tests e build usando scripts
npm estáveis. Quando um Goal alterar render, executar render sintético + `ffprobe` + full decode.

## 6. Não-goals globais

- reativar ou ampliar Google Flow antes do D6;
- timeline tipo CapCut;
- preview frame-perfect;
- plataforma multiusuário;
- microservices, filas externas ou containers obrigatórios;
- publicação social;
- automação de websites ElevenLabs/HeyGen;
- hardening para internet pública;
- abstrações para providers hipotéticos.
