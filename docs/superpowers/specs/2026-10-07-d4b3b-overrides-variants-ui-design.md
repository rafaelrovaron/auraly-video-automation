# D4B.3b — Overrides e variantes A/B pela UI

Status: direção conversacional e especificação aprovadas pelo usuário em
2026-10-07. Plano em preparação; implementação ainda não iniciada.
Base: D4B.3a integrado na main em `f3ac38a`.

## Resultado e fronteiras

Configurar edições de um MP4 HeyGen já disponível dentro da campanha, sem JSON
manual. Selecionar profile publicado, ajustar overrides e enumerar variantes
de headline; validar e salvar um plano editorial reutilizando imagem, Voice
Master e HeyGen. Uso pessoal/local e velocidade de entrega são prioridades.

Reutilizar EditBatchRequest, EditOverrides, EditBatchPlan e o planner D3B.
Não criar outro motor, endpoint, schema, migration ou dependência. Não gerar
copy, captions alternativas, imagem, voz ou vídeo. Não executar render nem
aprovar automaticamente mídia. Flow permanece pausado e não bloqueante.
Preview aproximado D4B.3c e renderer D5 são próximos recortes, não esta entrega.
AGENTS.md e sources/ permanecem intactos.

Escolha aprovada: formulário sobre API existente. Editor JSON seria menos
prático; novo backend duplicaria capacidades já implementadas.

## Fluxo operacional

Adicionar seção **Edição e variantes** ao detalhe da campanha. Um rascunho
trabalha com um MP4 por vez; não há batch entre vários MP4 nem seleção latest.

1. Selecionar render HeyGen com source local e status `ready`.
   Mostrar IDs de render/cena, source path relativo, hash e duração já expostos
   pela API. Sem player, upload, servir mídia ou aceitar MP4 arbitrário.
2. Selecionar profile por ID/versão/hash exatos, usando a lista/consulta D4B.3a.
   Link para a gestão global de profiles; aviso de descarte se houver rascunho.
3. Informar videoId seguro, inicialmente preenchido com renderId, e headline
   base obrigatória. Não copiar automaticamente da copy atual da campanha:
   a copy vinculada ao render pode ser uma versão anterior.
4. Ajustar camadas opcionais de campanha e vídeo; enumerar variantes com key,
   label e override de headline/texto. Iniciar com uma variante `a`, label `A`;
   adicionar/remover outras por ação explícita. Key segura e única, sem usar
   label para paths ou recalcular keys ao reordenar. Cada variante permite
   herdar headline base ou substituí-la por texto não vazio.
5. **Validar plano** executa planner com persist=false. Exibir outputs resolvidos,
   contagem, provenance e pendências, somente depois de resultado confirmado.
6. **Salvar plano** envia o mesmo request validado com persist=true. Confirmar
   resultado e consultar o artefato exato por videoId/planHash antes de anunciar
   salvo. Listagem e consulta dos planos publicados sobrevivem a reload.

Limite inicial maxOutputs=3, inteiro positivo ajustável explicitamente. Contagem
é o tamanho da lista; rejeitar excesso sem truncar. Sem produto cartesiano ou
geração automática de novas headlines. Salvar plano não produz MP4 final.

## Overrides sem perda de herança

Precedência existente: profile < campaign < video < outputVariant.
As camadas campaign/video pertencem ao request deste plano: não são novas
preferências persistentes globais da campanha, nem alteram o profile publicado.

Cada campo oferece **Herdar** ou **Substituir**. Herança omite a propriedade do
payload, em vez de mandar null ou uma cópia dos defaults. Substituir envia
somente o valor escolhido. False, zero, RGBA e strings não podem desaparecer por
checks de truthiness. Para campos nullable, oferecer **Limpar** explicitamente,
enviando null; isso é diferente de herdar. Input numérico vazio obrigatório é
inválido, não zero. Voltar a herdar remove o valor enviado.

