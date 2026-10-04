import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { VoicePanel } from './VoicePanel';
import { ApiError } from './api';
import type { CampaignDetail, Items, JobSummary, VoiceSummary } from './api';
import type { RemoteState } from './usePolling';

const copy = {copyMasterId: 'copy-one', version: 1, approvalState: 'approved', approvedBy: 'tester', sourceText: 'Source',
  sha256: 'a'.repeat(64), headline: 'Visual headline', hook: 'Hook', body: 'Body', cta: 'CTA'};
const campaign: CampaignDetail = {campaignId: 'campaign-one', character: 'susan-smith', storedStatus: 'draft', sceneCount: 1,
  createdAt: 'now', updatedAt: 'now', operationalStatus: 'needs_input', nextPending: null,
  proofObject: 'cards', voicePreset: 'voice', editPreset: 'edit', copyMasters: [copy, {...copy, copyMasterId: 'copy-two', version: 2}], sceneVariants: []};
const job: JobSummary = {jobId: 'job-one', campaignId: 'campaign-one', sceneVariantId: null, jobType: 'voice.generate', status: 'queued',
  attemptCount: 0, maxAttempts: 2, retrySafety: 'reconcile_before_retry', queuedAt: 'now', startedAt: null, completedAt: null, nextRetryAt: null, lastErrorCode: null};
function state<T>(data: T, readId = 1): RemoteState<T> {
  return {data, error: null, loading: false, lastSuccessAt: Date.now(), lastSuccessReadId: readId, refresh: () => readId + 1};
}
const detail = () => state(campaign);
const voices = () => state<Items<VoiceSummary>>({items: []});
const jobs = () => state<Items<JobSummary>>({items: []});
function props() { return {campaignId: 'campaign-one', detail: detail(), voices: voices(), jobs: jobs()}; }
function fakeApi(initial: unknown = {state: 'configured', currency: 'USD', limitCents: 1000}, submit?: (path: string, body: Record<string, unknown>) => Promise<Response>) {
  let budget = initial;
  const calls: {path: string; body: Record<string, unknown>}[] = [];
  vi.stubGlobal('fetch', async (path: string, options: RequestInit) => {
    if (options.method === 'POST') {
      const body = JSON.parse(String(options.body)); calls.push({path, body});
      if (submit) return submit(path, body);
      if (path.endsWith('/budget')) { budget = {state: 'configured', currency: body.currency, limitCents: body.limitCents}; return Response.json(budget); }
      return Response.json({operation: 'voice_generate', campaignId: 'campaign-one', jobId: 'job-one', voiceMasterId: 'voice-one'});
    }
    if (path.endsWith('/budget')) return Response.json(budget);
    if (path.endsWith('/jobs/job-one')) return Response.json(job);
    return Response.json({items: []});
  });
  return calls;
}
async function fillGenerate() {
  await screen.findByText('USD · limite 1000 centavos');
  for (const [label, value] of [['Voice ID', 'voice-test'], ['Model ID', 'model-test'], ['Responsável pela geração', 'tester'], ['Teto desta geração (centavos)', '500']])
    fireEvent.change(screen.getByLabelText(label), {target: {value}});
  fireEvent.change(screen.getByLabelText('Versão da copy para voz'), {target: {value: '1'}});
}
const generate = () => screen.getByRole('button', {name: 'Enfileirar geração de voz'});

