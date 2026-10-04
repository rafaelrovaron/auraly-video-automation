import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { buildSourceText, CampaignCreateForm, CopyVersionForm } from './CampaignForms';
import { campaignDetail } from './api';
import type { CampaignDetail } from './api';
import type { RemoteState } from './usePolling';

const copy = { headline: 'H', hook: 'K', body: 'B', cta: 'C' };
const initial: CampaignDetail = {
  campaignId: 'campaign-one', character: 'susan-smith', storedStatus: 'draft', sceneCount: 1,
  createdAt: '2026-10-04T00:00:00Z', updatedAt: '2026-10-04T00:00:00Z', operationalStatus: 'needs_input', nextPending: null,
  proofObject: 'cards', voicePreset: 'voice', editPreset: 'edit',
  copyMasters: [{ copyMasterId: 'old', version: 1, approvalState: 'approved', approvedBy: 'tester',
    sourceText: 'Old source', sha256: 'a'.repeat(64), headline: 'Old headline', hook: 'Old hook', body: 'Old body', cta: 'Old CTA' }],
  sceneVariants: [{ sceneVariantId: 'scene-one', variantId: 'first', location: 'Room', action: 'Talk', prompt: 'Portrait' }],
};
const newCopy = { copyMasterId: 'new', version: 2, approvalState: 'approved', approvedBy: 'tester',
  sourceText: 'Headline:\nH\n\nHook:\nK\n\nBody:\nB\n\nCTA:\nC', sha256: 'b'.repeat(64), ...copy };

function state(data = initial, readId = 1): RemoteState<CampaignDetail> {
  return { data, error: null, loading: false, lastSuccessAt: Date.now(), lastSuccessReadId: readId, refresh: () => 2 };
}
function fillCopy() {
  for (const [label, value] of Object.entries({ Headline: ' H ', Hook: ' K ', Body: ' B ', CTA: ' C ', 'Aprovado por': 'tester' }))
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
  fireEvent.click(screen.getByLabelText('Confirmo a aprovação da copy'));
}
function fillCreate() {
  fillCopy();
  for (const [label, value] of Object.entries({ 'ID da campanha': 'campaign-one', 'Objeto de prova': 'cards',
    'Voice preset': 'voice', 'Edit preset': 'edit', 'ID da variante 1': 'first', 'Localização 1': 'Room',
    'Ação 1': 'Talk', 'Prompt 1': 'Portrait' })) fireEvent.change(screen.getByLabelText(label), { target: { value } });
}
const createButton = () => screen.getByRole('button', { name: 'Criar campanha com copy aprovada' }) as HTMLButtonElement;
const copyButton = () => screen.getByRole('button', { name: 'Adicionar versão de copy aprovada' }) as HTMLButtonElement;

it('builds canonical source text from trimmed fields', () => {
  expect(buildSourceText({ headline: ' H ', hook: 'K', body: 'B', cta: 'C' }))
    .toBe('Headline:\nH\n\nHook:\nK\n\nBody:\nB\n\nCTA:\nC');
});

it('requires explicit approval, actor and required campaign fields before writing', () => {
  const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher);
  render(<CampaignCreateForm onCreated={() => {}} />);
  expect(createButton().disabled).toBe(true);
  expect((screen.getByLabelText('Confirmo a aprovação da copy') as HTMLInputElement).checked).toBe(false);
  fillCopy();
  fireEvent.submit(screen.getByRole('form', { name: 'Criar campanha' }));
  expect(fetcher).not.toHaveBeenCalled();
  expect(screen.getByRole('alert').textContent).toMatch(/obrigatórios/);
});

it('creates an approved campaign once, with initial scenes and empty config/budget', async () => {
  let body: Record<string, unknown> | null = null;
  const onCreated = vi.fn();
  const fetcher = vi.fn(async (_path: string, options: RequestInit) => {
    body = JSON.parse(options.body as string);
    return Response.json({ ...initial, copyMasters: [{ ...newCopy, version: 1 }] }, { status: 201 });
  });
  vi.stubGlobal('fetch', fetcher);
  render(<CampaignCreateForm onCreated={onCreated} />); fillCreate();
  fireEvent.click(createButton()); fireEvent.click(createButton());
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith('campaign-one'));
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(body).toMatchObject({ campaignId: 'campaign-one', status: 'draft', budget: {}, config: {},
    copyMaster: { headline: 'H', hook: 'K', body: 'B', cta: 'C', approvalState: 'approved', approvedBy: 'tester',
      sourceText: 'Headline:\nH\n\nHook:\nK\n\nBody:\nB\n\nCTA:\nC' },
    sceneVariants: [{ variantId: 'first', location: 'Room', action: 'Talk', prompt: 'Portrait', status: 'not_started' }] });
  expect(JSON.stringify(body)).not.toContain('spokenText');
});

it.each(['same-id', 'same-location'])('rejects %s initial scenes without POST', problem => {
  const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher);
  render(<CampaignCreateForm onCreated={() => {}} />); fillCreate();
  fireEvent.click(screen.getByRole('button', { name: 'Adicionar cena' }));
  for (const [label, value] of Object.entries({ 'ID da variante 2': problem === 'same-id' ? 'first' : 'second',
    'Localização 2': problem === 'same-location' ? ' room  ' : 'Park', 'Ação 2': 'Talk', 'Prompt 2': 'Portrait' }))
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
  fireEvent.submit(screen.getByRole('form', { name: 'Criar campanha' }));
  expect(fetcher).not.toHaveBeenCalled(); expect(screen.getByRole('alert').textContent).toMatch(/distintos/);
});

