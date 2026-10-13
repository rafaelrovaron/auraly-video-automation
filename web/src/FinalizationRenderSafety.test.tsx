import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { FinalizationPanel } from './FinalizationPanel';
import { editPlanFixture } from './editingTestSupport';

it('keeps plan selection locked while a render submission is unresolved', async () => {
  const plan = editPlanFixture();
  const { videoId, renderId, planHash, outputCount, maxOutputs, copyRef, voiceRef, source, outputs, captionInput } = plan;
  const summary = { videoId, renderId, planHash, outputCount, maxOutputs, copyRef, voiceRef, source,
    timingStatus: captionInput.timingStatus,
    outputs: outputs.map(({ manifest, ...output }) => { void manifest; return output; }) };
  const other = { ...summary, planHash: 'c'.repeat(64) };
  let submissions = 0;
  vi.stubGlobal('fetch', async (path: string, options: RequestInit) => {
    if (options.method === 'POST') { submissions++; return new Promise<Response>(() => {}); }
    if (path.endsWith('/editing/plans')) return Response.json({ items: [summary, other] });
    if (path.includes('/editing/renders?')) return Response.json({ items: [] });
    return Response.json(plan);
  });
  render(<FinalizationPanel campaignId={plan.campaignId} active preferredPlan={null} />);
  const select = await screen.findByRole('combobox', { name: 'Plano salvo para finalizar' });
  await screen.findAllByRole('option', { name: /variação/ });
  fireEvent.change(select, { target: { value: `${plan.videoId}/${plan.planHash}` } });
  await waitFor(() => expect((screen.getByRole('button', { name: 'Renderizar' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: 'Renderizar' }));
  expect(submissions).toBe(1);
  expect((select as HTMLSelectElement).disabled).toBe(true);
});

it('keeps plan selection locked while a QC submission is unresolved', async () => {
  const plan = editPlanFixture();
  const { videoId, renderId, planHash, outputCount, maxOutputs, copyRef, voiceRef, source, outputs, captionInput } = plan;
  const summary = { videoId, renderId, planHash, outputCount, maxOutputs, copyRef, voiceRef, source,
    timingStatus: captionInput.timingStatus,
    outputs: outputs.map(({ manifest, ...output }) => { void manifest; return output; }) };
  const jobId = '22222222-2222-4222-8222-222222222222';
  const outputVariantId = 'ab-a';
  const renderKey = 'f'.repeat(64);
  const completed = { schemaVersion: '1.0', campaignId: plan.campaignId, videoId, planHash,
    executionId: '11111111-1111-4111-8111-111111111111', jobId, status: 'completed', errorCode: null,
    renderStatus: 'succeeded', result: { schemaVersion: '1.0', planHash, dryRun: false, hasFailures: false,
      outputs: [{ key: 'a', outputVariantId, renderKey, status: 'rendered', fitMeasured: true,
        path: 'masters/a.mp4', error: null }] } };
  const reviewState = { schemaVersion: '1.0', campaignId: plan.campaignId, videoId, planHash, jobId,
    outputVariantId, renderKey, reports: [], review: null, runs: [] };
  let qcSubmissions = 0;
  vi.stubGlobal('fetch', async (path: string, options: RequestInit) => {
    if (options.method === 'POST' && path.endsWith('/qc')) { qcSubmissions++; return new Promise<Response>(() => {}); }
    if (path.endsWith('/editing/plans')) return Response.json({ items: [summary] });
    if (path.includes('/editing/renders?')) return Response.json({ items: [completed] });
    if (path.endsWith('/review-state')) return Response.json(reviewState);
    if (path.endsWith(`/editing/renders/${jobId}`)) return Response.json(completed);
    return Response.json(plan);
  });
  render(<FinalizationPanel campaignId={plan.campaignId} active preferredPlan={null} />);
  const planSelect = await screen.findByRole('combobox', { name: 'Plano salvo para finalizar' });
  await screen.findByRole('option', { name: /variação/ });
  fireEvent.change(planSelect, { target: { value: `${videoId}/${planHash}` } });
  await screen.findByRole('option', { name: /Render concluído/ });
  fireEvent.change(screen.getByRole('combobox', { name: 'Execução de render' }), { target: { value: jobId } });
  fireEvent.click(await screen.findByRole('button', { name: 'Executar QC' }));
  expect(qcSubmissions).toBe(1);
  expect((planSelect as HTMLSelectElement).disabled).toBe(true);
  expect((screen.getByRole('combobox', { name: 'Execução de render' }) as HTMLSelectElement).disabled).toBe(true);
});
