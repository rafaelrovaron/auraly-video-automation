import { expect, it, vi } from 'vitest';
import { getProfile, listProfiles, newProfile, profileView, publishProfile, sameProfileContent } from './profileApi';
import { profileFixture } from './profileTestSupport';

const signal = () => new AbortController().signal;
it('defaults_match_contract', () => {
  expect(newProfile('plain', 'Plain', '2026-10-07T00:00:00Z')).toEqual(profileFixture().profile);
});
it.each([
  (v: any) => { v.profileHash = 'bad'; },
  (v: any) => { v.profile.defaults.music.volumeDb = Infinity; },
  (v: any) => { v.profile.defaults.headline.fontSizePx = NaN; },
  (v: any) => { v.profile.defaults.output.fps = '30'; },
  (v: any) => { delete v.profile.defaults.headline.font; },
  (v: any) => { v.profile.extra = true; },
  (v: any) => { v.profile.schemaVersion = '2.0'; },
  (v: any) => { v.profile.profileId = 'con'; },
  (v: any) => { v.profile.version = 1.5; },
  (v: any) => { v.profile.createdAt = '2026-10-07'; },
  (v: any) => { v.profile.defaults.headline.safeLeft = 0.95; },
  (v: any) => { v.profile.defaults.headline.font = { path: 'fonts/a.ttf' }; },
  ...['https://a/font', '../a.ttf', 'C:/a.ttf', 'fonts\\a.ttf', 'fonts/CON.ttf'].map(path =>
    (v: any) => { v.profile.defaults.headline.font = { path, sha256: 'a'.repeat(64) }; }),
])('rejects_invalid_profile_view %i', change => {
  const v = profileFixture(); change(v); expect(profileView(v)).toBe(false);
});
it('content_ignores_only_date', () => {
  const a = profileFixture().profile, b = structuredClone(a);
  b.createdAt = '2026-10-08T00:00:00Z';
  expect(sameProfileContent(a,b)).toBe(true);
  b.defaults.headline.color = '#FFFFFF80'; expect(sameProfileContent(a,b)).toBe(false);
  b.defaults.headline.color = '#FFFFFF'; b.defaults.music.volumeDb = 0;
  expect(sameProfileContent(a,b)).toBe(false);
});
it('reads_exact_identity and validates list', async () => {
  vi.stubGlobal('fetch', async () => Response.json({ items: [profileFixture()] }));
  expect(await listProfiles(signal())).toHaveLength(1);
  vi.stubGlobal('fetch', async () => Response.json(profileFixture()));
  expect((await getProfile('plain',1,signal())).profile.version).toBe(1);
  await expect(getProfile('other',1,signal())).rejects.toMatchObject({code:'invalid_response'});
  await expect(getProfile('plain',2,signal())).rejects.toMatchObject({code:'invalid_response'});
});
it.each(['wrong-id','wrong-content','malformed','lost'])('malformed_post_is_unknown %s', async mode => {
  let calls = 0;
  vi.stubGlobal('fetch', async () => {
    calls++; const v = profileFixture();
    if(mode==='lost') throw new TypeError('private');
    if(mode==='wrong-id') v.profile.profileId='other';
    if(mode==='wrong-content') v.profile.defaults.music.volumeDb=0;
    return Response.json(mode==='malformed'?{}:v,{status:201});
  });
  await expect(publishProfile(profileFixture().profile)).rejects.toMatchObject({code:'command_unknown'});
  expect(calls).toBe(1);
});
it('publishes exact new version and preserves explicit rejection', async () => {
  const v = profileFixture(); v.profile.version=2;
  const calls: string[]=[];
  vi.stubGlobal('fetch', async (path: string, opts: RequestInit) => {
    calls.push(path); expect(JSON.parse(opts.body as string).version).toBe(2);
    return Response.json(v,{status:201});
  });
  expect(await publishProfile(v.profile,1)).toEqual(v);
  expect(calls).toEqual(['/api/v1/editing/profiles/plain/1/versions']);
  vi.stubGlobal('fetch',async()=>Response.json({error:{code:'invalid_request'}},{status:422}));
  await expect(publishProfile(v.profile,1)).rejects.toMatchObject({code:'invalid_request'});
});
