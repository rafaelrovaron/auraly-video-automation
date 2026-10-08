# D4B.3c — Preview aproximado: evidência de entrega

## Estado

Design, plano e execução Nativa aprovados em 2026-10-08. `IMPLEMENTED` na
branch `feat/d4b3c-preview`, base main `dd7203c`. `LOCAL_VERIFIED`: gate fresco
Windows pós-correções no código `b8164a5`, 19/19 etapas, exit0.
Sem merge/push desta etapa ou novo `PROVIDER_VERIFIED`.

Commits locais: design `f10bbcf`, plano `8ac5947`, poster `0616287`, preview
`65477c9`, integração `d831740`, correções da revisão `dc81434` e ajuste de
tipagem do teste `b8164a5`.

## Capacidade entregue e limites

- PNG do primeiro frame decodificado do MP4 ready, com campanha/ID/hash e
  caminho confiável verificados; memória limitada a 4 MiB, 720 px por eixo e
  timeout de 10 s. Processo encerrado/reaped nas falhas; no-store.
- CSS sobre canvas lógico, overlays por camadas existentes no draft ou pelo
  manifest exato confirmado; seleções estáveis de variantes. Caption confirmada
  vem da primeira cue ou primeiras doze palavras do captionInput do plano.
- Frame trocado por identidade ou reload explícito; abort e revoke Object URL.
  Editar layout não envia POST nem busca novamente o mesmo frame.
- Avisos de texto além de maxLines/safe zones/canvas fora da área recortada;
  viewport até 360 × 640 px preserva proporção, inclusive formatos extremos.
- Fallback de fonte explicitamente sinalizado; sem reprodução, sincronização,
  áudio, shrink/error exato, zoomEnd/highlight animado, renderer, novos Jobs,
  migration, cache persistente, dependências ou chamadas pagas.

## Verificação observada

- Task 1: rota ausente reproduziu 404; API/HTTP focused passou 30 testes e
  1 skip de symlink Windows; harness fast 3/3. Check adicional de mypy sem
  MYPYPATH falhou por ambiente; repetição com MYPYPATH=src passou.
- Task 2: módulo/função ausentes RED; focused GREEN 27 testes e build.
- Task 3: preview/origem ausentes RED; GREEN 31 EditingPanel, 348 frontend,
  10 browser e 11 poster/1 skip; build/types passaram. Uma expectativa nova
  de poster foi corrigida de largura720 para405, preservando proporção720×1280.
  Capturas 320/390 px inspecionadas; fixture sintética, não criativo aprovado.
- Revisão: dois testes de geometria RED (2 falhas/4 passes), depois GREEN6;
  browser real confirmou safe zones e ratios16×1024/1024×16 (2 passed).
  Após fix: frontend350, build e harness fast3/3 com browser12 passed.
- Gate pré-fix interrompido devido às correções; não é evidência de conclusão.
  Primeiro gate pós-fix: frontend350 e Python1823/26 skips (671,04 s) passaram;
  parou na etapa10/19 por mypy: variável local value inferida str reutilizada
  para dimensão int no teste novo. Renomeada para dimension; mypy119 e browser2
  passaram. Gate completo fresco após essa correção: **19/19 aprovado**,
  frontend350, Python1823/26 skips (669,34 s), mypy106 source/119 tests,
  Ruff/build/schemas sem drift. Audits no limiar high aprovados; cinco moderate
  transitivos preexistentes permanecem. Doctor passa com aviso de atualização e
  recursos opcionais (incluindo Docker ausente); não certifica render.
- Actions desta branch não executados. No main anterior, Windows passou e
  Linux foi cancelado durante preparação de ambiente, antes dos testes.

## Revisão independente

Um reviewer gpt-6-astra/high avaliou `dd7203c..d831740`, spec/plano/ledger e
probes Chromium readonly. Nenhum Critical; dois Important, ambos mantidos
por efeito real no usuário e corrigidos em uma única rodada RED→GREEN:

1. Texto em y=1/anchor=top desaparecia sem aviso; safeTop/safeBottom ignorados.
   Agora geometria posicionada é comparada às quatro safe zones e warnings
   ficam fora do canvas, com regressões unitárias e browser.
2. Output16×1024 gerava viewport23.040px alto. Agora escala preserva ratio
   com limite de altura640 e largura360, com regressões unitárias e browser.

Não houve re-review. A avaliação do reviewer não certifica o gate posterior
ou documentação final; essa responsabilidade permanece com o implementador.

### Minor adiada

Recomendação de fortalecer teste readonly com caption armazenada distinta da
copy corrente, cobrindo primeira cue e fallback de doze palavras. Código usa
captionInput do plano na inspeção; cobertura específica fica para próximo slice.

## Decisões tomadas (Rulings, em ordem)

1. Atualizar teste OpenAPI de16 GETs JSON para17, com PNG binary explícito:
   nova rota aprovada exige contrato binário. Risco: cobertura inexata se errado.
2. Usar assertions Vitest existentes e visibilidade browser, sem instalar
   matcher toBeVisible do exemplo. Risco: jsdom isolado não certifica visibilidade.
3. Sobrepor review readonly e gate após commits/focused GREEN; qualquer fix
   exige gate completo fresco. Risco: review não certifica gate ainda pendente.
4. Custom fonts exatas continuam adiadas com fallback visível. Risco:
   tipografia difere do render.
5. Shrink/error e glyph fitting continuam aproximados, avisados. Risco:
   fit pode enganar apesar do aviso.
6. Playback/timing/zoom/highlight/audio excluídos do static slice. Risco:
   efeitos temporais não podem ser avaliados aqui.
7. MP4 final/regeneration/provider pago excluídos; nenhum introduzido. Risco:
   confundir preview com vídeo entregue.
8. Sem cache persistente/Jobs/polling de poster. Risco: recarga explícita
   repete decode.
9. Modelo existente de path validation/hash/decode mantido, sem redesenho de
   handle atômico para uso pessoal/local. Risco: troca hostil entre checks/decode.
10. Reviewer não certifica full gate/Linux/docs; implementador finaliza gate
    local/docs sem alegar remoto. Risco: alegação de verificação sem evidência.
11. Sem mudança em submission/recovery preexistentes fora da integração;
    regressões existentes mantidas. Risco: bugs antigos fora do slice permanecem.

## Próximo passo

Decisão humana de integração; depois design incremental D5A para renderer
determinístico mínimo. D5B QC/review/delivery continua posterior. Flow pausado
e não bloqueante. Preview não aprova mídia nem dispensa aprovação final.
