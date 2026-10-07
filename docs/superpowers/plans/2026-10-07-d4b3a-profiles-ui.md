# D4B.3a Profiles UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Criar, consultar e versionar profiles de edição por formulário local, sem JSON manual, Jobs ou chamadas de provider.

**Architecture:** Cliente tipado e formulário React sobre os quatro endpoints de profiles existentes. Navegação global `#/profiles`, versões publicadas imutáveis e rascunhos em memória. API e serviços existentes são a fonte de verdade; não modificar backend ou contratos.

**Tech Stack:** React 19.3, TypeScript 5.9, Vite/Vitest/Testing Library existentes; FastAPI/Pydantic e pytest/Playwright existentes. Nenhuma dependência nova.

**Spec:** `docs/superpowers/specs/2026-10-07-d4b3a-profiles-ui-design.md`, aprovada em 2026-10-07.

## Global Constraints

- EditProfile/schemaVersion 1.0, ProfileView/profileHash; aliases camelCase e objeto completo tipado.
- Apenas GET lista, GET ID/versão, POST criação e POST baseVersion/versions existentes.
- Versão nova = base + 1, sem latest, overwrite ou busca automática de versão livre.
- Defaults atuais: 1080×1920/30, texto/música disabled, headline top/y=0.1, captions bottom/y=0.8, framing cover.
- Pixels do canvas, frações 0–1, segundos, dB, escala/zoom 1–1.25; cor RGB/RGBA hexadecimal.
- Path relativo ao project root + SHA-256 explícito para AssetRef; sem upload, acesso a disco ou hash no browser.
- `validate_assets=False` preservado: publicação não valida disponibilidade da mídia nem aceita seu uso.
- Sem variantes, preview, render, autosave, localStorage, catálogo, novos endpoints/migrations/Jobs/dependências.
- Rascunho em memória, confirmação de descarte, sem substituição por atualização da lista.
- Resultado incerto não autoriza POST automático; consulta exata e payload congelado.
- Preservar AGENTS.md, sources/, providers e mídia operacional; edits por apply_patch, comandos por rtk.

## Review Focus

- Resposta de consulta antiga chega após seleção nova: não pode trocar o profile atual — Task 3.
- Versão destino aparece entre not_found e tentativa explícita: consultar novamente e não sobrescrever — Task 3.
- RGBA, false, zero e campos disabled desaparecem ao versionar: conservar valores — Tasks 1/2.
- HTTP 201 com outro ID/versão ou payload malformado: resultado incerto, não sucesso — Tasks 1/3.
- Navegação de volta ou troca interna com rascunho: confirmação recusada mantém os valores — Tasks 3/4.

## Arquivos e interfaces

Criar `web/src/profileApi.ts` (contratos/cliente), `ProfileForm.tsx` (inputs e
validação), `ProfilePanel.tsx` (fluxo e estados). Criar respectivos `.test.ts`
ou `.test.tsx` e `web/src/profileTestSupport.ts` (fixtures somente para testes).
Modificar `web/src/App.tsx`, `App.test.tsx`, `styles.css` pontualmente.
Criar `tests/test_web_profiles_e2e.py`; reutilizar `tests/web_panel_support.py`
e `panel_page` de `tests/test_web_panel_e2e.py`, sem novo servidor de testes.
Documentação final: README e os três documentos de produto, spec/plano e
`docs/superpowers/2026-10-07-d4b3a-verification.md`.

### Task 1: Contratos completos e cliente de profiles

**Files:** `profileApi.ts`, `profileApi.test.ts`, `profileTestSupport.ts`.

**Interfaces:** Exportar tipos `AssetRef`, `TextStyle`, `HeadlineStyle`,
`CaptionStyle`, `OutputStyle`, `MusicStyle`, `FramingStyle`, `EditDefaults`,
`EditProfile`, `ProfileView` conforme schema atual. Exportar
`profileView(value: unknown): value is ProfileView`,
`newProfile(profileId: string, name: string, createdAt: string): EditProfile`,
`sameProfileContent(a: EditProfile, b: EditProfile): boolean`,
`listProfiles(signal: AbortSignal): Promise<ProfileView[]>`,
`getProfile(id: string, version: number, signal: AbortSignal): Promise<ProfileView>`,
`publishProfile(profile: EditProfile, baseVersion?: number): Promise<ProfileView>`.
Reutilizar `read`, `post`, `object`, `ApiError` de api.ts. Comparação por campos
conhecidos completos, independente de ordem de chaves, excluindo somente createdAt;
sem SHA próprio. Validador não aplica defaults a respostas incompletas.

- [x] Escrever `defaults_match_contract`, `rejects_invalid_profile_view`,
  `reads_exact_identity`, `malformed_post_is_unknown`, `content_ignores_only_date`.
  Assertions: `newProfile('plain','Plain',date).defaults.output.width === 1080`;
  `defaults.captions.y === 0.8`; `profileView({...valid,profileHash:'bad'}) === false`;
  `sameProfileContent(a,{...a,createdAt:otherDate}) === true`; mudar RGBA ou
  volumeDb torna false. Rejeitar extras, NaN/Infinity, tipos errados, campos
  faltantes, schema errado, ID reservado, version não inteiro/positivo,
  data sem timezone, AssetRef incompleto/URL/traversal/drive e safe zones inválidas.
  GET errado é invalid_response; POST 201 com ID/versão/conteúdo divergente é
  command_unknown; caminhos usam encodeURIComponent. Backend permanece autoridade.
