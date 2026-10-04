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
