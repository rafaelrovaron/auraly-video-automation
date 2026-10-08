# D5A.1 — Motor local de render

Status: abordagem aprovada pelo usuário; spec escrita para revisão.
Implementação não iniciada. Base: main em `42beda7`.

## Resultado esperado

Transformar um EditBatchPlan salvo em MP4 finais locais, reaproveitando o vídeo
HeyGen e seus assets. Um vídeo com três headlines deve produzir três masters
distintos, sem gerar novamente imagem, Voice Master ou HeyGen.

Uso pessoal/local, delivery-first. A escolha aprovada é implementar primeiro
o motor FFmpeg/ASS e depois conectar Jobs/API/UI em D5A.2. Não há mudança na
interface nesta etapa. O preview CSS continua aproximado, não uma promessa de
igualdade com o master.

Alternativas consideradas: integrar motor e UI juntos aumenta o tamanho da
entrega; HyperFrames acrescenta um runtime de composição que não é necessário
para este recorte. Reutilizar FFmpeg, pysubs2 e Pillow já instalados evita
dependências novas e um compositor genérico.

## Limites da entrega

Inclui serviço Python tipado, comando CLI, render sequencial das variantes,
headline, captions com timing fornecido, música e framing; validação técnica
do MP4 antes de publicação; recibos imutáveis e reaproveitamento de outputs.

Não inclui novas tabelas/migrations, Jobs, endpoints, alterações React,
timeline, efeitos livres, proxy, alinhamento automático, fontes remotas,
chamadas pagas, aprovação final ou delivery. D5A.2 acrescentará Jobs e UI;
D5B continuará responsável por QC editorial/loudness, review e entrega.
AGENTS.md, sources/, mídia original e decisões de aprovação ficam intactos.

O primeiro master aceita somente 1080 × 1920, 30 FPS, MP4 H.264/AAC e yuv420p.
Outros valores permitidos pelo contrato de edição são rejeitados neste renderer
com erro explícito, não convertidos silenciosamente. Ampliar esse conjunto
é uma extensão posterior, sem mudar os planos já publicados.

## Entrada e fluxo

1. CLI identifica plano por campaignId, videoId e planHash, usando os mesmos
   project-root/work-root existentes. Não aceita um comando FFmpeg do usuário.
2. EditBatchService.get_plan carrega o snapshot e verifica seus hashes e
   invariantes. O renderer usa os manifests e captionInput armazenados, não
   a copy/profile atuais e não recalcula overrides.
3. Revalidar paths, hashes e mídia referenciada antes de executar: source,
   fontes habilitadas, música habilitada e timingRef quando captions habilitadas.
   Reutilizar as validações de edição; expor helpers públicos estreitos quando
   necessário, sem duplicar regras nem acessar internals de Jobs.
4. Preflight global verifica FFmpeg/ffprobe, libass, encoder H.264/AAC e o
   source H.264/AAC com áudio, duração compatível e rotação zero. Source com
   rotação não suportada é rejeitado, não renderizado na orientação errada.
5. Para cada output, validar suporte/fit, verificar recibo existente ou renderizar
   em staging próprio. Falhas de fonte/música/fit/captions pertencem à variante;
   plano/source/runtime inválidos são falhas globais. Probe e full decode
   precedem a publicação.
6. Emitir resumo JSON por variante: rendered, reused ou failed, com identidade,
   paths relativos e erro sanitizado. Falha local de uma variante não descarta
   as demais. Falha global impede o lote; qualquer failed resulta em exit code 1.

Comando previsto: `auraly edit render --campaign-id ... --video-id ...
--plan-hash ...`, com roots existentes e `--dry-run` opcional. Dry-run verifica
entrada, assets e capacidades, informa saídas/reuso/bloqueios, mas não cria
diretórios, recibos, ASS ou MP4. Não promete medir fit rasterizado sem render.

## Headline e captions

Gerar ASS a partir dos estilos efetivos. Texto é literal: escapar braces,
backslashes, quebras e caracteres especiais; não aceitar tags ASS do operador.
styleId é metadado, não um seletor para substituir o estilo resolvido.

- Headline usa seu texto visual-only e intervalo startSec/endSec. Não altera
  áudio e nunca entra no texto falado ou no captionInput.
- Captions usam exclusivamente os cues resolvidos de captionInput e o timebase
  source_mp4. Captions habilitadas com timing_missing falham para aquela variante.
  Captions desabilitadas não exigem timing. Não inventar duração por palavra.
