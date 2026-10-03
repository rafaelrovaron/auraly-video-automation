import { describe, expect, it, vi } from 'vitest';
import { ApiError, campaignPath, post, read } from './api';

const signal = () => new AbortController().signal;

describe('API boundary', () => {
  it('reads a relative GET with no query and accepts an empty collection', async () => {
    const calls: [string, RequestInit | undefined][] = [];
    vi.stubGlobal('fetch', async (path: string, options?: RequestInit) => {
      calls.push([path, options]);
      return Response.json({ items: [] });
    });
    expect(await read('/api/v1/campaigns', signal())).toEqual({ items: [] });
    expect(calls[0][0]).toBe('/api/v1/campaigns');
    expect(calls[0][1]?.method).toBe('GET');
    expect(campaignPath('campaign-one', '/jobs')).toBe('/api/v1/campaigns/campaign-one/jobs');
  });

  it('sanitizes storage errors instead of displaying backend message', async () => {
    vi.stubGlobal('fetch', async () => Response.json({ error: {
      code: 'storage_unavailable', field: null, message: 'private-token',
    } }, { status: 503 }));
    const error = await read('/health', signal()).catch(e => e);
    expect(error).toBeInstanceOf(ApiError);
    if (!(error instanceof ApiError)) throw new Error('Expected sanitized ApiError');
    expect(error.status).toBe(503);
    expect(error.code).toBe('storage_unavailable');
    expect(error.message).not.toContain('private-token');
  });

  it.each(['not-json', '{}'])('rejects malformed collection %s', async body => {
    vi.stubGlobal('fetch', async () => new Response(body));
    await expect(read('/api/v1/campaigns', signal(), value => (
      typeof value === 'object' && value !== null && 'items' in value && Array.isArray(value.items)
    ))).rejects.toMatchObject({ code: 'invalid_response' });
  });

  it('sends a single JSON POST with campaign and kind', async () => {
    const requests: RequestInit[] = [];
    vi.stubGlobal('fetch', async (_: string, options: RequestInit) => {
      requests.push(options);
      return Response.json({ state: 'running' }, { status: 202 });
    });
    await post(campaignPath('campaign-one', '/worker/start'), { campaignId: 'campaign-one', kind: 'local_operations' });
    expect(requests).toHaveLength(1);
    expect(requests[0].method).toBe('POST');
    expect(requests[0].headers).toEqual({ 'Content-Type': 'application/json' });
    expect(JSON.parse(requests[0].body as string)).toEqual({ campaignId: 'campaign-one', kind: 'local_operations' });
  });

  it('does not retry a lost POST response', async () => {
    let attempts = 0;
    vi.stubGlobal('fetch', async () => { attempts++; throw new TypeError('private-path'); });
    await expect(post('/api/v1/campaigns/campaign-one/worker/start', {})).rejects.toMatchObject({ code: 'command_unknown' });
    expect(attempts).toBe(1);
  });

  it('times out a POST after 15 seconds without retry', async () => {
    vi.useFakeTimers();
    let attempts = 0;
    vi.stubGlobal('fetch', (_: string, options: RequestInit) => {
      attempts++;
      return new Promise((_resolve, reject) => options.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError'))));
    });
    const result = post('/api/v1/campaigns/campaign-one/worker/start', {}).catch(e => e);
    await vi.advanceTimersByTimeAsync(15000);
    expect(await result).toMatchObject({ code: 'command_unknown' });
    expect(attempts).toBe(1);
  });
});
