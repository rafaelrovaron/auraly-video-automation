# D5A.1 — Verificação do renderer local

Data: 2026-10-08. Branch `feat/d5a1-renderer`, base de código `42beda7`,
spec/plano aprovados `5299d85`/`52f0a9d`, código corrigido `f08fbdd`.
Estado: `IMPLEMENTED`, `LOCAL_VERIFIED` no Windows. Gate final sobre `7f0b7e6`;
atualização posterior somente documental registra o resultado, sem mudança de código.
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
- Gate completo pré-correções: 19/19, 350 frontend, 1.874 Python / 27 skips,
  pytest 735,20 s. Esse resultado não certifica sozinho as correções posteriores.
- Gate completo Windows pós-correções: **19/19**, exit 0; 350 frontend,
  **1.877 Python / 27 skips**, pytest 721,38 s. Ruff, mypy source (111 arquivos),
  mypy tests (125), build e schemas sem drift. Audits passaram no limiar high;
  cinco moderate transitivos preexistentes permanecem, sem upgrades fora de escopo.
- Revisão independente fresca (gpt-6-astra): nenhum Critical, três Important,
  nenhum Minor; lista Declined to judge vazia. Um único passe de correção,
  sem segunda revisão: os três testes reproduziram RED e passaram GREEN.
  Suite focada de render pós-correções: 54 passed / 1 skip, 47,80 s, fast 3/3;
  mypy dos seis novos arquivos de testes/helper passou.
- Actions Linux/Windows desta branch: ainda não executados; sem merge/push.
  Linux local não disponível (WSL não instalado); não inferir sucesso Linux
  do gate Windows. Após publicação autorizada, ambos os Actions devem ser verificados.

## Revisão e correções

1. Mix zerava separadamente o início da voz: source com vídeo em 0 s e áudio
   em 0,378 s publicava voz antecipada. Regressão
   `test_mix_preserves_delayed_voice_and_complete_duration` verificou silêncio
   inicial, ganho unitário e fala até o final. Normalização agora usa a referência
   comum do input FFmpeg; first_pts=0 mantém o atraso como silêncio, sem cortar voz.
2. Fade-out de música de 0,5 s sobre vídeo de 2 s era aplicado a silêncio em 1,8 s.
   `test_short_nonloop_music_fades_before_silence` verificou o decaimento real
   antes de 0,5 s; fim efetivo é min(segmento, vídeo) sem loop, vídeo com loop.
3. Shrink abortava no canvas limite, mesmo quando tamanho menor cabia.
   `test_shrink_continues_past_measurement_canvas_limit` usa 100 W a 100 px;
   candidato excedente agora continua em passos de 1 px, com raster limitado.
   Falhas reais de fonte/runtime continuam sendo erros, não shrink silencioso.

Sem achados Minor adiados neste recorte. Os Minor históricos de outros slices
não foram ampliados ou resolvidos neste trabalho.

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
