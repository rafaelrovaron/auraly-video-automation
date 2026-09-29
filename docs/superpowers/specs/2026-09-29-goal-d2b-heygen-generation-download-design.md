# Goal D2B — HeyGen Generation, Polling & Download

**Estado:** design para revisão; produção D2B ainda não implementada.
**Data:** 2026-09-29.
**Base entregue:** D2A em `main`, commit `b26d8f8`, `LOCAL_VERIFIED`.

## 1. Resultado e escopo

Uma campanha com três imagens aprovadas e uma Voice Master aprovada produz três MP4s
verticais, um por SceneVariant. A aplicação reutiliza os assets remotos de D2A, registra
cada geração, acompanha seu estado e publica o source local validado. Repetir a operação
retoma o trabalho existente, inclusive depois de restart.

O usuário já definiu MCP/OAuth, uso local/pessoal, importação manual de imagens, WAV
processado compartilhado e pausas aceitáveis entre requests. D2B implementa essas decisões
com polling/backoff limitado. Geração real e consumo de créditos pertencem ao canário D2C,
que exige aprovação específica.

Incluído: planejamento read-only, configuração tipada, budget por quantidade de renders,
jobs por variante, concorrência default 2, submissão, checkpoints, polling, reconciliação,
download, SHA-256, ffprobe e full decode. Interface operacional via CLI.

UI, edição, headlines, captions, música e publicação permanecem nos próximos Goals.
Nenhuma escolha editorial modifica os inputs HeyGen nesta etapa.

## 2. Escolha de execução

**Recomendação: batch local com um job por variante.** Reutilizar JobService e suas leases,
executando no máximo dois jobs HeyGen simultaneamente. Cada falha tem seu próprio estado
e cada vídeo recebe seu próprio checkpoint. Um comando opera a campanha inteira.

Alternativas consideradas:

- Batch remoto único: disponível na ferramenta `create_video_batch`, mas dificulta limitar
  a geração remota a dois itens e exige correlação/paginação adicional. Não necessário no
  primeiro slice pessoal.
- Execução estritamente sequencial: simples, porém deixa de atender à concorrência default 2
  já definida no roadmap.

Não adicionar fila externa, servidor, scheduler permanente ou dependências novas.

## 3. Contrato oficial e limites verificados

O endpoint continua `https://mcp.heygen.com/mcp/v1/`, através do adapter OAuth existente.
Na definição disponível do conector oficial, `create_video_from_image` aceita:

- `image` como `{type: asset_id, asset_id: ...}`;
- `audio_asset_id`;
- `aspect_ratio`, `resolution`, `output_format`, `fit`;
- `motion_prompt` e `expressiveness` opcionais;
- `callback_id` e `title`.

`get_video` consulta um ID. `create_video_batch` e `get_video_batch` existem, mas ficam fora
da execução recomendada. A metadata consultada não expõe engine nem idempotency key para
criação individual. Esses campos não serão inventados.

Antes de geração, um preflight de vídeo lista tools e valida o schema MCP remoto completo:
nome da ferramenta, propriedades, campos obrigatórios, enumerações e composição de schema
relevante. Ele valida especificamente o caminho imagem + áudio de assets. D2A mantém seu
preflight de assets separado para não obrigar operações de upload a suportar geração.

A configuração registra `generation_mode=image`, ferramenta, fingerprint do schema e
`engine_selection=provider_default`. Isso torna explícito que o MCP atual escolhe o engine.
Se o schema remoto exigir engine, ou expuser engine selecionável, o adapter deverá validar
uma seleção explícita antes de submeter. Nenhuma opção inexistente é enviada ou simulada.

Se a conta/tool não aceitar áudio importado ou inputs necessários, preflight falha antes
da ação paga. D2C comprovará interoperabilidade real; fake não estabelece essa evidência.

Referências de consulta:

- Metadata das ferramentas oficiais HeyGen disponíveis no conector: `create_video_from_image`,
  `get_video`, `create_video_batch`, `get_video_batch`, `bulk_video_statuses`.
