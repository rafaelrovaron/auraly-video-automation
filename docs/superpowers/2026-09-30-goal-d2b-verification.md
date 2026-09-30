# Goal D2B — evidência de entrega local

Data: 2026-09-30. Branch: `feat/heygen-d2b`. Base: `0b01358`.
Estado: `IMPLEMENTED` e `LOCAL_VERIFIED`; não `PROVIDER_VERIFIED`.

## Verificação

- Comando: `uv run python scripts/verify.py full`, Windows.
- Resultado final: 13/13 checks; 1357 testes aprovados, 18 skips, 183.90s de pytest.
- Ruff, mypy (80 arquivos src/77 testes), schemas sem drift, dependências compatíveis,
  npm audit sem vulnerabilidades e diff whitespace aprovados.
- 85 testes na verificação focada das correções.
- Nenhuma chamada real/paga HeyGen, mudança em AGENTS.md ou arquivos sources/.
- Revisão independente do branch `0b01358..e73195e`: três achados Important, nenhum Critical.
  Uma rodada de correções TDD seguida do gate completo; sem segunda revisão independente.

## Correções da revisão

- URL expirada em três execuções: regressão falhou antes e passou depois. Reconciliação explícita
  adiciona um job local de recuperação ao mesmo render/ID, preservando os jobs antigos e auditando
  `job.video_recovery_linked`. Replay não cria outra geração ou reserva. Transições render/job são
  atômicas; limites e fingerprints originais de Jobs não foram alterados.
- Engine opcional em `allOf/anyOf/oneOf`: três regressões RED→GREEN. Inspeção recursiva rejeita engine
  selecionável também nas definições locais; o payload original continua sem engine inventado.
- Fixture de assets dependia da data fixa de uma voz sintética: falha reproduzida, worker passou
  a selecionar somente uploads de assets. Mesma correção nos dois testes D2A afetados.
- Nova verificação revelou DLL Whisper bloqueada por Application Control: teste de CLI real com
  importação Whisper indisponível RED→GREEN; import adiado até transcrição. Nenhuma política foi
  desativada ou contornada. Transcrição nativa real continua dependente desse ambiente.

## Limitações e itens menores adiados

- Ampliar testes de claims realmente simultâneos e casos individuais de codec/áudio/duração/junction.
  O total de testes aprovados não comprova cada cenário listado no roteiro original.
- Interoperabilidade OAuth/HeyGen, responses reais e custo precisam do canário D2C autorizado.
- Corridas adversariais de substituição de filesystem não são comprovadas; containment estático e
  publicação sem overwrite foram revisados. Housekeeping global de stale Jobs permanece existente.
- HyperFrames doctor retornou zero, mas avisou sobre atualização e ferramentas opcionais ausentes
  (Docker/transcrição/TTS/BGM). Nenhum upgrade fora do slice foi executado.

## Decisões de execução (registro completo)

- Ruling: app worktree tool cannot target nested repository; use ignored Git worktree — preserves isolation — cost if wrong: app cannot manage cleanup.
- Ruling: Bash scripts unavailable and Windows blocks pytest launcher; use apply_patch ledger and `uv run python -m pytest` — equivalent task evidence, no policy changes — cost if wrong: manual progress record.
- Task 2: Ruling: store immutable material/config facts in typed item_json and QC facts in source_json alongside relational identity/checkpoints — fewer duplicated columns, same auditable data — cost if wrong: JSON querying instead of scalar columns.
- Task 2: Ruling: batch repo additionally takes validate_reuse callback — fingerprint conflicts must abort before commit, not after — cost if wrong: one extra callback interface.
- Task 4: Ruling: add publication.json checkpoint before publishing source.mp4 — recovery after missing source.json must verify provenance and expected hash — cost if wrong: one small extra sidecar per render.
- Task 6: Ruling: add optional client_factory dependency to handler/service and session parameter to shared validation — real streaming tests without network, approvals rechecked in reservation transaction — cost if wrong: optional arguments only.
- Task 6: Ruling: remote default adapter sessions are opened per operation/thread; fake shared only as explicit injected dependency — matches SQLite session-per-operation pattern — cost if wrong: OAuth storage concurrent use needs future provider canary.
- Task 6: Ruling: no undocumented listing tool used for recovery; exact ID/manual confirmation required when no checkpoint — avoids assuming unsupported correlation — cost if wrong: operator must recover ID manually.
- Task 7: Ruling: harness uses Python module entry points for pytest/Ruff — Windows Application Control rejected generated launcher, no policy bypass — cost if wrong: no semantic check change, portable module invocation.
- Task 7: Ruling: scenario tests consolidate related named assertions from plan (e.g. remote-ID uniqueness in budget race, resume in polling, JSON/secrets/budget in CLI flow) — retain assertions without duplicating costly campaign fixtures — cost if wrong: scenario failure may require tracing assertion.
- Final: Ruling: load Whisper only when transcribing — HeyGen/CLI does not require its native runtime; Windows policy remains enforced — cost if wrong: runtime import errors surface when native transcription is actually needed.
- Final: Ruling: exhausted known-ID recovery gets an explicitly linked local continuation job, atomically bound to the same render/remote ID — existing attempt bounds and full old job audit remain intact, no new paid reservation/create — cost if wrong: more than one historical job per render after repeated local failures.
- Final: Ruling: promote existing in-session reconciled resume to public API with compatibility alias — atomic render/job updates without private Job internals — cost if wrong: one additional public transaction method.
- Final: Ruling: recursively reject engine properties, including compositions/local definitions — fail closed on selectable engine — cost if wrong: an unused engine definition may conservatively block a compatible tool.
- Final: Ruling: real provider interoperability deferred to D2C; static path containment reviewed, adversarial filesystem replacement races outside local MVP; global stale-job housekeeping preserved; milestone wording follows fresh baseline — matches approved scope, no evidence of healthy unrelated jobs consumed — cost if wrong: provider/path/runtime issues may surface in canary or outside the stated threat model.

## Próximo passo

Escolher integração do branch. Depois, D2C: um canário real pequeno, com aprovação específica de
consumo de créditos. UI/editor/A/B continuam no roadmap; esta entrega não os implementa.