Cobrir os campos atuais de EditOverrides: output, headline (incluindo text e
start/end), captions (estilo/highlight, não texto), music e framing. Reutilizar
controles nativos/labels e tipos D4B.3a onde compatível, sem transformar o
ProfileForm em um engine genérico de schema. Overrides avançados recolhidos
nas camadas campaign/video/variante; key, label e headline ficam visíveis.
Mostrar valores herdados como ajuda, não como resultado validado antes do planner.

Fontes e música usam AssetRef de path relativo ao project root e SHA-256
explícito, sem catálogo, upload ou cálculo de hash pelo browser. Defaults e
limites numéricos continuam os contratos existentes; cores RGB/RGBA textuais.
Não inventar duração, fonte, arquivo ou trim. Invalidar validação anterior ao
alterar qualquer request, incluindo profile, source, música e timing.

## Música e captions

musicAccepted é confirmação explícita para esta edição, inicialmente false.
Se houver música habilitada em qualquer output, planner exige aceitação e
asset válido. Mostrar aviso e impedir Save enquanto validação não confirmou.
Trocar source/profile ou referências de música limpa a aceitação para nova
confirmação; apenas trocar texto da headline não precisa limpar aceitação.

Captions derivam somente da Copy Master exata vinculada à voz/render. Não há
campo para texto de captions, transcrição substituta, ASR ou editor de cues.
Timing opcional usa sidecar existente por path relativo/hash; sem timing,
captions habilitadas ficam timing_missing. Isso permite salvar o plano com
pendência, mas nunca apresentar como pronto para render ou sincronizado.
Sidecar fornecido inválido é erro, mesmo com captions desabilitadas; não fazer
fallback silencioso. Planner valida vínculos, cobertura, tempos e aceitação.

Ao contrário de publicar profiles, validar plano verifica assets/source pelo
serviço existente. As referências técnicas não são uma aprovação visual final.

## Contratos e arquitetura

Frontend tipado específico para EditBatchRequest/plan/result/summary, validação
das respostas e componentes focados para overrides, variantes e fluxo do painel.
Reutilizar api.ts, profileApi.ts, useUnsavedChanges e controle de worker existentes.
Backend e contratos permanecem autoridade, sem calcular hashes editoriais na UI.

Endpoints existentes:

- GET /api/v1/editing/profiles e GET /api/v1/editing/profiles/{profileId}/{version}.
- POST /api/v1/campaigns/{campaignId}/editing/plans: EditPlanOperation com
  campaignId, operation=edit_plan, request completo e persist=false ou true.
- GET /api/v1/campaigns/{campaignId}/operations/{jobId} para Job conhecido
  e controles existentes de worker.
- GET /api/v1/campaigns/{campaignId}/editing/plans para lista.
- GET /api/v1/campaigns/{campaignId}/editing/plans/{videoId}/{planHash} para artefato.

POST retorna 202 e Job local, não plano pronto. Validar e salvar criam/reutilizam
operações locais determinísticas existentes e registros SQL de Job/audit; não
criam Jobs pagos. persist=false não publica plano/manifests nem altera MP4/WAV.
Worker local_operations precisa ser iniciado explicitamente nos controles
existentes; nenhuma ação ao abrir/recarregar, nenhum worker auto-start.
Mostrar Job e estado aguardando execução, com polling somente das leituras.
Não alterar o worker nem introduzir retries de POST.

## Estados, confirmação e falhas

Separar listas/source/profile, rascunho, request enviado congelado, validação
confirmada, Save e consulta readonly. Bloquear clique duplo e mutação do request
durante submissão pendente; refresh de listas não troca seleção nem descarta
rascunho. Guardas de campanha/seleção/AbortController rejeitam respostas antigas.

