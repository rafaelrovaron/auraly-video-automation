# D4B.1 — Painel local de campanhas

Data: 2026-10-03

Status: design aprovado pelo usuário em 2026-10-03; implementação ainda não iniciada.

## Objetivo e ponto de partida

Entregar a primeira interface local utilizável da Auraly: consultar campanhas, entender suas pendências e acompanhar/controlar explicitamente o worker existente. Uso pessoal, foco em delivery e pouca complexidade.

A base é a main em `9cd873190ccdca33df1743efdfb115aad68c3904`, com D4A.2 integrado e Actions Linux/Windows concluídos com sucesso. A API FastAPI, seus contratos e seus serviços são capacidade entregue. O frontend descrito aqui é roadmap novo, não capacidade já implementada.

Este documento não autoriza implementação, instalação de dependências, chamadas pagas ou publicação. Após aprovação do design escrito, o próximo artefato será o plano de implementação.

## Escopo desta entrega

- Lista de campanhas e detalhe de uma campanha.
- Copy, cenas, imagens, Voice Masters e renders HeyGen como metadados consultáveis.
- Status operacional, pendências, Jobs, tentativas, erros sanitizados e atualização periódica.
- Seleção do tipo de worker e ações explícitas de iniciar/parar.
- Estados de carregamento, vazio, erro, informação desatualizada e acessibilidade básica.

Não entram: criação de campanha, importação, aprovação de assets, geração de voz/HeyGen por formulário, cancelamento/retry de Jobs, OAuth no navegador, profiles, variants, preview, renderer, mídia servida ao navegador, timeline, canvas, login, cloud ou hardening amplo. As ações de geração/enfileiramento continuam nos fluxos existentes.

## Arquitetura mínima

Frontend em `web/`, usando React, TypeScript e Vite, com package e lock próprios. Preservar o package/lock atuais da raiz e o ambiente HyperFrames. Não adicionar roteador, biblioteca de estado global, SDK gerado, UI kit ou sistema de design nesta fatia.

A API é a única fonte de verdade. O frontend usa um cliente pequeno de `fetch`, tipos dos DTOs utilizados e estado React. Não acessa banco, filesystem, providers ou repositórios de Jobs. Não replica cálculo de aprovação, orçamento, elegibilidade ou status operacional.

Navegação por hash: `#/campaigns` e `#/campaigns/<campaignId codificado>`. Links continuam funcionando após reload sem exigir fallback de SPA no backend. Estado de campanha vem sempre da API; não persistir dados operacionais em localStorage.

### Conexão local

Nesta primeira fatia, dois processos: API em `127.0.0.1:8000` e Vite em `127.0.0.1:5173`, com porta estrita. O navegador usa caminhos relativos. O proxy do Vite encaminha apenas `/api` e `/health` ao destino fixo loopback.

Preservar Host e Origin do navegador no proxy (`changeOrigin: false`), para o middleware existente comparar a mesma origem. Não reescrever Origin para contornar validação, nem liberar CORS no FastAPI. Origin estrangeiro ou `null` continua rejeitado. Validar esse comportamento com o proxy e navegador reais, não apenas mock de fetch.

Vite fica restrito ao diretório `web/`, sem permissão ampla de filesystem nem carregamento de env da raiz. Sem bind público, allowedHosts irrestrito, alvo de proxy configurável pelo usuário ou logging de payloads/segredos. Selecionar versões fixadas compatíveis com o Node usado no CI durante o plano, não atualizar dependências do renderer por consequência.

