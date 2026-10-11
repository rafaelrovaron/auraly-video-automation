# D5B.2B — QC e review humano na API/UI local

Data: 2026-10-11. Status: design escrito aprovado pelo usuário; não implementado.
Base: `main` em `d9d0db3`, com D5B.1 QC e D5B.2A review backend/CLI.
O fluxo conceitual foi aprovado na conversa. Este documento detalha a integração;
sua aprovação permite escrever o plano, não iniciar implementação ou fazer push.

## Objetivo e escolha

Permitir ao Rafael executar QC técnico, assistir e ouvir o master final e registrar
uma decisão humana sem sair da tela de render. Uso pessoal/local, delivery-first.
Sucesso: selecionar um output existente, revisar sua versão exata e recuperar
QC/decisão após reload sem depender de JSON manual ou estado apenas no navegador.

Escolha aprovada: controles por variante na tela atual. Uma página independente
adicionaria navegação; QC automático após render adicionaria processamento implícito.
Não implementar essas alternativas. Reutilizar serviços, fila, worker, mídia e
contratos existentes. Nenhuma nova dependência, tabela ou migration.

## Fluxo do operador

1. Salvar/consultar um plano confirmado e selecionar uma execução de render.
2. Para cada output `rendered` ou `reused`, consultar QC/review existentes.
   Outputs failed mantêm seu diagnóstico; não recebem ações de review.
3. Clicar **Executar QC** para analisar somente aquela variante, explicitamente.
   A tela acompanha a operação local e mostra queued/running/concluída/falha.
4. Exibir QC técnico como passed/blocked, com checks de integridade, áudio e texto,
   motivos e medidas disponíveis. Erro operacional é distinto de blocked.
   Exibir permanentemente: “QC técnico aprovado não significa aprovação humana”.
5. Assistir e ouvir o MP4 final usando `<video controls preload="metadata">`, sem
   autoplay/mute obrigatórios. Manter links existentes de abrir/baixar como fallback.
6. Informar nome do revisor, comentário e checkbox **Assisti e ouvi este master**.
   Aprovar exige QC passed, nome não vazio e checkbox marcado. Rejeitar exige
   QC passed ou blocked, nome e comentário; não exige checkbox marcado.
7. Avisar que a decisão é definitiva para essa versão, confirmar o envio e salvar.
   Exibir decisão, revisor, comentário e data UTC com indicação de fuso na tela.
   Uma decisão existente é somente leitura, sem botão de editar/revogar.
8. Rejeição orienta editar/salvar outro plano e renderizar/revisar outra versão;
   não regenera nem sobrescreve qualquer arquivo.

## Alvo e integridade

O alvo é campaignId + videoId + planHash consumidor + outputVariantId + renderKey,
derivado no servidor do Job de render selecionado e do plano salvo. Nenhum alvo é
inferido de rascunho, preview, caminho fornecido pelo navegador ou “último render”.
API verifica campanha, tipo/estado do Job, presença e sucesso do output e identidade
do plano antes de executar QC/review. Um render parcialmente bem-sucedido permite
revisar somente seus outputs bem-sucedidos.

Reutilizar `QcService.run/get_report`, `HumanReviewService.record/get_review` e
`open_verified_render`. A identidade da decisão permanece a do D5B.2A, incluindo
master SHA e receipt SHA. Outro plano consumidor ou variante não herda aprovação,
mesmo reutilizando o mesmo MP4. QC key diferente não permite segunda decisão.
Consulta revalida evidência; arquivo presente ou Job completed não bastam.

Adicionar apenas consultas públicas específicas para descobrir relatórios QC e a
decisão dessa versão. Enumerar diretórios do alvo com validação de caminhos e ler
contratos tipados; resultados incluídos devem passar pelas consultas verificadas
existentes. Sem índice SQL, ponteiro latest, cache de aprovação ou varredura global.
QC de outro plano é filtrado pelo contexto e nunca adotado. Para review, calcular a
chave da versão atual a partir de master/receipt verificados e consultar o original.

Ausência real é estado vazio; corrupção, symlink/junction, identidade incompatível
ou evidência alterada são erro, nunca “ainda não revisado”. Se não for possível
validar o contexto com segurança, desabilitar decisão e pedir reparo/consulta.
Não implementar atestação contra um operador que reescreve toda a evidência local.

## API e execução

Usar uma família de rotas sob o output existente:
`/api/v1/campaigns/{campaignId}/editing/renders/{jobId}/outputs/{outputVariantId}`.

- `GET /review-state`: visão tipada/versionada do alvo, relatórios QC válidos,
  decisão humana verificada ou null e execuções QC desse alvo para recuperação.
  Consulta read-only, sem FFmpeg, geração ou escrita. Respostas não cacheáveis.
- `POST /qc`: request tipado com executionId UUID e contexto esperado
  videoId/planHash/renderKey. Servidor valida contexto e constrói QcRequest com
  exatamente um output. Responde 202 com identificação da operação.
