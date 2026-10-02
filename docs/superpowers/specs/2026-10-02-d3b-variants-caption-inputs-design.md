# D3B — Planejamento A/B e entradas de legenda

Status: direção conversacional aprovada em 2026-10-02; esta especificação
aguarda revisão do usuário. D3B ainda não implementado.

## Resultado e escopo

Planejar várias edições de um MP4 HeyGen já disponível, com textos de headline
e overrides editoriais independentes. Uma fonte com três headlines produz três
saídas planejadas, reutilizando imagem, Voice Master e geração HeyGen. Uso local
e pessoal; rapidez e ausência de custo upstream são os critérios principais.

Entregar contratos Pydantic, planejamento determinístico, entradas de legenda,
persistência JSON e CLI. Não executar render, criar Jobs, chamar providers,
modificar aprovações, gerar texto criativo ou alterar mídia. Sem UI, preview,
timeline, ASR/alinhamento novo, dependências ou migrações SQL neste slice.
Não alterar AGENTS.md ou arquivos sincronizados em sources/.

## Escolha mínima

Usar uma lista explícita de variantes por MP4. Cada variante contém chave
editorial estável, label e os `EditOverrides` já existentes. O operador enumera
as combinações desejadas; o contador é o tamanho da lista, sem produto cartesiano
oculto. Uma matriz automática multiplicaria saídas e exigiria regras extras;
fica fora desta etapa.

Reutilizar `EditProfile`, `EditResolveRequest`, resolver e validação de assets do
D3A. Manter `EditManifestV2` e seu schema sem alterações: o plano novo contém
snapshots v2 e uma entrada de legenda separada. Não criar manifest v3 apenas
para planejamento, nem inserir texto/timing em campos de estilo v2.

## Contratos novos, todos v1.0

### EditVariant e EditBatchRequest

`EditVariant`: `key`, `label`, `overrides`. A key segue o identificador seguro do
D3A, é única dentro da entrada e não depende da posição na lista. Label é texto
público não vazio, usado para identificação humana, nunca para montar paths.
Headline varia por `overrides.headline.text`; os demais overrides editoriais
continuam limitados aos campos tipados do D3A.

`EditBatchRequest`: `schemaVersion`, `campaignId`, `renderId`, `videoId`,
`profileRef`, `headlineText`, overrides comuns `campaign`/`video`,
`musicAccepted`, `variants`, `maxOutputs` e referência opcional ao sidecar de
timing (`path` relativo e SHA-256). `renderId` identifica o render HeyGen local
já entregue; `videoId` segue o identificador seguro do D3A. A entrada não aceita
texto livre de captions ou substituição de Copy Master/Voice Master.

Lista não vazia; keys únicas; `maxOutputs` inteiro positivo estrito, default 3,
alinhado ao piloto do PRD. O operador pode aumentar esse limite explicitamente.
Verificar quantidade antes de consultas de mídia e antes de qualquer publicação.
Rejeitar a entrada inteira se exceder o limite, sem truncamento silencioso.
Um comando planeja um MP4; vários MP4 usam chamadas explícitas, não outro motor
de batch nesta etapa.

### CaptionTimingInput

Sidecar JSON tipado com `schemaVersion`, `sourceSha256`, `copyMasterId`,
`copyHash`, `processedAudioSha256`, `timebase="source_mp4"`,
`origin` (`manual` ou `external_alignment`), `acceptedBy` e `cues`.
Aceitação significa que o operador conferiu o timing contra este MP4, não que
o sistema mediu sincronização ou executou alinhamento. Identificação do operador
usa as validações públicas existentes; não aceitar tokens/URLs como identidade.

Cada cue contém `startSec`, `endSec`, `tokenStart`, `tokenEnd`. Texto de cue não
é entrada: deriva de `CopyMaster.spoken_text.split()` no intervalo de tokens
inicial inclusivo/final exclusivo, unido por espaço. Essa regra preserva palavras
e pontuação, sem depender de tokenizer novo ou do texto de uma transcrição ASR.

