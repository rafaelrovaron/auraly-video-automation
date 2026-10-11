# D5B.2B — Evidência de implementação local

Data: 2026-10-11. Branch: `feat/d5b2b-qc-review-ui`, base `4ea77b7`.
Design e plano aprovados; execução Native com TDD e commits por tarefa.
Sem merge/push nesta etapa. `IMPLEMENTED`, `LOCAL_VERIFIED` (Windows);
verificações pós-fix aprovadas, revisão independente concluída.

## Escopo

Descoberta verificada readonly de QC/review, API por output do Job/plano salvo,
edit_qc na fila existente, player nativo e decisão definitiva no RenderPanel.
Recuperação após reload/respostas perdidas, múltiplos QC, conflito de outro cliente
e invalidação de evidência. Sem provider pago, encode durante QC/review, nova
dependência, tabela/migration, timeline, proxy ou entrega. AGENTS e sources preservados.

## Verificações executadas

- Task 1 RED: 11 missing-method failures + 1 symlink skip; GREEN 62 passed / 1 skipped,
  Ruff/mypy, harness 3/3 (147,33 s; task-done repetiu em 151,70 s).
- Task 2 RED: 10 falhas por API/schema ausentes; GREEN 55 passed / 1 skipped,
  harness 3/3 (40,77 s; task-done 41,09 s). Três schemas exportados e protegidos.
- Task 3 RED: dois imports ausentes; GREEN 65 frontend em quatro arquivos;
  TypeScript/build aprovados. Todos UI: 399 passed em 24 arquivos (19,01 s).
- E2E real local: 2 passed (39,23 s): dois masters sintéticos, player metadata
  1080×1920, aprovação/reload/original, rejeição/comentário/reload; hashes upstream,
  master/receipt/plano e budget preservados, renderer bloqueado e eventos provider vazios.
  Corrupção de QC remove decisão acionável e apresenta erro sanitizado.
- Fixtures corrigidas sem relaxar produção: receipt é render.json; caminhos longos
  Windows usam work root do serviço; MP4 compartilhado era silencioso e bloqueou QC
  corretamente, por isso este E2E usa tom sintético mensurável. Seletor JS corrigido.
- Gate completo inicial interrompido após POST inventory antigo; segundo gate
  chegou ao final do pytest com 2.092 passed / 28 skipped e uma falha apenas no
  GET inventory antigo (1.024,05 s). Contagens atualizadas para 24 POSTs / 21 GETs;
  ambas regressões passaram, harness fast 3/3. mypy tests: 144 arquivos sem erro.
  Terceiro gate completo: exit 0, **19/19 PASS**. Frontend 399 passed / 24 arquivos
  (29,04 s); Python 2.093 passed / 28 skipped (1.027,18 s). Ruff, mypy src
  (125 arquivos) / tests (144), schemas sem drift, build e harness aprovados.
  Audit produção UI sem vulnerabilidades; root possui cinco moderadas transitivas
  via HyperFrames (limiar high aprovado). Sem upgrade breaking fora do escopo.
  HyperFrames doctor exit 0; Docker e runtimes AI opcionais ausentes.
  Não há alegação de execução Linux local nem Actions deste slice.

Gate full pré-revisão aprovado; revisão independente concluída.
Pós-fix: frontend **401 passed**, Python **2.093 passed / 28 skipped**
(1.001,47 s), Ruff/mypy/schema/deps/build/doctor aprovados. O comando full passou
as primeiras 17 etapas e parou na 18 por ENOENT de DNS no registry.npmjs.org.
Retomada pelo mesmo harness `run_steps(build_full_steps()[17:])`: exit 0,
audit e whitespace **2/2 aprovados**. Todos os 19 checks verificados sobre o
código corrigido; não é uma execução full atômica 19/19. Nenhum código mudou
entre full e retomada. Audit mantém cinco vulnerabilidades moderadas transitivas,
zero high; sem upgrade breaking. Logs locais preservados em
`.worktrees/d5b2b-verification-logs/` no checkout principal após integração.
Testes locais não significam aprovação editorial de campanha real ou PROVIDER_VERIFIED.

## Decisões de execução