it('reconciles a lost creation response only by a new GET, never a retry POST', async () => {
  const methods: string[] = []; const onCreated = vi.fn();
  vi.stubGlobal('fetch', async (_: string, options: RequestInit) => {
    methods.push(options.method ?? 'GET');
    if (options.method === 'POST') throw new TypeError('private detail');
    return Response.json({ ...initial, copyMasters: [{ ...newCopy, version: 1 }] });
  });
  render(<CampaignCreateForm onCreated={onCreated} />); fillCreate(); fireEvent.click(createButton());
  expect(await screen.findByText(/command_unknown/)).toBeTruthy(); expect(createButton().disabled).toBe(true);
  expect(onCreated).not.toHaveBeenCalled(); expect(methods).toEqual(['POST']);
  fireEvent.click(screen.getByRole('button', { name: 'Reconciliar criação' }));
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith('campaign-one'));
  expect(methods).toEqual(['POST', 'GET']);
});

it('keeps malformed successful creation responses unknown', async () => {
  vi.stubGlobal('fetch', async () => Response.json({}, { status: 201 }));
  const onCreated = vi.fn(); render(<CampaignCreateForm onCreated={onCreated} />); fillCreate(); fireEvent.click(createButton());
  expect(await screen.findByText(/command_unknown/)).toBeTruthy(); expect(onCreated).not.toHaveBeenCalled();
});

it('ignores a creation response after unmount', async () => {
  let resolve!: (value: Response) => void;
  vi.stubGlobal('fetch', () => new Promise<Response>(done => { resolve = done; }));
  const onCreated = vi.fn(); const view = render(<CampaignCreateForm onCreated={onCreated} />);
  fillCreate(); fireEvent.click(createButton()); view.unmount();
  await act(async () => { resolve(Response.json({ ...initial, copyMasters: [{ ...newCopy, version: 1 }] })); });
  expect(onCreated).not.toHaveBeenCalled();
});

it('waits for a post-settlement GET before reconciling a new copy version', async () => {
  let reject!: (reason: Error) => void;
  vi.stubGlobal('fetch', () => new Promise<Response>((_, fail) => { reject = fail; }));
  const before = state(); before.refresh = () => 3;
  const view = render(<CopyVersionForm campaignId="campaign-one" detail={before} />); fillCopy(); fireEvent.click(copyButton());
  const after = { ...initial, copyMasters: [...initial.copyMasters, newCopy] };
  view.rerender(<CopyVersionForm campaignId="campaign-one" detail={{ ...state(after, 2), refresh: before.refresh }} />);
  await act(async () => { reject(new TypeError('lost')); });
  expect(copyButton().disabled).toBe(true);
  view.rerender(<CopyVersionForm campaignId="campaign-one" detail={{ ...state(after, 3), refresh: before.refresh }} />);
  expect(await screen.findByText(/Versão de copy registrada/)).toBeTruthy();
  expect((screen.getByLabelText('Confirmo a aprovação da copy') as HTMLInputElement).checked).toBe(false);
  expect(after.copyMasters[0].headline).toBe('Old headline');
});

it('leaves ambiguous copy reconciliation unknown', async () => {
  vi.stubGlobal('fetch', async () => { throw new TypeError('lost'); });
  const view = render(<CopyVersionForm campaignId="campaign-one" detail={state()} />); fillCopy(); fireEvent.click(copyButton());
  await screen.findByText(/command_unknown/);
  view.rerender(<CopyVersionForm campaignId="campaign-one" detail={state({ ...initial,
    copyMasters: [...initial.copyMasters, newCopy, { ...newCopy, copyMasterId: 'another', version: 3 }] }, 2)} />);
  expect(copyButton().disabled).toBe(true); expect(screen.queryByText(/Versão de copy registrada/)).toBeNull();
});

it('ignores a late copy response after changing campaign', async () => {
  let resolve!: (response: Response) => void;
  vi.stubGlobal('fetch', () => new Promise<Response>(done => { resolve = done; }));
  const view = render(<CopyVersionForm campaignId="campaign-one" detail={state()} />); fillCopy(); fireEvent.click(copyButton());
  view.rerender(<CopyVersionForm campaignId="campaign-two" detail={state({ ...initial, campaignId: 'campaign-two' })} />);
  await act(async () => { resolve(Response.json({ ...initial, copyMasters: [...initial.copyMasters, newCopy] })); });
  expect(screen.queryByText(/Versão de copy registrada/)).toBeNull();
  expect((screen.getByLabelText('Confirmo a aprovação da copy') as HTMLInputElement).checked).toBe(false);
});

it('warns before unloading an unsaved draft and permits cancellation of link navigation', async () => {
  window.location.hash = '#/campaigns'; vi.stubGlobal('confirm', () => false);
  render(<><CampaignCreateForm onCreated={() => {}} /><a href="#/campaigns/other">Other</a></>);
  fireEvent.change(screen.getByLabelText('Headline'), { target: { value: 'Unsaved' } });
  const closing = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(closing);
  expect(closing.defaultPrevented).toBe(true);
  fireEvent.click(screen.getByRole('link', { name: 'Other' }));
  await act(async () => {}); expect(window.location.hash).toBe('#/campaigns');
});

it.each(['headline', 'sha256'])('rejects a copy DTO missing %s', key => {
  const malformed = structuredClone(initial); delete (malformed.copyMasters[0] as unknown as Record<string, unknown>)[key];
  expect(campaignDetail(malformed)).toBe(false);
});
