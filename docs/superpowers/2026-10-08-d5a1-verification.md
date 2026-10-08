# D5A.1 — Verificação do renderer local

Data: 2026-10-08. Branch `feat/d5a1-renderer`, base de código `42beda7`,
spec/plano aprovados `5299d85`/`52f0a9d`, implementação até `7f82932`.
Execução Native, sem implementadores paralelos, paid calls, novos providers,
dependências, Jobs/API/UI/migrations, alterações em AGENTS.md ou sources/.

## Evidência observada

- Contratos/runtime: fast 3/3, 51 testes; texto: 22 testes; mídia: 33 testes.
- Integridade complementar: 28 domain/media; timeout real termina/recolhe FFmpeg,
  corrupto/sem áudio/matriz de rotação rejeitados antes de publicação.
- Serviço: fast 3/3, 39 passed / 3 skips (privilégio de symlink Windows).
- CLI: fast 3/3, 53 passed; JSON rendered/reused/failed, exit 0/1,
  dry-run sem writes, suporte de roots e sanitização.
- mypy dos novos testes/helpers passou com MYPYPATH=src.
- CLI externo sobre source sintético de 2 s: três rendered, replay três reused,
  inputs inalterados. Nove PNGs extraídos início/meio/fim; frames de três variantes
  inspecionados visualmente: headline íntegra no topo, crop central e quadrantes
  proporcionais. Mídia permanece somente em diretório temporário.
- Caption ASS inspecionada visualmente; alpha bbox ausente em 14/30 s,
  presente em 0.5 e 29/30 s, ausente em 1 s: bordas exatas do cue [0.5, 1).
  Texto completo e pontuação preservados, safe zones dentro do master.
- Gate completo Windows `rtk uv run python scripts/verify.py full`: em andamento.
- Revisão independente final: pendente.
- Actions Linux/Windows desta branch: ainda não executados; sem merge/push.

## Decisões de execução (Rulings)

1. Manter retorno de seis arquivos de export_editing_schemas; export separado
   dos dois contratos de render preserva consumidores com zip strict.
   Custo se errado: uma função adicional de export.
2. Pillow apenas dimensiona/quebra conservadoramente a probe; raster libass decide
   fit real. Custo se errado: linhas mais conservadoras, não a largura máxima possível.
3. Backslash literal protegido por Unicode word joiner invisível; braces escapados
   conforme libass, que não suporta escape de backslash por duplicação.
   Custo se errado: controle invisível afeta shaping; teste de imagem cobre tags literais.
4. Fontconfig isolado por staging; arquivos distintos da mesma família rejeitados
   quando seleção ambígua. Custo se errado: operador precisa escolher fontes não ambíguas.
5. Windows usa work root extended-length nativo e staging diretamente no root seguro:
   teste real falhou mkdir por MAX_PATH; hashes completos mantidos.
   Custo se errado: ferramentas Windows legadas podem exigir work root mais curto.

## Limites da entrega

Renderer sequencial local, masters fixos, pesos 400/700, sem word highlight,
captions somente com timing já aceito; sem alinhamento ou QC/review/delivery final.
Mix conserva voz por ganho unitário; limiter só reduz picos, ducking é estático.
AAC copiado sem música. Reuso preserva planHash do produtor e não adota órfãos.
Preview CSS permanece aproximado. D5A.2 Jobs/UI e D5B são PLANNED.
Nenhuma nova certificação de provider ou uso de créditos.
