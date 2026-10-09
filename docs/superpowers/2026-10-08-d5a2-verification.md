# D5A.2 — Evidência de implementação e verificação

Data: 2026-10-08. Branch `feat/d5a2-render-integration`. Base produção `501d312`.
Design/plano aprovados; execução Native autorizada. Merge/push autorizados em 2026-10-08.

## Estado

`IMPLEMENTED`, `LOCAL_VERIFIED` (Windows), código `8a34ffc`. Actions desta etapa não executados;
sem chamadas pagas, sem nova evidência PROVIDER_VERIFIED. D5B permanece planejado.

## Verificação realizada

- Task 1: contratos/schema/harness — 64 testes focados aprovados.
- Task 2: handler/worker/heartbeat — 30 testes focados aprovados.
- Task 3: fila/identidade/reuso — 26 aprovados / 2 skips existentes.
- Task 4: HTTP/download/integridade — 39 testes aprovados, fast 3/3.
- Task 5: cliente tipado — 37 testes focados, UI 4/4.
- Task 6: controles/polling/reconciliação — 54 testes focados, UI 4/4.
- Task 7: E2E real/CI harness — 38 testes aprovados, fast 3/3, 33,90 s.
- mypy tests com MYPYPATH=src: 132 arquivos, sem erros.
- Ajuste final OpenAPI: 31 testes HTTP/media aprovados, fast 3/3, 43,29 s.
- UI após correções da revisão: 378 testes em 22 arquivos, gate 4/4 (tipagem/build/audit).

E2E usa FFmpeg real e fonte local real, três headlines → três masters distintos,
downloads via browser + check_master/full decode, reload e novo executionId com reused.
Recibos e assets upstream/budget/copy permanecem iguais. Segundo E2E comprova que
alterar draft não muda hash/inputs do alvo salvo. Remover temporariamente o wiring
do RenderPanel fez este teste falhar; restaurar passou, sem alteração permanente.

Ajustes observados: middleware global rejeitava query de filtros/download; whitelist
restrita mantém demais queries rejeitadas. Windows requer caminhos longos nativos
também para leitura/limpeza dos fixtures. Seletor após reload tem nome acessível explícito.
Nova execução limpa resultado anterior durante envio, evitando exibir conclusão antiga
como resultado do novo Job; regressão observada RED→GREEN.

## Gate completo e revisão

`rtk uv run python scripts/verify.py full` sobre `8a34ffc`: exit 0, **19/19**.
378 testes frontend / 22 arquivos (26,79 s), tipagem/build aprovados;
1.924 Python / 27 skips (812,77 s); Ruff, mypy src 116 arquivos / tests 132,
schemas sem drift, dependências e whitespace aprovados.
UI production audit: zero vulnerabilidades. Audit raiz mantém cinco moderadas
preexistentes da cadeia HyperFrames, abaixo do threshold high; não aplicado upgrade
breaking. HyperFrames doctor retorna 0, mas informa runtime antigo e opcionais
ausentes (Docker/whisper/TTS/BGM); esses componentes não são necessários ao renderer
FFmpeg usado e não foram declarados instalados/verificados.
Execução diagnóstica anterior: 1 failed / 1.923 passed / 27 skipped, 835,12 s;
parou em 6/19. Falha `test_all_spec_get_routes_and_openapi`: expectativa antiga
de 17 GETs/JSON não incluía três rotas de render e MP4 binário. Reproduzida
isoladamente; corrigido somente teste existente, mantendo aplicação e contratos.
Não contar essa execução como gate aprovado. Uma execução anterior interrompida
sem resultado recuperável também não foi contada.
Revisão independente final sobre `501d312..835ec8a`: zero Critical, dois Important,
zero Minor. Revisor read-only examinou também a documentação em andamento e confirmou
os dois casos por React/jsdom em memória. Primeiro modelo indisponível por usage limit
não realizou revisão; o revisor efetivo disponível foi gpt-6.1-sol.

Achados corrigidos em `9902de5`, um passe RED→GREEN:

1. POST desconhecido que não chegou ao servidor bloqueia pending mesmo após consulta vazia.
   Recuperação escolhida: abandono explícito, com confirmação após consulta bem-sucedida;
   nenhuma nova identidade/envio automático nem cancelamento do Job.
2. Failed por variante perde motivo útil. Exibir diagnósticos locais permitidos para timing,
   fonte, fit e órfão/recibo, com fallback seguro sem mensagens privadas arbitrárias.

Cinco regressões novas falharam antes da correção (5 failed / 7 passed);
após o passe, UI 4/4 e 378 testes aprovados; gate completo pós-correção 19/19.
Nenhum Minor adiado; sem re-review, apenas regressões e suite inteira após o passe.

