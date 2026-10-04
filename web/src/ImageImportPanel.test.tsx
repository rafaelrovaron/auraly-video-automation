import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { ImageImportPanel } from './ImageImportPanel';
import type { CampaignDetail, Items, JobSummary, SceneImages } from './api';
import type { RemoteState } from './usePolling';
const manifest = { operation: 'image_manifest', manifestPath: `pipeline/work/inbox/image-import-${'a'.repeat(64)}.json`, imagesPath: 'pipeline/work/inbox/images',
  manifestSha256: 'a'.repeat(64), items: [{ variantId: 'first', path: 'images/a.png' }] };
const dry = { operation: 'image_import', mode: 'dry_run', total: 1, created: 0, reused: 0, approved: 0, valid: true,
  manifestSha256: 'a'.repeat(64), validationId: 'check-one', issues: [],
  items: [{ variantId: 'first', sceneVariantId: 'scene-one', imageCandidateId: null, action: 'create', sha256: 'b'.repeat(64), width: 360, height: 640, sizeBytes: 500, format: 'png' }] };

const detail: CampaignDetail = { campaignId: 'campaign-one', character: 'susan-smith', storedStatus: 'draft', sceneCount: 1,
  createdAt: '', updatedAt: '', operationalStatus: 'needs_input', nextPending: null, proofObject: 'cards', voicePreset: 'voice', editPreset: 'edit', copyMasters: [],
  sceneVariants: [{ sceneVariantId: 'scene-one', variantId: 'first', location: 'Room', action: 'Talk', prompt: 'Portrait', timeAtmosphere: null, proofObject: null }] };
const candidate = { imageCandidateId: 'image-one', sceneVariantId: 'scene-one', sourceKind: 'manual_import', reviewStatus: 'pending_review', sourcePath: 'campaigns/source.png',
  sha256: 'b'.repeat(64), width: 360, height: 640, sizeBytes: 500, format: 'png', rejectionReason: null };
const job: JobSummary = { jobId: 'saved', jobType: 'api.local.operation', campaignId: 'campaign-one', sceneVariantId: null, status: 'completed', attemptCount: 1, maxAttempts: 1,
  retrySafety: 'manual_only', queuedAt: '', startedAt: null, completedAt: '', nextRetryAt: null, lastErrorCode: null };
function state<T>(data: T, id = 1): RemoteState<T> { return { data, error: null, loading: false, lastSuccessAt: 1, lastSuccessReadId: id, refresh: () => 2 }; }
const props = { campaignId: 'campaign-one', detail: state(detail), images: state<Items<SceneImages>>({ items: [{ sceneVariantId: 'scene-one', items: [candidate] }] }), jobs: state<Items<JobSummary>>({ items: [job] }) };
const button = (name: string) => screen.getByRole('button', { name }) as HTMLButtonElement;

function api(invalid = false) {
  const posts: { path: string; body: Record<string, unknown> }[] = [];
  const results: Record<string, unknown> = { saved: manifest };
  vi.stubGlobal('fetch', async (path: string, options: RequestInit) => {
    if (options.method === 'POST') {
      const body = JSON.parse(options.body as string); posts.push({ path, body });
      if (path.endsWith('/review')) return Response.json({ ...candidate, reviewStatus: body.action === 'reject' ? 'rejected' : 'approved' });
      const id = `job-${posts.length}`;
      results[id] = path.endsWith('/prepare') ? { operation: 'image_prepare', manifestPath: 'pipeline/work/inbox/image-import.json', imagesPath: 'pipeline/work/inbox/images', variantCount: 1 }
        : path.endsWith('/manifests') ? { ...manifest, items: body.items }
          : body.mode === 'dry_run' ? { ...dry, validationId: body.validationId,
            ...(invalid ? { valid: false, items: [], issues: [{ code: '__proto__', variantId: 'first' }] } : {}) }
            : { ...dry, mode: 'execute', valid: null, manifestSha256: null, validationId: null, created: 1, items: [{ ...dry.items[0], imageCandidateId: 'image-one' }] };
      return Response.json({ campaignId: 'campaign-one', jobId: id, operation: (results[id] as { operation: string }).operation }, { status: 202 });
    }
    const id = path.split('/').at(-1)!; const result = results[id] as { operation: string };
    return Response.json({ campaignId: 'campaign-one', jobId: id, operation: result.operation, status: 'completed', errorCode: null, result });
  });
  return posts;
}
async function savedBatch() {
  fireEvent.change(screen.getByLabelText('Pasta de importação (relativa ao work root)'), { target: { value: 'inbox' } });
  fireEvent.click(button('Preparar pasta'));
  await screen.findByDisplayValue('pipeline/work/inbox');
  fireEvent.change(screen.getByLabelText('Arquivo de first'), { target: { value: 'images/a.png' } });
  fireEvent.click(button('Salvar associações'));
  await screen.findByText(/Associações salvas/);
}
async function validBatch() {
  await savedBatch(); fireEvent.click(button('Validar batch')); await screen.findByText('Batch válido');
}

