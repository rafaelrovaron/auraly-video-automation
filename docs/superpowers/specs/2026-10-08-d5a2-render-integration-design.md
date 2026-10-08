# D5A.2 — Render Jobs, API e UI local

Data: 2026-10-08.
Status: escopo conversacional aprovado; este design aguarda revisão do usuário.
Implementação não iniciada. Não substitui o plano de implementação.

## 1. Intenção e base entregue

Permitir que o usuário renderize um plano editorial salvo pela interface local,
acompanhe a execução e acesse os masters de cada variante sem usar a CLI.
Prioridade: rapidez e uso pessoal, reutilizando a arquitetura existente.

D5A.1 já entrega `RenderService.render(campaign_id, video_id, plan_hash)`,
variantes sequenciais, resultados individuais, recibos imutáveis e reutilização
por outputHash/runtime. Não serão reimplementados FFmpeg, ASS, fit, mix ou encoding.
A CLI continua disponível e compatível.

Base de código: `501d312`. Actions desse commit concluídos com sucesso:
https://github.com/rafaelrovaron/auraly-video-automation/actions/runs/37792802719.
Essa evidência valida D5A.1/CI, não a capacidade ainda planejada neste documento.

## 2. Abordagem escolhida

Usar o JobService persistido e o LocalApiWorker existentes. Um Job dedicado
`editing.render` representa a execução de um plano inteiro; seu handler chama
o renderer já entregue. Acrescentar o kind `editing_render` ao worker existente,
sem criar outro executor, fila, serviço ou banco.

Alternativa descartada: render dentro da requisição HTTP, que bloquearia a resposta.
Também não usar uma operação genérica que drene a mesma fila de comandos de
voz/HeyGen: o disparo do render deve consumir somente Jobs `editing.render`.

Sequência de entrega: backend Jobs/API primeiro, depois integração React.
Ambos pertencem à D5A.2, com commits pequenos e testes independentes.

## 3. Contratos e execução

Request versionado Pydantic: `schemaVersion`, `campaignId`, `videoId`,
`planHash`, `executionId` (UUID fornecido pelo cliente). Nenhum caminho,
manifest avulso, override ou configuração FFmpeg recebido no disparo.

O plano deve existir no armazenamento editorial e pertencer à campanha/vídeo.
Validar identidade/hash do plano na admissão e novamente no handler; os assets
continuam sendo validados pelo RenderService na execução. O handler não lê
estado mutável do formulário nem resolve novos overrides.

Um Job por request, com chave de idempotência derivada do payload canônico,
incluindo executionId. Reenvio do mesmo request retorna o mesmo Job.
Nova execução voluntária usa novo executionId; não altera plano nem recibos.
Isso permite tentar novamente após corrigir uma condição local sem eternamente
retornar o resultado de uma execução anterior. Receipts continuam sendo a
autoridade para reaproveitar outputs, não o executionId.

Política: `MANUAL_ONLY`, `max_attempts=1`, sem retry automático. Reutilizar
lease/heartbeat e estados do JobService. Não aumentar timeout de CI para
esconder problemas de execução nem introduzir recuperação automática de órfãos.

O renderer processa variantes em ordem e continua após falhas individuais,
como já faz. Persistir o RenderBatchResult completo no output do Job.
`completed` significa que o handler terminou e publicou o resultado, não que
todas as variantes foram renderizadas. A view inclui `renderStatus`, derivado:
`succeeded` quando não há falhas, `partial_failure` quando há sucessos e falhas,
`failed` quando todas falham. Falha global do handler permanece um estado de
falha do Job, sem inventar resultados individuais.

Enquanto running, mostrar apenas execução em andamento. Resultados por variante
aparecem ao término; sem percentual, estimativa ou checkpoint por frame/variante.
Interrupção do processo segue a reconciliação manual existente. Não cancelar
FFmpeg pela tela. Parar o worker impede novos Jobs, não aborta o Job ativo.

## 4. API local

Rotas novas, seguindo os contratos/erros e escopo de campanha atuais:

- `POST /api/v1/campaigns/{campaignId}/editing/renders`: request acima;
  retorna 202 com jobId e identidade do plano. Só enfileira, não codifica.
- `GET /api/v1/campaigns/{campaignId}/editing/renders`: lista execuções do
  tipo correto nessa campanha; filtro opcional videoId/planHash.
- `GET /api/v1/campaigns/{campaignId}/editing/renders/{jobId}`: identidade,
  estado do Job, renderStatus quando concluído, resultado e erro sanitizado.
- `GET /api/v1/campaigns/{campaignId}/editing/renders/{jobId}/outputs/{outputVariantId}/media`:
  MP4 de um resultado rendered/reused, inline ou download por query `download=true`.

