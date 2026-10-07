# D4B.3a — Profiles de edição pela UI local

Status: especificação aprovada pelo usuário em 2026-10-07.
Plano e execução Nativa aprovados em 2026-10-07; implementação concluída,
`IMPLEMENTED`, `LOCAL_VERIFIED`: gate final 19/19, revisão independente concluída,
três Important corrigidos com regressões; browser Back específico adiado como Minor.
Evidência: `docs/superpowers/2026-10-07-d4b3a-verification.md`.

## Resultado pretendido

Permitir que Rafael crie, consulte e versione estilos reutilizáveis de edição
sem editar JSON ou usar CLI. Uso pessoal e local, com rapidez e simplicidade.
Reaproveitar `EditProfile` v1, API FastAPI, persistência JSON imutável e a UI
React existentes. Não mudar o contrato nem criar outra fonte de verdade.

Esta é a primeira entrega da D4B.3. Variantes A/B e preview aproximado são
entregas posteriores; renderer continua na D5. Um profile salvo não é um
vídeo editado, um plano de edição ou uma aprovação de assets.

## Abordagem e alternativas

Escolha: formulário React sobre os quatro endpoints de profiles existentes.
É o menor caminho para um fluxo operacional sem JSON manual.
Um editor JSON seria mais curto, mas não atende ao uso pretendido.
Um catálogo com upload, instalação de fontes e seleção de mídia exigiria
novo fluxo de assets; fica fora desta entrega, sem bloquear profiles.

Não adicionar dependências, migrations, endpoints, Jobs, autosave, catálogo
de presets, framework de formulários ou abstração genérica de campos.

## Navegação e fluxo

Adicionar entrada global “Profiles de edição” e rota `#/profiles`, independente
da campanha. Profiles são reutilizáveis; não salvar vínculo de campanha aqui.
Manter rotas e painéis atuais funcionando.

1. A tela lista ID, nome e versões disponíveis, com estados loading/empty/error.
2. “Novo profile” abre formulário com ID e nome, versão 1 e defaults atuais.
3. Selecionar ID/versão consulta seu conteúdo; versão publicada é somente leitura.
4. “Criar nova versão” copia a versão selecionada em rascunho, mantém ID e
   fixa versão de destino como base + 1. Não usa alias `latest`.
5. “Salvar versão” publica somente após ação explícita e confirmação pela API.
6. Após sucesso, consultar a versão persistida e atualizar a lista. Reabrir
   ou recarregar exibe o mesmo conteúdo salvo, não o rascunho anterior.

Se a versão base + 1 já existir com conteúdo diferente, não sobrescrever,
não procurar automaticamente outro número: informar o conflito e permitir
consultar a versão existente ou escolher outra base.

Rascunhos ficam em memória, sem localStorage/autosave. Reaproveitar
`useUnsavedChanges` para saída/fechamento; troca de seleção ou descarte
dentro da tela também exige confirmação quando houver alterações.
Atualização de lista não substitui silenciosamente um rascunho.

## Formulário e unidades

Organizar em seções nativas de formulário, com opções avançadas recolhíveis.
Oferecer todos os campos dos estilos atuais, não texto específico de campanha:

- Identidade: ID validado pelas regras atuais, nome; versão e data automáticas.
- Output: largura, altura, FPS; MP4/H.264 fixos pelo contrato.
- Headline e captions: habilitar, styleId, referência de fonte, peso, tamanho,
  line-height, cor, stroke, sombra, fundo, anchor, x/y, safe zones, maxLines
  e fitPolicy. Headline inclui início/fim; captions inclui highlight e cor.
- Music: habilitar, referência de asset, volumeDb, duckUnderVoiceDb, loop,
  trimStart/trimEnd, fadeIn/fadeOut.
- Framing: cover/contain, escala, x/y, zoomStart/zoomEnd.

Valores iniciais seguem o contrato: output 1080×1920/30, textos e música
desabilitados, headline topo/y=0.1, captions bottom/y=0.8, framing cover.
Não presumir fonte instalada nem asset musical disponível.

Usar controles HTML nativos e labels em português. Pixels referem-se ao canvas
de output; posições e safe zones são frações 0–1; tempos em segundos, áudio
em dB, escala/zoom 1–1.25. Não converter percentuais implicitamente.
Cor aceita RGB/RGBA hexadecimal; não perder alpha ao editar versões existentes.
Campos nullable vazios representam null; campos numéricos vazios obrigatórios
são inválidos, não zero. Preservar false/zero e valores de seções desabilitadas.

Fonte e música usam campos explícitos de path relativo ao project root e
SHA-256 conhecido, conforme `AssetRef`; não exigem JSON. Preencher ambos ou
deixar a referência ausente. Não aceitar URL ou caminho absoluto, calcular
hash no browser, abrir arquivos privados ou fazer upload nesta entrega.
Este caminho técnico é a simplificação inicial; um seletor de assets local
poderá substituí-lo em outro recorte aprovado, sem mudar `AssetRef`.