- No renderer 1.0, highlightEnabled=true é rejeitado explicitamente: os cues
  atuais não fornecem timing individual de palavras. Highlight/karaoke fica
  fora deste recorte; não colorir uma frase inteira fingindo destacar palavras.
- Fonte é o TTF/OTF local com SHA do manifest. Sem fallback silencioso para fonte
  do sistema. Identificar a face e garantir sua seleção pelo libass; se isso não
  puder ser confirmado, falhar antes de publicar o master. Arquivos temporários
  de fonte são cópias locais verificadas, nunca alterações no asset original.
- Peso inicial suportado: 400 (regular) e 700 (bold), usando a face fornecida
  e a semântica de bold do ASS. Outros pesos são rejeitados; não afirmar suporte
  a eixos de fonte variável ou ao catálogo completo de pesos CSS.
- Respeitar tamanho, cor/alpha, stroke, sombra com offsets, fundo com padding,
  lineHeight, anchor, x/y, safe zones e maxLines. Posicionamento usa x/y no canvas,
  alinhamento horizontal central e anchor vertical top/center/bottom.
  Layers: vídeo, fundo de texto, captions, headline; não há collision avoidance
  automático entre headline e captions.

Fit é calculado no canvas final, incluindo stroke, sombra e padding. Quebras
explícitas são preservadas. wrap quebra por palavras sem reduzir fonte; error
não introduz quebras; shrink permite wrap e reduz tamanho em passos de 1 px,
até o mínimo de 1 px. Todos falham se o bloco não couber nas safe zones ou
exceder maxLines; nenhuma política trunca texto ou reposiciona silenciosamente.

Usar rasterização local do próprio libass para conferir dimensões dos blocos,
com Pillow para inspecionar seus pixels, em vez de tratar métricas CSS como
medidas finais. Reutilizar medidas iguais dentro da execução. O desenho é de
blocos estáticos por headline/cue, sem animação de texto. LineHeight e padding
devem ter resultados verificáveis; se alguma propriedade não puder ser atendida
pelo caminho ASS, parar e documentar a restrição antes de substituir o motor.

## Framing

Normalizar o vídeo ao canvas 1080 × 1920 e FPS 30. cover preserva proporção e
corta excesso; contain preserva proporção e preenche a área restante em preto.
x/y definem a posição no excesso do crop ou espaço livre do contain, de 0 a 1.
Aplicar scale e zoom sem deformar o vídeo: fator efetivo é scale multiplicado
por zoom interpolado linearmente de zoomStart a zoomEnd ao longo da duração.
Mesmo valor nas pontas produz enquadramento estático. O zoom afeta só o vídeo,
nunca os overlays. Sem keyframes, transições ou zoom no áudio.

## Áudio e música

A referência de voz é o áudio já sincronizado no source MP4; voiceRef registra
a provenance. Não reinserir o WAV nem fazer trim novo, pois isso pode deslocar
o lip-sync. Sem música, preservar o stream AAC via copy quando compatível.

Com música: usar somente asset aceito no manifest, validar trim, repetir o
segmento recortado se loop=true ou completar com silêncio se loop=false.
Aplicar fades no início/final da trilha efetiva e encerrar na duração do source.
Neste recorte, duckUnderVoiceDb é uma atenuação fixa adicional durante todo o
source falado: ganho da música = volumeDb + duckUnderVoiceDb. Não há detector
de fala nem sidechain dinâmico; registrar essa interpretação no recibo.

Misturar com voz em ganho unitário, `amix normalize=0`, e limiter sem auto-gain.
Limiter pode reduzir picos quando necessário; não prometer amostras idênticas
no caminho mixado/AAC. Não fazer loudness normalization da voz. O teste compara
a componente de voz usando sinais sintéticos separados, e testa clipping/picos
do mix. Medição editorial de loudness/true peak permanece em D5B.

