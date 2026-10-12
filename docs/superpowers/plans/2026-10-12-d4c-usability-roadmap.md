# D4C — Plano de melhoria da experiência do painel

**Data:** 2026-10-12
**Status:** `PLANNED`. Planejamento solicitado pelo usuário; nenhuma alteração de produto implementada neste documento.
**Objetivo:** operar uma campanha sem interpretar hashes, memorizar IDs ou procurar ações em uma página extensa, tanto no desktop quanto no celular.
**Execução:** incremental e nativa, reutilizando React, FastAPI e os serviços existentes. Cada slice terá tarefas pequenas, testes proporcionais e revisão conforme o workflow do repositório.

## 1. Diagnóstico e prioridade

O usuário relatou dificuldade de uso, excesso de identificadores, ausência de separação por etapas e experiência móvel inadequada. A inspeção do código confirma:

- `CampaignPanel.tsx` monta resumo, copy, imagens, voz, HeyGen, edição, Jobs e worker na mesma página. Títulos de assets usam IDs; listas mostram SHA-256 e caminhos completos.
- `App.tsx` só distingue campanhas, detalhe e profiles; não existe navegação entre etapas da campanha.
- `EditingPanel.tsx` usa IDs/hashes nos seletores e contém `RenderPanel`, que contém revisão e entrega. O usuário precisa atravessar a edição para chegar à finalização.
- `useUnsavedChanges.ts` trata mudanças de hash como saída do formulário. Adicionar abas sem adaptar esse comportamento pode descartar rascunhos ou produzir confirmações indevidas.
- `styles.css` já tem algumas regras responsivas; elas não resolvem a hierarquia, a densidade de informações ou a navegação móvel. Não se afirma ausência total de suporte responsivo.
- `EditProfile` já tem `name` e variantes editoriais têm `label`. A API de campanhas ainda não expõe um nome de apresentação separado de `campaignId`.
- Fontes, música e timing usam referências por caminho/hash. Ocultar apenas a exibição do hash não elimina sua digitação nos formulários.

D4C passa a ser a próxima prioridade antes de concluir D6. Reutilizar a campanha piloto e arquivos existentes para validar usabilidade, sem nova geração paga. O histórico de capacidades D1–D5 permanece válido; usabilidade melhorada ainda é planejada.

## 2. Abordagem escolhida para planejamento

Reorganizar os componentes existentes em etapas e simplificar a apresentação. Apenas mudar cores/espaçamentos deixaria o fluxo confuso. Um wizard obrigatório dificultaria voltar à edição, acompanhar lotes e revisar versões; por isso as etapas serão acessíveis por navegação livre, com ações bloqueadas somente pelos pré-requisitos reais.

Não introduzir um framework de UI, nova biblioteca de estado ou roteador apenas para esta mudança. Evoluir o roteamento por hash existente. Não criar timeline, editor frame-perfect, upload cloud, sincronização de rascunhos entre dispositivos ou automação nova de providers.

## 3. Navegação e organização

Cabeçalho da campanha: nome legível, estado atual, pendência prioritária e uma ação principal contextual. Progresso por contagens verificadas, como “2 de 3 vídeos prontos”; sem porcentagem ou previsão de tempo inventada.

| Aba | Conteúdo e ação principal |
|---|---|
| Visão geral | Progresso por etapa, pendências, atividade recente e “Continuar em…” apontando para a etapa necessária. |
| Roteiro | Texto, cenas e versões; criar/revisar o roteiro com aprovação explícita. |
| Imagens | Importação manual em lote, associação às cenas e cobertura; validar e confirmar a importação. |
| Voz | Áudio principal, versões, geração/importação e aprovação. |
| Vídeos | HeyGen, preparação do lote, confirmação de quantidade/custo disponível e acompanhamento por cena. |
| Edição | Estilo visual, headline, legendas, música, enquadramento, variações A/B e preview aproximado. |
| Finalização | Plano salvo, geração dos arquivos finais, verificação de qualidade, assistir/ouvir, aprovação e entrega. |

“Estilos de edição” substitui “Profiles de edição” na navegação global e fica acessível também pela aba Edição. Atividade detalhada e diagnóstico ficam em área secundária; Jobs e Worker deixam de ser etapas de produto.

URLs propostas: `#/campaigns/:id/:stage`, com `overview`, `script`, `images`, `voice`, `videos`, `editing`, `finalization`. O link antigo `#/campaigns/:id` abre Visão geral. Navegação visual por abas no desktop; no celular, seletor de etapa com nome e posição, além de “Continuar” quando houver próximo passo conhecido. Usar links com `aria-current` para as páginas, sem simular semântica de tabs incompleta.

Back/Forward, refresh e link direto preservam a etapa. Etapa desconhecida mostra orientação para voltar à visão geral. Trocar de etapa na mesma campanha conserva rascunhos em memória; sair da campanha ou recarregar com alterações não salvas mantém o aviso. Não prometer restauração de rascunho após fechar o navegador.

