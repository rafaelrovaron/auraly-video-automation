# D4B.1 Campaign Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar lista/detalhe de campanhas com metadados, pendências, Jobs e controle explícito do worker local.

**Architecture:** React consome exclusivamente a API FastAPI entregue, com navegação por hash e estado local. Vite em loopback encaminha `/api` e `/health` preservando Host/Origin; nenhum serviço de domínio, endpoint ou armazenamento novo é necessário.

**Tech Stack:** React/TypeScript/Vite; Vitest, Testing Library e jsdom para comportamento; pytest/Playwright Python existente para integração real de navegador/proxy/API.

**Spec:** `docs/superpowers/specs/2026-10-03-d4b1-campaign-panel-design.md` (aprovado em 2026-10-03).

Status: execução Nativa concluída em branch isolado, com revisão independente e uma rodada de correção. Base: `ec9496b`, sobre D4A.2 em `9cd8731`. Gate pós-fix Windows: 19/19, 56 testes frontend, 1.711 Python aprovados / 24 skips. Merge/push não realizados.

## Global Constraints

- API em `127.0.0.1:8000` e Vite em `127.0.0.1:5173`, com porta estrita.
- Preservar Host e Origin do navegador no proxy (`changeOrigin: false`). Não liberar CORS no FastAPI.
- Frontend em `web/`, usando React, TypeScript e Vite, com package e lock próprios.
- Preservar o package/lock atuais da raiz e o ambiente HyperFrames.
- Navegação por hash: `#/campaigns` e `#/campaigns/<campaignId codificado>`.
- Atualizar lista a cada 5 segundos; status, Jobs e worker do detalhe a cada 2 segundos enquanto a página estiver visível.
- Nunca repetir POST automaticamente após timeout/falha de rede.
- Sem mídia servida, OAuth novo, criação/import/review/geração por formulário, profiles, variants ou preview nesta fatia.
- Sem alterações em AGENTS.md, `sources/`, migrations, providers, contratos de domínio ou gates de aprovação/orçamento.
- Verificação usa somente fixtures/fakes locais. Não usar credenciais reais nem executar canary pago.

## Review Focus

- Hash malformado/ID inválido: erro de navegação local legível, sem request para recurso errado (T1).
- Resposta tardia da campanha anterior e aba oculta: nunca substituir a campanha atual ou acumular polling (T2).
- Erro parcial de assets: conservar dados anteriores como desatualizados, não apresentar vazio aprovado (T2).
- Worker 404 de outra campanha e POST com resposta perdida: estado desconhecido, sem retry nem parada do proprietário errado (T3).
- Porta ocupada/proxy/origem estrangeira: falhar claramente, não conectar a outro serviço nem enfraquecer o limite HTTP (T4).

## Arquivos e responsabilidades

Criar somente a estrutura necessária:

- `web/package.json`, `web/package-lock.json`, `web/tsconfig.json`, `web/vite.config.ts`, `web/index.html`: dependências, build, testes e conexão local.
- `web/src/main.tsx`, `web/src/App.tsx`, `web/src/styles.css`: entrada, navegação hash, layout e acessibilidade.
- `web/src/api.ts`: DTOs camelCase usados e cliente fetch sem retries.
- `web/src/usePolling.ts`: leitura com cancelamento, atualização manual e último resultado válido.
- `web/src/CampaignPanel.tsx`: lista/detalhe, seções de metadados e detalhe de Job/operação.
- `web/src/WorkerControls.tsx`: confirmação e envio de start/stop.
- `web/src/testSetup.ts`, `web/src/api.test.ts`, `web/src/App.test.tsx`, `web/src/usePolling.test.tsx`, `web/src/CampaignPanel.test.tsx`, `web/src/WorkerControls.test.tsx`: testes de comportamento.
- `tests/web_panel_support.py`, `tests/test_web_panel_e2e.py`: fixture HTTP/browser real e regressões de integração, reutilizando `tests/api_helpers.py`.
- Modificar `.gitignore`, `package.json` (scripts apenas), `scripts/verify.py`, `tests/test_verify_harness.py`, `.github/workflows/verify.yml`, README, PROJECT-MEMORY e GOAL-ROADMAP para comandos/verificação/status da fatia entregue.

Sem framework de componentes ou abstração de repositório frontend. Manter DTOs/cliente juntos até haver necessidade real de separar.