Cues devem cobrir todos os tokens falados exatamente uma vez, em ordem, sem
lacunas/duplicação. Índices são inteiros estritos; tempos finitos, não negativos,
com início menor que fim, dentro da duração do MP4. Não permitir sobreposição
temporal; pausas entre cues são válidas. Lista de cues não vazia. O sidecar é
validado mesmo se captions estiverem desabilitadas, quando fornecido.

Hashes e identidade devem corresponder ao render, à copy e ao WAV aprovados.
Timing da voz não vira timing do vídeo apenas porque durações parecem iguais.
Sidecars com timebase de áudio, texto alternativo ou vínculos divergentes falham;
a conversão/alinhamento acontece fora deste slice e exige novo sidecar aceito.
A transcrição local atual fornece texto sem timestamps e não serve como timing.

### CaptionInput e EditBatchPlan

`CaptionInput`: referência da copy exata (ID, versão, hash), referência da voz
exata (ID e hash do WAV processado), `text` igual a `spoken_text`,
`timingStatus` (`missing` ou `provided`), referência/hash do sidecar e seus cues
resolvidos com texto quando disponíveis. Copiar também origin, acceptedBy e
timebase para o snapshot, sem depender de reler o sidecar para explicar a origem.
Sem sidecar: referência e metadados de timing nulos e cues vazios; nunca distribuir
palavras artificialmente pelo tempo.

`EditBatchPlan`: schema/versão do planner, campanha/render/vídeo, fonte MP4,
referências upstream verificadas, `maxOutputs`, `outputCount`, `captionInput`,
lista ordenada de outputs e `planHash`. Cada output contém key/label,
`outputVariantId`, manifest v2 completo e seu hash, `outputHash`, filename MP4
planejado e `captionState` (`disabled`, `timing_missing`, `timing_provided`).

Estado de captions vem do enabled resolvido em cada manifest e do timing comum.
`timing_provided` comprova apenas presença e validação estrutural/vínculos com
aceitação humana; não é aprovação visual, prova de sincronização automática ou
estado de render concluído. D5 deverá consumir o plano, validar novamente os
assets e recusar render de captions habilitadas sem timing. O plano não contém
renderer version fictícia, timestamps operacionais ou status de Job.

Headline fica apenas no manifest editorial, nunca no `spoken_text` ou nos cues.
Não remover palavras faladas por coincidirem com palavras da headline.

## Fluxo e fronteiras

O serviço local consulta o SQLite existente em modo somente leitura, sem
construtores que rodem migrações ou inicializem providers/JobService. Verifica
render HeyGen `ready` da campanha, source disponível, Voice Master `approved`
usada naquele render e a Copy Master aprovada exata vinculada à voz. Não usar
`latest` se houver versões mais novas. Confere hashes de imagem/áudio do item
do render com seus registros, hash do MP4 e integridade dos inputs locais
referenciados. Não exige baixar/revalidar a imagem já transformada no MP4.

Ausência de banco, schema necessário, copy/voz aprovada ou render ready é erro
explícito; não criar estruturas automaticamente. Derivar source e referências
dos registros verificados em vez de aceitar alegações livres da requisição.
Paths persistidos permanecem relativos ao project root; converter o path de
source do work root por contenção verificada, sem copiar o MP4. Roots fora do
project root que tornem isso impossível falham com orientação de configuração.

Depois: verificar sidecar opcional, carregar profile exato, construir requests
D3A por variante e resolver cada manifest com `persist=False`. Resolver puro
recebe inputs já verificados; SQL, filesystem e ffprobe ficam no serviço local.
Não acessar helpers privados de outros domínios. Reutilizar o serviço D3A público;
helpers de publicação/leitura dentro de editing podem ser compartilhados no
mesmo domínio sem criar repository abstrato ou framework genérico.

Validar todos os outputs antes de publicar. Uma variante inválida cancela o plano
inteiro e não publica manifests intermediários. Persistir apenas o plano completo,
com manifests embutidos; o D3A continua publicando manifests unitários pelo fluxo
existente. CLI e futuros consumidores não dependem de arquivos duplicados.

## Identidade e replay

