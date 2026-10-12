import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { FinalizationPanel } from './FinalizationPanel';
import { editPlanFixture } from './editingTestSupport';

vi.mock('./RenderPanel', () => ({ RenderPanel: ({ plan }: { plan: { planHash: string } | null }) =>
  <div>Plano em finalização: {plan?.planHash ?? 'nenhum'}</div> }));

const plan = editPlanFixture();
const { videoId, renderId, planHash, outputCount, maxOutputs, copyRef, voiceRef, source } = plan;
const summary = { videoId, renderId, planHash, outputCount, maxOutputs, copyRef, voiceRef, source,
  timingStatus: plan.captionInput.timingStatus, outputs: plan.outputs.map(({ manifest, ...output }) => { void manifest; return output; }) };

it('opens a saved plan from finalization after a reload without writing', async () => {
  const calls: string[] = [];
  vi.stubGlobal('fetch', async (path: string, options: RequestInit) => {
    calls.push(`${options.method ?? 'GET'} ${path}`);
    return Response.json(path.endsWith('/editing/plans') ? { items: [summary] } : plan);
  });
  render(<FinalizationPanel campaignId="campaign-one" active preferredPlan={null} />);
  const select = await screen.findByRole('combobox', { name: 'Plano salvo para finalizar' });
  expect(await screen.findByRole('option', { name: /1 variação/ })).toBeTruthy();
  fireEvent.change(select, { target: { value: `${videoId}/${planHash}` } });
  expect(await screen.findByText(`Plano em finalização: ${planHash}`)).toBeTruthy();
  expect(calls.every(call => call.startsWith('GET '))).toBe(true);
});

it('accepts a newly saved editing plan without another submit', async () => {
  vi.stubGlobal('fetch', async () => Response.json({ items: [] }));
  const view = render(<FinalizationPanel campaignId="campaign-one" active={false} preferredPlan={null} />);
  view.rerender(<FinalizationPanel campaignId="campaign-one" active preferredPlan={plan} />);
  expect(await screen.findByText(`Plano em finalização: ${planHash}`)).toBeTruthy();
});

it('clears loading when a pending plan selection is cancelled', async () => {
  vi.stubGlobal('fetch', async (path: string) => path.endsWith('/editing/plans')
    ? Response.json({ items: [summary] })
    : new Promise<Response>(() => {}));
  render(<FinalizationPanel campaignId="campaign-one" active preferredPlan={null} />);
  const select = await screen.findByRole('combobox', { name: 'Plano salvo para finalizar' });
  await screen.findByRole('option', { name: /1 variação/ });
  fireEvent.change(select, { target: { value: `${videoId}/${planHash}` } });
  expect(screen.getByText('Consultando configuração salva…')).toBeTruthy();
  fireEvent.change(select, { target: { value: '' } });
  expect(screen.queryByText('Consultando configuração salva…')).toBeNull();
});
