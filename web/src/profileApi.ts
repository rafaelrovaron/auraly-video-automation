import { ApiError, object, post, read } from './api';

export type AssetRef = {path: string; sha256: string};
export type TextStyle = {
  enabled: boolean; styleId: string; font: AssetRef | null; fontWeight: number; fontSizePx: number;
  lineHeight: number; color: string; strokeWidthPx: number; strokeColor: string;
  shadowEnabled: boolean; shadowColor: string; shadowOffsetX: number; shadowOffsetY: number;
  backgroundEnabled: boolean; backgroundColor: string; backgroundPaddingPx: number;
  anchor: 'top' | 'center' | 'bottom'; x: number; y: number; safeTop: number; safeRight: number;
  safeBottom: number; safeLeft: number; maxLines: number; fitPolicy: 'wrap' | 'shrink' | 'error';
};
export type HeadlineStyle = TextStyle & {startSec: number; endSec: number | null};
export type CaptionStyle = TextStyle & {highlightEnabled: boolean; highlightColor: string};
export type OutputStyle = {width: number; height: number; fps: number; format: 'mp4'; codec: 'h264'};
export type MusicStyle = {enabled: boolean; asset: AssetRef | null; volumeDb: number; duckUnderVoiceDb: number;
  loop: boolean; trimStartSec: number; trimEndSec: number | null; fadeInSec: number; fadeOutSec: number};
export type FramingStyle = {fit: 'cover' | 'contain'; scale: number; x: number; y: number; zoomStart: number; zoomEnd: number};
export type EditDefaults = {output: OutputStyle; headline: HeadlineStyle; captions: CaptionStyle; music: MusicStyle; framing: FramingStyle};
export type EditProfile = {schemaVersion: '1.0'; profileId: string; name: string; version: number; createdAt: string; defaults: EditDefaults};
export type ProfileView = {profile: EditProfile; profileHash: string};