## 4. Linguagem, nomes e identidade

| Hoje | Apresentação proposta |
|---|---|
| Copy Master / hook / body | Roteiro / abertura / desenvolvimento |
| Voice Master | Áudio principal |
| Profile / overrides | Estilo de edição / ajustes personalizados |
| Job / worker | Tarefa / processamento |
| QC / render | Verificação de qualidade / gerar vídeo final |
| Hash de plano | Configuração salva, vídeo de origem e quantidade de variações |
| ID de asset ou output | Cena/local + tipo + versão/variação e data quando disponíveis |

Usar `profile.name`, `output.label`, local da cena, versão da copy e duração já disponíveis. Nomes repetidos devem ser diferenciados por contexto e versão/data; caso ainda ambíguos, acrescentar um código curto estável. Nunca usar posição variável na lista como identidade persistente nem selecionar dados pelo nome visível.

Adicionar nome opcional persistido à campanha, compatível com registros antigos, mantendo `campaignId` imutável. Campanhas antigas mostram o identificador atual como fallback até receberem nome. Criação recebe nome e gera código válido uma vez por intenção; conflito orienta ajuste, e reconciliação de resposta desconhecida reutiliza a mesma identidade. Usar metadados existentes se o domínio suportar sem ambiguidades; qualquer mudança de contrato deve ser aditiva e verificada antes da UI.

SHA-256, IDs integrais, códigos de erro, caminhos técnicos e recibos continuam disponíveis em “Detalhes técnicos”, recolhido por padrão, com copiar valor. O fluxo normal não depende de copiar/digitar hash. Não remover evidências nem alterar integridade, idempotência ou aprovação.

## 5. Formulários e feedback

- Uma ação principal por contexto, com estado “Validando…”, “Na fila”, “Processando”, “Pronto” ou “Precisa de atenção”. Ações indisponíveis têm motivo e caminho para resolver.
- Erros mostram o que falhou e o próximo passo. Exemplo: conexão perdida após envio → “Não foi possível confirmar o envio. Consulte o andamento antes de enviar novamente.” Código e ID ficam no detalhe técnico. Nunca traduzir resposta desconhecida como falha definitiva.
- Distinguir estado vazio de falha de leitura. Conteúdo anterior pode permanecer com aviso de desatualização; não autorizar ações que dependam de dados frescos.
- Seletores mostram nome/contexto, não hashes. Ajustes comuns aparecem primeiro; herança campanha/vídeo/variante fica em opções avançadas, mantendo sua precedência e indicando origem do valor.
- Selecionar fontes/música/timing locais por nome e resolver hash no servidor, via serviço pequeno limitado às raízes já autorizadas. O backend revalida a referência no uso. Sem catálogo genérico, leitura arbitrária do disco ou migração dos manifests existentes.
- Importação continua em lote a partir da máquina que executa o Auraly. Explicar que o celular controla essa máquina: um caminho digitado não acessa arquivos do telefone. Upload de mídia do celular fica fora deste ajuste.
- Aprovações e ações pagas continuam explícitas. Quantidade de vídeos novos e reutilizados deve aparecer antes da confirmação; mostrar custo apenas se houver dado confiável.
- Processamento fica junto da etapa: “Iniciar processamento pendente” ou “Consultar andamento”, reutilizando os comandos atuais. Não iniciar workers ao abrir aba e explicar quando a fila pode conter outras tarefas.
- Reutilizar players/preview existentes. Miniaturas de imagem e reprodução do áudio principal entram no slice de assets somente por endpoints de mídia validados; se inexistentes, incluir a menor extensão necessária, sem expor paths como URLs públicas.

## 6. Mobile e acessibilidade desde o primeiro slice

- Layout de uma coluna em telas estreitas, formulários sem larguras fixas e seletor de etapas que caiba em 320 px. Desktop pode usar preview e controles lado a lado.
- Alvos de toque de pelo menos 44 × 44 CSS px; campos de texto com fonte mínima de 16 px; espaçamento entre ações e nenhuma ação dependente de hover.
- Texto e selects longos não alargam a página. Detalhes técnicos quebram linha; player e preview preservam proporção e cabem na viewport.
- Foco visível, labels associados aos campos, erros associados ao campo, anúncio de operações sem ruído a cada polling e estados compreensíveis sem depender só de cor.
- Teclado virtual não deve esconder a ação do formulário; evitar rodapés fixos nesta primeira versão. Navegação por teclado e zoom de 200% continuam utilizáveis.
- Paleta/tipografia/espaçamento e estilos de botões unificados em CSS pequeno. Manter identidade atual, melhorar contraste/hierarquia, sem criar design system próprio.

## 7. Sequência de execução e critérios por slice

### D4C.1 — Navegação por etapas e continuidade

Arquivos principais: `web/src/App.tsx`, `CampaignPanel.tsx`, `EditingPanel.tsx`, `RenderPanel.tsx`, `useUnsavedChanges.ts`, `styles.css`. Criar shell de campanha/etapas e contexto de sessão apenas para estado compartilhado necessário.

