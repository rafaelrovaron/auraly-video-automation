# D4B.3a — evidência de entrega local

Data: 2026-10-07. Branch: `feat/d4b3a-profiles`, base `aaa78dd`.
Design/plano aprovados; execução Nativa, TDD e uma revisão independente final.
`IMPLEMENTED`, `LOCAL_VERIFIED`. Gate fresco pós-correções: 19/19.

## Escopo

UI global `#/profiles`: lista/consulta readonly, criação v1, cópia base + 1,
formulário completo EditProfile v1 e versões imutáveis pelos quatro endpoints
existentes. Payload congelado quando o resultado é incerto; GET exato compara
conteúdo, sem repost automático. Rascunhos em memória e confirmação de descarte.
Referências de fonte/música por path relativo e SHA-256 explícito; publicar
metadados não valida mídia (`validate_assets=False`). Sem backend, migrations,
Jobs, dependências novas ou chamadas pagas. Overrides/variants, preview e renderer
continuam planejados. AGENTS.md e sources/ preservados.

## Verificação

- Cliente/formulário/painel: 54 testes focados após revisão.
- Frontend completo pós-correções: 275/275; typecheck/build aprovado.
- Browser/API/arquivos reais: 6/6, sem provider; persistência/reload, v1 intacta,
  perda de resposta POST, conflito, descarte recusado, teclado e 320/390 pixels.
- Gate inicial: 19/19; substituído como evidência final porque houve correções
  durante sua execução. Gate fresco pós-correções obrigatório.
- Gate fresco: `rtk proxy uv run python -u scripts/verify.py full`, exit 0,
  19/19; frontend 275/275, Python 1.800 aprovados / 25 skips (602,27 s).
  Ruff, mypy source/tests, schemas sem drift, build e dependency checks passaram.
  Audits passam no limiar high; cinco moderate transitivos preexistentes permanecem.
- Nenhum merge/push ou Actions executado para esta branch.

## Revisão independente e regressões

Nenhum Critical. Três Important reproduzidos RED e corrigidos em uma única
passagem, com suíte frontend verde:

1. GET de confirmação divergente: manter payload/rascunho congelado e conflito,
   não sinalizar publicação confirmada (`divergent_confirmation_get_keeps_frozen_submission`).
2. Enums em arrays aceitos por coerção: exigir strings reais, cinco casos inválidos.
3. Campo inválido escondido em opções avançadas: mensagem específica, abrir
   seção, foco e aria-invalid/aria-describedby; limpar atributos após correção.

Minor adiado: não há regressão específica de browser Back nesta nova rota.
Descarte interno e navegação por anchor estão cobertos; hook existente preservado.
Nenhum comportamento foi recusado para julgamento; sem segunda revisão.

Observação histórica: uma execução frontend intermediária falhou no teste legado
de HeyGen stale por ordem de resolução de requests da fixture. Suíte inalterada
passou depois; não foi tratado como bug corrigido nem recebeu retry/timeout.

## Decisões de execução

- Bash fora do PATH: scripts das skills pelo Git bash instalado, sem custo de produto.
- Revisão readonly sobreposta ao gate inicial: reduz tempo, mas o gate pendente
  não é evidência verde; repetir sobre correções. Risco: interpretar o gate pendente
  como aprovação, mitigado pela execução final separada.

Próximo recorte: D4B.3b — overrides por vídeo e variantes A/B de headline,
reutilizando voz/imagem/HeyGen; design e plano próprios antes da implementação.