- [x] RED: `rtk npm --prefix web test -- src/profileApi.test.ts`; confirmar
  falha causada pelo módulo/comportamento ausente, não pelo ambiente.
- [x] Implementar tipos/defaults/validação e wrappers; constranger resposta de
  publicação ao payload enviado. Manter erro 4xx explícito, desconhecido para
  transporte/5xx/resposta inválida, sem retry. Fixture `PROFILE` e função
  `profileFixture(): ProfileView` fornecem cópia independente em cada teste.
- [x] GREEN: mesmo comando e `rtk npm --prefix web run typecheck`; ambos exit 0.
- [x] Revisar diff e commit `feat: add typed editing profile client`.

### Task 2: Formulário completo, sem perda de valores

**Files:** `ProfileForm.tsx`, `ProfileForm.test.tsx`, styles.css apenas se necessário.

**Interfaces:** `ProfileForm({initial, readOnly, disabled, onSubmit, onDirtyChange})`;
initial: EditProfile; flags boolean; callbacks `(profile: EditProfile) => void`
e `(dirty: boolean) => void`. ID editável só em versão 1 nova; distinguir esse
caso por prop `creating: boolean`, não pelo número da versão publicada.
Versão/data não editáveis. Rascunho numérico como texto até validar; componente
montado com key do rascunho para reset explícito, não por polling.

- [x] Escrever `submits_all_style_fields`, `preserves_disabled_values_and_alpha`,
  `blank_required_number_is_not_zero`, `nullable_empty_is_null`,
  `rejects_partial_asset_reference`, `readonly_cannot_submit`.
  Assertions por onSubmit: volumeDb=0, loop=false, color='#FFFFFF80' conservados;
  endSec vazio resulta null; fontSizePx vazio não chama onSubmit; x=1.1 e
  safeLeft+safeRight>=1 falham; scale/zoom fora de 1–1.25 falham; path sem hash
  falha. Fonte ausente continua null, sem instalar/sugerir fonte presumida.
- [x] RED: `rtk npm --prefix web test -- src/ProfileForm.test.tsx`.
- [x] Implementar seções com labels, inputs nativos e details para avançados;
  todos os campos listados na spec. Preservar cor alpha com input textual;
  números finitos/inteiros conforme campo, peso 100–900, tempos não negativos,
  fim nullable positivo, intervalos conhecidos crescentes e safe zones válidas.
  Sem duração fictícia ou exigência de arquivo existente. Path/hash opcionais
  como par, mensagens sanitizadas, foco no primeiro erro. Mostrar aviso fixo
  de assets e informar que música/timing ainda não foram aprovados/verificados.
- [x] GREEN: teste focado e `rtk npm --prefix web run typecheck`.
- [x] Revisar diff e commit `feat: add editing profile form`.

### Task 3: Navegação, publicação e recuperação explícita

**Files:** `ProfilePanel.tsx`, `ProfilePanel.test.tsx`, App.tsx/App.test.tsx/styles.css.

**Interfaces:** `ProfilePanel(): React.JSX.Element`, consome cliente Task 1 e
formulário Task 2. App adiciona Route `{page:'profiles'}` e parseRoute aceita
somente `#/profiles` exato. Estado local separa lista, seleção confirmada,
rascunho e submissão congelada; não usa worker nem status de campanha.

- [x] Escrever `lists_and_versions_profiles`, `conflict_never_overwrites`,
  `unknown_queries_exact_version_without_repost`, `not_found_retry_rechecks`,
  `late_get_does_not_replace_selection`, `refresh_preserves_draft`,
  `discard_cancel_keeps_draft`, `published_content_is_readonly`.
  Assertions: criar usa POST /profiles versão 1; versionar base 1 usa
  POST /profiles/plain/1/versions versão 2; primeira versão intacta.
  Em 503 gravado, número de POST permanece 1 após consulta correspondente;
  GET falho não libera novo save; resposta de ID errado não confirma sucesso.
  Antes de retry explícito após 404, GET encontra conflito e nenhum segundo
  POST ocorre. GET atrasado de A não substitui B, nem após sair da rota.
- [x] RED: `rtk npm --prefix web test -- src/ProfilePanel.test.tsx src/App.test.tsx`.
- [x] Implementar lista/consulta readOnly/novo/versão; loading/empty/error e
  estados pending/confirmed/unknown/rejected. Bloquear clique duplo, congelar
  timestamp/payload, consultar publicação e refrescar lista sem perder edição.
  Resultado desconhecido oferece consulta exata; comparação ignora createdAt
  somente. Ausência confirmada permite tentativa explícita com GET prévio e
  mesmo payload. Conflito não renumera; informar artifact_invalid sem fingir
  distinguir corrupção de conflito sem consulta válida. Não reexecutar POST.
  Reaproveitar useUnsavedChanges, guardas internas de descarte e AbortController
  com verificação da seleção antes de aplicar respostas. Sem polling necessário.
