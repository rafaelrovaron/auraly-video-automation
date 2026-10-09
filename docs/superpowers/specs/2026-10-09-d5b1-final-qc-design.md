# D5B.1 — QC técnico dos masters existentes

Data: 2026-10-09. Estado: design conversacional aprovado; especificação escrita aguardando revisão do usuário. Não é funcionalidade entregue nem autorização de implementação.

## Objetivo e baseline entregue

Para uso pessoal, verificar rapidamente se cada master existente está tecnicamente apto a seguir para revisão humana. Preservar o fluxo delivery-first: imagens manuais, Voice Master aprovado, HeyGen e renderer existentes, sem regeneração upstream.

O baseline é `30b799ca6c04d9c077b42820516a04617e880292`, com render integrado, histórico de execuções e acesso aos masters por receipt/hash. Os dois jobs do [Actions 37899431421](https://github.com/rafaelrovaron/auraly-video-automation/actions/runs/37899431421), Linux full e Windows focused, terminaram com sucesso. Isso comprova CI, não execução real de providers nem QC final já entregue.

Sucesso neste recorte: um comando analisa variantes selecionadas de um plano salvo, publica relatórios imutáveis por identidade exata e permite consultá-los sem rodar FFmpeg novamente. Nenhum resultado significa aprovação humana.

## Recortes e alternativas

Abordagem escolhida: separar QC, revisão humana e entrega local, reutilizando contratos e ferramentas existentes.

- D5B.1: serviço de QC, contratos, persistência em arquivos e CLI.
- D5B.2: UI de revisão, escuta obrigatória da clareza da voz e aprovação/rejeição com comentário; proxy/contact sheet somente nesse recorte.
- D5B.3: cópia local de masters aprovados com verificação dos hashes de origem e destino.

Construir os três juntos amplia o risco e atrasa a primeira capacidade verificável. Manter tudo manual reduz código agora, mas repete verificações e não oferece evidência vinculada ao arquivo. O recorte escolhido automatiza apenas as verificações determinísticas.

## Escopo e exclusões

Inclui integridade do MP4, conformidade com o contrato do renderer, volume integrado, true peak e medição de encaixe da headline e dos captions.

Não inclui UI, endpoints HTTP, Jobs, worker, novas tabelas/migrações, novos providers, transcrição, OCR, separação de voz/música, correção automática de áudio, encoding de novos MP4 ou alterações em Voice Master/HeyGen/render. Google Flow continua pausado e não-bloqueante. Não adiciona dependências, timeline, preview frame-perfect, agendamento ou framework de políticas configuráveis.

## Entrada e seleção dos masters

`QcRequest` usa `schemaVersion: "1.0"`, `campaignId`, `videoId`, `planHash` e `outputs`, uma lista não vazia limitada ao limite de variantes já adotado pelo batch de edição. Cada item contém `outputVariantId` e `renderKey`; não aceita caminho arbitrário nem overrides de política. IDs de variante devem ser únicos.

O plano salvo é a autoridade para manifest, output hash, filename e inputs declarados. Cada variante solicitada deve pertencer ao plano. O `renderKey` seleciona explicitamente a execução histórica: não se calcula o alvo a partir do runtime atualmente instalado.

Resolver o master pelo layout canônico existente, validar o receipt e comparar campanha, vídeo, variante, manifest/output hash, render key, inputs, mix policy, tamanho e SHA-256 do master. Não confiar no caminho vindo do receipt. Validar o render key contra o runtime registrado no receipt, não contra o runtime do QC.

O `planHash` produtor do receipt pode diferir do plano consumidor quando o renderer reutilizou o output; isso não é motivo de rejeição. A identidade do output e seus inputs devem continuar iguais.

Extrair apenas a validação comum de artefato atualmente existente em `api/render_commands.py` para um helper público no domínio de edição, consumido pela API e pelo QC. Preservar comportamento e testes da API. O QC não depende do módulo HTTP e não chama métodos privados de `RenderService` nem `render()`/dry-run para localizar outputs.

## Verificações

### Integridade e contrato de mídia

Reutilizar `check_master` e `probe_media`, incluindo decode completo:

- container MP4, vídeo H.264/yuv420p e áudio AAC;
- exatamente os dois streams esperados, 1080×1920, 30 fps, rotação zero e faststart;
- duração finita e positiva, compatível com o source conforme a tolerância já definida pelo renderer;
- decode completo sem erro, sem publicar mídia nova.

A duração esperada vem do source probe do plano salvo. Verificar que o probe atual do master é compatível com o receipt. Não flexibilizar o contrato do renderer neste recorte.

Identidade/receipt inválido ou probe divergente do receipt é error. Uma mídia cuja identidade é válida mas viola o contrato técnico ou falha no decode é blocked. Falta do executável, timeout ou falha operacional de análise é error, não diagnóstico de mídia corrompida. A implementação deve distinguir essas causas, sem depender de mensagens cruas de stderr.

### Áudio: política inicial fixa

Analisar o primeiro stream de áudio do master inteiro, com `loudnorm` e saída descartada no sink null. Consumir as medidas de entrada (`input_i`, `input_tp`), nunca as medidas do áudio normalizado de saída. Usar o wrapper existente `invoke_ffmpeg`, captura de stderr, timeout e erros sanitizados; não copiar o subprocess framework de Voice Master nem chamar funções privadas daquele módulo.

O filtro oferece estatísticas JSON e medição de true peak; referência técnica: [FFmpeg loudnorm](https://ffmpeg.org/ffmpeg-filters.html#loudnorm). As unidades do novo relatório são LUFS e dBTP, sem renomear contratos históricos de Voice Master.

Política `final-qc-v1`, versionada e fixa no código:

| Condição | Resultado |
| --- | --- |
| Integrated loudness finito abaixo de −30 LUFS | blocked: `audio_low_loudness` |
| True peak finito maior ou igual a 0 dBTP | blocked: `audio_peak_risk` |
| Integrated loudness e true peak iguais a `-inf` | blocked: `audio_silent` |
| Loudness `-inf` com true peak finito | blocked: `audio_loudness_unmeasurable` |
| JSON inválido, métrica ausente, NaN, `+inf` ou combinação não prevista | error: `audio_measurement_failed` |

Os valores −30 LUFS e 0 dBTP são propostas iniciais internas para este MVP, não requisitos de plataforma nem limites validados pela Auraly. Serão revisados pelo usuário nesta especificação; não criar painel de configuração.

Medidas não finitas previstas são serializadas como `null` com o finding correspondente; JSON nunca contém NaN/Infinity. Não atribuir silêncio total a um áudio curto/baixo apenas por LUFS não mensurável.

True peak alto sinaliza risco, não prova todo clipping histórico. Áudio já distorcido pode passar nessa medida. Um mix com música alta pode ter LUFS suficiente e voz incompreensível: clareza da voz e distorção perceptível continuam exigindo escuta humana em D5B.2. Não estabelecer aprovação automática baseada em loudness.

### Encaixe de texto

Reutilizar a preparação/medição pública de ASS do renderer (`write_ass`) em diretório temporário próprio, com as fontes e timing referenciados pelo plano e hashes validados. Aplicar os mesmos critérios de wrap, shrink, max lines, safe zones e clipping a todos os blocos habilitados de headline/captions.

Texto desabilitado é `not_applicable`, não falha. Incompatibilidade de layout é blocked; asset ausente, hash divergente ou falha de runtime é error. Não copiar a lógica privada de layout nem codificar vídeo novo.

Esse check mede o layout do manifest com o runtime do QC, não reconhece o texto queimado no MP4 nem prova ausência de toda sobreposição visual. O relatório registra essa limitação e o runtime usado. Revisão visual humana permanece necessária.

## Relatórios, identidade e reutilização

`QcReport` com `schemaVersion: "1.0"` registra:

- campanha, vídeo, variante, plano consumidor, manifest/output hash e render key;
- SHA-256 e tamanho do master, SHA-256 dos bytes exatos do receipt;
- versão do QC, política com parâmetros explícitos e seu hash;
- runtime de medição e fingerprint, reutilizando `RenderRuntime`;
- probe, checks (`passed`, `blocked`, `not_applicable`, `skipped`), métricas de áudio e findings com código/campo/mensagem sanitizada;
- resultado global `passed` ou `blocked`, e `humanReviewRequired: true`.

Definir `qcKey` como hash canônico do plano consumidor, variante, render key, master hash, receipt hash, versão do QC, policy hash e runtime fingerprint. Incluir o plano consumidor evita conflito de contexto entre relatórios de planos distintos; não obriga igualdade com o plano produtor do receipt.

Caminho canônico relativo ao work root:

```text
campaigns/{campaignId}/editing/qc/{outputVariantId}/{renderKey}/{qcKey}/report.json
```

Usar validação de containment/symlinks e publicação exclusiva/atômica já existentes, com suporte a long paths no Windows. Não sobrescrever relatório, master ou receipt. Mudança de runtime, política, master ou receipt gera outra identidade; artefato alterado contra o receipt é erro, não autorização para adotar o arquivo.

Rehash do master e receipt antes/depois da análise; revalidar fontes/timing usados na medição antes de publicar. Se algum input mudar durante o trabalho, retornar error sem publicar relatório válido. Sem novo locking distribuído ou hardening para armazenamento adversarial.

Reutilizar relatório existente somente após validar sua estrutura, identidade, checks/status e hashes atuais. Reuso evita decode, loudness e text-fit; não evita leitura/hash de integridade. Relatório corrupto/conflitante retorna error e nunca é sobrescrito silenciosamente.

Persistir apenas resultados completos `passed`/`blocked`. Erros operacionais ou de identidade retornam no batch, sem relatório cacheado, permitindo nova tentativa explícita após reparo. Não armazenar relatório parcial como passed. Falha de uma variante não impede as outras.

Executar integridade antes de áudio/texto. Se ela bloquear, registrar os checks dependentes como `skipped` com motivo, sem tentar analisar mídia inválida; esse é um relatório blocked completo. Nos demais casos executar áudio e texto. Qualquer erro operacional prevalece sobre blocked e impede persistência; sem erros, qualquer check blocked determina blocked. Passed exige todos os checks obrigatórios passed (ou not_applicable para texto desabilitado), nunca skipped.

## Serviço e CLI

Um serviço pequeno no pacote `editing` coordena loader público de artefato, checks existentes e publicação. Contratos seguem `EditingModel`, aliases camelCase, hashes e erros sanitizados. Sem camada de repositório abstrata ou sistema extensível de plugins.

`QcBatchResult` contém o contexto do plano e resultados na ordem solicitada. Cada item tem variante, render key, status `passed`/`blocked`/`error`, `reused`, e qc key/caminho relativo para relatório válido ou diagnóstico para error. `hasFailures` é verdadeiro para blocked/error.

CLI proposta, com as opções de project/work root já adotadas:

```text
edit qc --request request.json
edit qc-get --campaign-id ID --video-id ID --plan-hash HASH \
  --output-variant-id ID --render-key HASH --qc-key HASH
```

`qc` valida toda a admissão (request/plano/seleção) antes de escrever qualquer relatório, analisa variantes sequencialmente e emite o batch JSON. Exit 0 somente se todas passaram; exit 1 para blocked/error ou entrada inválida. Sem retries automáticos ou dry-run nesta etapa.

`qc-get` devolve o relatório exato após validar plano/contexto/identidade e hashes atuais do receipt/master. Não detecta runtime atual, não executa FFmpeg, não recalcula checks e não modifica nada. Um report blocked continua consultável; erro de integridade impede servir evidência antiga como válida.

Em `qc-get`, exit 0 significa consulta válida, inclusive para report blocked; exit 1 significa falha de consulta. O consumidor lê o status do relatório para conhecer o resultado do QC.

Exportar schemas de request/report/batch e verificar drift pelo mecanismo existente. Manter compatibilidade dos schemas e comandos de render atuais. Nenhum campo de aprovação humana é criado.

## Testes e critérios de aceite

Usar fixtures sintéticas e FFmpeg local, sem créditos ou providers. Cobrir:

1. Contratos, seleção explícita, limites e duplicatas; paths não aceitos, JSON finito e fronteiras exatas da política de áudio.
2. Masters válidos e inválidos: streams, codec, resolução, FPS, duração, faststart e decode completo.
3. Áudio normal, baixo, silêncio, pico excessivo e loudness não mensurável; conferir medidas de entrada e impedir auto-normalização/publicação de MP4.
4. Fit de headline/captions, texto desabilitado, fontes/timing alterados e falha do runtime; sem OCR ou teste frame-perfect.
5. Receipt produtor de outro plano com output legitimamente reutilizado; seleção de render histórico independente do runtime atual.
6. Reuso exato sem novas medições; política/runtime diferentes geram novo qc key; report inválido ou master/receipt alterado não retorna passed antigo.
7. Mudança de inputs durante análise impede publicação; error não cacheado permite tentativa explícita após reparo.
8. Batch misto continua por variante; entrada global inválida não escreve artefatos; comandos JSON/exit e consulta sem FFmpeg.
9. Hashes de master, receipt e assets upstream preservados; nenhuma chamada a encode/provider/Job; compatibilidade da API que compartilha o loader.
10. Long paths no Windows e containment/symlinks conforme capacidades e gates de plataforma existentes. Incluir testes novos no job Windows focused e Linux full.

Antes de declarar entregue: testes focados, gate completo existente, schema/type checks e revisão de código. README, PROJECT-MEMORY, GOAL-ROADMAP e PRD deverão distinguir QC entregue de revisão/entrega ainda futuras. CI ou fixtures locais não serão rotuladas como provider-verified.

## Handoff

Esta especificação não muda o produto. Após revisão e aprovação escrita do usuário, elaborar o plano D5B.1; a execução depende da aprovação desse plano e da escolha de método. Merge/push não fazem parte deste pedido de design.