Referência técnica: [filtros oficiais do FFmpeg](https://ffmpeg.org/ffmpeg-filters.html),
especialmente ASS, amix, alimiter, atrim, afade e framing. Capacidades da versão
instalada devem ser verificadas, não inferidas da documentação online.

## Identidade, recibos e publicação

Não modificar EditManifestV2/EditBatchPlan nem seus hashes. ManifestHash sozinho
não identifica um render com captions: o outputHash existente também inclui
captionInput. Usar renderKey = hash canônico de outputHash, rendererVersion e
fingerprint do runtime (versões FFmpeg/libass e configuração de encoding).
Mudanças no timing, estilo ou runtime não reutilizam um master incompatível.

Diretório sob work-root:
`campaigns/<campaignId>/editing/renders/<outputVariantId>/<renderKey>/`.
Usar o filename já definido no plano para o MP4, acompanhado de `render.json`.
Recibo Pydantic versionado 1.0 registra planHash, manifestHash, outputHash,
renderKey, rendererVersion, fingerprint, source/assets SHAs usados, política
de mix, path relativo, tamanho, SHA do MP4 e probe. Não guardar secrets,
paths privados absolutos, stderr bruto, comandos fornecidos ou mídia no Git.

Reuso exige recibo válido com identidade exata, tamanho/hash do master e probe
compatíveis. planHash no recibo identifica o plano produtor: não impede reuso
por outro plano validado contendo o mesmo outputHash/renderKey. Não reescrever
esse recibo para mudar sua provenance. Full decode obrigatório em primeira publicação; o recibo registra
essa conclusão. Master corrompido ou recibo ausente/inconsistente é conflito:
não sobrescrever, não adotar arquivo órfão, não apagar mídia automaticamente.
Mensagem indica reparação manual do artefato conflitante antes de tentar de novo.

Staging exclusivo dentro de roots validados. FFmpeg usa argumentos separados,
sem shell, sem stdin, protocolos locais, timeout finito e saída não sobrescrita.
Matar/recolher subprocesso em timeout e limpar somente staging próprio. Publicar
MP4 sem overwrite e recibo por último; revalidar paths e hashes dos inputs antes
de publicação. Interrupção entre MP4 e recibo exige reparação explícita, não
um mecanismo novo de recuperação distribuída. Não anunciar sucesso sem recibo.

Duração final compatível com source: tolerância máxima de um frame mais um
frame AAC (1/30 + 1024/sampleRate segundos). Verificar vídeo H.264, áudio AAC, canvas/FPS, yuv420p, faststart,
streams esperados, tamanho positivo e full decode. Isso é integridade técnica,
não aprovação final de vídeo ou certificação de loudness.

## Verificação e critérios de saída

TDD com fixtures sintéticas curtas, sem providers nem mídia privada:

1. Um source + três headlines gera três MP4 e recibos distintos. Trocar somente
   headline muda o output sem chamadas de imagem/Voice/HeyGen.
2. Headline e cues aparecem/desaparecem nos intervalos corretos, dentro da
   tolerância de um frame/precisão ASS; captions nunca incluem headline.
3. Cobrir font selection, texto literal, cores/alpha, stroke/sombra/fundo,
   safe zones, anchors, lineHeight e fit wrap/shrink/error; extrair frames para
   inspeção visual e verificar bounds. Sem golden MP4 idêntico entre OS.
4. Captions sem timing, highlight e pesos não suportados falham explicitamente;
   variante sem captions continua renderizável no mesmo lote.
5. Cobrir cover/contain, posição, scale e zoom nas duas pontas; música loop/trim,
   fades, atenuação e ganho da voz. Sem música, comprovar audio stream copy.
6. Reexecução reutiliza o mesmo master íntegro sem encode; novo timing/runtime
   produz nova identidade. Conflitos/corrupção não sobrescrevem artefatos.
7. Paths POSIX/Windows, symlink/junction, inputs alterados, texto com sintaxe ASS,
   subprocess failure/timeout e uma variante falha preservam inputs e irmãos.
8. Masters passam probe/full decode; hashes de todos os inputs permanecem iguais.
   CLI dry-run não escreve e comunica limites e bloqueios.

Rodar o gate determinístico completo existente e revisão independente antes
de marcar D5A.1 LOCAL_VERIFIED. Verificar execução real do FFmpeg tanto Linux
quanto Windows com fixtures sintéticas; registrar exatamente qualquer skip.
Não marcar D5A inteiro entregue: Jobs/UI D5A.2 continuam planejados. D5A.1 não
estabelece PROVIDER_VERIFIED nem aprovação humana dos masters.

## Próxima aprovação

Esta spec aguarda revisão do usuário, incluindo os limites explícitos de
highlight, pesos de fonte, master fixo e ducking estático. Após aprovação,
escrever o plano de implementação em tarefas pequenas; execução só começa
depois da revisão/aprovação do plano e escolha do método de execução.
