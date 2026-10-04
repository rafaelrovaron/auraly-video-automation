import { expect, it } from 'vitest';
import { campaignBudgetView, voiceSubmission } from './voiceApi';
import { voiceSummary } from './api';

it('test_budget_guard_rejects_invalid_and_unsafe_integer', () => {
  expect(campaignBudgetView({ state: 'configured', currency: 'USD', limitCents: 1000 })).toBe(true);
  expect(campaignBudgetView({ state: 'missing', currency: null, limitCents: null })).toBe(true);
  for (const value of [true, 0, 1.5, 9007199254740992, null])
    expect(campaignBudgetView({ state: 'configured', currency: 'USD', limitCents: value })).toBe(false);
  expect(campaignBudgetView({ state: 'missing', currency: 'USD', limitCents: 1000 })).toBe(false);
  expect(campaignBudgetView({ state: 'invalid' })).toBe(false);
});

it('submission requires operation and generation voice ID', () => {
  expect(voiceSubmission({ operation: 'voice_generate', campaignId: 'campaign-one', jobId: 'job-one' })).toBe(false);
  expect(voiceSubmission({ operation: 'voice_generate', campaignId: 'campaign-one', jobId: 'job-one', voiceMasterId: 'voice-one' })).toBe(true);
  expect(voiceSubmission({ operation: 'image_prepare', campaignId: 'campaign-one', jobId: 'job-one' })).toBe(false);
});

it('rejects incomplete voice approval metadata', () => {
  expect(voiceSummary({voiceMasterId: 'one', copyMasterId: 'copy-one', copyMasterVersion: 1, provider: 'imported', status: 'review_required',
    processedAudioPath: null, durationSeconds: null, transcriptMatchStatus: null, headlineSpoken: null, qcFindings: [],
    approvalReviewReason: null, rejectionReason: null})).toBe(false);
});
