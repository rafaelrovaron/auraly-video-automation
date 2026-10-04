import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from './api';
export type RemoteState<T> = { data: T | null; error: ApiError | null; loading: boolean; lastSuccessAt: number | null; refresh: () => void };
export function usePolling<T>(key: string, load: (signal: AbortSignal) => Promise<T>, intervalMs: number | null): RemoteState<T> {
  const empty = { data: null, error: null, loading: true, lastSuccessAt: null };
  const [snapshot, setSnapshot] = useState<Omit<RemoteState<T>, 'refresh'> & { key: string }>({ ...empty, key });
  const loader = useRef(load);
  loader.current = load;
  const trigger = useRef(() => {});
  const refresh = useCallback(() => trigger.current(), []);
  useEffect(() => {
    let active = true, running = false, queued = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | undefined;
    setSnapshot(previous => previous.key === key ? previous : { ...empty, key });
    const run = async () => {
      clearTimeout(timer);
      if (!active || document.hidden) return;
      if (running) { queued = true; return; }
      running = true;
      controller = new AbortController();
      setSnapshot(previous => ({ ...previous, loading: true }));
      try {
        const data = await loader.current(controller.signal);
        if (active) setSnapshot({ key, data, error: null, loading: false, lastSuccessAt: Date.now() });
      } catch (error) {
        if (active) setSnapshot(previous => ({ ...previous, loading: false,
          error: error instanceof ApiError ? error : new ApiError('connection_lost') }));
      } finally {
        running = false;
        if (active && !document.hidden) {
          if (queued) { queued = false; void run(); }
          else if (intervalMs !== null) timer = setTimeout(run, intervalMs);
        }
      }
    };
    trigger.current = () => { void run(); };
    const visibility = () => { clearTimeout(timer); if (!document.hidden) void run(); };
    document.addEventListener('visibilitychange', visibility);
    void run();
    return () => {
      active = false; clearTimeout(timer); controller?.abort();
      document.removeEventListener('visibilitychange', visibility);
    };
  }, [key, intervalMs]);
  return { ...(snapshot.key === key ? snapshot : empty), refresh };
}
