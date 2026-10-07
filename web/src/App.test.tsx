import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { App, parseRoute } from './App';

it('opens global profiles without a campaign',async()=>{
  window.location.hash='#/profiles';
  vi.stubGlobal('fetch',async()=>Response.json({items:[]}));
  render(<App/>);
  expect(await screen.findByRole('heading',{name:'Profiles de edição'})).toBeTruthy();
  expect(parseRoute('#/profiles')).toEqual({page:'profiles'});
  expect(parseRoute('#/profiles/extra')).toEqual({page:'invalid'});
});

export const campaign = {
  campaignId: 'campaign-one', character: 'susan-smith', storedStatus: 'draft', sceneCount: 1,
  createdAt: '2026-10-03T00:00:00Z', updatedAt: '2026-10-03T00:00:00Z', operationalStatus: 'needs_input',
  nextPending: { code: 'voice_missing', stage: 'voice', entityId: 'copy-one', message: 'Voice missing.' },
};

it('shows campaign metadata without writes and navigates to its ID', async () => {
  const writes: string[] = [];
  vi.stubGlobal('fetch', async (path: string, options: RequestInit) => {
    if (options.method === 'POST') writes.push(path);
    return Response.json({ items: [campaign] });
  });
  render(<App />);
  const link = await screen.findByRole('link', { name: 'campaign-one' });
  expect(screen.getByText('susan-smith')).toBeTruthy();
  expect(screen.getByText(/Voz ausente/)).toBeTruthy();
  expect(link.getAttribute('href')).toBe('#/campaigns/campaign-one');
  window.location.hash = '#/campaigns/campaign-one';
  fireEvent(window, new HashChangeEvent('hashchange'));
  expect(await screen.findByRole('heading', { name: 'campaign-one' })).toBeTruthy();
  expect(writes).toEqual([]);
});

it('shows empty state and creation guidance without POST', async () => {
  vi.stubGlobal('fetch', async () => Response.json({ items: [] }));
  render(<App />);
  expect(await screen.findByText(/Nenhuma campanha/)).toBeTruthy();
  expect(screen.getByText('Criar campanha', { selector: 'summary' })).toBeTruthy();
});

it.each(['#/campaigns/%ZZ', '#/campaigns/A', '#/unknown', '#/campaigns/a/b'])('rejects invalid navigation %s locally', hash => {
  window.location.hash = hash;
  const fetcher = vi.fn();
  vi.stubGlobal('fetch', fetcher);
  render(<App />);
  expect(screen.getByText(/Rota inválida/)).toBeTruthy();
  expect(fetcher).not.toHaveBeenCalled();
  expect(parseRoute(hash)).toEqual({ page: 'invalid' });
});

it('opens a direct hash after reload', () => {
  window.location.hash = '#/campaigns/campaign-one';
  render(<App />);
  expect(screen.getByRole('heading', { name: 'campaign-one' })).toBeTruthy();
});

it('renders server text as text rather than HTML', async () => {
  vi.stubGlobal('fetch', async () => Response.json({ items: [{ ...campaign, character: '<img src=x onerror=alert(1)>' }] }));
  const { container } = render(<App />);
  expect(await screen.findByText('<img src=x onerror=alert(1)>')).toBeTruthy();
  expect(container.querySelector('img')).toBeNull();
});