O proxy é uma escolha de desenvolvimento local, não um pacote de distribuição pronto. Servir build estático pelo FastAPI ou criar launcher único fica fora desta entrega. Referência: [configuração oficial do servidor Vite](https://vite.dev/config/server-options.html#server-proxy).

## Experiência de uso

### Lista

Exibir campaignId, personagem, quantidade de cenas, status operacional e próxima pendência. A API não possui título amigável persistido: não inventar esse campo. Cada item abre o detalhe. Campanhas inexistentes mostram estado vazio e explicam que criação ainda ocorre pelo fluxo existente.

### Detalhe

Cabeçalho com campaignId, personagem, status, próxima pendência, última atualização bem-sucedida e botão Atualizar. Abaixo, seções simples:

1. Resumo: contagens de copies/vozes/imagens aprovadas, renders disponíveis e planos existentes.
2. Cenas: referências de copy/voz/imagem/render e pendências por cena.
3. Assets: metadados de imagens e Voice Masters, duração/QC/review e caminhos relativos retornados pela API.
4. HeyGen: renders, vínculos, status, remoteVideoId quando disponível e código de erro.
5. Jobs: tipo, status, tentativas, próxima tentativa e erro; detalhe seguro de operação local quando aplicável.
6. Worker: estado conhecido, campanha/tipo associados e controles explícitos.

Usar labels em português, mantendo IDs e códigos técnicos disponíveis para diagnóstico. Mostrar fatos retornados, sem percentual global inventado nem declarar entrega pronta quando existe pendência de renderer. Paths são texto, não URLs ou autorização para leitura local. Sem thumbnails, player de áudio ou MP4: a API atual não serve essa mídia.

Layout simples, sem dependência visual adicional: lista legível, seções e tabelas/listas que se adaptem à largura. Controles com label, foco visível, mensagens anunciadas e sem depender apenas de cor.

## Contratos utilizados

GETs existentes, sem query parameters:

- `/health`.
- `/api/v1/campaigns` e `/api/v1/campaigns/{campaignId}`.
- `/api/v1/campaigns/{campaignId}/status`, `/images`, `/voices`, `/heygen/renders`, `/jobs` e `/worker`.
- Detalhe de Job/operation somente quando selecionado: `/jobs/{jobId}` e `/operations/{jobId}`.

Somente dois POSTs da API são expostos nesta entrega:

- `/api/v1/campaigns/{campaignId}/worker/start`, JSON `{campaignId, kind}`.
- `/api/v1/campaigns/{campaignId}/worker/stop`, JSON `{campaignId}`.

Os tipos disponíveis são os existentes: `local_operations`, `voice_generate`, `voice_import`, `heygen_assets` e `heygen_videos`. Não criar endpoint global de worker nem nova regra de domínio só para facilitar a UI.

## Controles de worker e custo

Nenhum carregamento de página, polling ou reload inicia worker ou enfileira operações. Iniciar exige ação e confirmação mostrando campanha, tipo e aviso de que Jobs já enfileirados podem consumir créditos. `local_operations` também merece esse aviso: operações existentes podem encaminhar trabalho HeyGen. Contagens exibidas são fotografia da fila, não garantia de quantidade/custo futuro.

Confirmação da UI não substitui aprovações, orçamento ou validações dos serviços. Durante envio, bloquear clique duplicado. Nunca repetir POST automaticamente após timeout/falha de rede. Se a resposta se perder, informar resultado desconhecido e consultar estado/Jobs; não presumir que a ação falhou.

Parar significa impedir novas capturas e aguardar a operação corrente. Não cancela Job em andamento, desfaz geração ou devolve créditos. Mostrar `stopping` e manter acompanhamento até o estado retornado mudar.

### Limitação real do estado por campanha

Há um worker por processo. Seu estado mantém a última campanha/tipo, inclusive quando idle. Consultar `/worker` de outra campanha pode retornar 404: isso não prova que o worker está idle ou inexistente.

A UI apresenta estado desconhecido/não associado à campanha selecionada nesse caso. Pode oferecer início explícito, com o backend decidindo se há conflito; 409 comunica worker ocupado. Não oferece parada de worker de outra campanha sem identificar corretamente o proprietário. Após reload, não inventa proprietário a partir de cache. Idle também não significa que todos os Jobs da campanha terminaram.

## Atualização e falhas

Atualizar lista a cada 5 segundos; status, Jobs e worker do detalhe a cada 2 segundos enquanto a página estiver visível. Assets/detail são carregados na entrada, atualização manual e transições relevantes observadas no polling. Nunca sobrepor ciclos; cancelar requisições ao navegar e impedir respostas tardias da campanha A de substituir dados da B. Pausar polling com aba oculta e atualizar ao voltar.

Manter último dado válido acompanhado de timestamp e aviso de desatualização quando ocorrer erro. Se uma seção falhar, marcá-la indisponível, não zerar contagens nem transformar erro em aprovação. Falha da lista inicial gera estado de erro com ação Atualizar. Falha de conexão desabilita comandos até nova leitura bem-sucedida pertinente.

Mostrar códigos do envelope sanitizado da API e mensagens compreensíveis; não exibir traceback, resposta bruta ou payload de provider. Necessidade de autenticação HeyGen orienta usar o fluxo CLI existente, sem implementar OAuth novo. Conflitos permanecem explícitos e não disparam retry automático de comandos.

## Verificação e critérios de aceite

O plano deverá incluir testes de comportamento React, typecheck/build e integração no navegador com API e proxy reais. Reutilizar a infraestrutura Python/Playwright existente quando adequada, sem introduzir uma segunda infraestrutura E2E por conveniência. Testes de providers usam fakes: nenhum crédito real é necessário nesta entrega.

Aceite:

- Campanhas e seus dados existentes são visíveis e correspondem aos DTOs/status do backend.
- Reload/navegação/polling não causam POST, chamadas pagas ou alterações no banco.
- Confirmação inicia somente o tipo/campanha selecionados; stop representa draining corretamente.
- Worker de outra campanha/404, busy/409 e resposta perdida nunca aparecem como sucesso presumido.
- Polling não sobrepõe ciclos, descarta respostas antigas e sinaliza dados desatualizados.
- O POST positivo funciona pelo proxy; Origin estrangeiro/null continua rejeitado; nenhuma regra existente de aprovação/orçamento é enfraquecida.
- Erros, vazio e navegação por teclado são verificáveis; IDs/path metadata não viram acesso arbitrário a mídia.
- Testes frontend/build e harness completo existente passam, incluindo Linux/Windows, antes de integrar implementação.

## Sequência após esta fatia

D4B.2: formulários para os fluxos existentes de campanha/importação/review/Voice/HeyGen, com ações e custos explícitos. Imagens continuam geradas manualmente e importadas em batch; Google Flow não bloqueia delivery.

D4B.3: edição de profiles/manifests/overrides, variantes A/B de headline sem regenerar voz/imagem/HeyGen, e preview aproximado claramente identificado. Servir mídia controlada, se necessário, terá contrato próprio; sem timeline ou exigência frame-perfect.

Essas etapas não fazem parte do aceite D4B.1. O resultado desta fatia é um painel operacional pequeno sobre a API entregue, não a UI completa do MVP.