- [ ] Introduzir rotas/abas e seletor móvel; extrair Finalização do formulário de edição, mantendo seleção explícita do plano salvo.
- [ ] Preservar rascunhos e alvo selecionado na sessão da campanha. Manter a coordenação de requests/polling e evitar duplicação de submits quando painéis mudarem.
- [ ] Testar link antigo, link direto, refresh, Back/Forward, etapa inválida, troca de campanha, rascunho entre abas e nenhuma mutação disparada por navegação.
- [ ] Entregar navegação funcional a 320/390 px e desktop, com um único conteúdo de etapa visível e acessível por vez.

### D4C.2 — Nomes legíveis e informações técnicas secundárias

Arquivos principais: `CampaignPanel.tsx`, `CampaignForms.tsx`, `VoicePanel.tsx`, `HeyGenPanel.tsx`, `ProfilePanel.tsx`, `EditingPanel.tsx`, `RenderPanel.tsx`, `ReviewPanel.tsx`, `DeliveryPanel.tsx`, DTOs/serviços de campanha se necessários. Criar helpers pequenos de apresentação compartilhada.

- [ ] Aplicar vocabulário, nomes existentes e detalhes técnicos recolhidos; adicionar nome persistido de campanha e códigos gerados sem substituir IDs existentes.
- [ ] Atualizar cards, cabeçalhos, selects e mensagens de operação. Exibir datas/duração/tamanho em formato humano quando úteis.
- [ ] Testar registros antigos, nomes iguais/longos, nomes ausentes, reload, identificação inequívoca e payloads com os IDs originais.
- [ ] Saída: usuário escolhe campanha, cena, voz, estilo e variante pelo contexto; hashes não aparecem na visão principal.

### D4C.3 — Operação guiada e seleção de assets

Arquivos principais: formulários de imagem/voz/HeyGen/profiles/overrides, `WorkerControls.tsx`, `api.ts`, `profileApi.ts` e módulos FastAPI de assets/mídia a acrescentar somente conforme a necessidade descrita acima.

- [ ] Trocar path/hash manual no caminho comum por seleção de asset autorizado com referência resolvida pelo backend; manter opção avançada compatível.
- [ ] Aproximar iniciar/consultar processamento da ação da etapa, melhorar validação e estados de falha parcial/conexão perdida e explicitar arquivos na máquina local.
- [ ] Exibir prévias de imagem/áudio via acesso validado, reutilizando infraestrutura disponível.
- [ ] Testar asset ausente/alterado, seleção inválida, campos obrigatórios, bloqueios reais, resposta perdida e reuso A/B sem nova chamada de provider.
- [ ] Saída: fluxo comum sem digitação de SHA-256 e sem necessidade de descobrir o nome interno de um worker.

### D4C.4 — Consistência visual, validação móvel e fechamento

Arquivos principais: `styles.css`, componentes ajustados nos slices anteriores, testes React existentes e `tests/test_web_*_e2e.py`; adicionar cobertura de navegação/usabilidade concentrada, sem duplicar toda a suíte para cada viewport.

- [ ] Uniformizar densidade, hierarquia, botões, espaçamento, mensagens e estados vazios em todas as etapas, incluindo estilos globais.
- [ ] Verificar 320, 390, 768 e 1440 px com nomes longos e dados reais/sintéticos representativos; nenhuma rolagem horizontal da página. Testar teclado, foco, zoom e erro de rede.
- [ ] Executar fluxo local com provider fake: campanha → importação → voz → vídeos → edição A/B → finalização → aprovação → entrega, preservando os contratos e guardas atuais.
- [ ] Fazer inspeção visual em navegador e teste manual no Safari/iPhone via endereço remoto disponível; registrar separadamente teste real e viewport emulada, sem alegar equivalência.
- [ ] Rodar testes focados, `npm --prefix web run test`, `npm --prefix web run build` e gate completo `uv run python scripts/verify.py full` antes de marcar o Goal `LOCAL_VERIFIED`. Coordenar portas dos E2E com os serviços locais para evitar colisões conhecidas.
- [ ] Registrar evidências e atualizar docs de operação; retomar D6 depois da validação de usabilidade.

## 8. Critério de aceitação do conjunto

Rafael consegue localizar cada etapa, entender a pendência, configurar três headlines sobre um vídeo existente, gerar/revisar/entregar um resultado e consultar progresso no celular sem interpretar hashes, copiar IDs entre telas ou perder rascunhos ao navegar. Confirmação humana de usabilidade é distinta de testes determinísticos. A/B não regenera voz/imagem/HeyGen; aprovações permanecem explícitas; falhas e respostas desconhecidas não causam repetição automática.

Este documento é o plano de execução por slices solicitado para o roadmap. Não representa redesign implementado, aprovação visual de telas ainda inexistentes ou autorização para executar novas gerações pagas. O primeiro entregável de desenvolvimento é D4C.1.
