# Goal D2A — HeyGen Contract, Preflight & Asset Reuse Design

## Status e boundary

Esta especificação define o futuro `Goal D2A — HeyGen Contract, Preflight & Asset Reuse`. Ela não
implementa o Goal. D2A conecta a aplicação local ao Remote MCP oficial do HeyGen por OAuth, valida
as capacidades necessárias e prepara imagens e Voice Master como assets remotos reutilizáveis.

D2A inclui:

- conexão OAuth iniciada localmente e reutilizável;
- preflight sanitizado da conta e das ferramentas MCP necessárias;
- boundary pequeno e substituível por fake;
- persistência de assets remotos por conta, tipo e hash;
- upload batch das imagens selecionadas e da Voice Master aprovada;
- jobs retomáveis `heygen.asset.upload`;
- deduplicação, reconciliação e CLI operacional.

D2A não inclui criação de vídeo, polling ou download de MP4, UI FastAPI/React, webhooks, múltiplos
providers, automação do website HeyGen ou canário real pago. Essas capacidades permanecem em D2B,
D2C e D4.

## Decisão de integração

A integração usará o Remote MCP oficial do HeyGen em:

```text
https://mcp.heygen.com/mcp/v1/
```

A autenticação será OAuth vinculada à conta Web do usuário, sem API key. A aplicação usará o SDK
Python oficial do MCP para discovery, PKCE, troca e refresh de tokens e transporte Streamable HTTP.
Não será implementado um cliente MCP/OAuth próprio.

O login pertence à aplicação local, não à sessão do Codex ou ChatGPT. Isso permite que CLI, worker
e futura UI utilizem a mesma conexão sem depender desta conversa.

Referências oficiais verificadas em 2026-09-29:

