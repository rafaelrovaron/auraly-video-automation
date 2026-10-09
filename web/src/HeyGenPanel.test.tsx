import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { CampaignDetailPanel } from './CampaignPanel';

const time = '2026-10-05T00:00:00Z';
const copy = {copyMasterId: 'copy-one', version: 1, approvalState: 'approved', approvedBy: 'tester', sourceText: 'Source',
  sha256: 'a'.repeat(64), headline: 'Headline', hook: 'Hook', body: 'Body', cta: 'CTA'};
const scene = {sceneVariantId: 'scene-one', variantId: 'scene-one', location: 'Room', action: 'Talk', prompt: 'Portrait', timeAtmosphere: null, proofObject: null};
const summary = {campaignId: 'campaign-one', character: 'susan-smith', storedStatus: 'draft', sceneCount: 1,
  createdAt: time, updatedAt: time, operationalStatus: 'ready_for_editing', nextPending: null};
const detail = {...summary, proofObject: 'cards', voicePreset: 'voice', editPreset: 'edit', copyMasters: [copy], sceneVariants: [scene]};
const status = {...summary, approvedCopyCount: 1, approvedVoiceCount: 1, approvedImageCount: 1, readyRenderCount: 0, planCount: 0,
  scenes: [{sceneVariantId: 'scene-one', variantId: 'scene-one', currentCopyId: 'copy-one', currentVoiceId: 'voice-one', approvedImageId: 'image-one', readyRenderIds: [], planHashes: [], pending: []}]};
const voice = {voiceMasterId: 'voice-one', campaignId: 'campaign-one', copyMasterId: 'copy-one', copyMasterVersion: 1, generation: 1,
  provider: 'imported', status: 'approved', processedAudioPath: 'voice/processed.wav', processedSha256: 'b'.repeat(64), durationSeconds: 1,
  transcriptMatchStatus: 'matched', headlineSpoken: false, qcFindings: [], approvedAt: time, approvedBy: 'tester', approvalReviewReason: null,
  rejectedAt: null, rejectedBy: null, rejectionReason: null, createdAt: time, updatedAt: time};
const image = {imageCandidateId: 'image-one', sceneVariantId: 'scene-one', sourceKind: 'manual', reviewStatus: 'approved',
  sourcePath: 'images/one.png', sha256: 'a'.repeat(64), width: 1080, height: 1920, sizeBytes: 100, format: 'png', rejectionReason: null};
const child = {jobId: 'child-one', campaignId: 'campaign-one', sceneVariantId: null, jobType: 'heygen.asset.upload', status: 'queued',
  attemptCount: 0, maxAttempts: 2, retrySafety: 'reconcile_before_retry', queuedAt: time, startedAt: null, completedAt: null, nextRetryAt: null, lastErrorCode: null};
const assetResult = {operation: 'heygen_assets', uploadCount: 2, reusedCount: 0, jobId: 'child-one'};
type Call = {path: string; body: Record<string, unknown>};
function fakeApi(options: {result?: unknown; operation?: string; submit?: (path: string, body: Record<string, unknown>) => Promise<Response>} = {}) {
  const calls: Call[] = [];
  const data: Record<string, unknown> = {'': detail, '/status': status, '/images': {items: [{sceneVariantId: 'scene-one', items: [image]}]},
    '/voices': {items: [voice]}, '/jobs': {items: [child]}, '/heygen/renders': {items: []}, '/budget': {state: 'configured', currency: 'USD', limitCents: 1000},
    '/worker': {state: 'idle', campaignId: null, kind: null, errorCode: null}};
  let wrapper: unknown = {jobId: 'wrapper-one', campaignId: 'campaign-one', operation: options.operation ?? 'heygen_assets',
    status: 'completed', errorCode: null, result: options.result === undefined ? assetResult : options.result};
  vi.stubGlobal('fetch', async (path: string, request: RequestInit) => {
    if (request.method === 'POST') {
      const body = JSON.parse(String(request.body)); calls.push({path, body});
      return options.submit ? options.submit(path, body) : Response.json({jobId: 'wrapper-one', campaignId: 'campaign-one', operation: body.operation});
    }
    if (path.includes('/operations/')) return wrapper instanceof Response ? wrapper : Response.json(wrapper);
    const body = data[path.replace('/api/v1/campaigns/campaign-one', '')];
    return body instanceof Response ? body : Response.json(body);
  });
  return {calls, data, setWrapper: (value: unknown) => {wrapper = value;}};
}
const prepare = () => screen.getByRole('button', {name: 'Preparar assets HeyGen'});
async function openPanel() {
  // Flush initial reads, including HeyGen's request after campaign detail, before role queries.
  await act(async () => { render(<CampaignDetailPanel campaignId="campaign-one" />); });
  await waitFor(() => expect((prepare() as HTMLButtonElement).disabled).toBe(false));
}