Submission e views incluem campaignId/videoId/planHash/executionId, além de jobId,
para identificar exatamente o envio e reconciliar uma resposta perdida pela lista.

Usar as rotas atuais de start/status/stop do worker com kind `editing_render`.
Worker ocupado retorna o conflito existente; o Job enfileirado permanece
consultável e pode ser executado quando o worker estiver livre.

Views validam tipo/campanha do Job e a correspondência entre request, plano e
resultado persistido; Job de outra campanha/tipo não é acessível nessa rota.
Media não aceita caminho enviado pelo cliente. Resolver somente o output conhecido,
validar containment/symlinks, recibo, identidade, tamanho/hash do MP4 e integridade
do plano antes de servir. Reutilização entre planos não exige que o planHash
de provenance do recibo seja o plano consumidor: preservar a regra de D5A.1.
Não executar FFmpeg/full decode novamente em cada GET; esses checks já são
exigidos na publicação/reutilização pelo renderer.

Não expor paths absolutos, logs FFmpeg, secrets ou erros internos. Preservar as
proteções e semântica de mídia locais existentes, sem novo hardening genérico.
Publicar/regenerar os schemas dos contratos afetados seguindo o export atual.

## 5. Interface

Adicionar controles ao fluxo editorial existente, preferencialmente em um
componente de render pequeno em vez de ampliar toda a lógica do EditingPanel.

O alvo é um plano salvo ou consultado do armazenamento, identificado visivelmente
por videoId/planHash. Rascunho e plano somente validado não podem ser renderizados.
Mudanças não salvas não afetam o plano salvo: a tela explicita essa distinção.

`Renderizar` cria executionId, congela o request, enfileira e solicita start do
worker dedicado. Duplicar clique/repetir transporte conserva o executionId.
Se o worker estiver ocupado, exibir queued e permitir iniciar depois; não
reenfileirar. Resposta perdida exige consulta da lista/Job antes de nova execução;
não fazer retry automático nem alegar autoria sem identidade correspondente.

Consultar o Job com polling simples, serial e limitado à seleção ativa. Abandonar
a tela/acompanhamento não cancela o Job. Ignorar respostas obsoletas ao trocar
campanha/plano; limpar timers ao desmontar. Execuções persistidas são consultáveis
após reload, sem depender de armazenamento adicional no navegador.

Ao terminar, mostrar uma linha por variante: rendered/reused/failed, erro
sanitizado quando houver e link para abrir/baixar o MP4 válido. O master é
`renderizado`, nunca `aprovado` ou `entregue` automaticamente.

Nova execução é uma ação explícita e usa novo executionId, com aviso de que
outputs íntegros serão reaproveitados. Sem alteração de inputs, perfis ou plano.
Preview aproximado continua separado do MP4 final, sem prometer paridade visual.

## 6. Limites

Sem regenerar imagem, voz ou HeyGen; sem chamadas externas pagas.
Sem timeline, preview frame-perfect, novos efeitos, alinhamento automático,
paralelismo de variantes, render distribuído ou novas dependências.
Sem migration/tabela de render: plano/recibo em arquivos e execução em Jobs.
Sem refatoração geral de Jobs/API/UI ou acesso a atributos privados do JobService.

QC editorial/loudness/clipping, approve/reject, contact sheet e cópia para pasta
de entrega permanecem na D5B. Probe/full decode técnico existente não equivale
a aprovação humana. Google Flow continua pausado/não bloqueante.

## 7. Critérios de saída e verificação

- POST retorna 202 sem executar renderer; worker roda apenas editing.render.
- Mesmo request retorna mesmo Job; execução explícita nova cria outro Job,
  reutiliza masters íntegros e não altera recibos.
- Plano salvo com três headlines produz três masters via Job, sem upstream.
- Resultados mistos não aparecem como sucesso integral na API/UI.
- Erros globais, plano inválido, assets alterados e órfãos falham de modo claro.
- Isolamento campanha/tipo/plano/output e mídia alterada/symlink têm regressões.
- Heartbeat mantém Job durante render demorado; stop não aborta o render ativo.
- UI cobre plano não salvo, clique duplicado, worker ocupado, resposta perdida,
  polling obsoleto, reload, resultados mistos e acesso ao master válido.
- E2E local usa mídia sintética e renderer real; nenhuma credencial/crédito.
- CLI e testes D5A.1 continuam verdes. Testes novos entram no CI Windows/Linux.
- Gate completo local, revisão independente e Actions antes de declarar a
  integração verificada; IMPLEMENTED não significa PROVIDER_VERIFIED.

## 8. Próxima decisão

Revisão deste design pelo usuário. Após aprovação, escrever o plano de
implementação Jobs/API → React, submetê-lo à revisão e escolher execução.
O aceite do escopo não autoriza saltar esses gates do Superpowers.
