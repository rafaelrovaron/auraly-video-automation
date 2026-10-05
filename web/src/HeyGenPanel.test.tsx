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
async function openPanel() { render(<CampaignDetailPanel campaignId="campaign-one" />); await waitFor(() => expect((prepare() as HTMLButtonElement).disabled).toBe(false)); }

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