it('test_prepare_enqueues_wrapper_without_start', async () => {
  const api = fakeApi(); await openPanel(); fireEvent.click(prepare());
  expect(await screen.findByText(/Uploads planejados: 2/)).toBeTruthy();
  expect(api.calls).toHaveLength(1);
  expect(api.calls[0].body).toEqual({operation: 'heygen_assets', campaignId: 'campaign-one', requestId: expect.stringMatching(/^[0-9a-f-]{36}$/)});
  expect(api.calls.some(call => call.path.endsWith('/worker/start'))).toBe(false);
  expect(screen.getByText(/Inicie heygen_assets explicitamente/)).toBeTruthy();
});

it.each(['failed', 'blocked'])('test_assets_child_failure_is_not_completion (%s)', async failure => {
  const api = fakeApi(); api.data['/jobs'] = {items: [{...child, status: failure}]}; await openPanel(); fireEvent.click(prepare());
  expect(await screen.findByText(failure === 'failed' ? 'Upload falhou; consulte o Job filho.' : 'Upload bloqueado; consulte o Job filho.')).toBeTruthy();
  expect(screen.queryByText('Upload concluído.')).toBeNull();
});

it('test_reused_assets_without_child_do_not_offer_upload_start', async () => {
  fakeApi({result: {...assetResult, uploadCount: 0, reusedCount: 2, jobId: null}}); await openPanel(); fireEvent.click(prepare());
  expect(await screen.findByText(/Nenhum upload novo enfileirado/)).toBeTruthy();
  expect(screen.queryByText(/Inicie heygen_assets explicitamente/)).toBeNull();
});

it('test_unknown_persisted_response_never_reposts_or_claims_identity', async () => {
  const api = fakeApi({submit: async () => Response.json({error: {code: 'internal_error'}}, {status: 503})});
  await openPanel(); fireEvent.click(prepare());
  expect(await screen.findByText(/Resultado do comando desconhecido/)).toBeTruthy();
  fireEvent.click(prepare()); expect(api.calls).toHaveLength(1); expect((prepare() as HTMLButtonElement).disabled).toBe(true);
});

it.each([
  {jobId: 'wrapper-one', campaignId: 'foreign', operation: 'heygen_assets', status: 'completed', result: assetResult, errorCode: null},
  {jobId: 'wrong-job', campaignId: 'campaign-one', operation: 'heygen_assets', status: 'completed', result: assetResult, errorCode: null},
  {jobId: 'wrapper-one', campaignId: 'campaign-one', operation: 'heygen_video_plan', status: 'completed', result: null, errorCode: null},
  {jobId: 'wrapper-one', campaignId: 'campaign-one', operation: 'heygen_assets', status: 'completed', result: null, errorCode: null},
])('test_wrong_wrapper_never_adopts_child ($campaignId/$jobId/$operation)', async wrapper => {
  const api = fakeApi(); api.setWrapper(wrapper); await openPanel(); fireEvent.click(prepare());
  expect(await screen.findByText(/Leitura da operação indisponível/)).toBeTruthy();
  expect(screen.queryByText(/Uploads planejados:/)).toBeNull();
});

it('test_missing_or_foreign_child_never_advances_phase', async () => {
  const api = fakeApi(); api.data['/jobs'] = {items: []}; await openPanel(); fireEvent.click(prepare());
  expect(await screen.findByText(/Job filho ainda não comprovado/)).toBeTruthy();
  expect(screen.queryByText(/Inicie heygen_assets explicitamente/)).toBeNull();
});

it('test_pending_clicks_submit_once', async () => {
  let release!: (response: Response) => void;
  const api = fakeApi({submit: () => new Promise(resolve => {release = resolve;})}); await openPanel();
  fireEvent.click(prepare()); fireEvent.click(prepare()); expect(api.calls).toHaveLength(1);
  await act(async () => {release(Response.json({jobId: 'wrapper-one', campaignId: 'campaign-one', operation: 'heygen_assets'}));});
});

