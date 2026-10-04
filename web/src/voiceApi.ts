import { object } from './api';

export type CampaignBudgetView = {state: 'configured'; currency: string; limitCents: number}
  | {state: 'missing' | 'invalid'; currency: null; limitCents: null};
export function campaignBudgetView(value: unknown): value is CampaignBudgetView {
  if (!object(value)) return false;
  if (value.state === 'configured') return typeof value.currency === 'string' && /^[A-Z]{3}$/.test(value.currency)
    && typeof value.limitCents === 'number' && Number.isSafeInteger(value.limitCents) && value.limitCents > 0;
  return ['missing', 'invalid'].includes(String(value.state)) && value.currency === null && value.limitCents === null;
}
export type VoiceSubmission = {operation: 'voice_generate' | 'voice_import' | 'voice_review'; campaignId: string; jobId: string; voiceMasterId?: string | null};
export function voiceSubmission(value: unknown): value is VoiceSubmission {
  return object(value) && ['voice_generate', 'voice_import', 'voice_review'].includes(String(value.operation))
    && typeof value.campaignId === 'string' && !!value.campaignId && typeof value.jobId === 'string' && !!value.jobId
    && (value.operation !== 'voice_generate' || (typeof value.voiceMasterId === 'string' && !!value.voiceMasterId));
}

export type VoiceImportResult = {operation: 'voice_import'; voiceMasterId: string; jobId: string};
export type VoiceReviewResult = {operation: 'voice_review'; voiceMasterId: string; status: string};
export type VoiceOperationView = {jobId: string; campaignId: string; operation: 'voice_import' | 'voice_review';
  status: string; result: VoiceImportResult | VoiceReviewResult | null; errorCode: string | null};
const nonempty = (value: unknown): value is string => typeof value === 'string' && value.length > 0;
export function voiceOperationView(value: unknown): value is VoiceOperationView {
  if (!object(value) || !nonempty(value.jobId) || !nonempty(value.campaignId)
    || !['voice_import', 'voice_review'].includes(String(value.operation)) || !nonempty(value.status)
    || !(value.errorCode === null || typeof value.errorCode === 'string')) return false;
  if (value.result === null) return value.status !== 'completed';
  return object(value.result) && value.result.operation === value.operation && nonempty(value.result.voiceMasterId)
    && (value.operation === 'voice_import' ? nonempty(value.result.jobId) : nonempty(value.result.status));
}
