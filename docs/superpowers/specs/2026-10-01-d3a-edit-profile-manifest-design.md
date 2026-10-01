# D3A — EditProfile, EditManifest e resolução de overrides

Status: desenho e especificação aprovados pelo usuário em 2026-10-01.
Não há implementação D3A nem plano aprovado.

## Resultado pretendido

Preparar configurações de edição para o MVP local de uso pessoal, separando
estilo reutilizável de texto e assets de cada vídeo. Resolver e persistir um
manifest determinístico usando um MP4 já disponível, sem editar o vídeo,
regenerar voz/imagem ou chamar HeyGen. Rapidez e simplicidade são prioridades;
validação de entradas e preservação dos arquivos continuam obrigatórias.

Este slice entrega contratos, resolução, persistência local, schemas e exemplos.
Não entrega UI, preview, renderer, timeline, keyframes, planejamento em lote de
A/B, alinhamento de legendas, novas tabelas SQL ou chamadas pagas. Esses fluxos
continuam nos Goals seguintes do roadmap delivery-first.

## Decisão e alternativas

Criar contratos de edição novos ao lado do `EditManifest` legado v1.0.
Estender v1.0 no lugar misturaria seus eventos de timeline com os novos estilos
e alteraria consumidores existentes. Persistir cada entidade em SQL exigiria
migrações sem necessidade de consulta demonstrada neste slice. A solução
escolhida é Pydantic existente, resolver puro e arquivos JSON versionados.

Reutilizar contratos, configuração de roots, validações e padrões de publicação
existentes quando compatíveis. Não criar framework de configuração, repository
abstrato, engine de merge genérica ou dependência nova. Não acessar helpers
privados de outros domínios por conveniência.

## Contratos e fronteiras

### EditProfile v1.0

Contém `schemaVersion`, identificador estável, nome, versão inteira positiva,
data de criação e defaults tipados de output, headline, captions, music e
framing. Versões publicadas são imutáveis. Alterar um profile cria uma nova
versão explícita; resolver sempre exige a versão, nunca um alias `latest`.

O profile não contém texto de headline específico de campanha, texto/timing de
legendas, identidade de campanha/vídeo/variante ou caminho do MP4. Referências
a fontes e música reutilizáveis são permitidas; não há fonte presumidamente
instalada nem download automático de assets.

### Entrada de resolução

Uma entrada tipada reúne profile e versão, campanha, vídeo e variante de saída,
descritor do MP4 local com path relativo, SHA-256, duração e identidade de
origem, headline base e três camadas opcionais de overrides: campanha, vídeo
e variante. Referências disponíveis a Copy Master e Voice Master carregam
identidades/hashes existentes, sem reabrir ou alterar suas aprovações.

A headline base é entrada editorial explícita, não gerada pelo resolver nem
extraída do áudio. D3A não constrói payload de voz ou job de provider.
Dados de timing de captions não são inventados: este slice resolve seu estilo;
texto, timing e proveniência de alinhamento pertencem ao D3B.

### EditManifest v2.0

Snapshot completo e validado: schema e versão do resolver, identidades de
campanha/vídeo/variante, descritores dos inputs e hashes, referência e hash do
profile exato, output, headline final, estilo de captions, music e framing,
camadas de overrides fornecidas e proveniência por campo resolvido.

O snapshot não se apresenta como render, entrega ou aprovação visual. Não
contém renderer version fictícia: a versão efetivamente usada será evidência
do render no D5A. Também não contém URLs de provider, credenciais ou paths
absolutos privados.

## Campos configuráveis

Os schemas aceitam somente os campos enumerados; extras são erros.

- Output: dimensões positivas, FPS finito positivo, MP4/H.264.
- Headline: enabled, texto na entrada/overrides, identificação do estilo,
  fonte local, peso, tamanho, line-height, cor, stroke, shadow, background,
  anchor, posição, safe zone, máximo de linhas, política de fit e início/fim.
  Continua visual-only; nenhuma opção permite torná-la falada.
- Captions: enabled, identificação do estilo, fonte, peso/tamanho, cor,
  anchor/posição/safe zone, máximo de linhas e highlight. Sem texto livre ou
  eventos de timeline neste contrato D3A.
- Music: enabled, asset local opcional, volume, referência de mix da voz,
  loop/trim e fades. Desabilitada por padrão; nenhum mix é executado aqui.
- Framing: fit/crop, escala e posição; zoom sutil com valor inicial/final
  limitado, sem lista de keyframes.

Posições e safe zones são frações normalizadas do canvas; tamanhos de texto
são pixels do canvas de output. Cores usam hexadecimal RGB/RGBA. Números
não finitos são proibidos. Enums, limites e relações entre campos aparecem
nos modelos e JSON Schemas, não em lógica permissiva de merge.

Intervalos de headline usam segundos, início inclusivo e fim exclusivo,
dentro da duração do MP4, com fim maior que início. Um fim omitido significa
a duração da fonte na resolução. Desabilitar headline/captions/music usa
`enabled=false`; não exige apagar valores herdados. Fontes são obrigatórias
para texto habilitado, e asset é obrigatório para música habilitada.

## Resolução e proveniência

Precedência fixa: `profile < campaign < video < outputVariant`.
A headline base é o valor inicial de texto, com origem `input`; os demais
defaults têm origem `profile`. Cada campo explícito de uma camada substitui
o campo correspondente, sem apagar irmãos da seção.

