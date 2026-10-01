# D3A — evidência de implementação e verificação

Data: 2026-10-01. Branch `feat/d3a-editing`, base `5812e4f`.
Status: `IMPLEMENTED`, `LOCAL_VERIFIED`; revisão independente e rodada de correções concluídas.

## Entregue

Contratos profile v1/request v1/manifest v2 e schemas separados; estilos tipados,
resolver puro, precedence/provenance, hashes; persistência JSON versionada sem
overwrite; validação de paths/links/hashes/mídia/fontes; CLI `edit` e exemplos.
Legado v1 preservado. Sem SQL migrations, dependencies, Jobs, UI ou renderer novos.

Commits de tarefas: `7df79c0`, `92f3040`, `fe59188`, `28c5261`.
Cada tarefa teve RED por ausência da capability e GREEN no harness fast (3/3).
Resultados focados: Task 1 36 passed; Task 2 32 passed; Task 3 19 passed/1 skip;
Task 4 39 passed. Skip adicional: SO Windows não permitiu criar symlink;
junction real foi criada e rejeitada pelo teste.

Baseline antes das mudanças: 1432 passed/19 skipped em 254,08 s.
Gate inicial: `rtk uv run python scripts/verify.py full` PASS, 15/15 steps.
1481 passed/20 skipped em 250,41 s; Ruff source/tests/scripts e mypy source (91)
e tests (88) passaram. Schemas v1, imagens, voz e editing sem drift; dependências
locked/check e npm production audit passaram (zero vulnerabilidades).
HyperFrames doctor exit=0 com ferramentas opcionais/Docker ausentes;
nenhuma composição/render foi alterada nem se declara esses extras disponíveis.

## Revisão independente e correções

Reviewer independente: 0 Critical, 3 Important, 0 Minor; 48 passed/1 skipped
no seu teste focado. Os três Important foram reproduzidos com RED e corrigidos
em uma única rodada (`99147d7`): roots lexicais até validação de links, regras
semânticas compartilhadas no snapshot (incluindo leitura/validate com checksum
recalculado) e redaction de chaves desconhecidas nos erros públicos.
GREEN focado após correções: 90 passed/1 skipped, Ruff/mypy source passaram.
Gate completo pós-correções PASS: 15/15 steps, 1491 passed/20 skipped em 253,14 s;
Ruff, mypy source/tests, schemas sem drift e dependências/audit passaram.
É a evidência final do código em `99147d7`; não usar somente o gate inicial.
Smoke com MP4 real repetido após correções: mesmo resultado/hash, fonte intacta.
Sem nova revisão por segundo reviewer; correções provadas por RED→GREEN e gate.

Reviewer não julgou resistência a troca adversarial de diretórios ou glyph/layout:
esses limites são explícitos abaixo e permanecem fora de D3A. Nenhum Minor adiado.

## Teste com fonte real existente

MP4 D2C lido sem modificação; duração 7,224 s, H.264/AAC.
Source SHA-256 `721f35a2d054742d3600ca5dd6f61459a948bcf32bc1663641fcaabe377dba69`.
Profile desabilitou headline/captions/music para este smoke sem pressupor fontes.
Manifest SHA-256 `e1df8fc153e747dd75c52d8a3b845bb4bacb264002ff9e6da3d4cd74f529b901`.
Resolve duas vezes + get persistido produziram o mesmo contrato/hash.
Source hash/mtime intactos; artefatos novos somente em diretório temporário isolado.
Zero paid calls, nenhum write no banco/voz/HeyGen ou workspace D2C.

## Limites

Fonte sfnt é verificada estruturalmente, não prova encaixe/glyphs/layout renderizado.
Checksum de profile detecta corrupção, não assinatura contra adulteração deliberada
de conteúdo e checksum simultaneamente; request exige o hash da versão exata.
Filesystem local de operador único, sem promessa de resistência a adversário
substituindo diretórios durante a publicação. Reparse points são recusados.
Não há texto/timing de captions, render, batch A/B, UI/preview ou aprovação final
automática. D3B é o próximo slice. Nenhuma nova evidência PROVIDER_VERIFIED.

## Decisões de execução

App worktree tool não encontrou o Git no chat espelhado; fallback Git criou
worktree irmã isolada. Custo: cleanup dessa worktree é manual, não pelo app.
Revisão final executada dentro da Task 5, antes de task-done, para cumprir a própria
exigência da tarefa; contagens finais de gate atualizadas pelo autor. Custo:
essa atualização de evidência final não teve nova revisão independente.
Nenhuma mudança em AGENTS.md, fontes sincronizadas ou mídia original.
