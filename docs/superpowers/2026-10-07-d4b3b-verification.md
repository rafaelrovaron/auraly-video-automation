# D4B.3b — evidência de entrega local

Data: 2026-10-07. Branch `feat/d4b3b-editing`, base `12823be`.
Design/plano aprovados; execução Nativa e uma revisão independente final.
Status: `IMPLEMENTED`, `LOCAL_VERIFIED`. Revisão e gate completo concluídos;
sem merge/push ou Actions desta branch.

## Escopo

Painel de campanha para um MP4 HeyGen ready e profile exato, overrides em três
camadas e variantes explícitas A/B. Todos os campos atuais de output/headline/
captions/music/framing; herdar omite, limpar nullable envia null, false/zero/RGBA
preservados. Limite inicial três; sem produto cartesiano.

Validar e Salvar usam os endpoints e Jobs locais existentes, com worker
local_operations iniciado manualmente. Validação não publica artefatos; Save
revalida o mesmo request/plano e confirma arquivo via GET. Unknown congela o
payload, sem repost; artefato encontrado sem Job não prova autoria do POST perdido.
Consulta readonly não substitui draft. Rascunhos somente em memória.

Legendas vinculadas à copy/voz aprovadas; timing ausente fica pendente. Assets
por path/hash explícitos, música com aceite separado. Texto normalizado antes de
congelar o request para coincidir com o armazenamento dos Jobs locais.
Sem backend/schema/migration/dependências novos, provider pago, preview ou render.
AGENTS.md/sources preservados. D4B.3c/D5 continuam planejados.

## Verificações já executadas

- Frontend completo antes da revisão: 336/336, typecheck/build PASS.
- Frontend pós-correções na execução completa: 341/341, typecheck/build PASS.
- Browser/API/proxy/worker reais com providers fake: 6/6.
- Pós-revisão: sete browser E2E editoriais PASS dentro do gate fresco.
- Fast: 3/3; 25 Python aprovados / 1 skip, incluindo browser, batch service e API E2E.
- Browser: três headlines e reload, persist=false sem plano/manifest/mídia,
  Save/GET exato, bytes/hashes upstream/profile e budget/aprovação de copy intactos,
  captions timing_missing, POST perdido sem reenvio, Back/source/profile recusados,
  teclado e viewport 320/390 com texto/configuração longa.
- Correções observadas RED→GREEN: contrato real headline.spoken=false;
  Back/popstate sem unmount do draft; normalização de strings pela fila local;
  remoção de variante só de headline preservando aceite de música; overflow de JSON.
- Testes inicialmente corrigidos contra contratos existentes: coluna SQL id,
  labels reais e mensagem queued transitória; não tratados como bugs de produto.
- Nenhum novo PROVIDER_VERIFIED, uso de créditos, merge/push ou Actions nesta branch.

## Decisões de execução

- Scripts das skills pelo Git bash instalado fora do PATH; sem custo de produto.
- Temporary root curto por teste: o root padrão de pytest ultrapassou MAX_PATH
  na publicação imutável Windows. Backend preservado; roots configurados longos
  continuam falhando com segurança e exigem root local curto.
- Revisão readonly antes do gate completo para que correções recebam uma execução
  final fresca; um gate pendente nunca é evidência verde.

## Revisão e gate final

Revisão independente readonly em `12823be..b34876c`: nenhum Critical,
três Important mantidos pela reclassificação e corrigidos em uma rodada no commit
`1b07d10`, sem re-review:

- multi-caller navigation guard: aceitar um formulário e recusar outro não pode
  desarmar o aviso do primeiro; regressão click RED→GREEN e popstate coberto;
- fallback de key: após b–z, colisão com v27 travava o botão; browser RED por
  timeout→GREEN com candidato que avança a cada colisão;
- foco de erro: ID/key inseguros e label vazia focavam maxOutputs; três regressões
  RED→GREEN com guard compartilhado e nomes dos controles.

Checks pós-correção: 33 frontend focados PASS e regressão browser de colisão PASS.
Primeiro gate em `1b07d10`: 341 frontend e 1.807 Python / 25 skips PASS,
parou na etapa 10 (mypy): post_data_json nullable indexado no teste de resposta
perdida. Narrowing mínimo de dict no teste, sem mudança de produto (`e33d9df`).
Mypy tests com MYPYPATH=src do harness passou em 118 arquivos; fast 3/3 e
sete browser PASS. Uma invocação direta inicial sem MYPYPATH gerou erros
import-untyped alheios ao código; ambiente exato do harness restaurado, sem
alteração de configuração.

Gate completo repetido sobre `e33d9df`: `rtk proxy uv run python -u scripts/verify.py full`,
exit 0, **19/19 PASS**, 341/341 frontend, **1.807 Python / 25 skips**, pytest
660,75 s. Ruff source/tests/harness, mypy source/tests, build, schemas sem drift,
uv dependency check, locked installs e audits no threshold high aprovados.
UI audit zero vulnerabilidades; raiz mantém cinco moderate transitivos preexistentes,
sem upgrade forçado. HyperFrames doctor exit 0 com avisos de memória baixa e
recursos opcionais ausentes; não representa prova de prontidão/qualidade do renderer.
Revisão/fixes e full gate foram uma rodada de correções, sem segundo reviewer.

### Minor adiado

- Guard de manifest no cliente não rejeita trim de música invertido; backend
  autoritativo rejeita, limitando impacto operacional atual.
- Limpar seleção de plano durante GET pode deixar mensagem de loading visível.

### Julgamentos que o reviewer deixou ao executor

- Gate completo/docs: exigir execução fresca e documentação final antes da
  classificação LOCAL_VERIFIED; caso contrário haveria falsa alegação de conclusão.
- Providers pagos/render/qualidade visual: fora deste slice; não há novo
  PROVIDER_VERIFIED e a qualidade do vídeo final não foi demonstrada.
- Roots longos no Windows: conservar backend fail-safe e raiz curta de teste;
  roots configurados longos ainda podem impedir publicação.
- Recomputação criptográfica/lookup independente da copy no browser: manter
  backend como resolver autoritativo; sua verificação continua necessária.

### Fechamento documental

Após o gate fresco, só documentação foi finalizada. task-done executa o diff check
final, usando o full gate acima como evidência de runtime, sem terceira execução
de onze minutos para mudanças de prosa. Custo/limite: essa evidência deixa de
valer se código/testes forem alterados posteriormente.

Próximo recorte: D4B.3c — preview aproximado, sem timeline/frame-perfect.
