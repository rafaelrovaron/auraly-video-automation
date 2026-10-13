import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { CampaignDetailPanel, CampaignList } from './CampaignPanel';
import { readWorker } from './api';

const time = '2026-10-03T00:00:00Z';
const summary = { campaignId: 'campaign-one', character: 'susan-smith', storedStatus: 'draft', sceneCount: 1, createdAt: time, updatedAt: time,
  operationalStatus: 'needs_input', nextPending: { code: 'renderer_not_implemented', stage: 'editing', entityId: 'render-one', message: 'Renderer not implemented.' } };
const detail = { ...summary, proofObject: 'cards', voicePreset: 'voice', editPreset: 'default', copyMasters: [{
  copyMasterId: 'copy-one', campaignId: 'campaign-one', version: 1, approvalState: 'approved', approvedBy: 'tester', approvedAt: time,
  createdAt: time, updatedAt: time, sourceText: 'Original source', sha256: 'a'.repeat(64), headline: 'Copy headline', hook: 'Copy hook', body: 'Copy body', cta: 'Copy CTA',
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

function fakeApi(overrides: Record<string, unknown> = {}, renderGate?: Promise<void>) {
  const calls: { path: string; method: string }[] = [];
  vi.stubGlobal('fetch', async (path: string, options: RequestInit) => {
    calls.push({ path, method: options.method ?? 'GET' });
    const suffix = path.replace('/api/v1/campaigns/campaign-one', '');
    if (renderGate && suffix === '/heygen/renders') await renderGate;
    const body = { '': detail, '/status': status, '/images': { items: [{ sceneVariantId: 'scene-one', items: [] }] },
      '/voices': { items: [voice] }, '/budget': {state: 'missing', currency: null, limitCents: null}, '/heygen/renders': { items: [renderItem] }, '/jobs': { items: [job] },
      '/jobs/job-one': job, '/worker': { state: 'idle', campaignId: null, kind: null, errorCode: null }, ...overrides }[suffix];
    return Response.json(body);
  });
  return calls;
}

it('shows a campaign name and keeps technical identity behind details', async () => {
  vi.stubGlobal('fetch', async () => Response.json({ items: [{ ...summary, displayName: 'Piloto Soulmate' }] }));
  render(<CampaignList />);
  expect(await screen.findByRole('link', { name: 'Piloto Soulmate' })).toBeTruthy();
  expect(screen.getByText('campaign-one').closest('details')?.open).toBe(false);
});

it('distinguishes campaigns that share the same visible name', async () => {
  vi.stubGlobal('fetch', async () => Response.json({ items: [
    { ...summary, campaignId: 'campaign-one', displayName: 'Piloto Soulmate' },
    { ...summary, campaignId: 'campaign-two', displayName: 'Piloto Soulmate' },
  ] }));
  render(<CampaignList />);
  const links = await screen.findAllByRole('link', { name: /Piloto Soulmate/ });
  expect(links).toHaveLength(2);
  expect(links[0].textContent).not.toBe(links[1].textContent);
  expect(links.map(link => link.getAttribute('href'))).toEqual(['#/campaigns/campaign-one', '#/campaigns/campaign-two']);
});

it('uses the campaign name in the detail heading without changing its route', async () => {
  fakeApi({ '': { ...detail, displayName: 'Piloto Soulmate' } });
  render(<CampaignDetailPanel campaignId="campaign-one" stage="overview" />);
  expect(await screen.findByRole('heading', { name: 'Piloto Soulmate' })).toBeTruthy();
  expect(screen.getByRole('navigation', { name: 'Etapas da campanha' }).querySelector('a')?.getAttribute('href'))
    .toBe('#/campaigns/campaign-one/overview');
});

it('lets an older campaign receive a name without changing its ID', async () => {
  fakeApi();
  const original = globalThis.fetch;
  let savedName = ''; let submitted: { path: string; body: unknown } | null = null;
  vi.stubGlobal('fetch', (path: string, options: RequestInit) => {
    if (options.method === 'POST') {
      submitted = { path, body: JSON.parse(options.body as string) };
      savedName = 'Novo nome';
      return Promise.resolve(Response.json({ ...detail, displayName: savedName }));
    }
    if (path === '/api/v1/campaigns/campaign-one') return Promise.resolve(Response.json({ ...detail, displayName: savedName || null }));
    return original(path, options);
  });
  render(<CampaignDetailPanel campaignId="campaign-one" stage="overview" />);
  fireEvent.click(await screen.findByText('Adicionar nome à campanha'));
  fireEvent.change(screen.getByLabelText('Nome da campanha'), { target: { value: 'Novo nome' } });
  fireEvent.click(screen.getByRole('button', { name: 'Salvar nome' }));
  expect(await screen.findByRole('heading', { name: 'Novo nome' })).toBeTruthy();
  expect(submitted).toEqual({ path: '/api/v1/campaigns/campaign-one/name', body: { displayName: 'Novo nome' } });
});

it('uses scene context for media titles and keeps hashes in closed technical details', async () => {
  fakeApi({ '/images': { items: [{ sceneVariantId: 'scene-one', items: [{
    imageCandidateId: 'image-one', sceneVariantId: 'scene-one', reviewStatus: 'approved', sourceKind: 'manual',
    sourcePath: 'imports/portrait.png', format: 'png', sha256: 'c'.repeat(64), width: 1080, height: 1920,
    sizeBytes: 2048, rejectionReason: null,
  }] }] } });
  const view = render(<CampaignDetailPanel campaignId="campaign-one" stage="images" />);
  const imageSection = await screen.findByRole('region', { name: 'Imagens' });
  expect(within(imageSection).getByRole('heading', { name: /Imagem.*Room.*portrait.png/ })).toBeTruthy();
  expect(within(imageSection).getByText('c'.repeat(64)).closest('details')?.open).toBe(false);
  view.rerender(<CampaignDetailPanel campaignId="campaign-one" stage="voice" />);
  expect(within(screen.getByRole('region', { name: 'Voice Masters' })).getByRole('heading', { name: /Áudio principal.*roteiro v1/ })).toBeTruthy();
  view.rerender(<CampaignDetailPanel campaignId="campaign-one" stage="videos" />);
  expect(await within(screen.getByRole('region', { name: 'HeyGen' })).findByRole('heading', { name: /Vídeo.*Room/ })).toBeTruthy();
});

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

it('shows one campaign stage with mobile navigation and separates finalization', async () => {
  const calls = fakeApi();
  const view = render(<CampaignDetailPanel campaignId="campaign-one" stage="voice" />);
  expect(await screen.findByRole('region', { name: 'Voice Masters' })).toBeTruthy();
  expect(screen.getByRole('navigation', { name: 'Etapas da campanha' })).toBeTruthy();
  expect(screen.getByRole('combobox', { name: 'Etapa da campanha' })).toBeTruthy();
  expect(view.container.querySelector('section[aria-label="Copy e cenas"]')?.hasAttribute('hidden')).toBe(true);
  expect(view.container.querySelector('section[aria-label="Edição e variantes"]')?.hasAttribute('hidden')).toBe(true);
  expect(view.container.querySelector('section[aria-label="Finalização"]')?.hasAttribute('hidden')).toBe(true);
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
  fireEvent.click(await screen.findByRole('button', { name: /Ver tarefa.*Na fila/ }));
  expect(await screen.findByText('unknown.type', { selector: 'dd' })).toBeTruthy();
  expect(calls.some(c => c.path.includes('/operations/'))).toBe(false);
  expect(screen.getAllByText(/CLI/).length).toBeGreaterThan(0);
});

it('labels a task by state and keeps its full identity in technical details', async () => {
  fakeApi(); render(<CampaignDetailPanel campaignId="campaign-one" stage="overview" />);
  const tasks = await screen.findByRole('region', {name: 'Jobs'});
  const button = within(tasks).getByRole('button', {name: /Ver tarefa.*Na fila/});
  expect(button.textContent).not.toContain('job-one');
  expect(within(tasks).getByText('oauth_required').closest('details')?.open).toBe(false);
  fireEvent.click(button);
  const detailRegion = await screen.findByRole('region', {name: 'Detalhe do Job'});
  expect(within(detailRegion).getByText('job-one', {selector: 'dd'}).closest('details')?.open).toBe(false);
});

it('shows an imported image through the verified campaign media route', async () => {
  fakeApi({'/images': {items: [{sceneVariantId: 'scene-one', items: [{imageCandidateId: 'image-one', sceneVariantId: 'scene-one',
    reviewStatus: 'pending', sourceKind: 'manual_import', sourcePath: 'campaigns/campaign-one/image.png', format: 'png',
    sha256: 'c'.repeat(64), width: 1080, height: 1920, sizeBytes: 123, rejectionReason: null}]}]}});
  render(<CampaignDetailPanel campaignId="campaign-one" stage="images" />);
  const image = await screen.findByRole('img', {name: /Imagem.*Room/});
  expect(image.getAttribute('src')).toBe(`/api/v1/campaigns/campaign-one/images/image-one/media/${'c'.repeat(64)}`);
  expect(image.getAttribute('src')).not.toContain('campaigns/campaign-one/image.png');
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
  fireEvent.click(await screen.findByRole('button', { name: /Ver tarefa.*Concluído/ }));
  expect(await screen.findByText('dry_run')).toBeTruthy();
  expect(screen.queryByText('secret-sentinel')).toBeNull();
  expect(screen.getByText('created', { selector: 'dt' }).nextElementSibling?.textContent).toBe('0');
});

it.each([
  ['Resumo', '/status', { ...status, scenes: [{ sceneVariantId: 'scene-one' }] }],
  ['Copy e cenas', '', { ...detail, copyMasters: [null] }],
  ['Imagens', '/images', { items: [{ sceneVariantId: 'scene-one', items: [null] }] }],
  ['Voice Masters', '/voices', { items: [{ ...voice, qcFindings: [{}] }] }],
  ['HeyGen', '/heygen/renders', { items: [{ ...renderItem, status: {} }] }],
  ['Jobs', '/jobs', { items: [null] }],
])('keeps last valid %s data stale and other sections usable after malformed nested DTOs', async (title, suffix, invalid) => {
  const overrides: Record<string, unknown> = {};
  // HeyGen loads after campaign detail; reproduce that independent request boundary.
  let releaseRenders!: () => void;
  const renderGate = new Promise<void>(resolve => { releaseRenders = resolve; });
  fakeApi(overrides, title === 'HeyGen' ? renderGate : undefined);
  render(<CampaignDetailPanel campaignId="campaign-one" />);
  expect(await screen.findByText('Copy hook')).toBeTruthy();
  queueMicrotask(releaseRenders);
  const section = screen.getByRole('region', { name: title as string });
  expect(await within(section).findByText(/Última leitura:/)).toBeTruthy();
  overrides[suffix as string] = invalid;
  fireEvent.click(screen.getByRole('button', { name: 'Atualizar' }));
  expect(await within(section).findByText(/invalid_response.*Dados desatualizados/)).toBeTruthy();
  expect(screen.getByText('Copy hook')).toBeTruthy();
  expect(screen.getByRole('heading', { name: 'Worker' })).toBeTruthy();
});

it('rejects invalid list entry fields rather than rendering an object', async () => {
  vi.stubGlobal('fetch', async () => Response.json({ items: [{ ...summary, character: {} }] }));
  render(<CampaignList />);
  expect(await screen.findByText(/invalid_response.*Seção indisponível/)).toBeTruthy();
});

it.each([
  ['Voice Masters', '/voices', {items: [{...voice, campaignId: 'campaign-two', voiceMasterId: 'foreign-voice'}]}, 'voice-one', 'foreign-voice'],
  ['Jobs', '/jobs', {items: [{...job, campaignId: 'campaign-two', jobId: 'foreign-job'}]}, 'Ver tarefa · Tarefa · Na fila · -one', 'foreign-job'],
  ['Jobs', '/jobs', {items: [{...job, campaignId: undefined, jobId: 'incomplete-job'}]}, 'Ver tarefa · Tarefa · Na fila · -one', 'incomplete-job'],
])('test_%s_context_guard_keeps_valid_snapshot_after_invalid_campaign_collection', async (title, suffix, invalid, previous, foreign) => {
  const overrides: Record<string, unknown> = {}; fakeApi(overrides);
  render(<CampaignDetailPanel campaignId="campaign-one" />); await screen.findByText('Copy hook');
  const section = screen.getByRole('region', {name: title}); expect(await within(section).findByText(previous)).not.toBeNull();
  overrides[suffix] = invalid; fireEvent.click(screen.getByRole('button', {name: 'Atualizar'}));
  expect(await within(section).findByText(/invalid_response.*Dados desatualizados/)).not.toBeNull();
  expect(within(section).getByText(previous)).not.toBeNull(); expect(within(section).queryByText(foreign)).toBeNull();
  expect((screen.getByRole('button', {name: 'Enfileirar geração de voz'}) as HTMLButtonElement).disabled).toBe(true);
});

it.each([
  ['/jobs/job-one', { ...job, status: {} }],
  ['/operations/job-one', { jobId: 'job-one', operation: {}, result: [], errorCode: {} }],
])('rejects malformed selected detail at %s without losing the campaign', async (path, invalid) => {
  const localJob = { ...job, jobType: 'api.local.operation' };
  fakeApi({ '/jobs': { items: [localJob] }, '/jobs/job-one': localJob, [path as string]: invalid });
  render(<CampaignDetailPanel campaignId="campaign-one" />);
  fireEvent.click(await screen.findByRole('button', { name: /Ver tarefa.*Na fila/ }));
  expect(await screen.findByText(/invalid_response.*Seção indisponível/)).toBeTruthy();
  expect(screen.getByText('Copy hook')).toBeTruthy();
});

const source = {path: 'outputs/scene-one.mp4', sha256: 'c'.repeat(64), sizeBytes: 1234,
  probe: {formatName: 'mov,mp4', durationSec: 2, sizeBytes: 1234, video: {codec: 'h264', width: 1080, height: 1920,
    fps: 30, nominalFps: 30, isVfr: false, rotation: 0}, audio: {codec: 'aac', sampleRate: 48000, channels: 1}, warnings: [], hasAudio: true}};
it('test_ready_render_shows_local_media_facts_without_player', async () => {
  const calls = fakeApi({'/heygen/renders': {items: [{...renderItem, status: 'ready', source}]}});
  const view = render(<CampaignDetailPanel campaignId="campaign-one" />); await screen.findByText('outputs/scene-one.mp4');
  expect(screen.getByText('1080 × 1920')).toBeTruthy(); expect(screen.getByText('h264')).toBeTruthy();
  expect(screen.getByText('1234', {selector: 'dd'})).toBeTruthy(); expect(screen.getByText('aac · 48000 Hz · 1 canais')).toBeTruthy();
  expect(view.container.querySelector('audio,video,img')).toBeNull(); expect(calls.every(call => call.method === 'GET')).toBe(true);
});
it.each([
  {...renderItem, campaignId: 'foreign'}, {...renderItem, sceneVariantId: 'foreign'}, {...renderItem, manualBinding: undefined},
  {...renderItem, source: {...source, sha256: ''}}, {...renderItem, source: {...source, probe: {...source.probe, video: {codec: 'h264'}}}},
  {...renderItem, source: {...source, path: 'https://signed.invalid/video.mp4'}},
])('test_foreign_or_incomplete_render_snapshot_preserves_last_good %#', async invalid => {
  const overrides: Record<string, unknown> = {}; fakeApi(overrides); render(<CampaignDetailPanel campaignId="campaign-one" />);
  await screen.findByRole('heading', {name: 'Vídeo · Room'}); overrides['/heygen/renders'] = {items: [invalid]};
  fireEvent.click(screen.getByRole('button', {name: 'Atualizar'}));
  expect(await within(screen.getByRole('region', {name: 'HeyGen'})).findByText(/invalid_response.*Dados desatualizados/)).toBeTruthy();
  expect(screen.getByRole('heading', {name: 'Vídeo · Room'})).toBeTruthy();
});
it('test_duplicate_render_ids_preserve_snapshot', async () => {
  const overrides: Record<string, unknown> = {}; fakeApi(overrides); render(<CampaignDetailPanel campaignId="campaign-one" />);
  await screen.findByRole('heading', {name: 'Vídeo · Room'}); overrides['/heygen/renders'] = {items: [renderItem, renderItem]};
  fireEvent.click(screen.getByRole('button', {name: 'Atualizar'}));
  expect(await within(screen.getByRole('region', {name: 'HeyGen'})).findByText(/invalid_response.*Dados desatualizados/)).toBeTruthy();
});