1. Worktree nativa não identificou checkout aninhado; fallback Git em pasta ignorada
   mantém isolamento. Custo se errado: limpeza manual do checkout.
2. find_review inspeciona siblings somente no alvo para detectar receipt alterado;
   uma chave nova não é tratada como ausência. Custo se errado: sibling malformado
   bloqueia consulta até reparo.
3. api/operations.py omite campos computados somente ao persistir edit_qc, seguindo
   RenderJobHandler; hasFailures é reconstruído na leitura. Custo se errado:
   compatibilidade de serialização dos resultados QC.
4. Atualizar os testes de inventário OpenAPI para as três rotas aprovadas.
   Custo se errado: contagens de rotas inexatas nos testes.
5. Autenticação e prova objetiva de playback seguem excluídas: checkbox é declaração
   humana em ferramenta local/pessoal. Custo se errado: identidade/escuta não comprováveis.
6. Reescrita maliciosa coerente de toda a evidência segue excluída: hashes protegem
   corrupção, não um proprietário local hostil. Custo se errado: adulteração deliberada
   pode não ser detectada.
7. Escalabilidade de enumeração/hashing em grandes acervos adiada: sem gargalo
   demonstrado no uso pessoal. Custo se errado: consulta lenta em acervos grandes.
8. Sem alegação editorial/provider real: mídia sintética local. Custo se errado:
   problemas de campanha/provider reais permanecem desconhecidos.
9. Entrega/cópia permanece D5B.3: slice termina no review definitivo. Custo se errado:
   operador copia masters manualmente até implementar entrega.
10. Sem certificação Linux/Actions: somente Windows local executado. Custo se errado:
    falhas específicas de plataforma podem aparecer no CI.
11. Retomar apenas etapas 18–19 após erro DNS: as primeiras 17 passaram sobre o
    mesmo código, sem motivo para repetir 16 minutos de testes. Custo se errado:
    possível mudança de ambiente entre execuções; não alegar full atômico.

## Revisão independente e correções

Revisor fresh-context gpt-6-astra, diff completo `4ea77b7..7aa292b`, somente leitura.
Zero Critical, dois Important e dois Minor. Important confirmados por RED (2 failures,
12 passes) e corrigidos em uma rodada:

- POST QC com artifact_invalid limpa state/seleção/checkbox e bloqueia envios até
  GET válido; regressão `QC integrity failure clears the prior actionable evidence`.
- Mudança no conjunto de relatórios exige seleção explícita quando há múltiplos QC,
  preservando o QC original de decisão já salva; regressão
  `one report becoming multiple after QC requires a fresh explicit selection`.

GREEN: 67 testes UI selecionados e TypeScript/build. Sem re-review; regressões e
gate completo pós-fix fornecem a evidência das correções.

Minor adiado (não alterado nesta etapa):

- Traduzir findings conhecidos text_fit/master_invalid/integrity_prerequisite; hoje
  usa fallback fixo sanitizado, sem exibir mensagem bruta.
- Exibir estado observado ao consultar worker; hoje consulta funciona, mas apresenta
  orientação genérica em vez do estado retornado.

## Limites / próximo passo

Integração autorizada pelo usuário em 2026-10-11: fast-forward de main
`d9d0db3 → d89edd7`, incluindo design/plano e cinco commits de implementação/fix.
Gate completo fresco no checkout main: **19/19 PASS**, exit 0; frontend
**401 passed / 24 arquivos** (37,71 s), Python **2.093 passed / 28 skipped**
(1.060,85 s). Ruff, mypy src125/tests144, schemas sem drift, build, deps,
audit e whitespace aprovados em uma execução full. Mesmos avisos moderados
transitivos e runtimes opcionais ausentes; nenhum upgrade fora do escopo.
Log bruto: `.worktrees/d5b2b-verification-logs/d5b2b-main-verification.log`.
Push solicitado pelo usuário; Actions deste slice ainda não confirmado.

Uso pessoal/local, decisão definitiva sem edição/revogação, escuta é declaração
humana e não prova de playback. Política de áudio/texto mantém limites D5B.1.
Worker local_operations pode drenar outras operações da campanha. D5B.3 entrega
de masters aprovados segue PLANNED e requer seu próprio design.
