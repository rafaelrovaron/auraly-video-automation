# Auraly Delivery-First Goal Roadmap

**Roadmap vigente:** 2026-09-27

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

O código Flow permanece no repositório. Não removê-lo, reescrevê-lo ou expandi-lo durante o novo
MVP sem um Goal específico aprovado.

## 4. Sequência delivery-first

```text
D0  Documentation Alignment                    DONE
D1  Manual Image Batch Intake                  NEXT
D2A HeyGen Contract, Preflight & Asset Reuse
D2B HeyGen Batch Generation, Polling & Download
D2C HeyGen Real Canary
D3A EditProfile, EditManifest & Override Resolution
D3B Headline A/B Planning & Caption Inputs
D4A FastAPI Operational API
D4B React Operations UI & Approximate Preview
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

**Status:** `PLANNED` — próximo Goal.

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
- migration somente se o modelo atual não conseguir representar provenance de importação.
- atualização futura da boundary de imagens no `AGENTS.md` somente depois que D1 existir; este
  replanejamento não altera esse arquivo.

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

**Status:** `PLANNED`.

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

## D2B — HeyGen Batch Generation, Polling & Download

**Status:** `PLANNED`.

### Objetivo

Gerar e baixar um MP4 HeyGen por variante, em lote retomável.

### Incluído

- configuração explícita de avatar/engine;
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

**Status:** `PLANNED`, requer aprovação e crédito.

### Objetivo

Validar o adapter e as suposições de payload contra um render real pequeno.

### Incluído

- preflight real;
- upload/reuso de uma imagem e uma Voice Master aprovada;
- uma geração real;
- polling, download e QC do MP4;
- registro sanitizado de IDs, custo e evidência.

### Saída

- `PROVIDER_VERIFIED` somente após MP4 real íntegro;
- diferenças de schema/capability voltam para um Goal corretivo estreito.

## D3A — EditProfile, EditManifest & Override Resolution

**Status:** `PLANNED`.

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

**Status:** `PLANNED`.

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

**Status:** `PLANNED`.

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

**Status:** `PLANNED`.

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
