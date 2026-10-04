import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { CampaignDetailPanel, CampaignList } from './CampaignPanel';
import { readWorker } from './api';

const time = '2026-10-03T00:00:00Z';
const summary = { campaignId: 'campaign-one', character: 'susan-smith', storedStatus: 'draft', sceneCount: 1, createdAt: time, updatedAt: time,
  operationalStatus: 'needs_input', nextPending: { code: 'renderer_not_implemented', stage: 'editing', entityId: 'render-one', message: 'Renderer not implemented.' } };
const detail = { ...summary, proofObject: 'cards', voicePreset: 'voice', editPreset: 'default', copyMasters: [{
  copyMasterId: 'copy-one', campaignId: 'campaign-one', version: 1, approvalState: 'approved', approvedBy: 'tester', approvedAt: time,
  createdAt: time, updatedAt: time, hook: 'Copy hook', body: 'Copy body', cta: 'Copy CTA',
}], sceneVariants: [{ sceneVariantId: 'scene-one', campaignId: 'campaign-one', variantId: 'v1', location: 'Room', timeAtmosphere: null, action: 'Talk', prompt: 'Portrait', proofObject: null, status: 'not_started', createdAt: time, updatedAt: time }] };
const status = { ...summary, approvedCopyCount: 1, approvedVoiceCount: 0, approvedImageCount: 0, readyRenderCount: 0, planCount: 0,
  scenes: [{ sceneVariantId: 'scene-one', variantId: 'v1', currentCopyId: 'copy-one', currentVoiceId: null, approvedImageId: null, readyRenderIds: [], planHashes: [], pending: [summary.nextPending] }] };
const voice = { voiceMasterId: 'voice-one', campaignId: 'campaign-one', copyMasterId: 'copy-one', copyMasterVersion: 1, generation: 1, provider: 'imported', status: 'pending',
  processedAudioPath: null, processedSha256: null, durationSeconds: null, transcriptMatchStatus: null, headlineSpoken: null, qcFindings: [],
  approvedAt: null, approvedBy: null, approvalReviewReason: null, rejectedAt: null, rejectedBy: null, rejectionReason: null, createdAt: time, updatedAt: time };
const renderItem = { renderId: 'render-one', campaignId: 'campaign-one', sceneVariantId: 'scene-one', imageCandidateId: 'image-one', voiceMasterId: 'voice-one', jobId: 'job-one', status: 'planned',
  remoteVideoId: null, manualBinding: false, imageSha256: 'a'.repeat(64), audioSha256: 'b'.repeat(64), source: null, errorCode: null, createdAt: time, updatedAt: time };
const job = { jobId: 'job-one', jobType: 'unknown.type', campaignId: 'campaign-one', sceneVariantId: null, status: 'queued', attemptCount: 0, maxAttempts: 3, retrySafety: 'manual_only',
  createdAt: time, updatedAt: time, queuedAt: time, startedAt: null, completedAt: null, cancelledAt: null, nextRetryAt: null, lastErrorCode: 'oauth_required' };

function fakeApi(overrides: Record<string, unknown> = {}) {
  const calls: { path: string; method: string }[] = [];
  vi.stubGlobal('fetch', async (path: string, options: RequestInit) => {
    calls.push({ path, method: options.method ?? 'GET' });
    const suffix = path.replace('/api/v1/campaigns/campaign-one', '');
    const body = { '': detail, '/status': status, '/images': { items: [{ sceneVariantId: 'scene-one', items: [] }] },
      '/voices': { items: [voice] }, '/heygen/renders': { items: [renderItem] }, '/jobs': { items: [job] },
      '/jobs/job-one': job, '/worker': { state: 'idle', campaignId: null, kind: null, errorCode: null }, ...overrides }[suffix];
    return Response.json(body);
  });
  return calls;
}

it('shows real metadata, nulls and renderer pending without players or POST', async () => {
  const calls = fakeApi();
  const { container } = render(<CampaignDetailPanel campaignId="campaign-one" />);
  expect(await screen.findByText('Copy hook')).toBeTruthy();
  expect(screen.getAllByText(/Renderer ainda não implementado/).length).toBeGreaterThan(0);
  const voices = screen.getByRole('region', { name: 'Voice Masters' });
  expect(within(voices).getAllByText('Não disponível').length).toBeGreaterThan(0);
  expect(container.querySelector('audio,video,img')).toBeNull();
  expect(calls.every(call => call.method === 'GET')).toBe(true);
});

