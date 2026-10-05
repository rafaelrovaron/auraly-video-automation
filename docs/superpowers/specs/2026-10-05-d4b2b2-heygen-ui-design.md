# D4B.2b.2 — HeyGen na UI local

Data: 2026-10-05.
Status: escopo conversacional aprovado; especificação escrita aguardando revisão.
Capacidade proposta: PLANNED, ainda não implementada.

## Objetivo e baseline

Operar preparação de assets, planejamento, submissão e acompanhamento de um
MP4 HeyGen por cena pela campanha existente, sem CLI ou JSON manual nesses
comandos. Uso local e pessoal, delivery-first. Reutilizar as imagens aprovadas
e o mesmo WAV processado/aprovado; não gerar voz ou imagens neste fluxo.

Baseline: main `4a06c4d015144e038afa8a2b9e0ac6ce274f5a71`.
D4B.2b.1 entrega Voice Master UI e orçamento inicial. Backend HeyGen, MCP/OAuth,
reserva/reuso, workers, polling/download e reconciliação já existem; a UI atual
mostra renders e Jobs, mas ainda não oferece esses formulários.

O [Actions do baseline](https://github.com/rafaelrovaron/auraly-video-automation/actions/runs/37282794254)
passou em Linux e Windows na tentativa 2. A tentativa inicial teve timeout
no carregamento do botão Criar campanha; não foi reproduzido em duas execuções
locais dos 13 testes de navegador nem no rerun Windows. Causa não confirmada,
sem correção de código ou aumento de timeouts. Não tratar rerun como prova de
correção da causa. Essa evidência não constitui novo teste real de provider.

## Abordagem e limites

Formulários nativos na seção HeyGen da campanha, usando React/FastAPI, SQLite,
DTOs, transporte, polling e controles de worker existentes. Um componente
HeyGen e um cliente tipado focado; não duplicar engine nem construir wizard
ou editor genérico de parâmetros. Preservar os fatos históricos de renders.

Inclui preparar assets, planejar batch, autorizar/submeter geração, acompanhar
Jobs/MP4s e reconciliar render bloqueado. Não inclui novas dependências,
migrations, catálogo de avatares, upload no browser, player, preview, edição,
download remoto pelo browser, seleção parcial de cenas, regeneração forçada,
auto-start, auto-retry de POST ou redesign do provider. D4B.3 mantém edição e
preview aproximado; D5 mantém renderer.

OAuth permanece no mecanismo existente da CLI/runtime. Mostrar instrução de
conexão quando houver erro de autenticação; nunca solicitar tokens, cookies,
credenciais ou URLs assinadas na UI. Não automatizar website HeyGen.

## Entradas e elegibilidade

O batch abrange todas as cenas da campanha. O backend resolve a voz aprovada
da campanha e a imagem aprovada de cada cena, valida paths/hashes e faz reuso
por material/conta. Não oferecer seletores que o contrato não suporta nem
inferir que a versão de copy mais recente substitui a voz aprovada.

Mostrar voz/copy associada, imagens e bloqueios com os fatos atuais da API.
Sem voz aprovada ou sem imagem aprovada por cena, explicar a pendência.
Esses indicadores são ajuda operacional; não substituem validação autoritativa
do serviço ou conhecimento do estado remoto. Leitura stale bloqueia novas
ações dependentes de assets/Jobs/renders atuais, preservando o último snapshot.

Configuração fixa deste recorte: defaults de `HeyGenVideoConfig`: image,
provider_default, 9:16, 1080p, MP4, cover, expressiveness medium, sem motion
prompt. Concorrência 2; polling inicial 10 s, máximo 60 s, timeout 1800 s.
Exibir os parâmetros materiais usados, sem controles avançados neste slice.
Manter pacing/retries existentes; não acrescentar sleeps arbitrários na UI.

## Preparação de assets

POST `/api/v1/campaigns/{campaignId}/heygen/assets/prepare` recebe
`HeyGenAssetsOperation` com `campaignId` e `requestId` único por intenção.
Essa submissão enfileira wrapper, não significa upload concluído.

1. Operador inicia `local_operations` explicitamente. O serviço consulta
   MCP/preflight, calcula uploads/reuso e reserva Job filho quando necessário.
2. Após resultado confirmado, mostrar uploadCount, reusedCount e jobId.
   Havendo filho, operador inicia `heygen_assets` explicitamente e acompanha
   esse Job; se não houver filho, explicar que não há upload novo enfileirado.

Wrapper concluído não equivale a filho concluído. Filho falho/bloqueado não
vira sucesso porque o runner ficou idle. Não inventar inventário de assets
remotos: a API atual não oferece GET desse inventário. O planejamento de
vídeos confirma a elegibilidade remota novamente. Upload não regenera áudio
ou imagem e não modifica os originais aprovados.

## Planejamento e autorização

Formulário solicita um inteiro positivo `maxPaidRenders`, rotulado como
**limite total de renders reservados da campanha**. O backend conta todas as
reservas existentes, não apenas novos vídeos ou somente os que terminaram.
Não apresentar saldo, preço, conversão monetária, centavos ou reembolso.
O orçamento monetário da geração de voz é independente deste limite.

POST `/heygen/videos/plan` usa `HeyGenVideoPlanOperation` com config, limite,
campanha e requestId. Executar wrapper via `local_operations` explicitamente.
Mostrar resultado persistido: newCount, reusedCount, reservedCount,
maxPaidRenders, totalAudioSeconds e sceneVariantIds. Relacionar IDs às cenas
da campanha apenas após validar seu contexto. Planejamento não gera vídeo,
mas seu worker pode consultar MCP/preflight; não é operação offline.

Plano é informativo, não um snapshot imutável que a API aceita como token.
A submissão recalcula o plano e revalida material/limite. Mostrar essa
limitação. Invalidar confirmação/plano utilizável quando mudar limite,
campanha ou fatos observados de voz, imagens ou reservas; exigir planejamento
novo. A UI não promete atomicidade entre as duas ações nem congela assets
remotos. Não alterar o contrato para criar lock/token de plano neste recorte.

Após plano atual confirmado, exigir responsável e checkbox inicialmente
desmarcado autorizando consumo de créditos dentro do limite informado.
POST `/heygen/videos/submit` recebe `HeyGenVideoSubmitOperation` com os mesmos
config/limite, approvedBy e novo requestId. Não reutilizar o requestId do plano.
Executar wrapper via `local_operations` explicitamente; resultado retorna
reservas/renders, não MP4s prontos. Não sobrescrever autoria histórica do reuso.

## Geração, polling e download

Operador inicia `heygen_videos` no controle existente, com confirmação de que
Jobs elegíveis desse tipo na campanha podem consumir créditos. Não limitar
a promessa ao último formulário enviado. Esse worker executa geração, polling
com backoff e download dos MP4s conforme o engine atual.

Reusar GET de renders, Jobs, operações e worker. Mostrar status de cada render,
cena/imagem/voz, IDs de Job/vídeo remoto, erro, hashes de entrada e caminho,
hash, tamanho e metadados disponíveis do MP4 local. READY deve vir da API,
nunca da conclusão do wrapper ou do estado idle. Falha em uma cena não deve
ocultar os demais renders. Preservar coleções anteriores se a leitura falhar.

Caminhos são relativos ao work root do servidor; orientar acesso no Explorer,
sem fingir que o browser abre arquivos locais ou gerar link de mídia remoto.
Start/stop seguem o escopo campanha/tipo atual. Stop aguarda trabalho ativo;
não cancela chamada paga nem reembolsa créditos. Reload não dispara mutações.

## Reconciliação

Oferecer ação somente para render do contexto atual cujo Job esteja bloqueado.
POST `/heygen/renders/{renderId}/reconcile` usa `HeyGenReconcileOperation` com
campaignId, renderId e novo requestId. Executar em `local_operations`.

Quando houver remoteVideoId conhecido, mostrar esse ID sem permitir substituição.
Sem ID conhecido, permitir informar o ID exato e exigir confirmação explícita
de vínculo manual. Não buscar/adotar por semelhança, timestamp ou posição.
Se o operador não tiver ID, permitir solicitar a reconciliação sem ID, com
texto de que somente o backend pode provar ausência de dispatch e liberar
retomada; a UI não deduz isso do DTO atual. Dispatch ambíguo continua bloqueado.

Preservar validação de conta e material remoto. Sucesso de reconciliação
significa estado reconciliado, não MP4 pronto; pode haver novo Job de recovery
ou Job retomado. Atualizar render/Job observado e exigir start explícito de
`heygen_videos` para continuar. Não usar resume genérico como atalho para um
render que exige reconciliação, nem mudar sua política de segurança.

## Estado incerto, navegação e identidade

Um requestId por intenção, criado pela UI. Pending impede submit duplo.
Resposta incerta (timeout, conexão perdida, 5xx ou DTO inválido) preserva a
intenção e mostra unknown, nunca segundo POST automático ou auto-start.
Se jobId foi comprovado, acompanhar `/operations/{jobId}`, validando campanha,
tipo, status e resultado antes de adotar IDs de filhos/renders. DTO válido
mas de outra campanha, IDs duplicados ou referências inconsistentes não
substituem o último snapshot válido.

Sem jobId comprovado, permitir GET/inspeção dos Jobs existentes, sem inferir
autoria exata por cena/config/campanha: OperationView não expõe requestId.
Adoção explícita de Job do mesmo tipo/campanha é acompanhamento, não prova
de correspondência à intenção nem nova autorização. Não acrescentar busca
genérica ou repost para eliminar incerteza. Resultado desconhecido não ganha
atalho para reenviar; nova ação intencional exige decisão explícita do operador.

Reusar guards de campanha/lifecycle, cancelamento e respostas tardias. Formulários
têm labels, feedback e navegação por teclado; preservar drafts independentes
e aviso de alterações não salvas. Aceitação de uma ação não apaga o draft de
outra. Mudança de campanha/config/material invalida confirmações pagas locais.

## Verificação e aceite

Implementação futura segue plano aprovado, TDD, commits pequenos, gate completo
e revisão independente. Não alterar AGENTS.md, sources, áudio, migrations ou
schema do provider. Alterar contratos públicos só se surgir necessidade
comprovada, com revisão de escopo antes da implementação.

Cobrir validação de DTO/contexto, stale, leitura falha, pending/double-submit,
unknown sem repost, navegação/reload, histórico e drafts. Exercitar preparação
com filho, reuso sem filho e falha do filho; plano sem geração; limite positivo
e reservas históricas; autorização separada; submissão/reservas e start explícito;
polling/download READY e falha parcial; reconciliação conhecida, vínculo manual,
recusa por material/conta e retomada somente após estado comprovado.

Browser/proxy/API/SQLite/workers reais, mídia sintética e provider fake devem
permitir preparar assets, planejar, autorizar, gerar e obter MP4s locais sem
CLI/JSON manual e sem chamadas pagas. Reabrir campanha não faz POST ou provider
call. Rodar `verify.py full`; incluir os novos testes na seleção Windows do CI.
Fake/local estabelece LOCAL_VERIFIED, nunca novo PROVIDER_VERIFIED.
Canary real é decisão separada e exige autorização específica.

## Handoff

Revisar esta especificação escrita antes de criar o plano de implementação.
Depois, revisar o plano e escolher execução antes de desenvolver. A aprovação
de escopo/design não autoriza merge/push, provider pago ou implementação automática.