## Decisões e limites

- API exporta computed hasFailures; persistência omite computed fields para reparse válido
  (risco se incorreto: falha ao reler resultado; coberto pelos testes).
- Render target corresponde ao último save confirmado ou consulta explícita, independente
  de draft/preview. UI mostra videoId/planHash e limpa alvo ao trocar fonte/profile/consulta
  (risco se incorreto: renderizar outro plano salvo).
- Parâmetros query só nas rotas declaradas; testes OpenAPI ajustados para 22 POSTs
  (risco se incorreto: rejeitar filtro/download ou aceitar queries indevidas).
- Testes usam assertions DOM existentes, sem instalar jest-dom só pelo exemplo do plano
  (risco se incorreto: corrigir matcher de teste).
- Fixture Windows usa raiz temporária única validada e cleanup extended-path; nenhum asset do usuário
  (risco se incorreto: sobras temporárias).
- Revisão read-only em paralelo ao gate sobre código terminado; evidência final acrescentada
  após resultado real, sem chamar gate em andamento de aprovado
  (risco: evidência final não estava na revisão inicial; registrada separadamente).
- Expectativa global OpenAPI atualizada para 20 GETs e resposta MP4 binária, conforme
  três rotas planejadas; risco se incorreta: teste perder rota/content type inesperados.
  Assertions binárias explícitas e testes focados das rotas continuam presentes.
- Sem cancelamento FFmpeg, checkpoint por variante, percentual fictício ou retry automático.
- Render não é approve/delivery; preview continua aproximado e CLI D5A.1 compatível.

Comportamentos que o revisor considerou fora do escopo e decisões mantidas: providers/Flow
reais só no piloto (risco: integração real ainda não provada nesta etapa); QC/approve/delivery
só D5B (risco: revisão humana manual ainda necessária); sem recuperação automática de
órfãos/interrupção (risco: reparo pelo operador); sem render distribuído/variantes paralelas
ou múltiplos processos externos (risco: sem garantia entre CLIs simultâneas); sem novo
hardening adversarial concorrente (risco: armazenamento pessoal continua sendo confiável).

CI histórico: Linux/Windows D5A.1 aprovado em `501d312`,
[run 37792802719](https://github.com/rafaelrovaron/auraly-video-automation/actions/runs/37792802719).
Não confundir essa evidência com Actions D5A.2 ainda não publicados.

## Integração autorizada

Merge local por fast-forward de `main` `501d312` → `38257de`, sem conflitos.
Gate completo fresco na cópia principal após merge: exit 0, 19/19;
378 frontend em 22 arquivos (29,11 s), 1.924 Python / 27 skips (821,87 s),
tipagem/build/schemas sem drift aprovados. Avisos opcionais e cinco moderadas
preexistentes HyperFrames permanecem os mesmos, sem upgrade breaking.
Actions desta integração ainda pendentes; não alegar CI_VERIFIED antes do resultado.
Worktree preservado por conter scratch de outra etapa: não remover arquivos alheios
para realizar cleanup de branch. Nenhuma alteração em AGENTS.md ou sources/.

## Correção mínima CI Windows — 2026-10-09

[Run 37845602297](https://github.com/rafaelrovaron/auraly-video-automation/actions/runs/37845602297),
commit `2ca9e29`: Linux full passou; Windows falhou no UI gate com um teste failed
e 377 passed. `test_prepare_enqueues_wrapper_without_start` esgotou a espera
buscando o botão antes das leituras iniciais, inclusive HeyGen dependente do detail.
Busca global por role em DOM grande pode ocupar o event loop e consumir o timeout.
Reprodução diagnóstica local: custo de 1.100 ms apenas na busca do botão ainda ausente
reproduziu a mesma falha; aguardar `act` assíncrono no render inicial passou com
esse mesmo diagnóstico. Instrumentação temporária removida depois de RED→GREEN.

Correção apenas no helper `openPanel` de `HeyGenPanel.test.tsx`: aguardar as
leituras iniciais antes da busca/assertion existentes. Sem alteração na aplicação,
timeout, retries, skips, dependências ou workflow. Gate UI local fresco 4/4:
378 testes / 22 arquivos (27,63 s), tipagem/build/audit aprovados.
Reexecução focada sem instrumentação: 60 testes HeyGen aprovados (9,92 s).
Revisão independente read-only do helper: zero Critical/Important/Minor;
não certificou a reprodução temporária nem o CI, que exigem evidência separada.
Isso não é uma nova execução local do gate Python completo; preserva a evidência
Linux e Windows local anteriores. Actions do push corretivo ainda pendentes.