- [HeyGen Remote MCP](https://developers.heygen.com/mcp/overview);
- [MCP Python SDK — OAuth clients](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/client/oauth-clients.md);
- [MCP Python SDK — HTTP transports](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/client/transports.md).

## Arquitetura

```text
CLI agora / botão na UI depois
          │
          ▼
HeyGenService
          │
          ├── OAuthConnection
          │     ├── abre navegador
          │     ├── recebe callback loopback
          │     └── salva sessão no cofre do sistema
          │
          ├── HeyGenMcpAdapter
          │     └── SDK MCP oficial → HeyGen remoto
          │
          └── RemoteAssetRepository
                └── SQLite: hash, tipo, remote ID e status
```

`HeyGenService` concentra regras de campanha, deduplicação e planejamento. `HeyGenMcpAdapter`
conhece nomes e schemas das ferramentas externas. O repositório conhece apenas estado persistente.
CLI, worker e futura API chamam o mesmo application service.

Não haverá factory de providers, plugin genérico ou abstração para providers especulativos. O
protocol existe somente porque o fake determinístico é necessário para testar ações externas.

## OAuth local

O comando inicial será:

```powershell
auraly heygen connect
```

O fluxo:

1. inicia um callback HTTP temporário somente em `127.0.0.1` e porta livre;
2. pede ao SDK MCP a URL de autorização;
3. abre essa URL no navegador padrão;
4. recebe `code`, `state` e `iss` no callback;
5. deixa o SDK validar PKCE, `state`, issuer e trocar o código;
6. persiste tokens e client registration no cofre do sistema operacional;
7. encerra o callback local.

Tokens, refresh tokens, client secrets e authorization URLs nunca entram no SQLite ou nos logs. O
storage OAuth será injetável: cofre nativo em produção e memória nos testes. Se o cofre não estiver
disponível, a conexão falha com orientação clara; não haverá fallback silencioso para texto puro.

`auraly heygen disconnect` apaga as credenciais locais. Revogação remota só será executada se o
servidor expuser uma operação oficial suportada; apagar localmente não será apresentado como
revogação da autorização no HeyGen.

## Preflight

```powershell
auraly heygen status
auraly heygen preflight
```

`status` lê apenas estado local seguro. `preflight` abre uma sessão MCP e valida:

- OAuth utilizável, incluindo refresh quando necessário;
- identidade estável da conta ou workspace autenticado;
- saldo/créditos, apenas como informação;
- disponibilidade e schemas mínimos das ferramentas requeridas;
- suporte a asset batch de até o volume solicitado;
- acesso de leitura e escrita a assets.

Ferramentas mínimas esperadas em D2A:

- `get_current_user`;
- `create_asset_upload_batch`;
- `complete_asset_batch`;
- `get_asset_batch`;
- `bulk_asset_statuses`;
- `get_asset`.

O resultado público contém estado, account reference sanitizada, capabilities, limites relevantes e
ações recomendadas. Tokens, headers, URLs assinadas e payloads brutos são descartados.

## Contrato do provider

O protocol interno expõe apenas operações usadas pelo domínio:

```text
preflight()
allocate_asset_batch(items, idempotency_key)
upload_file(slot, local_path)
complete_asset_batch(batch_id)
get_asset_batch(batch_id)
get_assets(asset_ids)
```

O adapter converte essas operações em tool calls MCP. O upload dos bytes usa o slot e os headers
temporários retornados pelo HeyGen e envia o arquivo diretamente ao storage indicado. URLs e
headers permanecem somente em memória.

O fake implementa o mesmo protocol e controla deterministicamente sucesso, rejeição, timeout e
resultado ambíguo. Ele não reproduz JSON-RPC nem OAuth; os testes validam a lógica Auraly.

## Modelo persistente

D2A adiciona uma tabela `remote_assets` com o mínimo necessário:

- `id` UUID local;
- `provider`, fixado em `heygen`;
- `provider_account_ref`, identificador estável e sanitizado da conta/workspace;
- `kind`, `image` ou `audio`;
- `sha256` do source local;
- `mime_type` e `size_bytes`;
- `remote_asset_id`;
- `remote_batch_id`;
- `status`;
- `last_error_code` e mensagem pública sanitizada;
- `created_at` e `updated_at`.

A restrição única é:

```text
provider + provider_account_ref + kind + sha256
```

Estados persistidos:

```text
allocated → processing → ready
                    └──→ failed
allocated/processing → reconciliation_required
```

Não será criada uma tabela de bindings. D2B resolve o asset de uma imagem ou Voice Master usando o
hash persistido no próprio domínio. Isso permite reuso entre campanhas sem duplicar associação.

`provider_account_ref` não será e-mail. O adapter usará um identificador opaco estável retornado
pelo HeyGen ou, se necessário, um fingerprint SHA-256 determinístico desse identificador.

## Seleção e validação dos inputs

Para cada campanha, o serviço exige:

- uma Voice Master `approved`;
- `processed_audio_path` e `processed_sha256` presentes;
- uma imagem selecionada/aprovada para cada SceneVariant;
- path e SHA-256 persistidos para cada imagem.

Antes do planejamento e novamente antes do upload, o serviço verifica existência, tamanho, tipo e
hash do arquivo. Divergência após aprovação bloqueia o item; nenhum source é alterado, movido ou
normalizado por D2A.

Uma campanha com três variantes produz no máximo quatro assets lógicos: três imagens e um WAV. A
Voice Master aparece uma única vez no plano e é reutilizada por todas as variantes.

## Planejamento e execução batch

```powershell
auraly heygen prepare-assets CAMPAIGN_ID
```

Antes de mutar estado remoto, o comando mostra:

- conta conectada;
- assets locais encontrados;
- assets remotos reutilizados;
- arquivos ausentes que serão enviados;
- total do batch.

Sem ausentes, o comando termina sem job. Com ausentes, pede confirmação explícita e enfileira um
job `heygen.asset.upload` com até 100 arquivos. O input do job contém somente referências locais
seguras, tipos, tamanhos e hashes; nunca contém credenciais ou URLs temporárias.

O handler:

1. repete o preflight;
2. revalida os arquivos;
3. consulta `remote_assets` e remove do plano os hashes já `ready`;
4. aloca o batch remoto usando uma idempotency key determinística;
5. persiste batch ID e asset IDs em uma transação antes de enviar bytes;
6. envia cada arquivo ao slot correspondente;
7. finaliza o batch;
8. acompanha o batch até cada item ficar `ready` ou `failed`;
9. preserva sucessos mesmo quando outro item falha.

A idempotency key deriva da account reference e da lista canônica ordenada de `kind:sha256`. Replay
da mesma intenção reutiliza o batch retornado pelo HeyGen.

## Idempotência, retry e reconciliação

- hash `ready` nunca é reenviado;
- reexecução idêntica não cria linha ou arquivo remoto lógico duplicado;
- falha anterior ao dispatch é retryable;
- rejeição conhecida de arquivo é terminal para aquele item;
- timeout após dispatch é ambíguo e exige reconciliação;
- criação ambígua repete somente a mesma idempotency key;
- `complete_asset_batch` pode ser repetido para o mesmo batch;
- restart consulta batch e asset IDs persistidos antes de qualquer nova criação;
- sucesso parcial permanece `ready` e não volta ao lote posterior.

Se um slot temporário expirar antes do envio e o HeyGen não oferecer renovação segura, o item fica
`reconciliation_required`. D2A não cria outro asset cegamente nem tenta apagar assets remotos. Uma
correção futura só será adicionada após observar o comportamento real no canário D2C.

## Mapeamento de falhas

As falhas seguem as categorias já usadas pelo projeto:

- `configuration`: conexão ausente, OAuth inválido ou capability obrigatória ausente;
- `retryable`: conexão falhou comprovadamente antes do dispatch;
- `terminal`: input rejeitado ou resposta incompatível conhecida;
- `ambiguous`: outcome remoto desconhecido depois do dispatch.

No job:

- configuração/autenticação resulta em `blocked` com ação recomendada;
- retryable usa a política existente de retry;
- terminal falha o item sem apagar progresso dos demais;
- ambiguous usa `RECONCILE_BEFORE_RETRY`.

Mensagens persistidas são curtas, sanitizadas e não incluem corpo bruto do provider.

## CLI e futura UI

Comandos D2A:

```text
auraly heygen connect
auraly heygen disconnect
auraly heygen status
auraly heygen preflight
auraly heygen prepare-assets CAMPAIGN_ID
```

Os comandos mantêm o contrato atual de um único documento JSON em stdout. Interação de navegador
e confirmação humana usam stderr/TTY sem contaminar o JSON final.

No D4, o botão “Conectar HeyGen” e as telas de assets chamarão os mesmos serviços. D2A não antecipa
rotas FastAPI nem componentes React.

## Verificação

Testes focados devem cobrir:

- migration nova e upgrade de banco existente;
- armazenamento OAuth injetável e sanitização;
- callback loopback restrito, `state` e issuer delegados ao SDK;
- preflight completo e capability ausente;
- três imagens mais um WAV resultando em quatro assets lógicos;
- uma Voice Master compartilhada entre variantes;
- deduplicação por conta, tipo e hash;
- replay sem novo upload;
- persistência dos remote IDs antes do upload;
- restart e reconciliação pelos IDs persistidos;
- sucesso parcial preservado;
- fake em sucesso, falha, timeout e ambiguidade;
- ausência de tokens e URLs temporárias em banco, logs e outputs;
- CLI JSON e job handler.

Verificação final:

```powershell
uv run python scripts/verify.py fast --pytest tests/test_heygen_domain.py tests/test_heygen_service.py tests/test_heygen_cli.py
uv run python scripts/verify.py full
```

CI não autentica no HeyGen nem executa ação remota. A confirmação real de OAuth, schemas e upload
fica no canário D2C; até lá, D2A pode ser `IMPLEMENTED` e `LOCAL_VERIFIED`, mas não
`PROVIDER_VERIFIED`.

## Critérios de saída

D2A está pronto quando:

- a aplicação conecta por OAuth sem API key e pode apagar a sessão local;
- preflight relata conta e capabilities sem vazar dados sensíveis;
- três imagens selecionadas e um WAV aprovado produzem quatro `RemoteAsset`s lógicos;
- todas as variantes apontam por hash para a mesma Voice Master remota;
- replay pelo mesmo hash reutiliza o asset na mesma conta;
- IDs remotos são persistidos antes do avanço do upload;
- timeout ambíguo bloqueia criação cega e entra em reconciliação;
- fake cobre sucesso, falha, timeout e ambiguidade;
- verificações fast e full passam em Windows e Linux.