- `POST /review`: recebe HumanReviewRequest existente, exige correspondência com
  o alvo da URL, salva diretamente e retorna HumanReviewRecord verificado.
  Não enfileirar a declaração humana nem alterar seu contrato de domínio.
- `GET /media` existente permanece o caminho de reprodução/download verificado.

QC é operação longa: acrescentar `edit_qc` ao request/result discriminado das
operações locais e reutilizar `api.local.operation` e worker `local_operations`.
Não criar outro executor, worker kind ou Job type. A API somente enfileira;
o handler chama QcService no worker. A UI informa que esse worker pode drenar outras
operações locais pendentes da mesma campanha, como já faz hoje.

executionId faz parte do payload idempotente: repetir o mesmo pedido retorna o
mesmo Job; uma nova execução explícita pode reutilizar relatório válido. Uma
falha operacional permite nova execução voluntária após corrigir inputs/runtime.
Sem retry automático. Job completed com output status error não significa QC
passed; apresentar os dois estados separadamente.

Usar serviços públicos de Jobs para recuperar as operações desse alvo, validar
payloads/resultados e encontrar executionId após resposta perdida/reload.
Nenhum acesso novo a atributos privados do JobRepository. Persistência de
operações existente é mecanismo de acompanhamento, não fonte da aprovação.

Se houver vários relatórios QC válidos, mostrar seletor por qcKey/policy/runtime;
não escolher por data de modificação ou presumir que o mais recente aprova.
Com um relatório, selecioná-lo; com vários, exigir seleção explícita. Se já houver
decisão, mostrar seu QC original, mesmo com outros relatórios disponíveis.

## UI, erros e recuperação

Adicionar componente de QC/review por output dentro de RenderPanel e um adaptador
API tipado, sem concentrar novo fluxo no editor de rascunhos. Estado isolado pela
identidade inteira do alvo; trocar campanha/plano/Job/output limpa formulário,
checkbox e mensagens antigas. Abort/cleanup e descarte de respostas tardias.

Polling serial a cada 2 s somente para QC ativo; reutilizar padrões de operação e
worker existentes. Clique duplicado não envia duas ações. Iniciar worker ocupado
não representa falha do QC enfileirado; oferecer consulta e início explícito quando
livre. Abandonar acompanhamento não cancela o Job, não gera novo executionId nem
reenvia POST. Não adicionar cancelamento de processamento nesta etapa.

Resposta perdida de QC: consultar pelo executionId antes de qualquer nova execução.
Resposta perdida de review: bloquear novos envios e consultar review-state; se
decisão foi salva, exibir original; se consulta bem-sucedida confirma ausência,
permitir retry explícito do mesmo request congelado. Nunca reenviar automaticamente
ou transformar erro de consulta em ausência. Reload descobre os registros no
servidor; sem localStorage como autoridade e sem presumir sucesso pelo clique.

API continua fonte das regras. Validação de corpo, campanha/contexto incorreto,
QC inválido/blocked para aprovação, evidência stale e conflito definitivo retornam
erros sanitizados conforme contratos existentes. Não expor paths absolutos,
tracebacks, secrets ou texto arbitrário de subprocesso. UI traduz apenas códigos
e findings conhecidos, com fallback seguro. Conflito pede consulta da decisão.

Após mudança/corrupção de evidência, não manter badge de aprovação anterior como
estado vigente. Checkbox é declaração humana, não medição de playback nem identidade
autenticada; não forçar timers de reprodução. Player mostra master renderizado,
nunca o preview aproximado. Labels, foco, teclado e status acessíveis.

## Verificação e critérios de aceite

TDD por tarefas; testes de serviço/HTTP, comportamento React e E2E local real.
Cobrir QC individual e reuso; operação queued/running/error; falha parcial; seleção
de vários QC; alvo estrangeiro/failed/alterado; approve/reject; confirmação humana;
conflito e repetição; reload; respostas perdidas; clique duplo; troca de alvo;
resposta tardia; erros sanitizados e ausência distinta de corrupção.

E2E com mídia sintética: render existente, QC, reprodução pelo endpoint de mídia,
aprovação, reload, consulta original; outra variante com rejeição e comentário.
Confirmar master/receipt/voz/imagem/HeyGen/plano/budget sem alteração, nenhum novo
encode ou provider acionado pelo QC/review. Nenhum teste aprova campanha real.

Gate completo `uv run python scripts/verify.py full`, schemas relevantes sem drift,
checks Windows existentes preservados e revisão independente antes da integração.
`LOCAL_VERIFIED` só após gate; não amplia `PROVIDER_VERIFIED`.

## Exclusões e handoff

Sem timeline, preview frame-perfect, proxy/contact sheet, playback tracking,
autenticação nova, revogação, edição de decisão, aprovação automática, batch approval,
QC implícito pós-render, provider, geração nova, dependência ou hardening geral.
Entrega/cópia de masters aprovados permanece D5B.3. Flow continua pausado.
AGENTS.md e referências sincronizadas não serão alterados.

Design escrito aprovado na conversa em 2026-10-11. Próximo gate: revisar o plano
de implementação em tarefas pequenas e aprovar o método de execução.