- [HeyGen Remote MCP](https://www.heygen.com/model-context-protocol).
- [Repositório oficial de skills](https://github.com/heygen-com/skills).

## 4. Inputs, plano e orçamento

`HeyGenVideoConfig` é versionado e fechado, carregado de JSON:

- `generation_mode=image`;
- `engine_selection=provider_default` para a capacidade descrita acima;
- `aspect_ratio=9:16`, `resolution=1080p` (720p permitido), `output_format=mp4`;
- `fit=cover`, `expressiveness=medium`, `motion_prompt` opcional;
- `concurrency=2`, configurável entre 1 e 2 neste MVP;
- `poll_initial_seconds=10`, `poll_max_seconds=60`, `poll_timeout_seconds=1800`.

O plano exige Copy Master, imagem por variante e Voice Master aprovados; assets remotos
`ready` na mesma conta; arquivos locais ainda íntegros; duração positiva do WAV processado.
Cada item contém IDs de campanha/scene/candidata/voz, hashes, IDs de assets e config resolvida.

`heygen plan-videos` mostra variantes, reutilizações, gerações novas, renders reservados,
limite aprovado e segundos totais de áudio. Não informa preço estimado como fato quando
a tarifa/unidade não está disponível. `max_paid_renders` é um inteiro explicitamente
fornecido para a campanha; valor ausente bloqueia submissão. Saldo de conta é informativo,
pois suas unidades podem não equivaler a renders.

Submissão revalida o plano e reserva orçamento junto com criação de registros/jobs em
uma transação `BEGIN IMMEDIATE`. A soma de reservas da campanha não pode exceder o limite.
Reservas ambíguas ou submetidas continuam consumindo o teto. Confirmada ausência de dispatch,
uma reserva pode ser reutilizada pelo mesmo job; falha remota não devolve orçamento
automaticamente. Replay de item existente não faz nova reserva nem nova ação paga.

A configuração e o limite de aprovação ficam registrados em metadata segura. `--yes`
confirma o plano exibido; não ignora budget nem aprovação dos inputs.

## 5. Persistência e identidade

Migration `0008_heygen_renders` cria uma tabela `heygen_renders`:

- UUID local, campaign_id, scene_variant_id, image_candidate_id, voice_master_id;
- provider_account_ref, image/audio hashes e remote_asset_ids;
- config_json, config_sha256, schema_fingerprint, logical_key, job_id;
- status, dispatch_started_at, remote_video_id opcional;
- budget limit aprovado/reserva e approved_by;
- source_path, source_sha256, source_size_bytes e probe_json opcionais;
- error_code/error_message sanitizados e timestamps.

Constraint única para identidade lógica por conta/campanha/variante/inputs/config e para
video ID remoto por conta. Mudanças materiais criam outra identidade e exigem nova reserva.
Headline/editorial não participa da identidade. URLs de download e tokens ficam em memória.

Estados mínimos: `planned`, `submitting`, `processing`, `download_pending`, `ready`,
`failed`, `reconciliation_required`. Job e registro nascem atomicamente pela orquestração
pública de JobService existente; não acessar internals privados do repositório de jobs.

## 6. Submissão, polling e retomada

Antes de paid call: repetir preflight/account match, validar inputs/asset IDs, confirmar
reserva e gravar `submitting` com marcador de dispatch. Enviar callback determinístico
derivado da chave lógica, apenas como correlação. Callback não equivale a idempotência.

Ao receber resposta válida, persistir `remote_video_id` e estado `processing` antes do
primeiro polling. Falha antes do dispatch é retomável. Timeout, resposta malformada ou
interrupção depois do marcador sem video ID resultam em `reconciliation_required`; jamais
repetir create automaticamente.

Com ID conhecido, restart consulta esse ID. Status processing/queued gera espera exponencial
10 → 20 → 40 → 60 segundos, limitada pelo timeout. Timeout local bloqueia somente o job e
preserva o ID. Falha de leitura/rate limit espera conforme resposta sanitizada e orçamento
de tempo; nenhuma espera autoriza nova criação.

`completed` habilita download; `failed` persiste falha daquela variante. Status desconhecido,
ID divergente ou resposta incompleta bloqueiam. Conta diferente bloqueia qualquer operação.

Um runner limitado à campanha e ao tipo `heygen.video.generate` usa até duas threads e leases
existentes. Cada thread abre sua própria sessão DB/provider. A seleção de jobs deve ocorrer
no claim público e atômico, não filtrando depois de claim global; outros jobs não são consumidos.

## 7. Reconciliação explícita

Com video ID persistido: consultar provider, atualizar estado e retomar polling/download.

Sem ID após dispatch ambíguo: procurar correlação exata se o schema de listagem realmente
suportar callback/chave. A lista sem correspondência não prova ausência de ação paga.
Se não houver recuperação inequívoca, manter bloqueado e permitir ao operador informar
`--video-id` recuperado da conta. Validar account ownership e identidade de inputs/callback
quando o provider retornar esses facts; se não retorná-los, exigir confirmação explícita
de vínculo manual e registrar essa provenance. ID já vinculado a outro item é recusado.

Não criar outro vídeo para resolver uma ambiguidade. Reiniciar geração deliberadamente
com outros inputs/config constitui outra requisição aprovada e consome nova reserva.

## 8. Download e QC

Layout proposto:
`campaigns/<campaign>/heygen/<variant>/<logical-key>/source.mp4` e `source.json`.

Resolver root e containment antes de criar arquivos; rejeitar symlinks/junctions que escapem.
Transferir HTTPS em streaming para `.part`, com timeout e limite de tamanho configurados.
Redirecionamento só para HTTPS. Signed URL permanece em memória e não entra em erros públicos.

Validar o `.part`: SHA-256, tamanho positivo, container MP4, H.264, áudio AAC, canvas vertical
correspondente à resolução selecionada, rotação coerente e duração dentro de
`max(2 segundos, 5% da duração do Voice Master)` do WAV. Executar full decode com FFmpeg
em timeout limitado, recusando mídia corrompida mesmo quando ffprobe aceita o header.

Publicar por operação que não sobrescreva final existente. Persistir manifest e source facts
antes de marcar `ready`. Se crash ocorrer entre publicação e commit, validar o artefato local
existente e completar o registro sem novo download. `.part` incompleto pode ser refeito
apenas sob a pasta daquele render. Source final divergente gera conflito, nunca overwrite.

## 9. Interface operacional

Comandos previstos, todos com JSON estável em stdout e progresso/plano em stderr:

```text
heygen plan-videos CAMPAIGN_ID --config FILE --max-paid-renders N
heygen generate-videos CAMPAIGN_ID --config FILE --max-paid-renders N --approved-by NAME [--yes]
heygen run-videos CAMPAIGN_ID
heygen videos CAMPAIGN_ID
heygen reconcile-video RENDER_ID [--video-id ID --confirm-manual-binding]
```

`generate-videos` prepara/reserva jobs, `run-videos` executa jobs da campanha. Repetir comandos
é idempotente localmente. O batch relata cada variante e falhas isoladas; uma falha não
remove nem invalida outros vídeos. Esses serviços serão reutilizados pela UI D4.

## 10. Verificação e critérios de saída

Testes locais usam fake e MP4/WAV sintéticos; não autenticam nem gastam créditos:

- campanha de três variantes percorre plano → jobs → download → QC → ready;
- uma imagem repetida pode compartilhar asset, mas cada variante conserva seu próprio vídeo;
- budget excedido, conta trocada e inputs adulterados bloqueiam antes do dispatch;
- reservas concorrentes e replay não ultrapassam o teto;
- crash antes/depois de dispatch e antes/depois de persistir video ID;
- timeout e restart retomam pelo ID, sem chamar create de novo;
- ambiguidade sem ID continua bloqueada; binding manual errado/duplicado é recusado;
- falha de uma variante preserva as outras, concorrência máxima observada 2;
- download interrompido, URL expirada, MP4 truncado, áudio ausente e duração inválida;
- crash após publicar arquivo recupera sem sobrescrever nem baixar novamente;
- tokens e signed URLs ausentes de DB, outputs e logs.

Executar harness completo no Windows; CI exercita Linux/Windows conforme workflow existente.
Atualizar README, memory, roadmap e PRD após código, testes e revisão independente.
D2B só recebe `IMPLEMENTED`/`LOCAL_VERIFIED` após esse baseline. `PROVIDER_VERIFIED`
continua pendente do canário D2C.
