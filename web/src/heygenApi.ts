import { object, renderSummary } from './api';
import type { RenderSummary } from './api';

export type HeyGenOperationKind = 'heygen_assets' | 'heygen_video_plan' | 'heygen_video_submit' | 'heygen_reconcile';
export type HeyGenSubmission = {jobId: string; campaignId: string; operation: HeyGenOperationKind};
export type HeyGenAssetsResult = {operation: 'heygen_assets'; uploadCount: number; reusedCount: number; jobId: string | null};
export type HeyGenPlanResult = {operation: 'heygen_video_plan'; newCount: number; reusedCount: number; reservedCount: number;
  maxPaidRenders: number; totalAudioSeconds: number; sceneVariantIds: string[]};
export type HeyGenReservedRender = RenderSummary & {campaignId: string};
export type HeyGenSubmitResult = {operation: 'heygen_video_submit'; renders: HeyGenReservedRender[]};
export type HeyGenOperationView = HeyGenSubmission & {status: string; errorCode: string | null; result: HeyGenAssetsResult | HeyGenPlanResult | HeyGenSubmitResult | null};
export const HEYGEN_DEFAULT_CONFIG = {schemaVersion: 1, generationMode: 'image', engineSelection: 'provider_default', aspectRatio: '9:16', resolution: '1080p',
  outputFormat: 'mp4', fit: 'cover', expressiveness: 'medium', motionPrompt: null, concurrency: 2,
  pollInitialSeconds: 10, pollMaxSeconds: 60, pollTimeoutSeconds: 1800} as const;
const kinds = ['heygen_assets', 'heygen_video_plan', 'heygen_video_submit', 'heygen_reconcile'];
const statuses = ['queued', 'running', 'completed', 'failed', 'blocked', 'retry_scheduled', 'cancelled'];
const text = (value: unknown): value is string => typeof value === 'string' && value.length > 0;
const count = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
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
    && result.renders.every(render => renderSummary(render) && object(render) && text(render.campaignId))
    && new Set(result.renders.map(render => render.renderId)).size === result.renders.length;
  return false;
}