export function newProfile(profileId: string, name: string, createdAt: string): EditProfile {
  const text: TextStyle = {
    enabled: false, styleId: 'plain', font: null, fontWeight: 700, fontSizePx: 60, lineHeight: 1.1,
    color: '#FFFFFF', strokeWidthPx: 0, strokeColor: '#000000', shadowEnabled: false,
    shadowColor: '#000000', shadowOffsetX: 0, shadowOffsetY: 0, backgroundEnabled: false,
    backgroundColor: '#000000', backgroundPaddingPx: 0, anchor: 'top', x: 0.5, y: 0.1,
    safeTop: 0.05, safeRight: 0.05, safeBottom: 0.05, safeLeft: 0.05, maxLines: 3, fitPolicy: 'wrap',
  };
  return {schemaVersion: '1.0', profileId, name, version: 1, createdAt, defaults: {
    output: {width: 1080, height: 1920, fps: 30, format: 'mp4', codec: 'h264'},
    headline: {...text, startSec: 0, endSec: null},
    captions: {...text, anchor: 'bottom', y: 0.8, highlightEnabled: false, highlightColor: '#FFFF00'},
    music: {enabled: false, asset: null, volumeDb: -22, duckUnderVoiceDb: -8, loop: true,
      trimStartSec: 0, trimEndSec: null, fadeInSec: 0.4, fadeOutSec: 1},
    framing: {fit: 'cover', scale: 1, x: 0.5, y: 0.5, zoomStart: 1, zoomEnd: 1},
  }};
}
const text = (v: unknown): v is string => typeof v === 'string' && !!v.trim() && !v.includes('\0');
const number = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const positive = (v: unknown): v is number => number(v) && v > 0;
const integer = (v: unknown): v is number => positive(v) && Number.isSafeInteger(v);
const fraction = (v: unknown): v is number => number(v) && v >= 0 && v <= 1;
const color = (v: unknown) => typeof v === 'string' && /^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$/.test(v);
const sha = (v: unknown) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
const reserved = (v: string) => /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(v);
const id = (v: unknown) => typeof v === 'string' && /^[a-z0-9][a-z0-9_-]{0,63}$/.test(v) && !reserved(v);
function keys(v: unknown, names: string[]): v is Record<string, unknown> {
  return object(v) && Object.keys(v).length === names.length && names.every(k => Object.hasOwn(v,k));
}
function asset(v: unknown): boolean {
  return v === null || (keys(v,['path','sha256']) && text(v.path) && sha(v.sha256)
    && !/[:\\]/.test(v.path) && v.path.split('/').every(p => !!p && p !== '.' && p !== '..'
      && !/[ .]$/.test(p) && !reserved(p)));
}
function style(v: Record<string,unknown>): boolean {
  return ['enabled','shadowEnabled','backgroundEnabled'].every(k=>typeof v[k]==='boolean')
    && text(v.styleId) && asset(v.font) && integer(v.fontWeight) && v.fontWeight>=100 && v.fontWeight<=900
    && positive(v.fontSizePx) && positive(v.lineHeight) && integer(v.maxLines)
    && ['color','strokeColor','shadowColor','backgroundColor'].every(k=>color(v[k]))
    && ['strokeWidthPx','backgroundPaddingPx'].every(k=>number(v[k]) && v[k]>=0)
    && ['shadowOffsetX','shadowOffsetY'].every(k=>number(v[k]))
    && ['x','y','safeTop','safeRight','safeBottom','safeLeft'].every(k=>fraction(v[k]))
    && Number(v.safeTop)+Number(v.safeBottom)<1 && Number(v.safeLeft)+Number(v.safeRight)<1
    && ['top','center','bottom'].includes(String(v.anchor)) && ['wrap','shrink','error'].includes(String(v.fitPolicy));
}
export function editProfile(v: unknown): v is EditProfile {
  if (!keys(v,['schemaVersion','profileId','name','version','createdAt','defaults']) || v.schemaVersion!=='1.0'
    || !id(v.profileId) || !text(v.name) || !integer(v.version) || typeof v.createdAt!=='string'
    || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(v.createdAt)
    || !Number.isFinite(Date.parse(v.createdAt)) || !keys(v.defaults,['output','headline','captions','music','framing'])) return false;
  const d = v.defaults, template=newProfile('plain','Plain','2026-10-07T00:00:00Z').defaults;
  if (!keys(d.output,Object.keys(template.output)) || !keys(d.headline,Object.keys(template.headline))
    || !keys(d.captions,Object.keys(template.captions)) || !keys(d.music,Object.keys(template.music))
    || !keys(d.framing,Object.keys(template.framing))) return false;
  const o=d.output,h=d.headline,c=d.captions,m=d.music,f=d.framing;
  return integer(o.width) && integer(o.height) && positive(o.fps) && o.format==='mp4' && o.codec==='h264'
    && style(h) && number(h.startSec) && h.startSec>=0 && (h.endSec===null || positive(h.endSec))
    && style(c) && typeof c.highlightEnabled==='boolean' && color(c.highlightColor)
    && typeof m.enabled==='boolean' && asset(m.asset) && typeof m.loop==='boolean'
    && number(m.volumeDb) && number(m.duckUnderVoiceDb)
    && ['trimStartSec','fadeInSec','fadeOutSec'].every(k=>number(m[k]) && m[k]>=0)
    && (m.trimEndSec===null || positive(m.trimEndSec))
    && ['cover','contain'].includes(String(f.fit)) && fraction(f.x) && fraction(f.y)
    && ['scale','zoomStart','zoomEnd'].every(k=>number(f[k]) && f[k]>=1 && f[k]<=1.25);
}
export function profileView(v: unknown): v is ProfileView {
  return keys(v,['profile','profileHash']) && sha(v.profileHash) && editProfile(v.profile);
}
function equal(a: unknown,b: unknown): boolean {
  if (object(a) && object(b)) return Object.keys(a).length===Object.keys(b).length
    && Object.keys(a).every(k=>Object.hasOwn(b,k) && equal(a[k],b[k]));
  return a===b;
}
export function sameProfileContent(a: EditProfile,b: EditProfile): boolean {
  const {createdAt: _a,...left}=a, {createdAt: _b,...right}=b;
  return equal(left,right);
}
const root='/api/v1/editing/profiles';
export async function listProfiles(signal: AbortSignal): Promise<ProfileView[]> {
  return (await read<{items:ProfileView[]}>(root,signal,v=>keys(v,['items']) && Array.isArray(v.items)
    && v.items.every(profileView))).items;
}
export function getProfile(id: string,version: number,signal: AbortSignal): Promise<ProfileView> {
  return read(`${root}/${encodeURIComponent(id)}/${version}`,signal,v=>profileView(v)
    && v.profile.profileId===id && v.profile.version===version);
}
export async function publishProfile(profile: EditProfile,baseVersion?: number): Promise<ProfileView> {
  const v=await post<unknown>(baseVersion===undefined?root:`${root}/${encodeURIComponent(profile.profileId)}/${baseVersion}/versions`,profile);
  if (!profileView(v) || !sameProfileContent(v.profile,profile)) throw new ApiError('command_unknown');
  return v;
}
