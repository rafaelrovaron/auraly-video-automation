import { object } from './api';

export type HeyGenOperationKind = 'heygen_assets' | 'heygen_video_plan' | 'heygen_video_submit' | 'heygen_reconcile';
export type HeyGenSubmission = {jobId: string; campaignId: string; operation: HeyGenOperationKind};
export type HeyGenAssetsResult = {operation: 'heygen_assets'; uploadCount: number; reusedCount: number; jobId: string | null};
export type HeyGenOperationView = HeyGenSubmission & {status: string; errorCode: string | null; result: HeyGenAssetsResult | null};
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
  return object(value.result) && value.result.operation === value.operation && value.operation === 'heygen_assets'
    && count(value.result.uploadCount) && count(value.result.reusedCount) && (value.result.jobId === null || text(value.result.jobId));
}