Campo omitido herda. Zero e false são valores explícitos, não ausência.
Null explícito só é aceito em campos declarados nullable, como referência de
music asset; nos demais é erro. Não há merge recursivo de dicionários livres,
concatenação de listas ou aceitação silenciosa de campo desconhecido.

Proveniência registra o caminho do campo e a camada que o forneceu; valores
derivados, como fim da headline, têm origem `source`. Mesmo quando um override
repete o valor anterior, sua origem explícita fica registrada. Depois de aplicar
as camadas, validar novamente o objeto completo e suas relações. Erros exibem
a camada e o caminho exato do campo, sem vazar dados sensíveis.

O resolver recebe inputs já verificados e não lê arquivos, banco, relógio,
variáveis de ambiente ou rede. Configuração e verificação de assets ficam
no serviço local, fora da função pura.

## Determinismo e hash

Usar SHA-256 do JSON canônico UTF-8: aliases públicos, chaves ordenadas,
separadores compactos, números finitos e defaults resolvidos explícitos.
O hash cobre todo o snapshot sem o próprio campo de hash. Inclui identidade
de saída, configurações, inputs, profile hash, overrides e proveniência;
portanto, é hash de snapshot, não apenas deduplicação de pixels.

Datas de criação/publicação ficam fora do snapshot resolvido e do seu hash.
O hash do conteúdo do profile exclui sua data de criação, mas inclui sua
identidade, nome, versão e defaults. Reordenar chaves de uma entrada equivalente
não altera a saída. Mesma entrada tipada produz mesmo snapshot/hash.
Uma alteração de headline muda o manifest, não os hashes dos inputs upstream.

## Arquivos, assets e persistência

Paths de mídia são relativos ao project root configurado, com regras POSIX
e Windows: sem absoluto, drive, NUL ou traversal. O serviço verifica existência,
arquivo regular, contenção canônica e symlinks/junctions antes de ler/publicar;
verifica hashes de inputs e assets. Não altera nem copia o MP4 para resolvê-lo.
Fontes locais TTF/OTF e música local devem existir e corresponder ao tipo
esperado; validação de disponibilidade não promete que o layout já foi renderizado.
Música habilitada exige confirmação explícita de uso pelo operador na entrada
do serviço; isso não inventa um catálogo de licenças ou aprovação automática.

Persistir sob o work root configurado:

```text
editing/profiles/<profile-id>/<version>/profile.json
campaigns/<campaign-id>/editing/<video-id>/<output-variant-id>/<manifest-hash>/manifest.json
```

IDs usados como segmentos são validados sem normalização silenciosa que possa
colidir. O serviço oferece create/list/get de profiles, nova versão e
resolve/persist/get de manifests; CLI fina torna o fluxo testável antes da API.
Não há watcher, worker, Job, edição in-place ou índice SQL neste slice.

Publicação é exclusiva e completa, seguindo os padrões existentes. Repetição
do mesmo conteúdo reutiliza o arquivo íntegro existente. Mesmo identificador
e versão com conteúdo diferente, arquivo adulterado ou destino inseguro causam
erro; nunca sobrescrever silenciosamente. Listagem é ordenada e valida os
documentos encontrados em vez de omitir corrupção sem aviso.

## Compatibilidade explícita

Manter `models.EditManifest`, ingest, exemplos legados e
`schemas/edit.schema.json` no contrato v1.0 atual. O novo domínio tem nome/módulo
distinto e schema v2 separado; dispatch por versão é explícito.

Não converter automaticamente v1 em v2: cuts, punch-ins e b-roll do legado
não têm representação neste slice e não podem desaparecer silenciosamente.
Entradas v1 continuam no consumidor legado; no novo resolver são recusadas
com indicação clara da versão suportada. Preservar testes de ingest e schema
legados. A coexistência é a estratégia de compatibilidade D3A, não uma migração
de mídia nem uma promessa de suporte de timeline pelo renderer futuro.

## Verificação e critérios de saída

TDD cobre contratos e erros com campo/camada, precedência completa, herança
parcial, false/zero/null, intervalos, proveniência e determinismo por replay e
ordem de chaves. Variação só de headline conserva identidade/hashes upstream.
Cobrir paths Windows/POSIX, escapes por links, assets ausentes/adulterados,
publicação sem overwrite e recuperação do mesmo snapshot.

Gerar schemas separados para profile, entrada/overrides e manifest v2, com
exemplos sintéticos sem mídia privada. Integrar export/drift no harness
existente e manter schema legado intacto. Não criar suites/frameworks novos.
Executar checks focados por tarefa e o gate determinístico completo antes de
declarar `LOCAL_VERIFIED`, com revisão independente conforme AGENTS.md.

Ao final da implementação, um profile versionado e uma entrada local válida
produzem um manifest persistido e hash verificável, com todas as origens
explicáveis, sem modificar inputs ou criar chamadas pagas. Validação com o
MP4 disponível é somente leitura e não equivale à aprovação visual final.

## Próxima aprovação

Após revisão desta especificação pelo usuário, escrever o plano com tarefas
pequenas, comandos de verificação e commits coerentes. A implementação só
começa depois da aprovação do plano e escolha do método de execução.
Não modificar AGENTS.md ou arquivos sincronizados em sources/.
