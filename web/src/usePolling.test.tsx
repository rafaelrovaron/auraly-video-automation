import { act, renderHook } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { ApiError } from './api';
import { usePolling } from './usePolling';

const flush = async () => { await act(async () => { await Promise.resolve(); }); };
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}

it.each([5000, 2000])('polls only after the %i ms interval', async interval => {
  vi.useFakeTimers();
  let reads = 0;
  const { result } = renderHook(() => usePolling('one', async () => ++reads, interval));
  await flush();
  expect(result.current.data).toBe(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(interval - 1); });
  expect(reads).toBe(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(result.current.data).toBe(2);
});

it('does not overlap inflight cycles or manual refresh and aborts on unmount', async () => {
  vi.useFakeTimers();
  const first = deferred<number>();
  const signals: AbortSignal[] = [];
  const { result, unmount } = renderHook(() => usePolling('one', signal => { signals.push(signal); return first.promise; }, 2000));
  act(() => { result.current.refresh(); result.current.refresh(); });
  await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
  expect(signals).toHaveLength(1);
  await act(async () => first.resolve(7));
  expect(result.current.data).toBe(7);
  expect(signals).toHaveLength(2); // refresh requests collapse into one follow-up
  unmount();
  expect(signals.at(-1)?.aborted).toBe(true);
});

it('pauses hidden polling and immediately refreshes when visible', async () => {
  vi.useFakeTimers();
  let hidden = false;
  vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden);
  let reads = 0;
  const { result } = renderHook(() => usePolling('one', async () => ++reads, 2000));
  await flush();
  act(() => { hidden = true; document.dispatchEvent(new Event('visibilitychange')); });
  await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
  expect(reads).toBe(1);
  act(() => { hidden = false; document.dispatchEvent(new Event('visibilitychange')); });
  await flush();
  expect(result.current.data).toBe(2);
});

it('clears the old campaign immediately and discards its late response', async () => {
  const old = deferred<string>();
  const next = deferred<string>();
  const signals: AbortSignal[] = [];
  const { result, rerender } = renderHook(({ id }) => usePolling(id, signal => {
    signals.push(signal); return id === 'A' ? old.promise : next.promise;
  }, null), { initialProps: { id: 'A' } });
  rerender({ id: 'B' });
  expect(result.current.data).toBeNull();
  expect(signals[0].aborted).toBe(true);
  await act(async () => next.resolve('campaign B'));
  await act(async () => old.resolve('campaign A'));
  expect(result.current.data).toBe('campaign B');
});

it('retains last good data and timestamp on partial failure until recovery', async () => {
  let fail = false;
  const { result } = renderHook(() => usePolling('asset', async () => {
    if (fail) throw new ApiError('storage_unavailable', 503);
    return 'approved-image';
  }, null));
  await flush();
  const timestamp = result.current.lastSuccessAt;
  act(() => { fail = true; result.current.refresh(); });
  await flush();
  expect(result.current.data).toBe('approved-image');
  expect(result.current.lastSuccessAt).toBe(timestamp);
  expect(result.current.error?.code).toBe('storage_unavailable');
  act(() => { fail = false; result.current.refresh(); });
  await flush();
  expect(result.current.error).toBeNull();
});
