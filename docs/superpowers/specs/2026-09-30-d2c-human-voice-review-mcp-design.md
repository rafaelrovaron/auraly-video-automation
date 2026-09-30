# D2C — aprovação humana da voz e contrato MCP real

Status: desenho em conversa aprovado em 2026-09-30; esta especificação aguarda
revisão do usuário. Implementação e teste real ainda não executados.

## Resultado e limites

Executar o canário já autorizado com uma cena, a imagem aprovada e o WAV
processado existente, sem modificar nem regenerar a voz. No máximo uma geração
paga, concorrência 1. A aceitação humana do áudio não deve fingir que o ASR
reconheceu todas as palavras, nem remover seus achados.

Este desenho substitui apenas a proibição de revisão humana da divergência
leve de transcrição no desenho de importação externa. Todos os demais gates
continuam ativos. Não modificar AGENTS.md, arquivos de referência sincronizados,
originais de mídia, geração ElevenLabs, UI ou renderer.

## Aprovação humana explícita

Estender o comando e serviço de aprovação existentes com um motivo opcional de
revisão da transcrição, sem criar outro fluxo de aprovação. Persistir um campo
opcional `approval_review_reason` no Voice Master; responsável e data reutilizam
`approved_by` e `approved_at`. Validar o motivo como texto público sanitizado,
não vazio, com limite de 512 caracteres.

Sem esse motivo, manter exatamente o gate atual. Com motivo, permitir somente
voz de origem `imported`, em `review_required`, cuja comparação seja
`review_required` e cujo único achado seja a necessidade de revisão da
transcrição. Comparação `mismatched`, headline falada, outros achados de QC,
metadados incompletos ou artefatos adulterados continuam bloqueados. Não abrir
essa exceção para geração ElevenLabs nesta etapa.

Preservar texto reconhecido, score, status da comparação, lista de achados,
manifesto e hashes originais. A decisão humana fica separada da evidência
automática e aparece na inspeção serializada do Voice Master. A voz aprovada
continua imutável e consumida pelo caminho HeyGen existente.

Alinhar domínio Pydantic, serviço, modelo SQLAlchemy, CHECK e trigger de
aprovação por uma nova migração após `0009_external_voice_import`. Preservar
registros, vínculos e triggers existentes; dados legados recebem motivo nulo.
Downgrade deve recusar perda de decisões de exceção já persistidas. Regenerar
os schemas afetados e testar migração com registros e vínculos existentes.

## Adapter MCP oficial

Usar o schema descoberto na sessão OAuth oficial como contrato de transporte:
`batchId`, `assetId`, `assetIds`/`batchIds` e `videoId`; listas de IDs na consulta
bulk são strings separadas por vírgula. Criação de vídeo usa `audioAssetId`,
`aspectRatio`, `outputFormat`, `callbackId` e `motionPrompt`. Os objetos aninhados
de arquivo e imagem continuam usando os nomes snake_case definidos pelo MCP.

Não enviar `idempotency_key` em ferramentas que não o expõem. A chave lógica
local, reservas e checkpoints permanecem; correlação não será apresentada como
garantia de idempotência do provider. Falha ambígua após criação, inclusive
resposta inválida sem ID utilizável, bloqueia nova criação automática.

Confirmar os envelopes de resposta e campos reais antes de confiar nos parsers;
guardar fixtures sanitizadas dos contratos relevantes, sem dados pessoais,
tokens ou URLs assinadas. Não inventar campos ausentes de expiração, limite de
upload ou identidade. Preservar validação de HTTPS, tamanho, identidade da conta
e schemas. Ajustar apenas os caminhos incompatíveis necessários ao canário.

## Verificação e canário

TDD: aprovação normal preservada; exceção importada auditável; divergência grave,
headline, outros achados e adulteração recusados; CHECK/trigger coerentes com
serviço/domínio; migração preserva dados e vínculos. Exercitar requests reais
camelCase, objetos aninhados e respostas sanitizadas, incluindo ambiguidade sem
reenvio. Executar baseline determinístico completo e revisão independente antes
do teste pago.

Depois registrar a aprovação humana já concedida ao áudio, repetir preflight,
submeter upload da imagem e WAV pela aplicação, persistir IDs, finalizar e
consultar os assets. Reservar e executar um único render pela aplicação, com
concorrência 1; polling e download retomam pelo mesmo ID, nunca nova criação.
Validar MP4 com probe, duração e decode existentes. Não consumir jobs de outras
campanhas. Falhas ambíguas param para reconciliação, sem gasto adicional.

Registrar evidência sanitizada e distinguir `IMPLEMENTED`, `LOCAL_VERIFIED` e
`PROVIDER_VERIFIED`. Só marcar o último após MP4 real baixado e validado; aprovação
visual final do vídeo continua humana. Merge/push desta alteração requer pedido
do usuário, separado da autorização do canário.
