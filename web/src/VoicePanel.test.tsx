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

const wrapper = {jobId: 'wrapper-one', campaignId: 'campaign-one', operation: 'voice_import', status: 'completed', errorCode: null,
  result: {operation: 'voice_import', voiceMasterId: 'voice-one', jobId: 'child-one'}};
function importApi(_operation: unknown = wrapper, _childStatus = 'queued', responseStatus = 200) {
  return fakeApi({state: 'missing', currency: null, limitCents: null}, async () => Response.json(responseStatus === 200
    ? {operation: 'voice_import', campaignId: 'campaign-one', jobId: 'wrapper-one'} : {error: {code: 'internal_error'}}, {status: responseStatus}));
}
function wrapReads(operation: unknown = wrapper, childStatus = 'queued') {
  const base = globalThis.fetch;
  vi.stubGlobal('fetch', async (path: string, options: RequestInit) => {
    if (options.method !== 'POST' && path.endsWith('/operations/wrapper-one')) return Response.json(operation);
    if (options.method !== 'POST' && path.endsWith('/jobs/child-one')) return Response.json({...job, jobId: 'child-one', jobType: 'voice.import', status: childStatus});
    return base(path, options);
  });
}
async function fillImport() {
  await screen.findByLabelText('Caminho relativo do áudio');
  fireEvent.change(screen.getByLabelText('Versão da copy para voz'), {target: {value: '1'}});
  fireEvent.change(screen.getByLabelText('Caminho relativo do áudio'), {target: {value: 'imports/voice.mp3'}});
  fireEvent.click(screen.getByLabelText('Confirmo o arquivo e a versão da copy'));
}
const importButton = () => screen.getByRole('button', {name: 'Enfileirar importação de voz'});