it('test_duplicate_jobs_block_commands', async () => {
  const api = fakeApi(); api.data['/jobs'] = {items: [child, child]};
  render(<CampaignDetailPanel campaignId="campaign-one" />);
  expect(await screen.findByText(/Dados desatualizados ou indisponíveis/)).toBeTruthy();
  fireEvent.click(prepare()); expect(api.calls).toHaveLength(0);
});

it('test_stale_child_read_preserves_completed_fact_without_offering_start', async () => {
  const api = fakeApi(); api.data['/jobs'] = {items: [{...child, status: 'completed'}]};
  await openPanel(); fireEvent.click(prepare()); expect(await screen.findByText('Upload concluído.')).toBeTruthy();
  api.data['/jobs'] = Response.json({}, {status: 503}); fireEvent.click(screen.getByRole('button', {name: 'Atualizar'}));
  expect(await screen.findByText(/Job filho ainda não comprovado/)).toBeTruthy();
  expect(screen.queryByText(/Inicie heygen_assets explicitamente/)).toBeNull();
});

it('test_unmounted_campaign_ignores_late_post', async () => {
  let release!: (response: Response) => void;
  const api = fakeApi({submit: () => new Promise(resolve => {release = resolve;})});
  const panel = render(<CampaignDetailPanel campaignId="campaign-one" />);
  await waitFor(() => expect((prepare() as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(prepare()); panel.unmount();
  await act(async () => release(Response.json({jobId: 'wrapper-one', campaignId: 'campaign-one', operation: 'heygen_assets'})));
  expect(api.calls).toHaveLength(1); expect(screen.queryByText(/Operação: wrapper-one/)).toBeNull();
});

const planResult = {operation: 'heygen_video_plan', newCount: 1, reusedCount: 0, reservedCount: 2,
  maxPaidRenders: 3, totalAudioSeconds: 1, sceneVariantIds: ['scene-one']};
const reservation = {renderId: 'render-one', campaignId: 'campaign-one', sceneVariantId: 'scene-one', imageCandidateId: 'image-one',
  voiceMasterId: 'voice-one', jobId: 'video-job', status: 'planned', remoteVideoId: null, source: null, errorCode: null,
  manualBinding: false, imageSha256: 'a'.repeat(64), audioSha256: 'b'.repeat(64), createdAt: time, updatedAt: time};
function fakePlanApi(result: unknown = planResult, publishJob = true) {
  const api = fakeApi({submit: async (_path, body) => {
    const jobId = body.operation === 'heygen_video_plan' ? 'plan-job' : 'submit-job';
    if (body.operation === 'heygen_video_submit' && publishJob) api.data['/jobs'] = {items: [{...child, jobId: 'video-job', jobType: 'heygen.video.generate', sceneVariantId: 'scene-one'}]};
    api.setWrapper({jobId, campaignId: 'campaign-one', operation: body.operation, status: 'completed', errorCode: null,
      result: body.operation === 'heygen_video_plan' ? result : {operation: 'heygen_video_submit', renders: [reservation]}});
    return Response.json({jobId, campaignId: 'campaign-one', operation: body.operation});
  }});
  return api;
}
const planButton = () => screen.getByRole('button', {name: 'Planejar batch HeyGen'});
const submitButton = () => screen.getByRole('button', {name: 'Enfileirar geração HeyGen'});
async function planBatch() {
  await openPanel(); fireEvent.change(screen.getByLabelText('Limite total de renders reservados da campanha'), {target: {value: '3'}});
  fireEvent.click(planButton()); await screen.findByText(/Novos: 1/);
}
function confirmPaid() {
  fireEvent.change(screen.getByLabelText('Responsável pela geração HeyGen'), {target: {value: 'tester'}});
  fireEvent.click(screen.getByLabelText(/Autorizo a geração paga/));
}
it('test_invalid_later_plan_read_blocks_paid_submit_and_preserves_confirmation', async () => {
  const api = fakePlanApi(); await planBatch(); confirmPaid();
  api.setWrapper({jobId: 'wrong-job', campaignId: 'campaign-one', operation: 'heygen_video_plan', status: 'completed', errorCode: null, result: planResult});
  fireEvent(document, new Event('visibilitychange'));
  await screen.findByText(/Leitura da operação indisponível/);
  expect((submitButton() as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByLabelText(/Autorizo a geração paga/) as HTMLInputElement).checked).toBe(true);
  expect(screen.getByText(/Novos: 1/)).toBeTruthy();
  api.setWrapper({jobId: 'plan-job', campaignId: 'campaign-one', operation: 'heygen_video_plan', status: 'completed', errorCode: null, result: planResult});
  fireEvent(document, new Event('visibilitychange'));
  await waitFor(() => expect((submitButton() as HTMLButtonElement).disabled).toBe(false));
  expect(api.calls).toHaveLength(1);
});
it('test_plan_never_authorizes_generation', async () => {
  const api = fakePlanApi(); await planBatch();
  expect((submitButton() as HTMLButtonElement).disabled).toBe(true); expect(api.calls).toHaveLength(1);
  expect(api.calls[0].body).toEqual({operation: 'heygen_video_plan', campaignId: 'campaign-one', requestId: expect.any(String), maxPaidRenders: 3,
    config: {schemaVersion: 1, generationMode: 'image', engineSelection: 'provider_default', aspectRatio: '9:16', resolution: '1080p',
      outputFormat: 'mp4', fit: 'cover', expressiveness: 'medium', motionPrompt: null, concurrency: 2,
      pollInitialSeconds: 10, pollMaxSeconds: 60, pollTimeoutSeconds: 1800}});
});
it('test_submit_uses_confirmed_plan_config_and_separate_request_id', async () => {
  const api = fakePlanApi(); await planBatch(); confirmPaid(); fireEvent.click(submitButton());
  expect(await screen.findByText(/Reserva: render-one/)).toBeTruthy(); expect(api.calls).toHaveLength(2);
  expect(api.calls[1].body).toEqual({...api.calls[0].body, operation: 'heygen_video_submit', approvedBy: 'tester', requestId: expect.any(String)});
  expect(api.calls[1].body.requestId).not.toBe(api.calls[0].body.requestId);
  expect(api.calls.some(call => call.path.endsWith('/worker/start'))).toBe(false);
});
it.each(['type', 'scene'])('test_submit_rejects_contradictory_child_job (%s)', async mismatch => {
  const api = fakePlanApi(planResult, false);
  api.data['/jobs'] = {items: [{...child, jobId: 'video-job', jobType: mismatch === 'type' ? 'heygen.asset.upload' : 'heygen.video.generate',
    sceneVariantId: mismatch === 'scene' ? 'foreign' : 'scene-one'}]};
  await planBatch(); confirmPaid(); fireEvent.click(submitButton());
  await screen.findByText(/Leitura da operação indisponível/);
  expect(screen.queryByText(/Reservas aceitas/)).toBeNull();
  expect(screen.getByText(/Novos: 1/)).toBeTruthy();
  expect((screen.getByLabelText(/Autorizo a geração paga/) as HTMLInputElement).checked).toBe(true);
  expect((submitButton() as HTMLButtonElement).disabled).toBe(true);
});
it('test_submit_waits_for_observed_child_before_adopting_reservations', async () => {
  const api = fakePlanApi(planResult, false); await planBatch(); confirmPaid(); fireEvent.click(submitButton());
  await screen.findByText(/Operação: submit-job/);
  expect(screen.queryByText(/Reservas aceitas/)).toBeNull();
  expect((submitButton() as HTMLButtonElement).disabled).toBe(true);
  api.data['/jobs'] = {items: [{...child, jobId: 'video-job', jobType: 'heygen.video.generate', sceneVariantId: 'scene-one'}]};
  fireEvent.click(screen.getByRole('button', {name: 'Atualizar'}));
  expect(await screen.findByText(/Reservas aceitas/)).toBeTruthy(); expect(api.calls).toHaveLength(2);
});
it('test_historical_reservations_are_not_free_budget', async () => {
  fakePlanApi(); await planBatch(); expect(screen.getByText(/Reservas históricas: 2/)).toBeTruthy();
  expect(screen.getByText(/Total após novas reservas: 3/)).toBeTruthy();
  expect(screen.getByRole('region', {name: 'HeyGen'}).textContent).not.toMatch(/saldo/i);
});
it.each(['0', '-1', '1.5', '9007199254740992'])('test_invalid_cap_blocks_plan (%s)', async value => {
  const api = fakePlanApi(); await openPanel();
  fireEvent.change(screen.getByLabelText('Limite total de renders reservados da campanha'), {target: {value}});
  fireEvent.click(planButton()); expect(api.calls).toHaveLength(0);
});
it.each([
  {...planResult, sceneVariantIds: ['foreign']}, {...planResult, sceneVariantIds: ['scene-one', 'scene-one']},
  {...planResult, maxPaidRenders: true}, {...planResult, maxPaidRenders: 0},
])('test_invalid_plan_does_not_authorize %#', async result => {
  fakePlanApi(result); await openPanel();
  fireEvent.change(screen.getByLabelText('Limite total de renders reservados da campanha'), {target: {value: '3'}}); fireEvent.click(planButton());
  expect(await screen.findByText(/Leitura da operação indisponível/)).toBeTruthy(); expect((submitButton() as HTMLButtonElement).disabled).toBe(true);
});
it.each(['voice', 'image', 'reservation', 'limit', 'stale'])('test_material_change_invalidates_paid_confirmation (%s)', async field => {
  const api = fakePlanApi(); await planBatch(); confirmPaid();
  if (field === 'limit') fireEvent.change(screen.getByLabelText('Limite total de renders reservados da campanha'), {target: {value: '4'}});
  else {
    if (field === 'voice') api.data['/voices'] = {items: [{...voice, processedSha256: 'c'.repeat(64)}]};
    if (field === 'image') api.data['/images'] = {items: [{sceneVariantId: 'scene-one', items: [{...image, sha256: 'c'.repeat(64)}]}]};
    if (field === 'reservation') api.data['/heygen/renders'] = {items: [reservation]};
    if (field === 'stale') api.data['/voices'] = Response.json({}, {status: 503});
    fireEvent.click(screen.getByRole('button', {name: 'Atualizar'}));
  }
  await waitFor(() => expect((submitButton() as HTMLButtonElement).disabled).toBe(true));
  await waitFor(() => expect((screen.getByLabelText(/Autorizo a geração paga/) as HTMLInputElement).checked).toBe(false));
  expect(api.calls).toHaveLength(1);
});
it('test_polling_timestamps_do_not_invalidate_plan', async () => {
  const api = fakePlanApi(); await planBatch(); confirmPaid(); api.data['/voices'] = {items: [{...voice, updatedAt: 'later'}]};
  fireEvent.click(screen.getByRole('button', {name: 'Atualizar'}));
  await waitFor(() => expect((submitButton() as HTMLButtonElement).disabled).toBe(false));
  expect((screen.getByLabelText(/Autorizo a geração paga/) as HTMLInputElement).checked).toBe(true);
});

it('test_material_changes_during_pending_plan_do_not_authorize_late_result', async () => {
  let release!: (response: Response) => void;
  const api = fakeApi({submit: () => new Promise(resolve => {release = resolve;})});
  api.setWrapper({jobId: 'plan-job', campaignId: 'campaign-one', operation: 'heygen_video_plan', status: 'completed', errorCode: null, result: planResult});
  await openPanel(); fireEvent.change(screen.getByLabelText('Limite total de renders reservados da campanha'), {target: {value: '3'}});
  fireEvent.click(planButton()); fireEvent.change(screen.getByLabelText('Limite total de renders reservados da campanha'), {target: {value: '4'}});
  await act(async () => release(Response.json({jobId: 'plan-job', campaignId: 'campaign-one', operation: 'heygen_video_plan'})));
  await screen.findByText(/Operação: plan-job/);
  expect(screen.queryByText(/Novos: 1/)).toBeNull(); expect((submitButton() as HTMLButtonElement).disabled).toBe(true);
});
it('test_unknown_submit_preserves_intent_without_retry', async () => {
  const api = fakePlanApi(); await planBatch(); confirmPaid();
  const fetchPlan = globalThis.fetch;
  vi.stubGlobal('fetch', (path: string, request: RequestInit) => request.method === 'POST' && path.endsWith('/submit')
    ? Promise.resolve(Response.json({}, {status: 503})) : fetchPlan(path, request));
  fireEvent.click(submitButton()); expect(await screen.findByText(/Resultado do comando desconhecido/)).toBeTruthy();
  fireEvent.click(submitButton()); expect((submitButton() as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByLabelText(/Autorizo a geração paga/) as HTMLInputElement).checked).toBe(true);
});

it('test_new_plan_intent_disarms_previous_confirmation', async () => {
  fakePlanApi(); await planBatch(); confirmPaid(); fireEvent.click(planButton());
  expect((screen.getByLabelText(/Autorizo a geração paga/) as HTMLInputElement).checked).toBe(false);
});

function fakeReconcileApi(known = false, returned?: unknown) {
  const api = fakeApi({submit: async (_path, body) => {
    api.setWrapper({jobId: 'reconcile-job', campaignId: 'campaign-one', operation: 'heygen_reconcile', status: 'completed', errorCode: null,
      result: {operation: 'heygen_reconcile', render: returned ?? {...reservation, jobId: 'recovery-job', remoteVideoId: body.videoId}}});
    return Response.json({jobId: 'reconcile-job', campaignId: 'campaign-one', operation: body.operation});
  }});
  api.data['/heygen/renders'] = {items: [{...reservation, status: 'reconciliation_required', remoteVideoId: known ? 'video-known' : null}]};
  api.data['/jobs'] = {items: [{...child, jobId: 'video-job', jobType: 'heygen.video.generate', status: 'blocked'}]};
  return api;
}
const reconcileButton = () => screen.getByRole('button', {name: 'Reconciliar render HeyGen'});
async function selectReconcile() {await openPanel(); fireEvent.change(screen.getByLabelText('Render para reconciliação'), {target: {value: 'render-one'}});}
it('test_invalid_later_reconcile_read_blocks_another_command', async () => {
  const api = fakeReconcileApi(); await selectReconcile(); fireEvent.click(reconcileButton());
  await screen.findByText(/Job retornado: recovery-job/);
  api.setWrapper(Response.json({}, {status: 503})); fireEvent(document, new Event('visibilitychange'));
  await screen.findByText(/Leitura da reconciliação indisponível/);
  expect((reconcileButton() as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(reconcileButton()); expect(api.calls).toHaveLength(1);
});
it('test_known_video_id_cannot_be_replaced', async () => {
  const api = fakeReconcileApi(true); await selectReconcile();
  const input = screen.getByLabelText('ID exato do vídeo HeyGen') as HTMLInputElement;
  expect(input.value).toBe('video-known'); expect(input.readOnly).toBe(true); fireEvent.click(reconcileButton());
  expect(await screen.findByText('Reconciliação recebida; consulte o render e o Job atual.')).toBeTruthy();
  expect(api.calls[0].body.videoId).toBe('video-known'); expect(api.calls[0].body.confirmManualBinding).toBe(false);
});
it('test_manual_binding_requires_confirmation', async () => {
  const api = fakeReconcileApi(); await selectReconcile();
  fireEvent.change(screen.getByLabelText('ID exato do vídeo HeyGen'), {target: {value: 'video-exact'}});
  expect((reconcileButton() as HTMLButtonElement).disabled).toBe(true); fireEvent.click(reconcileButton()); expect(api.calls).toHaveLength(0);
  fireEvent.click(screen.getByLabelText(/Confirmo o vínculo manual/)); fireEvent.click(reconcileButton());
  await screen.findByText('Reconciliação recebida; consulte o render e o Job atual.');
  expect(api.calls[0].body).toEqual({operation: 'heygen_reconcile', campaignId: 'campaign-one', renderId: 'render-one',
    requestId: expect.any(String), videoId: 'video-exact', confirmManualBinding: true});
});
it('test_no_id_reconciliation_does_not_assume_no_dispatch', async () => {
  const api = fakeReconcileApi(); await selectReconcile(); expect(screen.getByText(/Somente o backend pode provar ausência de dispatch/)).toBeTruthy();
  fireEvent.click(reconcileButton()); await screen.findByText('Reconciliação recebida; consulte o render e o Job atual.');
  expect(api.calls[0].body.videoId).toBeNull(); expect(api.calls[0].body.confirmManualBinding).toBe(false);
  expect(api.calls.some(call => call.path.endsWith('/resume') || call.path.endsWith('/worker/start'))).toBe(false);
});
it('test_reconciliation_observes_recovery_job_without_start', async () => {
  fakeReconcileApi(); await selectReconcile(); fireEvent.click(reconcileButton());
  expect(await screen.findByText(/Job retornado: recovery-job/)).toBeTruthy(); expect(screen.getByText(/Retomada ainda não comprovada/)).toBeTruthy();
});
it('test_same_job_recovery_requires_new_reads_and_resumed_states', async () => {
  const api = fakeReconcileApi(false, reservation); await selectReconcile(); fireEvent.click(reconcileButton());
  await screen.findByText(/Job retornado: video-job/);
  await act(async () => {});
  expect(screen.queryByText(/Retomada comprovada/)).toBeNull();
  expect(screen.getByText(/Retomada ainda não comprovada/)).toBeTruthy();
  api.data['/heygen/renders'] = {items: [reservation]};
  api.data['/jobs'] = {items: [{...child, jobId: 'video-job', jobType: 'heygen.video.generate', sceneVariantId: 'scene-one'}]};
  fireEvent.click(screen.getByRole('button', {name: 'Atualizar'}));
  expect(await screen.findByText(/Retomada comprovada/)).toBeTruthy();
  api.data['/heygen/renders'] = {items: [{...reservation, status: 'processing', remoteVideoId: 'video-after-resume'}]};
  fireEvent.click(screen.getByRole('button', {name: 'Atualizar'}));
  await screen.findByText(/video-after-resume/);
  expect(screen.getByText(/Retomada comprovada/)).toBeTruthy();
  api.data['/jobs'] = {items: [{...child, jobId: 'video-job', jobType: 'heygen.video.generate', status: 'blocked', sceneVariantId: 'scene-one'}]};
  fireEvent.click(screen.getByRole('button', {name: 'Atualizar'}));
  expect(await screen.findByText(/Retomada ainda não comprovada/)).toBeTruthy();
  expect(api.calls).toHaveLength(1);
});
it('test_acceptance_preserves_other_drafts', async () => {
  fakeReconcileApi(); await selectReconcile();
  fireEvent.change(screen.getByLabelText('Limite total de renders reservados da campanha'), {target: {value: '10'}});
  fireEvent.change(screen.getByLabelText('Responsável pela geração HeyGen'), {target: {value: 'my-draft'}});
  fireEvent.click(reconcileButton()); await screen.findByText('Reconciliação recebida; consulte o render e o Job atual.');
  expect((screen.getByLabelText('Responsável pela geração HeyGen') as HTMLInputElement).value).toBe('my-draft');
  expect((screen.getByLabelText('Limite total de renders reservados da campanha') as HTMLInputElement).value).toBe('10');
});
it('test_nonblocked_job_does_not_offer_reconciliation', async () => {
  const api = fakeReconcileApi(); api.data['/jobs'] = {items: [{...child, jobId: 'video-job', jobType: 'heygen.video.generate', status: 'running'}]};
  await openPanel(); expect(screen.queryByRole('option', {name: 'render-one'})).toBeNull(); expect((reconcileButton() as HTMLButtonElement).disabled).toBe(true);
});
it('test_wrong_reconcile_render_is_not_adopted', async () => {
  fakeReconcileApi(false, {...reservation, renderId: 'different'}); await selectReconcile(); fireEvent.click(reconcileButton());
  expect(await screen.findByText(/Leitura da reconciliação indisponível/)).toBeTruthy();
  expect(screen.queryByText('Reconciliação recebida; consulte o render e o Job atual.')).toBeNull();
});
it.each([503, 409])('test_reconcile_errors_preserve_draft (%s)', async httpStatus => {
  const api = fakeReconcileApi(); await selectReconcile();
  fireEvent.change(screen.getByLabelText('ID exato do vídeo HeyGen'), {target: {value: 'video-exact'}}); fireEvent.click(screen.getByLabelText(/Confirmo o vínculo manual/));
  const before = globalThis.fetch;
  let posts = 0;
  vi.stubGlobal('fetch', (path: string, request: RequestInit) => {
    if (request.method === 'POST') {posts++; return Promise.resolve(Response.json({error: {code: 'operation_not_allowed'}}, {status: httpStatus}));}
    return before(path, request);
  });
  fireEvent.click(reconcileButton()); await screen.findByText(httpStatus === 503 ? /Resultado do comando desconhecido/ : /Operação não permitida neste estado/);
  expect((screen.getByLabelText('ID exato do vídeo HeyGen') as HTMLInputElement).value).toBe('video-exact');
  if (httpStatus === 503) {fireEvent.click(reconcileButton()); expect(posts).toBe(1);}
  expect(api.calls).toHaveLength(0);
});

it('test_unmounted_reconciliation_ignores_late_post', async () => {
  const api = fakeReconcileApi(); let release!: (response: Response) => void;
  const before = globalThis.fetch;
  vi.stubGlobal('fetch', (path: string, request: RequestInit) => request.method === 'POST'
    ? new Promise<Response>(resolve => {release = resolve;}) : before(path, request));
  const view = render(<CampaignDetailPanel campaignId="campaign-one" />);
  await waitFor(() => expect((prepare() as HTMLButtonElement).disabled).toBe(false));
  fireEvent.change(screen.getByLabelText('Render para reconciliação'), {target: {value: 'render-one'}}); fireEvent.click(reconcileButton()); view.unmount();
  await act(async () => release(Response.json({jobId: 'reconcile-job', campaignId: 'campaign-one', operation: 'heygen_reconcile'})));
  expect(screen.queryByText(/Wrapper de reconciliação/)).toBeNull(); expect(api.calls).toHaveLength(0);
});
it('test_plan_acceptance_preserves_reconciliation_draft', async () => {
  const api = fakePlanApi(); api.data['/heygen/renders'] = {items: [{...reservation, status: 'reconciliation_required'}]};
  api.data['/jobs'] = {items: [{...child, jobId: 'video-job', jobType: 'heygen.video.generate', status: 'blocked'}]};
  await openPanel(); fireEvent.change(screen.getByLabelText('Render para reconciliação'), {target: {value: 'render-one'}});
  fireEvent.change(screen.getByLabelText('ID exato do vídeo HeyGen'), {target: {value: 'my-draft'}});
  fireEvent.change(screen.getByLabelText('Limite total de renders reservados da campanha'), {target: {value: '3'}}); fireEvent.click(planButton()); await screen.findByText(/Novos: 1/);
  expect((screen.getByLabelText('ID exato do vídeo HeyGen') as HTMLInputElement).value).toBe('my-draft');
});

it.each(['foreign-image', 'duplicate-image', 'duplicate-voice'])('test_material_context_blocks_commands (%s)', async kind => {
  const api = fakeApi();
  if (kind === 'foreign-image') api.data['/images'] = {items: [{sceneVariantId: 'scene-one', items: [image]}, {sceneVariantId: 'foreign', items: []}]};
  if (kind === 'duplicate-image') api.data['/images'] = {items: [{sceneVariantId: 'scene-one', items: [image]}, {sceneVariantId: 'scene-one', items: [image]}]};
  if (kind === 'duplicate-voice') api.data['/voices'] = {items: [voice, voice]};
  render(<CampaignDetailPanel campaignId="campaign-one" />); await act(async () => {});
  expect(screen.getByText(/Dados desatualizados ou indisponíveis/)).toBeTruthy();
  fireEvent.click(prepare()); expect(api.calls).toHaveLength(0);
});

it('test_foreign_scene_job_never_enables_reconciliation', async () => {
  const api = fakeReconcileApi(); api.data['/jobs'] = {items: [{...child, jobId: 'video-job', jobType: 'heygen.video.generate', status: 'blocked', sceneVariantId: 'foreign'}]};
  await openPanel(); expect(screen.queryByRole('option', {name: 'render-one'})).toBeNull();
});

it.each([{...reservation, sceneVariantId: 'foreign'}, {...reservation, imageCandidateId: 'foreign-image'}, {...reservation, audioSha256: 'd'.repeat(64)}])(
  'test_reconcile_result_must_match_selected_material %#', async returned => {
    fakeReconcileApi(false, returned); await selectReconcile(); fireEvent.click(reconcileButton());
    expect(await screen.findByText(/Leitura da reconciliação indisponível/)).toBeTruthy();
    expect(screen.queryByText('Reconciliação recebida; consulte o render e o Job atual.')).toBeNull();
  });

it('test_accepted_queued_wrapper_does_not_allow_duplicate_intent', async () => {
  const api = fakeApi(); api.setWrapper({jobId: 'wrapper-one', campaignId: 'campaign-one', operation: 'heygen_assets', status: 'queued', result: null, errorCode: null});
  await openPanel(); fireEvent.click(prepare()); await screen.findByText(/Operação: wrapper-one · queued/);
  expect((prepare() as HTMLButtonElement).disabled).toBe(true); fireEvent.click(prepare()); expect(api.calls).toHaveLength(1);
});

it('test_manual_reconciliation_result_must_preserve_exact_video', async () => {
  fakeReconcileApi(false, {...reservation, remoteVideoId: 'wrong-video'}); await selectReconcile();
  fireEvent.change(screen.getByLabelText('ID exato do vídeo HeyGen'), {target: {value: 'video-exact'}});
  fireEvent.click(screen.getByLabelText(/Confirmo o vínculo manual/)); fireEvent.click(reconcileButton());
  expect(await screen.findByText(/Leitura da reconciliação indisponível/)).toBeTruthy();
});