Resultado válido exige campanha, Job, operation/persist, renderId/videoId,
source.id=renderId, source.sha256/durationSec correspondentes ao render selecionado,
voiceRef/imageRef com IDs/hashes correspondentes e profileRef exato.
O path do render é relativo ao work root; source.path do plano é relativo ao
project root. Não comparar essas strings como se compartilhassem a mesma raiz
nem adivinhar prefixos no browser; backend valida containment e vínculo de mídia.
Contagem/keys/labels e overrides das camadas devem
corresponder ao request congelado. Validar DTO completo conhecido, finitude,
identidades, enums e consistência dos outputs; não aplicar defaults a respostas
incompletas. A API verifica hash/invariantes do plano. Não inventar outro hash
ou resolver paralelo no browser.
Normalizar somente seções vazias conhecidas dos overrides para comparação,
conservando propriedades fornecidas: backend serializa todas as seções, mas
omite campos não fornecidos; null explícito continua distinto de omissão.

Save só habilitado para o request da última validação confirmada. persist=true
revalida no backend. Seu planHash deve corresponder ao plano validado; caso
contrário, mostrar divergência, não afirmar Save conforme validado. GET exato
do artefato precisa confirmar o mesmo conteúdo antes de anunciar publicação.

Transporte/5xx/202 malformado são unknown, nunca sucesso ou permissão para
reenviar automaticamente. Conservar payload e dados exibidos, sem ecoar valores
arbitrários em erros. Com Job conhecido, consultar operação; com identidade de
plano conhecida, consultar artefato exato. Uma lista de Jobs/planos ou uma edição
existente não prova autoria do POST perdido: sem vínculo exato, continuar
unknown e orientar inspeção manual. Nenhum repost automático, adivinhação de
Job/hash ou renumeração. Abandonar acompanhamento exige confirmação explícita
e aviso de que a operação pode continuar; não cancela Job no backend.

Erro conhecido permite corrigir o rascunho, invalida confirmação antiga e exige
nova validação. Consultas readonly de planos não alteram rascunhos. Não oferecer
clonagem/reconstrução de request a partir do plano nesta etapa, porque o plano
não preserva uma entrada original completa para isso.

Rascunhos só em memória; confirmar antes de sair/trocar source/profile/descartar
quando há mudanças. Reload pode perder rascunho; aviso beforeunload existente,
sem autosave/localStorage. Planos salvos são consultáveis após reload; após
reload um Job inspecionado manualmente não restaura a correlação do draft perdido.

## Critérios de saída e verificação

- Um MP4 e três headlines produzem três outputs planejados distintos, sem
  nova imagem/voz/HeyGen, reserva de custo, alteração de aprovação ou mídia.
- Todos os campos permitidos preservam herança, false/zero/null/RGBA e camada;
  profile publicado não muda. Excesso, keys duplicadas, valor inválido e timing
  inconsistente impedem resultado válido sem publicação parcial.
- Validar persist=false, editar/invalidate, Save persist=true e GET de confirmação
  funcionam via API/worker real local. persist=false não cria plano no disco;
  operações locais podem escrever Job/audit. Save conserva MP4/WAV e upstream.
- Plano salvo reaparece após reload com conteúdo/identidades exatos. Captions
  timing_missing são pendência visível, não sincronização demonstrada.
- Falhas, Job failed, resposta perdida, resultado divergente, GET atrasado,
  duplo clique, refresh e navegação recusada não produzem falso sucesso ou POST
  automático; draft preservado. Cobrir browser Back explicitamente nesta rota.
- Inputs com labels, teclado e foco no campo inválido, incluindo details
  recolhidos; sem overflow em 320/390 pixels com IDs/paths/textos longos.
- TDD por tarefa, frontend/browser/API reais com providers fake, fast checks,
  gate completo 19 etapas e revisão independente antes de LOCAL_VERIFIED.
  Nenhuma chamada paga necessária; não ampliar PROVIDER_VERIFIED.

## Próximo gate

Revisar esta especificação antes de escrever o plano de implementação.
Execução só depois de plano aprovado e método confirmado. D4B.3a permanece
capacidade entregue; D4B.3b segue PLANNED até implementação/verificação real.