it('requires explicit prepare/save/validate/confirm and sends the source snapshot without auto-start', async () => {
  const posts = api(); render(<ImageImportPanel {...props} />);
  await validBatch(); expect(button('Importar batch validado').disabled).toBe(true);
  fireEvent.click(screen.getByLabelText('Confirmo a importação deste batch'));
  fireEvent.click(button('Importar batch validado')); await screen.findByText(/Importação concluída/);
  expect(posts).toHaveLength(4); expect(posts.some(item => item.path.includes('/worker/'))).toBe(false);
  expect(posts[3].body).toMatchObject({ mode: 'execute', manifestSha256: 'a'.repeat(64), expectedSources: [{ variantId: 'first', sha256: 'b'.repeat(64) }] });
});
it('invalidates validation immediately after an association changes', async () => {
  api(); render(<ImageImportPanel {...props} />); await validBatch();
  fireEvent.click(screen.getByLabelText('Confirmo a importação deste batch'));
  fireEvent.change(screen.getByLabelText('Arquivo de first'), { target: { value: 'images/other.png' } });
  expect(button('Importar batch validado').disabled).toBe(true); expect(screen.queryByText('Batch válido')).toBeNull();
});
it('completed invalid validation remains non-importable and unknown codes are safe text', async () => {
  const posts = api(true); render(<ImageImportPanel {...props} />); await savedBatch();
  fireEvent.click(button('Validar batch')); await screen.findByText('Validação concluída com erros');
  expect(button('Importar batch validado').disabled).toBe(true); expect(posts).toHaveLength(3);
  expect(screen.getByText(/Não foi possível validar/)).toBeTruthy();
});
it('uses a new validation ID for every explicit revalidation', async () => {
  const posts = api(); render(<ImageImportPanel {...props} />); await validBatch();
  fireEvent.click(button('Validar batch')); await waitFor(() => expect(posts).toHaveLength(4));
  expect(posts[2].body.validationId).not.toBe(posts[3].body.validationId);
});
it('recovers saved associations after reload but never adopts old validation', async () => {
  api(); render(<ImageImportPanel {...props} />);
  fireEvent.change(screen.getByLabelText('Job de imagens'), { target: { value: 'saved' } });
  await waitFor(() => expect(button('Usar associações salvas').disabled).toBe(false));
  fireEvent.click(button('Usar associações salvas'));
  expect(screen.getByDisplayValue('images/a.png')).toBeTruthy(); expect(button('Importar batch validado').disabled).toBe(true);
});
it('does not replay a lost POST and permits inspection only after a fresh jobs read', async () => {
  const posts: string[] = [];
  vi.stubGlobal('fetch', async (path: string, options: RequestInit) => {
    if (options.method === 'POST') { posts.push(path); throw new TypeError('private detail'); }
    return Response.json({ campaignId: 'campaign-one', jobId: 'saved', operation: 'image_manifest', status: 'completed', errorCode: null, result: manifest });
  });
  const view = render(<ImageImportPanel {...props} />);
  fireEvent.change(screen.getByLabelText('Pasta de importação (relativa ao work root)'), { target: { value: 'inbox' } });
  fireEvent.click(button('Preparar pasta')); await screen.findByText(/command_unknown/);
  expect(posts).toHaveLength(1); expect(button('Preparar pasta').disabled).toBe(true);
  expect((screen.getByLabelText('Job de imagens') as HTMLSelectElement).disabled).toBe(true);
  view.rerender(<ImageImportPanel {...props} jobs={state({ items: [job] }, 2)} />);
  expect((screen.getByLabelText('Job de imagens') as HTMLSelectElement).disabled).toBe(false);
  expect(posts).toHaveLength(1);
});
it('requires explicit actor/confirmation for approval and reason for rejection', async () => {
  const posts = api(); render(<ImageImportPanel {...props} />);
  fireEvent.change(screen.getByLabelText('Candidato de imagem'), { target: { value: 'image-one' } });
  expect(button('Aplicar revisão').disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('Ator da revisão'), { target: { value: 'tester' } });
  fireEvent.click(screen.getByLabelText('Confirmo esta revisão de imagem'));
  fireEvent.click(button('Aplicar revisão'));
  await screen.findByText(/Revisão recebida/);
  expect(posts[0].body).toMatchObject({ campaignId: 'campaign-one', action: 'approve', actor: 'tester' });
});
it('ignores a delayed validation after editing associations', async () => {
  api(); render(<ImageImportPanel {...props} />); await savedBatch();
  const fetcher = globalThis.fetch; let resolve!: (value: Response) => void; let id = '';
  vi.stubGlobal('fetch', (path: string, options: RequestInit) => {
    if (options.method === 'POST') id = JSON.parse(options.body as string).validationId;
    if (path.includes('/operations/job-3')) return new Promise<Response>(done => { resolve = done; });
    return fetcher(path, options);
  });
  fireEvent.click(button('Validar batch')); await waitFor(() => expect(resolve).toBeTypeOf('function'));
  fireEvent.change(screen.getByLabelText('Arquivo de first'), { target: { value: 'images/new.png' } });
  await act(async () => { resolve(Response.json({ campaignId: 'campaign-one', jobId: 'job-3', operation: 'image_import', status: 'completed', errorCode: null, result: { ...dry, validationId: id } })); });
  expect(screen.queryByText('Batch válido')).toBeNull(); expect(button('Importar batch validado').disabled).toBe(true);
});

