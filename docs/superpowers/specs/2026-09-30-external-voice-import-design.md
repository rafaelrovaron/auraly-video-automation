# Voice Master externo — desenho para revisão

Status: proposta; implementação e canário ainda não executados.

## Objetivo e escopo

Desbloquear o D2C com uma imagem e uma narração externa escolhidas pelo operador,
sem regenerar a voz e sem consumir créditos durante a importação. O áudio deve
entrar no mesmo fluxo de revisão, aprovação e reutilização de Voice Master que o
HeyGen já consome. A geração automatizada ElevenLabs permanece inalterada.

O desenho inicialmente pequeno precisa de revisão de contratos e migração: hoje
o domínio e o SQLite restringem `provider` a `elevenlabs`. Não simular uma geração
ElevenLabs para cadastrar arquivos externos.

## Entrada e processamento

- Novo comando `voice import CAMPAIGN_ID --source FILE`, com Copy Master aprovado
  da campanha como texto esperado. Aceitar inicialmente MP3 e WAV, com limite de
  100 MiB, arquivo regular não vazio e validação de formato por conteúdo.
- A origem deve estar dentro do project root confiável configurado. Validar
  caminhos canônicos, componentes symlink/junction/reparse e contenção antes de
  copiar. Nunca registrar caminhos absolutos privados em contratos ou logs.
- Preservar o original. Copiar exclusivamente para um diretório novo do Voice
  Master, registrar o hash e confirmar que os bytes lidos correspondem ao hash.
- Decodificar integralmente a cópia para WAV intermediário local, com FFmpeg
  falhando diante de erro de decodificação. Depois reutilizar
  `process_voice_audio`: WAV mono 48 kHz, normalização e trim apenas nas bordas,
  preservando pausas internas. Não enfraquecer o gate MP3 da geração ElevenLabs.
- Motivo do intermediário: o MP3 curto selecionado decodifica, mas não passa pelo
  gate atual de metadados de frames MP3. O fato de decodificar não prova a
  completude semântica da fala; comparação da transcrição e revisão continuam
  necessárias. Não classificar o arquivo como corrompido apenas por esse gate.

## Origem, persistência e repetição

- Adicionar origem `imported` ao contrato Voice Master e ao banco. Manter a
  versão e o histórico de contratos explícitos, com migração compatível com os
  registros existentes e regeneração dos schemas afetados.
- Usar identificadores documentados de importação nos campos de voz/modelo que
  hoje são obrigatórios, sem atribuir uma voz ou modelo ElevenLabs não conhecido.
  Registrar o estado local `local_ready`, distinto de resposta de provider.
- Job `voice.import` local, sem autorização de cobrança nem chamada ElevenLabs;
  registrar submissão, processamento e resultado no mecanismo de Jobs existente.
- Identidade lógica por campanha, versão de Copy Master, hash da origem e versão
  do processamento. Repetição idêntica retorna a importação existente, não cria
  novo Voice Master. Revalidar os artefatos para não reutilizar dados adulterados.
- Preservar exclusividade do Voice Master ativo/aprovado por campanha. Não
  substituir automaticamente um Voice Master existente. Falhas ficam explícitas,
  com artefatos de diagnóstico preservados; nenhuma aprovação parcial.
- A migração deve preservar os triggers de aprovação, imutabilidade, histórico
  e vínculo com Copy Master/Job; permitir o novo Job apenas para origem importada.
  Migração de retorno deve recusar perda de registros importados.

## Transcrição e aprovação

- Reutilizar o transcritor local já utilizado no pipeline e comparar o texto
  reconhecido com o Copy Master aprovado, incluindo a regra de headline visual
  não falada. Não usar o próprio texto esperado como transcrição reconhecida.
- Publicar manifesto e inspeção com origem, hashes, formato original, métricas,
  texto reconhecido, resultado da comparação e versão do processamento.
- A importação termina em `review_required`, nunca em `approved`. A aprovação
  humana continua separada e só é permitida com QC compatível, hashes íntegros
  e metadados completos. Divergência exige correção ou nova entrada, não override.
- Se o runtime de transcrição estiver indisponível, falhar de forma sanitizada;
  não contornar Application Control nem substituir transcrição por texto manual
  nesta etapa. O ambiente Windows já apresentou bloqueio da DLL Whisper; validar
  essa dependência antes de prometer que o canário está liberado.

## Integração e limites

O HeyGen continua recebendo apenas o WAV processado de um Voice Master aprovado.
Uploads e geração mantêm seus gates atuais; não ampliar o batch de vídeos para
mais de uma geração paga no primeiro canário. Criar uma campanha de uma única
SceneVariant para o teste, pois o serviço atual planeja todas as cenas da campanha.

Não adicionar UI, importação batch de áudio, novos serviços de transcrição,
alterações no renderer, nem recuperação genérica. Não modificar AGENTS.md.

## Verificação exigida

TDD para importação sem chamadas pagas, origem verdadeira, saída WAV 48 kHz mono,
preservação do original, repetição idempotente, bloqueio de caminhos inseguros,
arquivos inválidos, conflitos, falha do transcritor, divergência e approval gate.
Exercitar migração com registros ElevenLabs existentes e verificar os triggers.
Validar uso do áudio importado pelo serviço HeyGen com provider determinístico,
sem classificar esse resultado como verificação real do provider.

Antes de concluir a implementação: baseline determinístico completo, revisão do
diff e evidência registrada. Só depois preparar os assets reais, obter aprovação
de copy/imagem/voz e conectar OAuth para o canário D2C autorizado.
