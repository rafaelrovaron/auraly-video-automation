import { object, renderSummary } from './api';
import type { RenderSummary } from './api';

export type HeyGenOperationKind = 'heygen_assets' | 'heygen_video_plan' | 'heygen_video_submit' | 'heygen_reconcile';
export type HeyGenSubmission = {jobId: string; campaignId: string; operation: HeyGenOperationKind};
export type HeyGenAssetsResult = {operation: 'heygen_assets'; uploadCount: number; reusedCount: number; jobId: string | null};
export type HeyGenPlanResult = {operation: 'heygen_video_plan'; newCount: number; reusedCount: number; reservedCount: number;
  maxPaidRenders: number; totalAudioSeconds: number; sceneVariantIds: string[]};
export type HeyGenReservedRender = RenderSummary & {campaignId: string};
type MediaProbe = {formatName: string; durationSec: number; sizeBytes: number;
  video: {codec: string; width: number; height: number; fps: number; nominalFps: number; isVfr: boolean; rotation: number};
  audio: {codec: string; sampleRate: number; channels: number} | null; warnings: string[]; hasAudio: boolean};
export type HeyGenRenderView = HeyGenReservedRender & {manualBinding: boolean; imageSha256: string; audioSha256: string;
  createdAt: string; updatedAt: string; source: {path: string; sha256: string; sizeBytes: number; probe: MediaProbe} | null};
export type HeyGenSubmitResult = {operation: 'heygen_video_submit'; renders: HeyGenRenderView[]};
export type HeyGenReconcileResult = {operation: 'heygen_reconcile'; render: HeyGenRenderView};
export type HeyGenOperationView = HeyGenSubmission & {status: string; errorCode: string | null; result: HeyGenAssetsResult | HeyGenPlanResult | HeyGenSubmitResult | HeyGenReconcileResult | null};
export const HEYGEN_DEFAULT_CONFIG = {schemaVersion: 1, generationMode: 'image', engineSelection: 'provider_default', aspectRatio: '9:16', resolution: '1080p',
  outputFormat: 'mp4', fit: 'cover', expressiveness: 'medium', motionPrompt: null, concurrency: 2,
  pollInitialSeconds: 10, pollMaxSeconds: 60, pollTimeoutSeconds: 1800} as const;
const kinds = ['heygen_assets', 'heygen_video_plan', 'heygen_video_submit', 'heygen_reconcile'];
const statuses = ['queued', 'running', 'completed', 'failed', 'blocked', 'retry_scheduled', 'cancelled'];
const text = (value: unknown): value is string => typeof value === 'string' && value.length > 0;
const count = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
const positive = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value > 0;
const sha = (value: unknown): value is string => text(value) && /^[a-f0-9]{64}$/.test(value);
const date = (value: unknown): value is string => text(value) && Number.isFinite(Date.parse(value));
export function heygenRenderView(value: unknown): value is HeyGenRenderView {
  if (!object(value) || !renderSummary(value) || !['planned', 'submitting', 'processing', 'download_pending', 'ready', 'failed', 'reconciliation_required'].includes(String(value.status))
    || !['renderId', 'campaignId', 'sceneVariantId', 'imageCandidateId', 'voiceMasterId', 'jobId'].every(key => text(value[key]))
    || typeof value.manualBinding !== 'boolean' || !sha(value.imageSha256) || !sha(value.audioSha256) || !date(value.createdAt) || !date(value.updatedAt)) return false;
  if (value.source === null) return value.status !== 'ready';
  const source = value.source;
  if (!object(source) || !text(source.path) || /^[\\/]|:/.test(source.path) || source.path.split(/[\\/]/).some(part => !part || part === '.' || part === '..')
    || !sha(source.sha256) || !count(source.sizeBytes) || source.sizeBytes === 0 || !object(source.probe)) return false;
  const probe = source.probe, video = probe.video, audio = probe.audio;
  return text(probe.formatName) && positive(probe.durationSec) && count(probe.sizeBytes) && typeof probe.hasAudio === 'boolean'
    && Array.isArray(probe.warnings) && probe.warnings.every(item => typeof item === 'string') && object(video) && text(video.codec)
    && count(video.width) && video.width > 0 && count(video.height) && video.height > 0 && positive(video.fps) && positive(video.nominalFps)
    && typeof video.isVfr === 'boolean' && typeof video.rotation === 'number' && Number.isSafeInteger(video.rotation)
    && (audio === null ? !probe.hasAudio : object(audio) && probe.hasAudio && text(audio.codec)
      && count(audio.sampleRate) && audio.sampleRate > 0 && count(audio.channels) && audio.channels > 0);
}
export function heygenSubmission(value: unknown): value is HeyGenSubmission {
  return object(value) && text(value.jobId) && text(value.campaignId) && kinds.includes(String(value.operation));
}
export function heygenOperationView(value: unknown): value is HeyGenOperationView {
  if (!object(value)) return false;
  const submission: unknown = value;
  if (!heygenSubmission(submission) || !statuses.includes(String(value.status))
    || !(value.errorCode === null || text(value.errorCode))) return false;
  if (value.result === null) return value.status !== 'completed';
  const result = value.result;
  if (!object(result) || result.operation !== value.operation) return false;
  if (value.operation === 'heygen_assets') return count(result.uploadCount) && count(result.reusedCount) && (result.jobId === null || text(result.jobId));
  if (value.operation === 'heygen_video_plan') return count(result.newCount) && count(result.reusedCount) && count(result.reservedCount)
    && count(result.maxPaidRenders) && result.maxPaidRenders > 0 && typeof result.totalAudioSeconds === 'number'
    && Number.isFinite(result.totalAudioSeconds) && result.totalAudioSeconds >= 0
    && Array.isArray(result.sceneVariantIds) && result.sceneVariantIds.every(text)
    && new Set(result.sceneVariantIds).size === result.sceneVariantIds.length;
  if (value.operation === 'heygen_video_submit') return Array.isArray(result.renders)
    && result.renders.every(heygenRenderView)
    && new Set(result.renders.map(render => render.renderId)).size === result.renders.length;
  if (value.operation === 'heygen_reconcile') return heygenRenderView(result.render);
  return false;
}