## Dependências fixadas para esta fatia

Metadados consultados no registry npm em 2026-10-03; não instalados durante planejamento. Node local `22.23.0`; usar Node 22 com mínimo `22.12.0` no frontend e nos dois jobs CI.

Runtime: `react=19.3.0`, `react-dom=19.3.0`.

Dev: `vite=7.3.6`, `@vitejs/plugin-react=5.1.4`, `typescript=5.9.3`, `vitest=5.0.3`, `jsdom=26.1.0`, `@testing-library/react=16.3.3`, `@testing-library/dom=10.4.2`, `@types/react=19.3.0`, `@types/react-dom=19.3.0`, `@types/node=22.20.5`.

Vite 7/plugin 5 mantêm a configuração convencional sem novas dependências de compiler; TypeScript/jsdom fixados evitam upgrade amplo sem necessidade. Não adicionar canvas opcional, jest-dom, user-event, React Router, query/state libraries ou Playwright npm. Gerar lock via npm, sem `--force`/`--legacy-peer-deps`; se instalação/audit apontar incompatibilidade ou vulnerabilidade, corrigir a versão mínima envolvida e registrar evidência antes de seguir.

Referências primárias: [Vite](https://www.npmjs.com/package/vite/v/7.3.6), [plugin React](https://www.npmjs.com/package/@vitejs/plugin-react/v/5.1.4), [Vitest](https://www.npmjs.com/package/vitest/v/5.0.3), [React](https://www.npmjs.com/package/react/v/19.3.0).

## Preparação de execução

- [x] Ler spec e plano aprovados; inspecionar status/diff e usar `superpowers:using-git-worktrees` para isolamento no início da execução. Não criar worktree durante a revisão deste plano.
- [x] Executar baseline existente `rtk proxy uv run python scripts/verify.py fast --pytest tests/test_api_http.py tests/test_api_worker.py tests/test_verify_harness.py`; interromper diante de falha preexistente antes de alterações.

### Task 1: Lista navegável sobre a API e bootstrap local

**Files:** Criar configuração/entrada/CSS, `api.ts`, `api.test.ts`, `App.tsx`, `App.test.tsx`, `CampaignPanel.tsx`, `testSetup.ts`; modificar scripts da raiz e `.gitignore`.

**Interfaces:**
- `Items<T> = {items: T[]}`; DTOs usam nomes e nulabilidade dos modelos em `api/contracts.py`, `api/action_contracts.py` e `campaigns/domain.py`, sem inventar title/mediaUrl.
- `ApiError extends Error` expõe `status: number | null`, `code: string`, `field: string | null`; mensagens estáticas em português, sem corpo bruto de resposta.
- `read<T>(path: string, signal: AbortSignal, accepts?: (value:unknown) => boolean): Promise<T>` faz um único GET relativo, sem query; rejeita JSON inválido e aplica o predicate quando fornecido. Chamadas de coleções verificam objeto com items array; chamadas de detalhe verificam os campos essenciais usados, sem biblioteca de schema. `post<T>(path: string, body: object): Promise<T>` faz um único POST JSON com timeout de 15 segundos, nunca retry. Timeout de escrita usa código `command_unknown`.
- `campaignPath(campaignId: string, suffix?: string): string` codifica IDs no path.
- `parseRoute(hash: string): {page:'list'} | {page:'detail'; campaignId:string} | {page:'invalid'}` aceita apenas IDs com o pattern real da API.
- `CampaignList(): React.JSX.Element`; `App(): React.JSX.Element` seleciona página por hashchange.

- [x] Criar package/config/test setup e testes antes do código funcional. Scripts web: `dev=vite`, `test=vitest run`, `typecheck=tsc --noEmit`, `build=tsc --noEmit && vite build`. Scripts raiz `ui:dev`, `ui:test`, `ui:build` delegam com `npm --prefix web run ...`. Ignorar `web/dist/` e `web/*.tsbuildinfo`.
- [x] Escrever testes `api.test.ts`: GET relativo correto, items vazio, 503 sanitizado, resposta não JSON/malformada vira `invalid_response`, timeout/network de POST tem uma tentativa, JSON body inclui campaignId. `App.test.tsx`: lista mostra campaignId/personagem/status/nextPending; vazio não gera POST; clicar link/reload hash abre ID correto; `%ZZ`, ID fora do pattern e rota desconhecida mostram rota inválida sem GET de detalhe; strings HTML são texto.

```ts
expect(parseRoute('#/campaigns/campaign-one')).toEqual({page: 'detail', campaignId: 'campaign-one'});
expect(parseRoute('#/campaigns/%ZZ')).toEqual({page: 'invalid'});
expect(postRequests).toHaveLength(0); // carregar lista/reload não executa ações
```

- [x] Instalar somente as dependências fixadas e executar `rtk proxy npm --prefix web run test -- src/api.test.ts src/App.test.tsx`. Confirmar falha pelas funções/componentes ausentes, não por erro de setup; registrar RED.
- [x] Implementar interfaces e lista mínima; rota de detalhe nesta tarefa mostra somente campaignId e indicação de detalhe ainda indisponível, substituída em T2. Vite usa root/envDir absolutos de `web/` calculados da localização do config (não cwd), `server.fs.strict=true` com allow apenas `web/`, host/port/strictPort fixos, `cors=false`, proxies `/api` e `/health` com target fixo e `changeOrigin=false`. Nenhum console de payload ou env secreto exposto. Incluir types de config e testes no typecheck.
- [x] Executar os testes focados e `rtk proxy npm run ui:build`; exigir zero falhas e build gerado somente em diretório ignorado.
- [x] Revisar lock/diff por escopo e executar `rtk proxy git diff --check`; commit `feat(ui): add local campaign list and API client`, incluindo apenas os arquivos desta tarefa.

### Task 2: Detalhe consultável com polling e erro parcial

**Files:** Criar `usePolling.ts`, `usePolling.test.tsx`, `CampaignPanel.test.tsx`; modificar `api.ts`, `App.tsx`, `CampaignPanel.tsx`, `styles.css`.

**Interfaces:**
- `RemoteState<T> = {data:T | null; error:ApiError | null; loading:boolean; lastSuccessAt:number | null; refresh:() => void}`.
- `usePolling<T>(key:string, load:(signal:AbortSignal) => Promise<T>, intervalMs:number | null): RemoteState<T>`; stable load, key identifica recurso/campanha e controla cancelamento/identidade, null carrega na entrada/refresh sem timer. Trocar campanha limpa dados/timestamp anteriores imediatamente; refresh do mesmo recurso conserva último resultado válido.
- `WorkerObservation = {scope:'known'; value:WorkerState} | {scope:'unassociated'}`; `readWorker(campaignId:string, signal:AbortSignal): Promise<WorkerObservation>` transforma somente 404 específico do endpoint worker em unassociated. Outros endpoints 404 continuam erro.
- `CampaignDetailPanel({campaignId}:{campaignId:string}): React.JSX.Element` usa hooks separados por recurso, permitindo erro parcial sem apagar outras seções.
- `JobDetail({campaignId,jobId}:{campaignId:string;jobId:string}): React.JSX.Element` consulta Job e, somente para `api.local.operation`, seu OperationView.

- [x] Escrever testes com fake timers/deferred promises: `list_polls_at_5000ms`, `live_sections_poll_at_2000ms`, `inflight_cycle_does_not_overlap`, `hidden_tab_pauses_and_visible_refreshes`, `old_campaign_response_is_discarded`, `manual_refresh_does_not_overlap`, `partial_asset_error_keeps_last_value_with_timestamp`, `worker_404_is_not_idle`. Assert abort no unmount e que nenhum teste de leitura faz POST.
- [x] Executar `rtk proxy npm --prefix web run test -- src/usePolling.test.tsx src/CampaignPanel.test.tsx` e registrar falha funcional esperada.
- [x] Implementar polling com setTimeout após conclusão, AbortController e proteção de identidade/geração; sem setInterval sobreposto. Detalhe/status/jobs/worker/cópias/cenas/paths precisam corresponder aos DTOs camelCase reais. GETs live com 2000ms; lista com 5000ms; detail/images/voices/renders com interval null, refresh manual e quando fingerprint de status/Jobs mudar (status, contagens e IDs/status/completedAt dos Jobs, não heartbeat/updatedAt a cada ciclo). A mudança de fingerprint chama refresh, não troca key de identidade. Pausar timers enquanto hidden e recarregar ao voltar.
- [x] Implementar resumo/copies/cenas/assets/HeyGen/Jobs com labels portugueses e timestamps por seção. Paths/IDs são texto; conteúdo nunca usa dangerouslySetInnerHTML. Operation result usa whitelist de campos: import mode/counts, prepare paths/count, voice ID/status, assets upload/reuse, video-plan counts/limite/duração, video-submit IDs/status, reconcile ID/status e edit-plan videoId/planHash; não renderizar JSON bruto.
- [x] Fixar testes adicionais: null duration/source/remoteVideoId vira “Não disponível”; `renderer_not_implemented` continua pendência; 503 não vira coleção vazia; Job de tipo desconhecido mostra metadados sem consulta operation; texto OAuth orienta CLI, sem link/token de provider.
- [x] Executar `rtk proxy npm run ui:test`, `rtk proxy npm run ui:build` e diff check; commit `feat(ui): add campaign detail and live status polling`.

### Task 3: Start/stop explícito, sem duplicação de comando

**Files:** Criar `WorkerControls.tsx`, `WorkerControls.test.tsx`; modificar `CampaignPanel.tsx`, `api.ts`, `styles.css`.

**Interfaces:**
- `WorkerKind` contém exatamente os cinco valores da spec; `WorkerState` contém state/campaignId/kind/errorCode reais.
- `WorkerControls({campaignId,worker,connected,onRefresh}:{campaignId:string;worker:RemoteState<WorkerObservation>;connected:boolean;onRefresh:() => void}): React.JSX.Element`.
- connected exige leituras atuais pertinentes bem-sucedidas; 404 unassociated é conexão bem-sucedida, não idle. Nunca inferir custo/eligibilidade por contagens da UI.

- [x] Escrever testes para todos os cinco tipos; confirmação cancelada envia zero POST; confirmar envia uma vez `{campaignId,kind}`; clique duplicado não reenvia; navegar durante confirmação fecha a confirmação e não envia campanha antiga; parar só está disponível para running da campanha selecionada; stopping desabilita ambos os comandos; unknown permite início explícito, não stop; 409 mantém erro de conflito; timeout/network mantém aviso desconhecido e só reconcilia por GET.

```ts
expect(startRequest.body).toEqual({campaignId: 'campaign-one', kind: 'heygen_videos'});
expect(startRequests).toHaveLength(1);
expect(stopButton.disabled).toBe(true); // worker scope unassociated
```

- [x] Executar `rtk proxy npm --prefix web run test -- src/WorkerControls.test.tsx` e registrar RED.
- [x] Implementar select com label e confirmação inline acessível, mostrando campaignId/tipo e “Jobs já enfileirados podem consumir créditos.” para todos os tipos, inclusive local_operations. Pending snapshot, se exibido, é contagem observada de queued/retry_scheduled, não previsão de custo. Preservar estado/erro de resultado desconhecido até reconciliação; não permitir novo comando enquanto a confirmação/resultados estiverem pendentes. Reconciliar leitura não pode provar retrospectivamente que um Job terminou: mostrar fatos atuais sem falsa mensagem de sucesso.
- [x] Implementar stop com aviso de draining: não cancela trabalho em andamento nem devolve créditos. Em falha de conexão desabilitar escrita até leitura pertinente bem-sucedida; 409 não produz fallback automático para outro tipo/campanha. Ao navegar, descartar respostas de comando para tela antiga, mas nunca abortar como promessa de cancelamento do trabalho remoto.
- [x] Executar todos os testes web/build/diff check; commit `feat(ui): add explicit local worker controls`.

### Task 4: Integração real, gates Linux/Windows e documentação operacional

**Files:** Criar `tests/web_panel_support.py`, `tests/test_web_panel_e2e.py`; modificar harness/testes CI e os três documentos listados no mapa.

**Interfaces:**
- `panel_servers(tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> Iterator[PanelServers]`, fixture pytest em support importada pelo teste; PanelServers expõe `ui_url:str`, `settings:ApiSettings`, `entered:Event`, `release:Event`.
- API real via uvicorn em thread com socket loopback prebound na porta 8000; Vite via subprocess direto `node <repo>/web/node_modules/vite/bin/vite.js` com path absoluto e cwd web, porta 5173 estrita. Antes de iniciar, verificar ambas as portas: ocupada falha clara, nunca matar processo alheio. Espera HTTP com deadline de 15s; teardown release Event, parar próprios processos/thread e fechar recursos mesmo em falha.
- Fixture reutiliza `create_api_fixture`/`database_dump`, cria segunda campanha e Job image_prepare por serviços públicos. Segura execute_operation via Event somente em teste para observar running/stopping; fake providers/nenhuma credencial. Playwright Python Chromium headless, independente do fixture Flow headed.
- `build_ui_steps(os_name:str = os.name) -> tuple[VerificationStep,...]` em `scripts/verify.py`: npm ci web, ui:test, ui:build e audit produção web (high). `ui` como subcomando reutiliza registro; full insere passos UI após uv sync e antes de full pytest, preservando ordem relativa e todos os passos existentes.

- [x] Escrever E2Es `test_panel_read_navigation_is_non_mutating`, `test_explicit_worker_start_stop_through_real_proxy`, `test_proxy_preserves_origin_boundary`, `test_panel_accessible_at_narrow_width`, `test_panel_servers_refuse_occupied_ports`. Assert dump de DB idêntico após GET/poll/reload; nenhum POST sem confirmação; start retorna 202 e stop 200 via navegador/proxy, stopping preserva Job ativo e impede próxima captura; Origin estrangeiro/null recebe 422 sem mudar DB/Jobs; hash direto funciona; foco/labels/controle de teclado e nenhuma mídia/segredo exposto. Request malformado sanitizado não vaza body bruto. Usar respostas reais de todas as seções (não route mocks) para provar correspondência camelCase/nulabilidade dos DTOs frontend; incluir fixture com assets/voz/render usando helpers existentes e mídia sintética local, sem geração externa.
- [x] Escrever testes do harness antes da alteração: steps UI precedem pytest full, npm.cmd no Windows, primeiro erro interrompe, ui mode correto, CI Windows tem Node22/UI antes E2E e conserva todos os targets atuais; Linux cache considera ambos locks. Teste de porta ocupada usa socket próprio e não requer serviço alheio.
- [x] Executar `rtk proxy uv run python scripts/verify.py fast --pytest tests/test_verify_harness.py tests/test_web_panel_e2e.py`; confirmar falha pelos gates/comportamentos ausentes, não dependências faltando, e registrar RED.
- [x] Implementar fixture/gates; CI Linux full herda passos UI do harness. CI Windows adiciona setup-node22, cache dos dois locks, `uv run python scripts/verify.py ui` antes do fast existente, acrescentando `tests/test_web_panel_e2e.py` uma única vez. Não remover testes, providers/segredos ficam ausentes. Browser tests não podem passar por skip quando web deps/Chromium faltarem: mensagem orienta preparação local.
- [x] Atualizar README com `npm --prefix web ci`, `uv run auraly api serve` (opções existentes quando necessárias), `npm run ui:dev`, URL, duas portas, worker/custos/draining e limitações de mídia. PROJECT-MEMORY/GOAL-ROADMAP registram D4B.1 implementado/local verificado apenas após gates; D4B.2/3 continuam planejados; preservar histórico técnico e não repetir estados antigos de PR2 como atuais.
- [x] Executar `rtk proxy uv run python scripts/verify.py full`. Exigir todos os passos, testes/typecheck/build sem falhas, schema drift ausente, nenhum arquivo gerado/segredo/media/DB/node_modules no diff. Registrar quantidades/evidência reais; não reutilizar baseline D4A.2 como prova do frontend.
- [x] Commit `feat(ui): verify campaign panel across local and CI workflows` com arquivos desta tarefa. Pedir revisão independente de todo o branch conforme fluxo nativo/repositório, corrigir achados com TDD e repetir gates afetados/full antes de declarar LOCAL_VERIFIED. Não declarar PROVIDER_VERIFIED.

## Handoff e limites

O usuário já indicou preferência por execução **Nativa** no histórico; preservar essa escolha, sujeita à aprovação deste plano. Não começar implementação durante a revisão. Merge/push exigem solicitação específica e evidência dos gates/review; esta aprovação de planejamento não publica nada.

Self-review: spec coberta por T1–T4; falhas Review Focus atribuídas a testes; interfaces mantêm DTOs reais e worker scoped; todos os ciclos têm RED/GREEN/commit; nenhuma ação de D4B.2/3 ou provider real foi adicionada.