Ordenar outputs pela key editorial, não pela ordem da lista enviada. Gerar
`outputVariantId` como `ab-` seguido do SHA-256 canônico de campanha, vídeo,
fonte e key, truncado a 60 caracteres hex para caber no ID seguro do D3A.
Payload exato de identidade: `campaignId`, `videoId`, `sourceId`,
`sourceSha256` e `key`, com os mesmos aliases e algoritmo canônico do D3A.
Verificar ausência de colisões na coleção. A key mantém identidade editorial;
alterar texto/configuração muda o conteúdo e os hashes, não a key.

`outputHash` cobre manifest v2 completo, entrada de legenda e versão do planner,
usando JSON canônico do D3A. Label não muda esse hash. Filename planejado:
`<outputVariantId>-<outputHash>.mp4`. É basename seguro, não path de entrega;
nenhum MP4 é criado. Mudança de timing muda outputHash mesmo sem alterar o v2.

`planHash` cobre todo o plano, incluindo labels, limites, outputs, caption states
e versões, exceto o próprio hash. Sem horário, UUID aleatório ou alias latest.
Reordenar variantes/chaves JSON preserva IDs, filenames e hashes. Mesmo plano
tem replay estável; alterar headline preserva hashes e IDs upstream.

Persistência sob work root:

```text
campaigns/<campaign-id>/editing/plans/<video-id>/<plan-hash>/plan.json
```

Publicação exclusiva e completa, replay verifica conteúdo/hash/identidades e
invariantes, corrupção ou conflito não sobrescreve. Roots/links/junctions e
nomes usam as proteções D3A; nenhum path absoluto, secret ou URL de provider no
plano. Erros são sanitizados e apontam variante/campo conhecido sem ecoar valores
arbitrários. O comando dry-run não cria diretórios, planos, manifests ou estado SQL.

## CLI e schemas

Adicionar `edit plan --request <json> --database <sqlite> [--dry-run]`, usando os
mesmos argumentos de project/work root do D3A. stdout contém o plano JSON e seu
contador; erro resulta em exit não zero sem saída parcial. O serviço oferece
plan/persist/get; `edit plan-get` recebe campanha, vídeo e plan hash. Consulta
valida o snapshot salvo; dry-run de novo revalida as fontes atuais.

Schemas separados para batch request, timing input e batch plan, com exemplos
sintéticos sem mídia privada. Integrar export/drift no harness existente, mantendo
schemas v1 legado e v2 D3A intactos. Não prometer renderability só por passar JSON.

## Critérios de saída e verificação

- Um MP4 e três headlines planejam três outputs distintos; nenhuma chamada a
  provider, Job, reserva de custo ou escrita SQL. Source/WAV permanecem intactos.
- Mesmo input em ordem diferente mantém IDs/hashes/filenames; mudança isolada de
  headline muda só artefatos editoriais, não copy/voz/imagem/HeyGen.
- Limite, keys duplicadas, extra fields, valores inválidos e variante inválida
  falham antes da publicação. False/zero/null/herança seguem o D3A.
- Captions reproduzem somente hook/body/CTA da copy aprovada exata; nunca source
  text, headline ou transcrição usada como substituto de copy.
- Sem timing, captions habilitadas indicam pendência; timing válido tem origem
  e aceitação registradas. Hash/identidade/timebase/cobertura/intervalos inválidos
  são erros, não fallback silencioso para missing.
- Replay/get verificam hash e invariantes; testar corrupção, paths Windows/POSIX,
  links/junctions, não overwrite e dry-run sem mutações.
- TDD por tarefa, checks focados, schemas sem drift, gate completo e revisão
  independente antes de declarar IMPLEMENTED/LOCAL_VERIFIED. Smoke somente
  leitura com o MP4 existente, sem créditos. PROVIDER_VERIFIED não se aplica
  ao planner; teste local não demonstra sincronização ou qualidade do render.

## Próxima aprovação

Revisar esta especificação antes de escrever o plano de implementação. Método
Nativa já escolhido pode ser mantido; execução só após aprovação do plano.
Documentação atual continua distinguindo D3A entregue de D3B planejado.