Mensagem permanente: “Profile salvo não confirma disponibilidade de fontes
ou música. Os arquivos serão verificados ao preparar a edição.”
Habilitar música não registra aceitação de uso; essa confirmação permanece
por edição no contrato existente. Não inventar timing de captions ou duração
de source para validar intervalos relativos ao vídeo nesta tela.

## API, persistência e limites

Usar exclusivamente:

- GET `/api/v1/editing/profiles`;
- GET `/api/v1/editing/profiles/{profileId}/{version}`;
- POST `/api/v1/editing/profiles`;
- POST `/api/v1/editing/profiles/{profileId}/{baseVersion}/versions`.

GET/POST retornam `ProfileView` com profile e profileHash. Cliente valida
estrutura, campos conhecidos, tipos finitos e identidade/versão esperadas,
seguindo o padrão dos clientes atuais. Não converter resposta malformada
em sucesso ou defaults. Backend continua responsável pela validação final.

Enviar objeto completo tipado, aliases camelCase e schemaVersion 1.0.
Fixar createdAt em UTC quando preparar a submissão; manter payload estável
enquanto o resultado estiver incerto. Não implementar hash canônico paralelo
no frontend; hash é fornecido pela API.

A publicação atual usa `validate_assets=False`: verifica o contrato e
persiste o profile, mas não prova existência/hash/tipo da mídia referenciada.
Preservar essa semântica e a validação posterior dos serviços de edição.
Persistência continua em `editing/profiles/<id>/<version>/profile.json`
sob o work root. Nenhuma alteração de SQL ou acesso direto do browser ao disco.

## Erros e resultado incerto

Validação local ajuda a preencher o formulário, sem substituir Pydantic.
Mostrar erros seguros da API e conservar o rascunho em caso de rejeição.
Impedir clique duplo e mutações simultâneas enquanto a publicação está pendente.

Falha de transporte, 5xx ou resposta inválida após POST significa resultado
incerto: não repetir POST, não incrementar versão e não anunciar falha definitiva.
Oferecer “Consultar versão enviada” via GET do ID/versão exatos e comparar
conteúdo tipado com a submissão congelada (createdAt não determina equivalência
do conteúdo, conforme hash atual). Correspondência confirma publicação;
conteúdo diferente informa conflito. GET inválido/falho permanece incerto.
GET not_found permite informar ausência atual e uma nova tentativa explícita
do mesmo payload, nunca retry automático. Antes dessa tentativa, repetir GET
para detectar publicação ocorrida entre as consultas; conflito continua sem overwrite.

Após reload, não recuperar automaticamente o payload não salvo: o operador
consulta o ID/versão publicado pela listagem. Não criar diário persistente
de submissões para um formulário local sem Jobs.

## Componentes e acessibilidade

Cliente dedicado `profileApi.ts` e painel `ProfilePanel.tsx`; separar formulário
em componente próprio somente se necessário para legibilidade. Alterações
pontuais em App/navegação/styles, sem refatoração dos painéis de providers.
Reutilizar tratamento de erros e proteção de rascunho já existentes.

Labels associados, navegação por teclado, status acessível, foco em erros
e layout utilizável em 320/390 px. Seletores não podem expandir a viewport;
conteúdo longo quebra dentro do painel. Não depender só de cor para estados.

## Verificação e critérios de saída

TDD por tarefa, testes frontend com infraestrutura existente e integração
browser/proxy/API reais com arquivos temporários. Sem provider ou créditos.

Cobrir criação, lista vazia, consulta, nova versão, conservação da anterior,
reload, conflito sem overwrite, false/zero/null/alpha, limites e safe zones,
referências incompletas, rascunho não sobrescrito, confirmação de descarte,
respostas inválidas e reconciliação de POST gravado com resposta perdida.
Garantir nenhuma ressubmissão automática e nenhuma chamada de voz/HeyGen.
Cobrir teclado e overflow a 320/390 px com teste de geometria real.

Antes do fechamento: gate `uv run python scripts/verify.py full`, diff revisado
para escopo/secrets e revisão independente conforme AGENTS.md. Evidência
local não implica provider verificado ou preview/render entregue.

Saída: operador cria duas versões por formulário, consulta ambas após reload
e confirma que a primeira não mudou. UI distingue profile publicado de
assets validados e de edição pronta. Nenhum JSON manual é necessário para
gerenciar profiles, embora referências locais ainda usem path/hash explícitos.

## Próxima aprovação

Revisar esta especificação antes de escrever o plano. Após aprovação, preparar
plano de tarefas pequenas e verificações; implementar somente após revisão
do plano e escolha do método de execução. Não alterar AGENTS.md nem sources/.
