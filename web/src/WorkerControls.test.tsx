import { act, fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { WorkerControls } from './WorkerControls';
import type { WorkerControlsProps } from './WorkerControls';
import type { WorkerKind, WorkerObservation } from './api';

const kinds: WorkerKind[] = ['local_operations', 'voice_generate', 'voice_import', 'heygen_assets', 'heygen_videos'];
function props(observation: WorkerObservation = { scope: 'known', value: { state: 'idle', campaignId: null, kind: null, errorCode: null } }): WorkerControlsProps {
  return { campaignId: 'campaign-one', worker: { data: observation, error: null, loading: false, lastSuccessAt: 1, lastSuccessReadId: 1, refresh: () => 2 }, connected: true, onRefresh: vi.fn() };
}
const start = () => fireEvent.click(screen.getByRole('button', { name: 'Iniciar worker' }));
const confirm = () => fireEvent.click(screen.getByRole('button', { name: 'Confirmar início' }));

it.each(kinds)('requires confirmation and sends exactly the selected campaign and kind %s', async kind => {
  const requests: { path: string; body: unknown }[] = [];
  vi.stubGlobal('fetch', async (path: string, options: RequestInit) => {
    requests.push({ path, body: JSON.parse(options.body as string) });
    return Response.json({ state: 'running', campaignId: 'campaign-one', kind, errorCode: null }, { status: 202 });
  });
  render(<WorkerControls {...props()} />);
  fireEvent.change(screen.getByLabelText('Tipo de worker'), { target: { value: kind } });
  start();
  expect(screen.getByText(/Jobs já enfileirados podem consumir créditos/)).toBeTruthy();
  expect(requests).toHaveLength(0);
  confirm(); confirm();
  await act(async () => {});
  expect(requests).toEqual([{ path: '/api/v1/campaigns/campaign-one/worker/start', body: { campaignId: 'campaign-one', kind } }]);
});

it('cancelled confirmation and navigation cannot submit the old campaign', () => {
  const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher);
  const view = render(<WorkerControls {...props()} />);
  start(); fireEvent.click(screen.getByRole('button', { name: 'Cancelar' }));
  expect(screen.queryByRole('button', { name: 'Confirmar início' })).toBeNull();
  start(); view.rerender(<WorkerControls {...props()} campaignId="campaign-two" />);
  expect(screen.queryByRole('button', { name: 'Confirmar início' })).toBeNull();
  expect(fetcher).not.toHaveBeenCalled();
});