it('does not enable import for a partial DTO or another manifest hash', async () => {
  api(); render(<ImageImportPanel {...props} />); await savedBatch();
  const fetcher = globalThis.fetch;
  vi.stubGlobal('fetch', async (path: string, options: RequestInit) => {
    const response = await fetcher(path, options);
    if (path.includes('/operations/job-3')) {
      const value = await response.json(); value.result.manifestSha256 = 'c'.repeat(64);
      return Response.json(value);
    }
    return response;
  });
  fireEvent.click(button('Validar batch'));
  await screen.findByText(/Operação image_import/);
  expect(screen.queryByText('Batch válido')).toBeNull(); expect(button('Importar batch validado').disabled).toBe(true);
});

it('requires a reason when rejecting and resets confirmation when choosing replace', async () => {
  const posts = api(); render(<ImageImportPanel {...props} />);
  fireEvent.change(screen.getByLabelText('Candidato de imagem'), { target: { value: 'image-one' } });
  fireEvent.change(screen.getByLabelText('Ator da revisão'), { target: { value: 'tester' } });
  fireEvent.change(screen.getByLabelText('Ação da revisão'), { target: { value: 'reject' } });
  fireEvent.click(screen.getByLabelText('Confirmo esta revisão de imagem'));
  expect(button('Aplicar revisão').disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('Motivo da revisão'), { target: { value: 'Fora do briefing' } });
  fireEvent.click(screen.getByLabelText('Confirmo esta revisão de imagem'));
  expect(button('Aplicar revisão').disabled).toBe(false);
  fireEvent.change(screen.getByLabelText('Ação da revisão'), { target: { value: 'replace' } });
  expect(button('Aplicar revisão').disabled).toBe(true); expect(posts).toHaveLength(0);
});