it('test_budget_save_does_not_authorize_or_start_voice', async () => {
  const calls = fakeApi({state: 'missing', currency: null, limitCents: null});
  render(<VoicePanel {...props()} />);
  const save = await screen.findByRole('button', {name: 'Configurar orçamento inicial'});
  fireEvent.change(screen.getByLabelText('Moeda da campanha'), {target: {value: 'USD'}});
  fireEvent.change(screen.getByLabelText('Limite da campanha (centavos)'), {target: {value: '1000'}});
  expect((save as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByLabelText('Confirmo este orçamento inicial'));
  fireEvent.click(save);
  await screen.findByText('USD · limite 1000 centavos');
  expect(calls).toEqual([{path: '/api/v1/campaigns/campaign-one/budget', body: {currency: 'USD', limitCents: 1000, confirmed: true}}]);
  expect((screen.getByLabelText('Autorizo o gasto desta geração') as HTMLInputElement).checked).toBe(false);
});

it('test_generation_pins_copy_and_requires_fresh_budget_and_confirmation', async () => {
  const calls = fakeApi(); render(<VoicePanel {...props()} />); await fillGenerate();
  expect((generate() as HTMLButtonElement).disabled).toBe(true); fireEvent.click(screen.getByLabelText('Autorizo o gasto desta geração'));
  fireEvent.click(generate()); fireEvent.click(generate());
  await screen.findByText(/Job na fila.*voice_generate/);
  expect(calls).toHaveLength(1);
  expect(calls[0].body).toEqual({campaignId: 'campaign-one', copyMasterVersion: 1, voiceId: 'voice-test', modelId: 'model-test',
    voiceSettings: {}, outputFormat: 'mp3_44100_128', transcriptMatchThreshold: 0.97, paidRequestApproved: true, paidRequestApprovedBy: 'tester', approvedBudgetCents: 500});
  expect(calls[0].path).toBe('/api/v1/campaigns/campaign-one/voices/generate');
  expect(await screen.findByText('voice.generate · queued')).not.toBeNull();
});

it('test_changed_inputs_clear_paid_confirmation', async () => {
  fakeApi(); render(<VoicePanel {...props()} />); await fillGenerate();
  const check = screen.getByLabelText('Autorizo o gasto desta geração'); fireEvent.click(check);
  fireEvent.change(screen.getByLabelText('Teto desta geração (centavos)'), {target: {value: '1001'}});
  expect((check as HTMLInputElement).checked).toBe(false); expect((generate() as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(check); fireEvent.change(screen.getByLabelText('Versão da copy para voz'), {target: {value: '2'}});
  expect((check as HTMLInputElement).checked).toBe(false);
});

it('test_lost_generation_response_never_matches_by_copy_or_provider', async () => {
  const calls = fakeApi(undefined, async () => Response.json({error: {code: 'internal_error'}}, {status: 503}));
  render(<VoicePanel {...props()} />); await fillGenerate(); fireEvent.click(screen.getByLabelText('Autorizo o gasto desta geração')); fireEvent.click(generate());
  await screen.findByText(/command_unknown/); expect((generate() as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(generate()); expect(calls).toHaveLength(1);
  expect(screen.queryByText(/Job na fila/)).toBeNull();
});

it('test_budget_commit_with_lost_response_requires_later_get', async () => {
  let readCount = 0;
  const posts: string[] = [];
  vi.stubGlobal('fetch', async (path: string, options: RequestInit) => {
    if (options.method === 'POST') { posts.push(path); return Response.json({error: {code: 'internal_error'}}, {status: 503}); }
    readCount++;
    return Response.json(readCount === 1 ? {state: 'missing', currency: null, limitCents: null} : {state: 'configured', currency: 'USD', limitCents: 1000});
  });
  render(<VoicePanel {...props()} />);
  await screen.findByLabelText('Moeda da campanha');
  fireEvent.change(screen.getByLabelText('Moeda da campanha'), {target: {value: 'USD'}});
  fireEvent.change(screen.getByLabelText('Limite da campanha (centavos)'), {target: {value: '1000'}});
  fireEvent.click(screen.getByLabelText('Confirmo este orçamento inicial')); fireEvent.click(screen.getByRole('button', {name: 'Configurar orçamento inicial'}));
  await screen.findByText(/Configuração solicitada observada/);
  expect(posts).toHaveLength(1); expect(readCount).toBeGreaterThan(1);
});

it('test_late_generation_does_not_mutate_other_campaign', async () => {
  let finish!: (response: Response) => void;
  fakeApi(undefined, () => new Promise(resolve => {finish = resolve;}));
  const view = render(<VoicePanel {...props()} />); await fillGenerate();
  fireEvent.click(screen.getByLabelText('Autorizo o gasto desta geração')); fireEvent.click(generate());
  await waitFor(() => expect(finish).toBeTypeOf('function'));
  view.unmount(); render(<VoicePanel {...props()} campaignId="campaign-two" detail={state({...campaign, campaignId: 'campaign-two'})} />);
  await act(async () => finish(Response.json({operation: 'voice_generate', campaignId: 'campaign-one', jobId: 'job-one', voiceMasterId: 'voice-one'})));
  expect(screen.queryByText(/Job na fila/)).toBeNull();
});

it('test_stale_or_incomplete_voice_dto_keeps_last_good_data', async () => {
  fakeApi(); const p = props(); const view = render(<VoicePanel {...p} />); await fillGenerate();
  view.rerender(<VoicePanel {...p} voices={{...p.voices, error: new ApiError('invalid_response')}} />);
  expect((generate() as HTMLButtonElement).disabled).toBe(true); expect(screen.getByText(/Leituras de voz desatualizadas/)).not.toBeNull();
});