it('test_import_enqueues_only_then_shows_wrapper_and_child', async () => {
  const calls = importApi(); wrapReads(); render(<VoicePanel {...props()} />); await fillImport();
  fireEvent.click(importButton()); fireEvent.click(importButton());
  await screen.findByText('Fase 2: processar voz importada'); await screen.findByText('voice.import · queued');
  expect(calls).toHaveLength(1); expect(calls[0].path).toBe('/api/v1/campaigns/campaign-one/voices/import');
  expect(calls[0].body).toEqual({operation: 'voice_import', campaignId: 'campaign-one', sourcePath: 'imports/voice.mp3',
    requestId: expect.any(String), request: {campaignId: 'campaign-one', copyMasterVersion: 1}});
  fireEvent.click(screen.getByRole('button', {name: 'Consultar operação de voz'})); expect(calls).toHaveLength(1);
  expect(screen.queryByText('Voz aprovada')).toBeNull();
});
it('test_completed_wrapper_with_failed_child_is_not_success', async () => {
  importApi(); wrapReads(wrapper, 'failed'); render(<VoicePanel {...props()} />); await fillImport(); fireEvent.click(importButton());
  await screen.findByText('voice.import · failed'); expect(screen.queryByText('Voz aprovada')).toBeNull();
});
it('test_invalid_source_and_changed_copy_reset_confirmation', async () => {
  importApi(); render(<VoicePanel {...props()} />); await fillImport();
  fireEvent.change(screen.getByLabelText('Caminho relativo do áudio'), {target: {value: '../voice.wav'}});
  expect((screen.getByLabelText('Confirmo o arquivo e a versão da copy') as HTMLInputElement).checked).toBe(false);
  fireEvent.click(screen.getByLabelText('Confirmo o arquivo e a versão da copy'));
  expect((importButton() as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('Versão da copy para voz'), {target: {value: '2'}});
  expect((screen.getByLabelText('Confirmo o arquivo e a versão da copy') as HTMLInputElement).checked).toBe(false);
});
it('test_import_unknown_result_does_not_resubmit', async () => {
  const calls = importApi(wrapper, 'queued', 503); render(<VoicePanel {...props()} />); await fillImport(); fireEvent.click(importButton());
  await screen.findByText(/command_unknown/); fireEvent.click(importButton()); expect(calls).toHaveLength(1);
  expect((importButton() as HTMLButtonElement).disabled).toBe(true);
});
it('test_adopt_wrapper_validates_campaign_kind_and_ids', async () => {
  importApi(); wrapReads({...wrapper, campaignId: 'campaign-two'});
  render(<VoicePanel {...props()} jobs={state({items: [{...job, jobId: 'wrapper-one', jobType: 'api.local.operation'}]})} />);
  fireEvent.change(screen.getByLabelText('Operação local de voz para inspecionar'), {target: {value: 'wrapper-one'}});
  await screen.findByText(/invalid_response/); expect(screen.queryByText('Fase 2: processar voz importada')).toBeNull();
});
it('test_reload_observes_without_post', async () => {
  const calls = importApi(); wrapReads(); const p = {...props(), jobs: state({items: [{...job, jobId: 'wrapper-one', jobType: 'api.local.operation'}]})};
  const view = render(<VoicePanel {...p} />); view.unmount(); render(<VoicePanel {...p} />);
  fireEvent.change(screen.getByLabelText('Operação local de voz para inspecionar'), {target: {value: 'wrapper-one'}});
  await screen.findByText('voice.import · queued'); expect(calls).toHaveLength(0);
});
it('test_pending_navigation_warns_and_late_import_is_ignored', async () => {
  let finish!: (response: Response) => void; fakeApi(undefined, () => new Promise(resolve => {finish = resolve;}));
  const view = render(<VoicePanel {...props()} />); await fillImport(); fireEvent.click(importButton());
  const event = new Event('beforeunload', {cancelable: true}); window.dispatchEvent(event); expect(event.defaultPrevented).toBe(true);
  view.unmount(); render(<VoicePanel {...props()} />);
  await act(async () => finish(Response.json({operation: 'voice_import', campaignId: 'campaign-one', jobId: 'wrapper-one'})));
  expect(screen.queryByText('Fase 2: processar voz importada')).toBeNull();
});

const voice: VoiceSummary = {voiceMasterId: 'voice-one', campaignId: 'campaign-one', copyMasterId: 'copy-one', copyMasterVersion: 1,
  generation: 1, provider: 'elevenlabs', status: 'review_required', processedAudioPath: 'voices/one.wav', processedSha256: 'b'.repeat(64),
  durationSeconds: 1.2, transcriptMatchStatus: 'matched', headlineSpoken: false, qcFindings: [], approvedAt: null, approvedBy: null,
  approvalReviewReason: null, rejectedAt: null, rejectedBy: null, rejectionReason: null};
const reviewProps = (v = voice) => ({...props(), voices: state({items: [v, {...v, voiceMasterId: 'voice-two'}]})});
async function fillReview() {
  fireEvent.change(screen.getByLabelText('Voice Master para revisar'), {target: {value: 'voice-one'}});
  fireEvent.change(screen.getByLabelText('Responsável pela revisão de voz'), {target: {value: 'reviewer'}});
}
const approveVoice = () => screen.getByRole('button', {name: 'Enfileirar aprovação de voz'});
const listen = () => screen.getByLabelText('Ouvi o WAV processado e revisei os resultados');
it('test_review_requires_actor_and_listening_confirmation', async () => {
  const calls = fakeApi(undefined, async () => Response.json({operation: 'voice_review', campaignId: 'campaign-one', jobId: 'wrapper-one'}));
  wrapReads({...wrapper, operation: 'voice_review', status: 'queued', result: null}); render(<VoicePanel {...reviewProps()} />);
  fireEvent.change(screen.getByLabelText('Voice Master para revisar'), {target: {value: 'voice-one'}}); fireEvent.click(listen());
  expect((approveVoice() as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('Responsável pela revisão de voz'), {target: {value: 'reviewer'}});
  expect((listen() as HTMLInputElement).checked).toBe(false); fireEvent.click(listen()); fireEvent.click(approveVoice());
  await screen.findByText(/Review enfileirado/);
  expect(calls[0]).toEqual({path: '/api/v1/campaigns/campaign-one/voices/voice-one/review', body: {operation: 'voice_review', campaignId: 'campaign-one', voiceId: 'voice-one', action: 'approve', actor: 'reviewer', reason: null}});
});
it('test_transcript_exception_is_narrow_and_requires_reason', async () => {
  fakeApi(); render(<VoicePanel {...reviewProps({...voice, provider: 'imported', transcriptMatchStatus: 'review_required', qcFindings: ['The narration transcript requires human review.']})} />);
  await fillReview(); fireEvent.click(listen()); expect((approveVoice() as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('Motivo da revisão de voz'), {target: {value: 'Checked narration'}}); fireEvent.click(listen());
  expect((approveVoice() as HTMLButtonElement).disabled).toBe(false);
});
it('test_mismatched_headline_or_extra_findings_cannot_be_approved', async () => {
  fakeApi(); const view = render(<VoicePanel {...reviewProps({...voice, transcriptMatchStatus: 'mismatched'})} />);
  await fillReview(); fireEvent.click(listen()); expect((approveVoice() as HTMLButtonElement).disabled).toBe(true);
  for (const delta of [{headlineSpoken: true}, {headlineSpoken: null}, {qcFindings: ['Other']}, {status: 'failed'}, {status: 'unknown'}]) {
    view.rerender(<VoicePanel {...reviewProps({...voice, ...delta})} />); expect((approveVoice() as HTMLButtonElement).disabled).toBe(true);
  }
  view.rerender(<VoicePanel {...reviewProps({...voice, processedAudioPath: null, processedSha256: null})} />);
  fireEvent.change(screen.getByLabelText('Motivo da revisão de voz'), {target: {value: 'No usable audio'}});
  fireEvent.click(screen.getByLabelText('Revisei os resultados para rejeitar'));
  expect((screen.getByRole('button', {name: 'Enfileirar rejeição de voz'}) as HTMLButtonElement).disabled).toBe(false);
});
it('test_review_accepted_is_not_approval_until_worker_and_fresh_get', async () => {
  fakeApi(undefined, async () => Response.json({operation: 'voice_review', campaignId: 'campaign-one', jobId: 'wrapper-one'}));
  wrapReads({...wrapper, operation: 'voice_review', status: 'queued', result: null}); render(<VoicePanel {...reviewProps()} />);
  await fillReview(); fireEvent.click(listen()); fireEvent.click(approveVoice());
  await screen.findByText(/voice_review · queued/); expect(screen.queryByText(/Estado observado.*approved/)).toBeNull();
});
it('test_lost_review_response_observes_state_without_claiming_authorship', async () => {
  const calls = fakeApi(undefined, async () => Response.json({error: {code: 'internal_error'}}, {status: 503}));
  const view = render(<VoicePanel {...reviewProps()} />); await fillReview(); fireEvent.click(listen()); fireEvent.click(approveVoice());
  await screen.findByText(/command_unknown/);
  view.rerender(<VoicePanel {...reviewProps()} voices={state({items: [{...voice, status: 'approved', approvedBy: 'someone-else', approvedAt: 'later'}]}, 2)} />);
  await screen.findByText(/Estado observado.*approved.*não confirma autoria/); fireEvent.click(approveVoice()); expect(calls).toHaveLength(1);
});
it('test_changing_selected_voice_clears_actor_reason_and_confirmations', async () => {
  fakeApi(); render(<VoicePanel {...reviewProps()} />); await fillReview();
  fireEvent.change(screen.getByLabelText('Motivo da revisão de voz'), {target: {value: 'Reason'}}); fireEvent.click(listen());
  fireEvent.change(screen.getByLabelText('Voice Master para revisar'), {target: {value: 'voice-two'}});
  expect((screen.getByLabelText('Responsável pela revisão de voz') as HTMLInputElement).value).toBe('');
  expect((screen.getByLabelText('Motivo da revisão de voz') as HTMLInputElement).value).toBe('');
  expect((listen() as HTMLInputElement).checked).toBe(false);
});