it('polls the list at 5000ms and live detail only at 2000ms', async () => {
  vi.useFakeTimers();
  const paths: string[] = [];
  vi.stubGlobal('fetch', async (path: string) => { paths.push(path); return Response.json({ items: [summary] }); });
  const list = render(<CampaignList />);
  await act(async () => {});
  expect(paths).toHaveLength(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(4999); });
  expect(paths).toHaveLength(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(paths).toHaveLength(2);
  list.unmount();
  const calls = fakeApi();
  render(<CampaignDetailPanel campaignId="campaign-one" />);
  await act(async () => {});
  const initial = calls.filter(c => c.path.endsWith('/voices')).length;
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(calls.filter(c => c.path.endsWith('/worker'))).toHaveLength(2);
  expect(calls.filter(c => c.path.endsWith('/status'))).toHaveLength(2);
  expect(calls.filter(c => c.path.endsWith('/jobs'))).toHaveLength(2);
  expect(calls.filter(c => c.path.endsWith('/voices'))).toHaveLength(initial);
});

it('keeps a section unavailable rather than reporting approved emptiness', async () => {
  fakeApi();
  const fetcher = globalThis.fetch;
  vi.stubGlobal('fetch', (path: string, options: RequestInit) => path.endsWith('/voices')
    ? Promise.resolve(Response.json({ error: { code: 'storage_unavailable', message: 'private-token', field: null } }, { status: 503 }))
    : fetcher(path, options));
  render(<CampaignDetailPanel campaignId="campaign-one" />);
  const section = screen.getByRole('region', { name: 'Voice Masters' });
  expect(await within(section).findByText(/storage_unavailable/)).toBeTruthy();
  expect(within(section).queryByText(/Nenhum/)).toBeNull();
  expect(screen.queryByText('private-token')).toBeNull();
});

it('treats worker 404 as unassociated but preserves other errors', async () => {
  vi.stubGlobal('fetch', async () => Response.json({ error: { code: 'not_found', field: null, message: 'Resource not found.' } }, { status: 404 }));
  expect(await readWorker('campaign-one', new AbortController().signal)).toEqual({ scope: 'unassociated' });
  vi.stubGlobal('fetch', async () => Response.json({ error: { code: 'storage_unavailable', field: null, message: 'fail' } }, { status: 503 }));
  await expect(readWorker('campaign-one', new AbortController().signal)).rejects.toMatchObject({ code: 'storage_unavailable' });
});

it('rejects a malformed voice collection without crashing other sections', async () => {
  fakeApi({ '/voices': { items: [summary] } });
  render(<CampaignDetailPanel campaignId="campaign-one" />);
  const section = screen.getByRole('region', { name: 'Voice Masters' });
  expect(await within(section).findByText(/invalid_response/)).toBeTruthy();
  expect(screen.getByText('Copy hook')).toBeTruthy();
});

it('shows unknown Job metadata without querying a local operation', async () => {
  const calls = fakeApi();
  render(<CampaignDetailPanel campaignId="campaign-one" />);
  fireEvent.click(await screen.findByRole('button', { name: 'Ver Job job-one' }));
  expect(await screen.findByText('unknown.type', { selector: 'dd' })).toBeTruthy();
  expect(calls.some(c => c.path.includes('/operations/'))).toBe(false);
  expect(screen.getAllByText(/CLI/).length).toBeGreaterThan(0);
});

it('refreshes assets when live counts change but not on unchanged polling', async () => {
  vi.useFakeTimers();
  const calls = fakeApi();
  const original = globalThis.fetch;
  let approved = 0;
  vi.stubGlobal('fetch', async (path: string, options: RequestInit) => path.endsWith('/status')
    ? Response.json({ ...status, approvedVoiceCount: approved }) : original(path, options));
  render(<CampaignDetailPanel campaignId="campaign-one" />);
  await act(async () => {});
  expect(calls.filter(c => c.path.endsWith('/voices'))).toHaveLength(1);
  approved = 1;
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(calls.filter(c => c.path.endsWith('/voices'))).toHaveLength(2);
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(calls.filter(c => c.path.endsWith('/voices'))).toHaveLength(2);
});

it('displays safe operation counts and dry-run mode without arbitrary fields', async () => {
  const localJob = { ...job, jobType: 'api.local.operation', status: 'completed', lastErrorCode: null };
  fakeApi({ '/jobs': { items: [localJob] }, '/jobs/job-one': localJob, '/operations/job-one': {
    jobId: 'job-one', campaignId: 'campaign-one', operation: 'image_import', status: 'completed', errorCode: null,
    result: { operation: 'image_import', mode: 'dry_run', total: 1, created: 0, reused: 0, approved: 0, items: [], privateField: 'secret-sentinel' },
  } });
  render(<CampaignDetailPanel campaignId="campaign-one" />);
  fireEvent.click(await screen.findByRole('button', { name: 'Ver Job job-one' }));
  expect(await screen.findByText('dry_run')).toBeTruthy();
  expect(screen.queryByText('secret-sentinel')).toBeNull();
  expect(screen.getByText('created', { selector: 'dt' }).nextElementSibling?.textContent).toBe('0');
});
