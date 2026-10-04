# D4B.2a — Campanhas, copy e importação manual na UI local

Data: 2026-10-04.
Status: design conversacional aprovado; spec escrita aguardando revisão do usuário.
Capacidade descrita abaixo: PLANNED, não implementada.

## Objetivo e baseline entregue

Permitir criar uma campanha e importar/revisar três imagens pela interface local,
sem CLI nem edição manual de JSON nesse fluxo. Uso pessoal, delivery-first,
reutilizando React, FastAPI, SQLite, serviços e worker existentes.

Baseline integrado: main `05c5a8e44b1eace1147811ea958dc280d3d9227a`.
O D4B.1 entregue oferece navegação de campanhas, metadados, jobs, resultados de
operações e controles explícitos do worker. Ainda não oferece os formulários
operacionais desta spec. O Actions desse SHA passou em Linux e Windows:
[run 37201726703](https://github.com/rafaelrovaron/auraly-video-automation/actions/runs/37201726703).

## Escopo e limites

Inclui criação de campanha com copy aprovada e cenas iniciais, novas versões de
copy, preparação de pasta, associação manual de arquivos a variantes, validação,
importação e revisão explícita de candidatos de imagem.

Não inclui edição/exclusão de cenas cadastradas, automação do Google Flow,
monitoramento de pasta, upload pelo navegador, seletor nativo, galeria de mídia,
voz, HeyGen, editor visual, preview ou novas bibliotecas. Voz/HeyGen ficam para
D4B.2b; edição e preview aproximado para D4B.3. Não haverá chamadas pagas aqui.

## Campanha e copy

A listagem atual ganha a ação de criar campanha. O formulário usa os contratos
existentes: identificador, personagem, objeto de prova, voice preset, edit preset,
headline, hook, body, CTA e uma lista de cenas. Cada cena tem identificador,
localização, atmosfera opcional, ação, prompt e objeto de prova opcional.

Personagem usa o enum atual; presets são referências de configuração, não uma
promessa de geração ou validação de recursos do provedor. Budget e config começam
como objetos vazios; não haverá editor JSON nem autorização implícita de gastos.
São aplicadas as regras existentes de IDs, campos obrigatórios, unicidade de
variantes e de localização normalizada. A campanha nasce com status draft.

O contrato atual só aceita copy aprovada. Portanto, a ação se chama **Criar
campanha com copy aprovada**, exige confirmação desmarcada inicialmente e ator
explícito. Não existe botão que finja salvar uma copy não aprovada. `source_text`
é composto deterministicamente dos quatro campos, com rótulos Headline, Hook,
Body e CTA, nessa ordem; o texto falado continua somente hook + body + CTA.

Na campanha, **Adicionar versão de copy aprovada** reutiliza o mesmo formulário e
confirmação. Versões anteriores permanecem disponíveis como histórico somente
leitura. Nenhuma atualização sobrescreve a versão anterior. As cenas são criadas
apenas no formulário inicial; não se acrescentará CRUD de cenas nesta fatia.

## Fluxo de imagens

1. **Preparar pasta:** informar um caminho relativo ao work root e submeter a
   operação existente de preparação. Após o job terminar, mostrar os caminhos
   retornados da pasta `images/` e do manifest modelo. A pasta não é monitorada.
2. **Copiar arquivos:** o operador coloca as imagens em `images/` pelo Explorer.
   A UI fornece instruções e caminhos copiáveis, sem abrir programas nativos.
3. **Associar:** uma linha por variante mostra contexto da cena e um campo de
   caminho relativo à pasta preparada, por exemplo `images/scene-01.png`.
   A associação é explícita; não inferir variantes por ordem ou nome de arquivo.
4. **Salvar associações:** publicar um manifest imutável pela operação descrita
   abaixo. O operador não edita o modelo JSON. Salvar não valida nem importa.
5. **Validar batch:** executar o dry-run e mostrar cobertura, ação create/reuse,
   hash e dimensões dos arquivos válidos, ou problemas por variante.
6. **Importar batch validado:** exigir confirmação explícita; criar/reutilizar
   candidatos pendentes de revisão. Não aprovar imagens automaticamente.
7. **Revisar:** usar approve/reject/replace existentes, com ator, confirmação para
   approve/replace e motivo obrigatório para reject. Exibir o estado retornado
   pelo servidor; não presumir que importação significa aprovação.

Alterar a pasta ou uma associação invalida a validação mostrada imediatamente.
Uma mudança externa no arquivo será detectada pelo backend antes de importar.
Conflito com imagem já aprovada é bloqueado conforme a política atual: mostrar o
problema e permitir revisão explícita nos termos dos contratos existentes, nunca
substituir uma aprovação automaticamente para viabilizar a importação.

## Extensão mínima do backend

### Publicação de associações

Adicionar uma operação local `image_manifest`, submetida por uma rota de ação
sob a campanha, usando a fila/worker/resultados existentes. Não criar tabelas,
scheduler, serviço de arquivos genérico ou segundo motor de jobs.

A entrada contém campaignId, directoryPath e itens variantId/path. directoryPath
é relativo ao project root, como os caminhos retornados pela preparação, e deve
resolver dentro do work root e identificar uma pasta preparada para a campanha.
Validar o manifest modelo da preparação, ownership e cobertura exata das cenas.
Não inspecionar imagens nessa operação: existência, formato e dimensões ficam no
dry-run. Caminhos de itens não podem escapar da pasta, inclusive por symlinks ou
junctions. Não aceitar caminhos absolutos ou travessia de diretórios.

O servidor define schemaVersion 1.0, campaignId, approveImported false e
approvedBy null. Ordena itens por variantId e serializa JSON UTF-8 canônico; publica
`image-import-<sha256-dos-bytes>.json` ao lado do modelo, sem sobrescrevê-lo.
Publicação é exclusiva/atômica; replay só reutiliza um arquivo com conteúdo
idêntico verificado. Conteúdo conflitante no destino resulta em erro, sem overwrite.

O resultado tipado inclui manifestPath, manifestSha256 dos bytes exatos, imagesPath
e associações. Jobs persistidos permitem recuperar esse resultado após reload ou
resposta perdida, sem adicionar descoberta geral de arquivos. Rascunhos de
formulário não salvos não são persistidos; avisar disso antes da navegação.

### Validação e vínculo com execução

Estender o resultado tipado de image_import/dry_run com valid, manifestSha256,
itens inspecionados e issues. A UI solicita o modo de diagnóstico por um campo
opcional includeDiagnostics. Nesse modo, um diagnóstico de domínio conhecido
termina o job com valid false; isso não equivale a batch válido ou importado.
Clientes legados mantêm a semântica atual de falha. Falhas inesperadas de
armazenamento continuam como job failed em ambos os modos.

Cada clique explícito de validação gera um validationId novo, incorporado à
identidade da operação; replay da mesma submissão conserva esse ID. Assim, uma
nova validação realmente relê os arquivos, em vez de reutilizar um dry-run antigo
apenas porque o manifest não mudou. Esse campo também é opcional para legados.

Itens válidos contêm variantId, sceneVariantId, ação, hash do arquivo, dimensões,
tamanho e formato. Em caso de erro, retornar somente dados realmente conhecidos:
não fabricar fatos de inspeção parcial. Issues expõem códigos permitidos e
variantId quando conhecido, com mensagens estáticas na UI; não expor exceções,
caminhos absolutos privados ou payloads de provedores.

`manifestSha256` significa sempre hash dos bytes exatos do arquivo, como no
contrato da API atual, não o hash normalizado interno do plano de importação.
O dry-run válido retorna o snapshot de hashes por variante. A UI envia esse
snapshot e o hash do manifest na execução confirmada. O worker relê o manifest,
recalcula o plano e exige correspondência exata de variantes e hashes antes de
executá-lo. A execução mantém as verificações existentes de fatos do arquivo e
hash da cópia: mudança depois da validação ou durante a cópia não é aceita.

Preservar contratos de CLI e clientes existentes: campos de diagnóstico são
aditivos; o snapshot de arquivos é opcional no contrato legado. A UI desta fatia
sempre exige dry-run válido e envia o snapshot completo. Não introduzir aprovação
de imagem, novo formato de batch ou semântica de retry nessa extensão.

## Estado da UI e respostas incertas

Reutilizar cliente HTTP, validação de DTOs, polling, última leitura válida e
barreira de reconciliação D4B.1. Formulários ficam em componentes focados, sem
router/store novos nem crescimento indiscriminado do painel de detalhes.

POST é enviado uma vez, com botão desabilitado durante a submissão; não há retry
automático. Timeout ou resposta perdida significa resultado desconhecido, não
falha comprovada. Reconciliar por GET iniciado depois da submissão encerrar e
permitir inspeção dos jobs/resultados. Se não houver identificação inequívoca,
mostrar a incerteza e exigir ação manual; não reenviar nem declarar sucesso.

Criação de campanha é recuperável pelo ID informado; versões de copy são
identificadas por conteúdo/hash e histórico retornado. Operações usam a identidade
persistida dos jobs e idempotência existente. A API permanece a autoridade para
replay e conflitos, inclusive revisão de imagens; UI não inventa novos retries.

Salvar/validar/importar não inicia o worker automaticamente. Mostrar job queued e
orientar uso dos controles existentes. Resultados só habilitam a etapa seguinte
quando correspondem ao manifest e às associações atuais. Reload permite escolher
o resultado de publicação persistido, mas exige nova validação antes de importar.
Dados malformados ou indisponíveis não habilitam escrita nem substituem a última
leitura válida por estados inventados. Aplicar labels, foco, teclado e mensagens
de erro acessíveis seguindo os componentes atuais.

## Critérios de aceitação e verificação

- Fluxo UI completo: criar campanha com três cenas e copy explicitamente
  aprovada; preparar pasta; copiar três fixtures locais; associar, salvar,
  validar, importar e revisar sem CLI/JSON no fluxo do operador.
- Nova copy preserva histórico, mantém headline fora do áudio e exige ator e
  confirmação; formulários inválidos não produzem escrita.
- Cobertura incompleta, variante desconhecida, arquivo ausente, fonte duplicada,
  imagem não portrait e conflito de aprovação produzem diagnóstico correto.
- Manifest alterado ou imagens alteradas após dry-run bloqueiam execução;
  alteração durante cópia não publica candidato incorreto nem deixa novos
  artefatos órfãos. Originais e candidatos históricos são preservados.
- Travessia, symlinks/junctions e pasta de outra campanha são rejeitados nos
  testes relevantes de Windows/POSIX, sem expandir o escopo para hardening geral.
- Replay de publicação/importação não duplica manifest, candidatos ou efeitos;
  respostas perdidas, GET antigo, double click e reload não provocam auto-POST,
  falsa aprovação ou falsa conclusão. Revisões respeitam transições existentes.
- Nova validação manual relê os arquivos mesmo com manifest idêntico; replay da
  mesma validação não cria outro job. Diagnóstico opt-in preserva erros legados.
- Testes de componentes e integração HTTP cobrem DTOs inválidos e incerteza;
  teste browser com backend real e fixtures cobre o caminho de três imagens.
- Preservar regressões D4B.1 e executar os gates atuais de backend/frontend,
  schemas, lint, typing, build e CI Linux/Windows antes de declarar entrega.

## Handoff

Após revisão desta spec, escrever o plano de implementação e submetê-lo para
revisão/seleção do método de execução. Não implementar nesta etapa documental.
Durante a implementação, atualizar README, PROJECT-MEMORY, GOAL-ROADMAP e PRD
somente conforme capacidades efetivamente verificadas, distinguindo entregue de
planejado. AGENTS.md e arquivos sincronizados em sources permanecem intactos.