it('does not stop an unassociated or foreign campaign worker', () => {
  const view = render(<WorkerControls {...props({ scope: 'unassociated' })} />);
  expect((screen.getByRole('button', { name: 'Parar worker' }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole('button', { name: 'Iniciar worker' }) as HTMLButtonElement).disabled).toBe(false);
  view.rerender(<WorkerControls {...props({ scope: 'known', value: { state: 'running', campaignId: 'campaign-two', kind: 'heygen_videos', errorCode: null } })} />);
  expect((screen.getByRole('button', { name: 'Parar worker' }) as HTMLButtonElement).disabled).toBe(true);
});

it('stops only its running worker and describes draining without cancellation', async () => {
  const requests: object[] = [];
  vi.stubGlobal('fetch', async (_: string, options: RequestInit) => { requests.push(JSON.parse(options.body as string)); return Response.json({ state: 'stopping', campaignId: 'campaign-one', kind: 'local_operations', errorCode: null }); });
  const input = props({ scope: 'known', value: { state: 'running', campaignId: 'campaign-one', kind: 'local_operations', errorCode: null } });
  const view = render(<WorkerControls {...input} />);
  fireEvent.click(screen.getByRole('button', { name: 'Parar worker' }));
  await act(async () => {});
  expect(requests).toEqual([{ campaignId: 'campaign-one' }]);
  expect(screen.getByText(/Não cancela/)).toBeTruthy();
  view.rerender(<WorkerControls {...props({ scope: 'known', value: { state: 'stopping', campaignId: 'campaign-one', kind: 'local_operations', errorCode: null } })} />);
  expect((screen.getByRole('button', { name: 'Iniciar worker' }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole('button', { name: 'Parar worker' }) as HTMLButtonElement).disabled).toBe(true);
});

it('keeps 409 visible without falling back or retrying', async () => {
  let count = 0;
  vi.stubGlobal('fetch', async () => { count++; return Response.json({ error: { code: 'operation_conflict', field: null, message: 'private' } }, { status: 409 }); });
  render(<WorkerControls {...props()} />); start(); confirm();
  expect(await screen.findByText(/operation_conflict/)).toBeTruthy();
  expect(count).toBe(1);
  expect(screen.queryByText('private')).toBeNull();
});

it('does not retry a lost response and waits for a new read without claiming success', async () => {
  let attempts = 0;
  vi.stubGlobal('fetch', async () => { attempts++; throw new TypeError('lost response'); });
  const input = props(); const view = render(<WorkerControls {...input} />);
  start(); confirm();
  expect(await screen.findByText(/Resultado do comando desconhecido/)).toBeTruthy();
  expect((screen.getByRole('button', { name: 'Iniciar worker' }) as HTMLButtonElement).disabled).toBe(true);
  view.rerender(<WorkerControls {...input} worker={{ ...input.worker, lastSuccessAt: 2, lastSuccessReadId: 2 }} />);
  expect((screen.getByRole('button', { name: 'Iniciar worker' }) as HTMLButtonElement).disabled).toBe(false);
  expect(screen.getByText(/Resultado do comando desconhecido/)).toBeTruthy();
  expect(screen.queryByText(/sucesso|concluído/i)).toBeNull();
  expect(attempts).toBe(1);
});

it('disables writes while disconnected and recovers after pertinent reads succeed', () => {
  const view = render(<WorkerControls {...props()} connected={false} />);
  expect((screen.getByRole('button', { name: 'Iniciar worker' }) as HTMLButtonElement).disabled).toBe(true);
  view.rerender(<WorkerControls {...props()} connected />);
  expect((screen.getByRole('button', { name: 'Iniciar worker' }) as HTMLButtonElement).disabled).toBe(false);
});

it('discards a late command response even after navigating away and back to the same campaign', async () => {
  let resolve!: (response: Response) => void;
  vi.stubGlobal('fetch', () => new Promise<Response>(done => { resolve = done; }));
  const input = props(); const view = render(<WorkerControls {...input} />);
  start(); confirm();
  view.rerender(<WorkerControls {...input} campaignId="campaign-two" />);
  view.rerender(<WorkerControls {...input} />);
  await act(async () => resolve(Response.json({ state: 'running', campaignId: 'campaign-one', kind: 'local_operations', errorCode: null })));
  expect(screen.queryByText(/Comando recebido/)).toBeNull();
  expect(input.onRefresh).not.toHaveBeenCalled();
});

it.each(['during POST', 'after POST'])('does not reconcile from a GET started before the outcome and completed %s', async completion => {
  let reject!: (reason: Error) => void;
  const fetcher = vi.fn(() => new Promise<Response>((_, fail) => { reject = fail; }));
  vi.stubGlobal('fetch', fetcher);
  const input = props(); input.worker.refresh = vi.fn(() => 3);
  const view = render(<WorkerControls {...input} />);
  start(); confirm();
  const earlierRead = { ...input.worker, lastSuccessAt: 2, lastSuccessReadId: 2 };
  if (completion === 'during POST') view.rerender(<WorkerControls {...input} worker={earlierRead} />);
  await act(async () => reject(new TypeError('lost response')));
  if (completion === 'after POST') view.rerender(<WorkerControls {...input} worker={earlierRead} />);
  expect((screen.getByRole('button', { name: 'Iniciar worker' }) as HTMLButtonElement).disabled).toBe(true);
  expect(input.worker.refresh).toHaveBeenCalledOnce();
  expect(fetcher).toHaveBeenCalledOnce();
  view.rerender(<WorkerControls {...input} worker={{ ...input.worker, lastSuccessAt: 3, lastSuccessReadId: 3 }} />);
  expect((screen.getByRole('button', { name: 'Iniciar worker' }) as HTMLButtonElement).disabled).toBe(false);
  expect(screen.getByText(/Resultado do comando desconhecido/)).toBeTruthy();
});
