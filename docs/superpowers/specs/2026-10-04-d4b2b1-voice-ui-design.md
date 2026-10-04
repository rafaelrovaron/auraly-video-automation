# D4B.2b.1 — Voice Master na UI local

Data: 2026-10-04.
Status: design conversacional aprovado, incluindo orçamento de campanha;
esta especificação escrita aguarda revisão do usuário.
Capacidade proposta: PLANNED, não implementada.

## Objetivo e baseline

Operar geração, importação e revisão de Voice Masters dentro da campanha,
sem CLI ou JSON manual nesse fluxo. Uso local e pessoal, delivery-first.
Ouvir o WAV fora da UI foi explicitamente aprovado para este recorte.

Baseline: main `04a6d84951f4e61ab05cda17f990d0729833e35d`.
D4B.2a entrega campanhas/copy aprovada, import/review de imagens e controles
de worker. Backend de voz já oferece geração ElevenLabs, import MP3/WAV,
processamento, QC e revisão; os formulários de voz ainda não existem.
O [Actions do baseline](https://github.com/rafaelrovaron/auraly-video-automation/actions/runs/37223383591)
concluiu com sucesso. Essa evidência não constitui teste real novo de provider.

D4B.2b será dividido em Voz (esta spec) e HeyGen (design/plano próprios depois).
D4B.3 mantém profiles, variants e preview; D5 mantém o renderer.

## Abordagem e limites

Usar formulários nativos na campanha existente, não wizard nem configurador
genérico. Reusar React/FastAPI, SQLite, serviços e workers atuais. Um componente
de voz concentra os formulários; um módulo pequeno de cliente concentra os
DTOs e requests. Não duplicar polling, transporte ou engine de jobs.

Inclui seleção de copy aprovada, orçamento de campanha, submissão de geração
ou import, acompanhamento e revisão explícita. Não inclui player, streaming,
upload pelo browser, seletor nativo, catálogo de vozes/modelos, presets novos,
editor JSON, regeneração forçada, retry automático da UI, HeyGen, OAuth,
alteração do processamento, novas dependências ou migrations.
Secrets continuam no runtime local, nunca em campos da UI, DTOs ou logs.

## Seleção da copy

Selecionar explicitamente uma versão aprovada, sem resolver implicitamente
para a versão mais recente ao submeter. Mostrar versão, headline visual e
narração hook/body/CTA. A headline não entra no texto falado.

O backend continua fonte de verdade para elegibilidade e bloqueios de voz
ativa/aprovada. Mostrar vozes históricas por versão; não apagar ou substituir
histórico. Mudança de versão ou configuração invalida as confirmações locais.

## Orçamento de campanha: extensão mínima aprovada

Campanhas criadas na UI possuem `budget={}`. O serviço de geração exige
`currency` e `limitCents`, mas a API atual não permite configurá-los nem
consultá-los em `CampaignDetail`. Não usar SQL direto, editar banco ou simular
um budget somente no browser.

Adicionar GET/POST focados em `/api/v1/campaigns/{campaignId}/budget` no mesmo
prefixo já usado pelas rotas de campanha. GET retorna uma projeção tipada:
`state` (`missing`, `configured` ou `invalid`), `currency` e `limitCents`, estes
dois nulos fora de `configured`. POST retorna essa mesma projeção após gravação.
Não expor o dicionário de metadata arbitrário. Valores legados inválidos não
viram um orçamento válido; a UI bloqueia geração e explica o impedimento.

POST recebe `currency`, `limitCents` e `confirmed: true`. A moeda é um código
de três letras ASCII maiúsculas; não há conversão cambial nem cotação. O limite
é inteiro positivo em unidades de 1/100 da moeda, nunca float ou boolean.
Usar campo numérico rotulado em centavos para evitar arredondamento monetário.

O serviço de campanhas grava na coluna JSON existente, em transação, preserva
outros metadados e atualiza `updated_at`. Configuração inicial é permitida
quando ambos os campos financeiros estão ausentes. Repetir exatamente a
configuração existente é um no-op idempotente. Alterar moeda/limite já
configurados ou reparar configuração parcial/inválida é recusado neste slice,
com conflito explícito: não reinterpretar autorizações ou gastos históricos.
Essa limitação aparece na tela; não apresentar um editor irrestrito de budget.

Salvar orçamento é ação curta e síncrona, sem Job e sem provider. Resposta
incerta exige GET fresco para observar a configuração, nunca repost automático.
A gravação não autoriza nenhuma geração. Não prometer que o limite representa
saldo disponível, custo estimado ou contabilização nova de gasto acumulado.

## Geração ElevenLabs

Formulário: versão da copy, `voiceId`, `modelId`, responsável e teto autorizado
em centavos. Exibir moeda/limite configurados. Não inventar IDs ou descobrir
recursos via chamada paga. Manter `voiceSettings={}`, formato e threshold
nos defaults atuais; não adicionar controles avançados neste recorte.

Checkbox inicialmente desmarcado confirma a autorização de gasto daquela
request. `approvedBudgetCents` deve ser positivo e não exceder o limite da
campanha. Validação no browser ajuda o operador; backend continua autoritativo.

POST `/voices/generate` usa `VoiceGenerateRequest` com versão explícita,
`paidRequestApproved=true` e `paidRequestApprovedBy`. Retorna IDs de voz e Job;
isso significa enfileirado, não provider concluído ou áudio aprovado. Não
inventar `requestId`: geração usa a identidade lógica existente do serviço.
Reuso da mesma identidade não significa regeneração ou uma nova autorização
que sobrescreve a histórica. Regeneração forçada está fora do escopo.

O operador inicia explicitamente `voice_generate` no controle existente;
essa ação pode executar jobs elegíveis desse tipo na campanha, não só o
formulário recém-enviado. Explicar esse escopo na confirmação de start.
Worker pode drenar e ficar idle. Stop não cancela uma chamada ativa nem
reembolsa créditos. Preservar políticas de retry/checkpoint do engine atual;
nunca acrescentar retry automático de POST, auto-start ou auto-resume pela UI.

## Importação local MP3/WAV

Selecionar copy aprovada e informar caminho relativo ao project root confiável.
O operador copia o arquivo pelo Explorer antes da submissão. Mostrar essa
orientação e os formatos/limite atuais (MP3/WAV, até 100 MiB).
Sem campo de upload e sem copiar/mover originais pela UI.

POST `/voices/import` usa `VoiceImportOperation`, request aninhada com campanha
e versão explícita, `sourcePath` e `requestId` criado pela UI uma vez por intenção.
Preservar a intenção enquanto seu resultado estiver incerto. Nova ação explícita
usa novo ID, sem contornar bloqueios e reuso existentes do serviço de import.

O fluxo tem duas fases, rotuladas separadamente:

1. Worker `local_operations` registra a entrada e cria voz/Job filho.
2. Após resultado confirmado, worker `voice_import` processa o áudio.

O sucesso do wrapper da fase 1 não significa sucesso do Job filho. Mostrar
IDs e status de ambos. Cada start é explícito e segue o escopo de campanha/tipo.
Falha no ASR ou QC não vira sucesso por o runner ter voltado a idle.
Original preservado; pipeline mantém WAV mono/48 kHz/24-bit, normalização e
trim de silêncio nas duas bordas. Não fornecer transcript manual substituto.

## Acompanhamento e revisão

Reusar GET de vozes, jobs e operações, mostrando somente fatos persistidos:
versão, origem, status, duração, caminho relativo e hash do WAV processado,
resultado da transcrição, headline detectada, achados de QC e histórico de review.
Não criar link de streaming ou fingir que o browser abre um arquivo local.
Exibir o caminho com orientação para ouvir o WAV no filesystem usando o work
root configurado no servidor. Quando houver path não significa aprovação.

POST `/voices/{voiceId}/review` é uma operação local: enfileirar, iniciar
`local_operations` explicitamente e observar resultado mais estado atualizado.
Responsável e confirmação de revisão são obrigatórios. Aprovação confirma
também a escuta do WAV; rejeição exige motivo. Backend preserva todos os gates.

A exceção de transcript só é oferecida para origem `imported`, comparação
`review_required`, headline explicitamente não falada e somente o finding de
revisão humana permitido por `permits_transcript_review`. Exigir motivo nessa
aprovação. Nunca liberar transcript `mismatched`, headline falada, achados
adicionais ou estado desconhecido por checkbox. O serviço decide definitivamente.

## Estados incertos, erros e navegação

Reusar validação de DTO, cancelamento e proteção contra resposta tardia do painel.
Preservar último snapshot válido e marcá-lo stale em erro. Ações que dependem
de leitura atual ficam bloqueadas até GET válido. Forms têm labels, feedback
legível, submit único enquanto pending e aviso compartilhado de draft não salvo.

POST com 5xx, timeout, resposta inválida ou perda de conexão pode ter persistido:
mostrar resultado desconhecido, preservar intenção e fazer apenas GET fresco
após assentamento da request. Status observado não prova autoria.

Para imports/reviews com Job aceito, recuperar `/operations/{jobId}` e validar
campanha/tipo/resultado antes de usar IDs. Para Job direto de geração, consultar
o Job correspondente e voz retornada; não tratá-lo como wrapper local.
Sem resposta/ID comprovável, listar novos fatos persistidos sem afirmar que
correspondem exatamente à intenção: o DTO atual não expõe todos os parâmetros
da geração. Não deduzir identidade por versão, timestamp ou provider apenas,
nem acrescentar endpoint de busca genérica para eliminar essa incerteza.
Permitir inspeção/adoção explícita de Job comprovado do mesmo tipo/campanha;
adoção não concede aprovação ou autorização nova. Reload não dispara mutações.

## Verificação e aceitação

Implementação futura segue TDD, commits pequenos, gate completo e revisão
independente conforme o repositório. Regenerar apenas schemas afetados por
contratos públicos; sem schema drift ou alterações em AGENTS.md/sources.

Cobrir orçamento vazio, válido, parcial/inválido, no-op e conflito; inteiros,
moeda, confirmação e preservação de metadata; nenhum Job/provider no save.
Cobrir versão fixada, headline fora da narração, autorização separada, submit
duplo bloqueado e resultado desconhecido sem segundo POST. Cobrir import em
duas fases, Job filho falho, defaults, histórico, review e exceção restrita.
Testar stale/reload/unmount, paths inválidos, erro de DTO e resposta perdida
depois de persistir. Não transformar fatos coincidentes em identidade exata.

Testes de integração usam browser/proxy/API/SQLite/worker reais, mídia sintética
e provider/transcriber fakes. Eles devem permitir configurar campanha nova,
gerar/processar uma voz fake e, em campanha separada, importar/processar/revisar
um áudio sem CLI/JSON manual nem chamadas pagas. Executar `verify.py full`.
Isso estabelece somente LOCAL_VERIFIED, não novo PROVIDER_VERIFIED. Canary
pago não é necessário para a entrega e depende de autorização específica.

## Handoff

Revisar esta especificação escrita antes de preparar o plano. Depois, revisar
o plano e escolher o método de execução antes de implementação. HeyGen fica
para o próximo design; nenhuma aprovação deste documento autoriza merge/push,
provider pago ou início automático de desenvolvimento.