it('reconciles lost review only with a post-settlement image read without claiming authorship', async () => {
  let posts = 0;
  vi.stubGlobal('fetch', async () => { posts++; throw new TypeError('private'); });
  const view = render(<ImageImportPanel {...props} />);
  fireEvent.change(screen.getByLabelText('Candidato de imagem'), { target: { value: 'image-one' } });
  fireEvent.change(screen.getByLabelText('Ator da revisão'), { target: { value: 'tester' } });
  fireEvent.click(screen.getByLabelText('Confirmo esta revisão de imagem'));
  fireEvent.click(button('Aplicar revisão')); await screen.findByText(/command_unknown/);
  const approved = { items: [{ sceneVariantId: 'scene-one', items: [{ ...candidate, reviewStatus: 'approved' }] }] };
  view.rerender(<ImageImportPanel {...props} images={state(approved, 1)} />);
  expect(button('Aplicar revisão').disabled).toBe(true); expect(screen.queryByText(/Estado solicitado observado/)).toBeNull();
  view.rerender(<ImageImportPanel {...props} images={state(approved, 2)} />);
  await screen.findByText(/Esta consulta não informa autoria/); expect(posts).toBe(1);
});

it('ignores a prepare response after unmount rather than refreshing another campaign', async () => {
  let resolve!: (value: Response) => void;
  vi.stubGlobal('fetch', () => new Promise<Response>(done => { resolve = done; }));
  const refresh = vi.fn(() => 2);
  const view = render(<ImageImportPanel {...props} jobs={{ ...props.jobs, refresh }} />);
  fireEvent.change(screen.getByLabelText('Pasta de importação (relativa ao work root)'), { target: { value: 'inbox' } });
  fireEvent.click(button('Preparar pasta')); view.unmount();
  await act(async () => { resolve(Response.json({ campaignId: 'campaign-one', jobId: 'job', operation: 'image_prepare' }, { status: 202 })); });
  expect(refresh).not.toHaveBeenCalled();
});

it.each([undefined, 'nested/inbox'])('does not reconcile a lost prepare using an old same-suffix directory (%s)', async outputPath => {
  vi.stubGlobal('fetch', async (_: string, options: RequestInit) => {
    if (options.method === 'POST') throw new TypeError('lost');
    return Response.json({ campaignId: 'campaign-one', jobId: 'saved', operation: 'image_prepare', status: 'completed', errorCode: null,
      result: { operation: 'image_prepare', manifestPath: 'pipeline/work/nested/inbox/image-import.json', imagesPath: 'pipeline/work/nested/inbox/images', variantCount: 1, outputPath } });
  });
  const view = render(<ImageImportPanel {...props} />);
  fireEvent.change(screen.getByLabelText('Pasta de importação (relativa ao work root)'), { target: { value: 'inbox' } });
  fireEvent.click(button('Preparar pasta')); await screen.findByText(/command_unknown/);
  view.rerender(<ImageImportPanel {...props} jobs={state({ items: [job] }, 2)} />);
  fireEvent.change(screen.getByLabelText('Job de imagens'), { target: { value: 'saved' } });
  await screen.findByText(/Operação image_prepare/);
  expect(screen.queryByText(/Pasta preparada. Copie/)).toBeNull();
  expect((screen.getByLabelText('Pasta preparada (relativa ao projeto)') as HTMLInputElement).value).toBe('');
  expect(button('Preparar pasta').disabled).toBe(true);
});

it('reconciles a lost prepare only with an exact persisted output path', async () => {
  vi.stubGlobal('fetch', async (_: string, options: RequestInit) => {
    if (options.method === 'POST') throw new TypeError('lost');
    return Response.json({ campaignId: 'campaign-one', jobId: 'saved', operation: 'image_prepare', status: 'completed', errorCode: null,
      result: { operation: 'image_prepare', manifestPath: 'custom/work/inbox/image-import.json', imagesPath: 'custom/work/inbox/images', variantCount: 1, outputPath: 'inbox' } });
  });
  const view = render(<ImageImportPanel {...props} />);
  fireEvent.change(screen.getByLabelText('Pasta de importação (relativa ao work root)'), { target: { value: 'inbox' } });
  fireEvent.click(button('Preparar pasta')); await screen.findByText(/command_unknown/);
  view.rerender(<ImageImportPanel {...props} jobs={state({ items: [job] }, 2)} />);
  fireEvent.change(screen.getByLabelText('Job de imagens'), { target: { value: 'saved' } });
  await screen.findByDisplayValue('custom/work/inbox');
});