- [x] GREEN: teste focado, `rtk npm --prefix web test`,
  `rtk npm --prefix web run build`; todos exit 0.
- [x] Revisar diff e commit `feat: manage immutable editing profiles in local UI`.

### Task 4: Browser/API reais, reload e telas estreitas

**Files:** `tests/test_web_profiles_e2e.py`; helpers existentes só se necessários.

**Interfaces:** Fixtures `panel_servers: PanelServers`, `panel_page: Page` existentes.
Importar fakes de suporte para dependências da fixture; nunca construir provider
real. Consultar profiles pela app.state.queries e snapshots pelos paths da spec.

- [x] Escrever `test_profiles_create_version_and_reload`: criar plain v1,
  mudar fonte/cor/música/framing em v2, verificar ambas via API e comparar bytes
  de profile.json v1 antes/depois; reload mantém conteúdo v2, nenhum Job novo
  e provider.events inalterado. Referências sintéticas são somente dados do profile.
  `test_saved_profile_with_lost_response`: route.fetch efetua POST real, substitui
  resposta por 503; consulta recupera sem outro POST; não mockar persistência.
  `test_profile_conflict_preserves_existing`, `test_profiles_discard_navigation`
  verificam conflito sem overwrite e cancelamento de diálogo na volta/troca interna.
  `test_profiles_narrow_width` parametrizado [320,390]: assert
  document.documentElement.scrollWidth <= window.innerWidth; labels/teclado
  acessíveis, texto longo de ID/nome/path não alarga viewport.
- [x] RED: `rtk uv run python -m pytest tests/test_web_profiles_e2e.py -q`.
  Se os fluxos funcionarem de primeira, não inventar falha; registrar integração
  GREEN e manter evidência RED→GREEN das tarefas anteriores. Correções novas
  exigem teste falhando pelo problema observado antes do patch.
- [x] Corrigir apenas comportamento demonstrado, sem retries/timeouts genéricos;
  preservar teste real de overflow e validações de persistência.
- [x] GREEN: mesmo comando; `rtk uv run python scripts/verify.py fast --pytest tests/test_web_profiles_e2e.py tests/test_api_actions_http.py tests/test_api_queries.py`.
- [x] Revisar diff e commit `test: verify profile UI persistence and recovery`.

### Task 5: Gate completo, revisão e documentação de entrega

**Files:** README.md, docs/PROJECT-MEMORY.md, docs/GOAL-ROADMAP.md,
docs/PRD-MVP-MASS-VIDEO-AUTOMATION.md, spec/plano e documento de evidência acima.

- [x] Rodar `rtk uv run python scripts/verify.py full`; exigir todos os steps PASS
  e registrar contagens reais frontend/Python e skips, sem aproveitar evidência antiga.
- [x] Revisão independente única da branch completa conforme AGENTS.md e método
  escolhido; foco nos cinco casos acima, paridade de contratos, perda de dados,
  semântica unknown e fronteiras de assets. Corrigir findings relevantes com
  regressão RED→GREEN, commits pequenos e gate completo repetido após mudanças.
- [x] Documentar somente D4B.3a entregue, caminho operacional, versões imutáveis,
  referência técnica path/hash e limite validate_assets=False. Variantes/preview
  D4B.3b/c e renderer D5 continuam PLANNED; nenhum PROVIDER_VERIFIED novo.
  Atualizar status/spec e checkboxes para refletir a execução real.
- [x] Revisar diff por secrets/mídia/paths privados/escopo; `rtk git diff --check`;
  commit `docs: record local editing profile UI verification`.
- [x] Handoff com evidência e próximo recorte. Não mergear/push sem pedido do usuário.

## Execução e self-review

Nativa previamente escolhida pelo usuário no projeto; recomendada também aqui:
três tarefas funcionais sequenciais, dependentes dos mesmos contratos, seguidas
de integração e uma revisão independente final. Confirmar que a preferência
continua antes da implementação (confirmada em 2026-10-07). Usar executing-plans e using-git-worktrees
na execução, criando/reutilizando checkout isolado com spec e plano incluídos;
não trabalhar a implementação em main nem apagar scratch de outros Goals.

Self-review: campos/contratos → Tasks 1/2; navegação/versões/unknown/rascunhos →
Task 3; API/persistência/reload/responsividade → Task 4; docs/gate/revisão → Task 5.
Os cinco Review Focus têm testes nomeados nas tarefas proprietárias. Interfaces
consumidas coincidem com as produzidas; nenhuma capability de variants/preview
ou validação de mídia foi adicionada à publicação. Plano aprovado em 2026-10-07;
Tasks 1–5 concluídas com TDD/commits, integração, revisão independente e gate final
19/19: 275 frontend e 1.800 Python / 25 skips. Handoff sem merge/push.
Evidência: `docs/superpowers/2026-10-07-d4b3a-verification.md`.
