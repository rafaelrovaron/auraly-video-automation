# D4B.3c — Preview aproximado simples

Status: abordagem e spec aprovadas pelo usuário em 2026-10-08.
Plano em preparação; implementação ainda não iniciada.
Base entregue: D4B.3b em main, `dd7203c`. O gate local passou 19/19 etapas;
Actions Windows passou. Actions Linux foi cancelado durante preparação do
ambiente, antes dos testes: não há confirmação Linux dessa correção.

## Resultado e escopo

Permitir avaliar headline, legenda e enquadramento de um MP4 HeyGen existente
antes do render. Uso pessoal/local; prioridade para entrega rápida e pouco código.

Escolha aprovada: um frame estático do MP4 com overlays HTML/CSS no painel
de edição existente. Player acrescentaria reprodução/sincronização; fundo
neutro sozinho não permitiria avaliar contraste e crop do vídeo real.

Não criar timeline, player, drag-and-drop, catálogo de fontes, alinhamento de
captions, mixagem, renderer, autosave, novo worker, migration ou dependências.
Não chamar providers nem alterar imagens, Voice Master, MP4, profiles ou
aprovações. AGENTS.md e sources/ permanecem intactos.

## Uso na interface

1. Selecionar MP4 e profile nos controles existentes de **Edição e variantes**.
2. Selecionar uma das variantes explícitas para visualizar. O preview aparece
   junto dos controles; em tela estreita, empilha sem overflow.
3. Alterar controles existentes: texto, enabled, tamanho, cor, posição, quebra
   e framing atualizam os overlays imediatamente, sem request por tecla.
4. Validar/salvar continuam ações explícitas D4B.3b. Preview não habilita Save,
   inicia worker, valida plano, publica artefato ou executa render.
5. Planos validados/salvos também podem ser visualizados em modo readonly,
   usando o manifest do output selecionado, sem reconstruir o rascunho.

Aviso permanente: **Preview aproximado — render final é a referência.**
Separar **Rascunho não validado** de **Plano validado/salvo**. Toda edição
invalida a confirmação anterior como hoje. Selecionar variante do preview
não modifica o request nem descarta rascunho. Respostas antigas não podem
trocar o frame após mudança de campanha/source.

## Composição visual

- Viewport padrão 9:16, representando o canvas lógico de output.width/height
  já configurado; outras proporções preservam a razão real e ficam sinalizadas.
  Escalar o canvas inteiro para a largura disponível, inclusive medidas em px.
- Frame original, sem overlays queimados, em fundo preto. Aplicar cover/contain,
  position x/y e escala existentes. Zoom animado não é reproduzido: usar zoomStart
  como amostra estática e sinalizar quando zoomEnd for diferente.
- Headline: texto da variante e estilo efetivo. Respeitar enabled, anchor,
  x/y, safe zones, fontWeight, fontSizePx, lineHeight, RGB/RGBA, fundo, padding,
  stroke e sombra com CSS. Não interpretar texto como HTML.
- Wrap e limite de linhas são aproximados. Mostrar aviso de overflow; não
  truncar silenciosamente nem afirmar fit validado. Shrink/error do renderer
  não são reproduzidos; sinalizar essa limitação quando selecionados.
- Neste primeiro recorte, fonte customizada **não é servida ao browser**.
  Usar fonte de sistema e aviso visível de fallback, indicando que fonte real
  e quebra precisam ser conferidas no render. Carregamento de fontes locais
  é extensão futura, não capacidade entregue por este slice.
- Captions no rascunho: amostra fixa identificada como **texto demonstrativo**,
  não copy aprovada. Em plano confirmado, usar primeiro cue existente, ou
  primeiras 12 palavras de captionInput.text quando timing está ausente.
  Preservar aviso de timing pendente. Não buscar a copy atual da campanha:
  pode divergir da versão vinculada ao render. Sem animação por palavra;
  highlightEnabled fica sinalizado como não simulado.
