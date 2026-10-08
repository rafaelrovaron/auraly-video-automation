# D4B.3c Approximate Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mostrar um frame do MP4 existente com headline/caption e framing aproximados, sem render nem regeneração de assets.

**Architecture:** Um GET local entrega PNG em memória a partir de render ready identificado por campanha/ID/hash. Um componente React usa CSS e dados existentes; EditingPanel fornece rascunho ou manifest confirmado, sem duplicar o planner.

**Tech Stack:** Python 3.11, FastAPI, FFmpeg existente, React/TypeScript/CSS, pytest/Vitest/Playwright existentes. Sem novas dependências.

**Spec:** `docs/superpowers/specs/2026-10-08-d4b3c-approximate-preview-design.md` (aprovada em 2026-10-08).

## Global Constraints

- Primeiro frame decodificável; PNG proporcional de no máximo 720 px por eixo; timeout 10 s e saída limitada a 4 MiB. Encerrar subprocess ao ultrapassar limite.
- Cache-Control: no-store; sem poster em disco, Job, worker, migration ou provider pago.
- Preservar trusted roots, hashes, campanha e symlink/junction; sem path/URL/comando vindo do browser.
- Fonte de sistema com fallback sinalizado; sem player, timeline, sincronização, mixagem ou render. AGENTS.md e sources/ intocados.
- **Preview aproximado — render final é a referência.** Rascunho não valida plano nem habilita Save.
- GET somente por troca de identidade ou Recarregar frame; nenhum POST automático por edição.
- Execução recomendada: Nativa, três tarefas dependentes e uma revisão independente final. Plano ainda aguarda revisão do usuário.

## Review Focus

1. Mesmo render ID com hash alterado: não mostrar PNG antigo nem aceitar metadata divergente (tarefas 1/2).
2. Resposta atrasada após troca/unmount: ignorar resultado e revogar Object URL (tarefa 2).
3. Remover/renomear variante selecionada: seleção por identidade de linha, sem colisão ou mutação de keys/request (tarefa 3).
4. Readonly de plano antigo: usar captionInput exato, não copy atual nem draft; retorno ao draft conserva valores (tarefa 3).
5. Valores CSS extremos, texto longo e proporção não 9:16: manter viewport utilizável e avisos, sem truncamento silencioso (tarefas 2/3).

## Arquivos e responsabilidades

- Novo `src/auraly_pipeline/api/poster.py`: decode PNG bounded, sem persistência.
- `api/queries.py`: lookup público e validação da source; `api/app.py`: rota binária.
- `web/src/heygenApi.ts`: leitura binária tipada, usando ApiError/campaignPath existentes.
- Novo `web/src/EditPreview.tsx`: composição visual, precedência visual e ciclo do poster.
- `web/src/EditingPanel.tsx` e `styles.css`: integração/seleção e layout responsivo.
- Testes focados novos `tests/test_api_poster.py` e `web/src/EditPreview.test.tsx`;
  ampliar `heygenApi.test.ts`, `EditingPanel.test.tsx`, `tests/test_web_editing_e2e.py`.
- Atualizar README, memória, roadmap e PRD somente com capacidades efetivamente verificadas no fechamento.

### Task 1: GET do poster local

**Interfaces:** Criar `decode_poster(path: Path) -> bytes` em `api/poster.py`.
Adicionar `ApiQueries.get_render_poster(campaign_id: str, render_id: str, source_sha256: str) -> bytes`.
Registrar GET `/api/v1/campaigns/{campaignId}/heygen/renders/{renderId}/poster/{sourceSha256}`,
usando parâmetros tipados existentes e resposta image/png. Erros: not_found para
identidade ausente/fora da campanha; artifact_invalid para estado/hash/path/decode
inválido; storage_unavailable para FFmpeg ausente/timeout. Nunca ecoar stderr.

- [ ] Escrever `test_ready_poster_is_png_without_writes` em `tests/test_api_poster.py`,
  usando `create_ready_api_fixture(tmp_path, mp4)` e plugin `tests.test_heygen_video_media`.
  Capturar dump SQL e hashes/nomes dos arquivos antes/depois. Com `TestClient` real:

```python
assert response.status_code == 200
assert response.headers['content-type'] == 'image/png'
assert response.headers['cache-control'] == 'no-store'
assert response.content.startswith(b'\x89PNG\r\n\x1a\n')
assert 0 < len(response.content) <= 4 * 1024 * 1024
width, height = struct.unpack('>II', response.content[16:24])
assert 0 < max(width, height) <= 720
assert after_files == before_files and after_sql == before_sql
```

- [ ] Rodar `rtk uv run pytest tests/test_api_poster.py -q`: RED esperado 404 da rota ausente.
- [ ] Implementar interfaces. Usar `self.campaign`, `self.renders.get`,
  `validate_editing_path`, `relative_path`, hash streaming hashlib e tamanho
  da source; conferir render.item.campaign_id, ready e source. Não ler internals
  de Job nem chamar o planner. FFmpeg via Popen/argumentos separados, stdin DEVNULL,
  stdout pipe, sem shell; limitar leitura em memória e matar/reap subprocess em
  limite/timeout. Descartar stderr sem armazenamento ilimitado. Sem cache/locks novos.
- [ ] Acrescentar regressões: `test_poster_rejects_wrong_campaign_hash_and_state`,
  `test_poster_rejects_missing_changed_or_linked_source`,
  `test_decode_bounds_and_sanitizes_failures`, `test_poster_keeps_http_boundary`.
  Assertar 404/409/503 conforme mapeamento, queries=422, host externo=400,
  limite exato aceito e limite+1 rejeitado; processo encerrado em timeout/overflow;
  mensagens não contêm path privado/stderr. Cobrir dimensões portrait/landscape,
  arquivo substituído com metadata antiga, POSIX e Windows reparse guard existente.
- [ ] GREEN: `rtk uv run python scripts/verify.py fast --pytest tests/test_api_poster.py tests/test_api_http.py`.
  Todos os testes e checks do comando passam; ampliar schemas somente se a exportação existente exigir.
- [ ] Commit dos quatro arquivos backend/test: `feat: serve bounded local render poster`.

### Task 2: Preview visual e leitura do frame

**Interfaces:** Em `heygenApi.ts`, exportar
`getRenderPoster(campaignId: string, renderId: string, sha256: string, signal: AbortSignal): Promise<Blob>`.
Em `EditPreview.tsx`, exportar `PreviewSource={campaignId:string;renderId:string;sha256:string}`,
`previewSettings(base: OverrideHints, campaign: OverrideDraft, video: OverrideDraft, variant: OverrideDraft): OverrideHints|null`
e `EditPreview({source,settings,captionText,mode,timingMissing})`, com source/settings
nullable, captionText string, mode `'draft'|'validated'|'saved'`, timingMissing boolean.
Types OverrideHints/OverrideDraft são importados do formulário existente.

- [ ] Em `heygenApi.test.ts`, escrever `poster_returns_only_bounded_png`:
  assertar rota codificada correta, signal/GET, Blob PNG <=4 MiB; rejeitar JSON,
  MIME errado, zero bytes, assinatura PNG inválida e erro HTTP como ApiError,
  sem eco de body arbitrário. Não usar reader JSON para resposta binária.
- [ ] Em `EditPreview.test.tsx`, escrever `draft_preview_preserves_layers_and_updates_without_posts`:
  fixtures `newProfile`/`newOverrideDraft`, headline base, override A/B, enabled true.

```tsx
expect(screen.getByText('Preview aproximado — render final é a referência.')).toBeVisible();
expect(screen.getByText('Rascunho não validado')).toBeVisible();
expect(screen.getByText('A')).toBeVisible();
expect(fetchMock.mock.calls.filter(([, options])=>options?.method==='POST')).toHaveLength(0);
```

- [ ] RED: `rtk npm --prefix web test -- src/EditPreview.test.tsx src/heygenApi.test.ts`.
  Esperar import/interface ausente ou comportamento não implementado, não falha de fixture.
- [ ] Implementar leitura binária e componente. `previewSettings` usa readOverrides,
  merge por seção/campo somente para visualização; null se input inválido. Não
  calcula hash, duração, fit final ou aprovação. CSS escala canvas lógico inteiro;
  object-fit/object-position e scale*zoomStart para frame; anchors/safe zones e
  estilos do texto conforme spec. Renderizar texto como React text, sem HTML.
- [ ] Implementar fetch abortável por source identity, token contra respostas antigas,
  Object URL revogada em troca/unmount e botão Recarregar frame. Sem polling/retry
  automático, POST, leitura de arquivo ou cópia do MP4 ao public/. Frame falho =
  fundo neutro + aviso; nunca frame antigo. Overlay changes não refazem GET.
- [ ] Testes `preview_clears_stale_frame_and_revokes_urls`,
  `visual_layers_preserve_null_false_zero_rgba`, `preview_warns_instead_of_hiding_overflow`:
  mesma renderId/novo hash limpa frame; late Blob/unmount não atualiza; refetch só explícito;
  disabled esconde overlay; valores inválidos não viram defaults. Confirmar avisos
  de fonte fallback, timing, highlight, zoomEnd, shrink/error, frame indisponível;
  maxLines não oculta excesso silenciosamente e layout usa output ratio real.
- [ ] GREEN: repetir Vitest acima e `rtk npm --prefix web run build`.
- [ ] Commit de componente/testes/CSS/client: `feat: add approximate static edit preview`.

### Task 3: Integração editorial, browser e fechamento

**Interfaces:** EditingPanel fornece uma única área EditPreview com seleção de
modo e variante. Rascunho usa row estável e settings da tarefa 2; planos usam
manifest do output selecionado como OverrideHints, source via plan.renderId/
source.sha256. Caption sample = primeiro cue, senão primeiras 12 palavras de
captionInput.text; rascunho usa string fixa `Texto demonstrativo de legenda`.
Não acessar copy atual, restaurar request de plano ou acrescentar campos persistidos.

- [ ] Escrever `preview_selection_does_not_mutate_editorial_request` e
  `readonly_preview_uses_exact_plan_without_overwriting_draft` em EditingPanel.test.tsx.
  Assertar três headlines distintas; editar invalida validated e Save; selecionar
  preview não altera POST/request, musicAccepted, worker nem dirty. Remover/renomear
  variante mantém seleção válida por row. Plano antigo usa captionInput do plano.
- [ ] RED: `rtk npm --prefix web test -- src/EditingPanel.test.tsx`.
- [ ] Integrar área e seletores com labels, sem novos inputs de edição. Alternar
  draft/validated/saved não modifica fonte/profile do formulário. Não mostrar
  resultado validated antigo após edição; readonly permanece identificado como
  plano consultado. Uma única instância evita GET por output listado. Não refatorar
  submissão, worker, Save ou navegação D4B.3b fora do necessário.
- [ ] Em `tests/test_web_editing_e2e.py`, ampliar teste das três headlines e criar
  `test_preview_frame_failure_and_readonly_keep_draft`,
  `test_preview_narrow_width_and_long_text`. Usar API/proxy/browser e MP4 sintético
  reais; usar font fixture existente se habilitar texto durante validação.
  Assertar image naturalWidth>0, screenshot visual inspecionada, A/B/C na área,
  nenhum POST por tecla/seleção, count GET estável e hashes upstream/SQL budget
  iguais. Interceptar resposta atrasada/falha; testar overflow e teclado em 320/390 px.
- [ ] GREEN: `rtk npm --prefix web test` e
  `rtk uv run pytest tests/test_web_editing_e2e.py tests/test_api_poster.py -q`.
- [ ] Commit da integração e regressões: `feat: integrate draft and saved-plan previews`.
- [ ] Executar `rtk uv run python scripts/verify.py full`; exigir 19/19 e exit 0.
  Revisar diff/segredos/generated media e fazer uma revisão independente final.
  Corrigir somente achados pertinentes com RED→GREEN; repetir gate se código mudar.
- [ ] Atualizar documentação/evidência com resultados reais, separando rascunho,
  preview aproximado, fonte fallback e renderer D5 ainda planejado. Sem afirmar
  Linux/PROVIDER_VERIFIED por teste local. Commit documental pequeno.

## Handoff

Revisar este plano antes de executar. Preferência anterior do projeto: Nativa;
recomendação preservada para este recorte. Implementação só após aprovação do
plano. Merge/push dependem de autorização no fechamento; não estão implícitos
na aprovação do design. O cancelamento do Actions Linux anterior continua
pendência de evidência remota, não justificativa para mudar CI nesta etapa.