- Headline/caption são amostras de layout, não conteúdo sincronizado com o
  frame inicial; start/end continuam disponíveis nos controles, sem simulação
  temporal. Música fica apenas nos controles/status, sem áudio no preview.

FR-020 continua sendo o alvo completo. Este slice entrega composição estática
útil; fonte real e políticas de fit/highlight exatas não são certificadas.

## Integração mínima

Frontend: componente focado de preview dentro do EditingPanel, reutilizando
tipos, readOverrides e os hints existentes. Para rascunho, aplicar somente
precedência visual profile < campaign < video < variant; manter ausência,
false, zero e null distintos. Não copiar resolver, hashes, validação de assets
ou regras de render para o browser. Valores inválidos resultam em aviso de
preview indisponível, nunca em persistência de defaults inventados.

Para plano confirmado, consumir manifest resolvido pela API, sem nova resolução
no frontend. Não reutilizar resultado validado antigo como se fosse o rascunho
atual. Remover variante selecionada escolhe outra existente, sem renomear keys.

Backend: adicionar somente um GET binário específico:

`/api/v1/campaigns/{campaignId}/heygen/renders/{renderId}/poster/{sourceSha256}`

- Resolver render da campanha pelo repositório público existente; exigir ready
  e source local, e conferir hash esperado contra metadata e arquivo real.
- Reutilizar validação existente de trusted work root, containment, filename
  e symlink/junction. Não aceitar path, URL, timestamp ou comando do browser.
  Source do render usa work root; não adivinhar prefixo de project root na UI.
- Usar FFmpeg já instalado, subprocess com argumentos separados, sem shell:
  primeiro frame decodificável, PNG proporcional de no máximo 720 px por eixo.
  Timeout de 10 s, limitar saída a 4 MiB e encerrar subprocess ao ultrapassar
  limite/timeout; parâmetros não configuráveis pela UI.
- Retornar image/png em memória e Cache-Control: no-store. Sem arquivo de poster,
  cache persistente, Job, audit novo ou mudança de estado; nunca sobrescrever mídia.
- Manter fronteiras HTTP existentes: localhost, queries rejeitadas, sem montar
  project/work root como diretório estático. Erros sanitizados nos códigos
  existentes; stderr/path privado não aparecem na resposta ou logs públicos.

GET é feito somente quando a identidade source/campanha muda ou por ação
explícita **Recarregar frame**. Usar fetch abortável e Object URL, revogada na
troca/unmount. Alterar overlays ou variante não decodifica novamente o MP4.
Não carregar poster para cada output listado nem fazer polling de mídia.

## Falhas e verificação

Falha de poster não bloqueia edição/validação/salvamento: mostrar fundo neutro
com aviso **Frame indisponível**, sem insinuar que há mídia real. Não reter
frame de outra campanha/source; reconsulta só explícita. Falta de FFmpeg,
arquivo ausente/divergente e decode inválido têm erro visível e sanitizado.

Critérios de saída:

- Um MP4 e três headlines mostram três layouts, sem regenerar assets ou mídia.
- Troca de source não reutiliza frame antigo; edição de texto não produz POST
  automático nem novo GET de poster. Consulta readonly não altera rascunho.
- Disabled, herança, null/false/zero, escala, RGB/RGBA e framing seguem os dados;
  estados inválidos e limitações acima permanecem explícitos.
- Caption demonstrativa não vira copy/cue persistido; timing ausente não vira
  sincronização aprovada. Preview não modifica música/aceite, Save ou worker.
- Testes focados frontend/backend e browser real com MP4 sintético cobrem
  layout/variantes, falhas, respostas atrasadas, path/hash/campanha divergentes,
  timeout/limites de decode, acessibilidade e 320/390 px sem overflow.
- Gate completo existente e revisão independente antes de LOCAL_VERIFIED.
  Sem provider pago e sem novo PROVIDER_VERIFIED.

## Próximo gate

Revisar esta spec. Após aprovação, escrever plano curto com tarefas TDD de
poster, componente e integração/verificação. Implementar somente após aprovação
do plano e confirmação do método de execução; D5 renderer permanece separado.
