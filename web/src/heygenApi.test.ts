import { expect, it } from 'vitest';
import { heygenOperationView, heygenSubmission } from './heygenApi';

const submission = {jobId: 'wrapper', campaignId: 'campaign', operation: 'heygen_assets'};
const view = {...submission, status: 'completed', errorCode: null,
  result: {operation: 'heygen_assets', uploadCount: 2, reusedCount: 0, jobId: 'child'}};
it('accepts a discriminated assets wrapper, including reuse without a child', () => {
  expect(heygenSubmission(submission)).toBe(true);
  expect(heygenOperationView(view)).toBe(true);
  expect(heygenOperationView({...view, result: {...view.result, uploadCount: 0, jobId: null}})).toBe(true);
});
it.each([null, {}, {...submission, jobId: ''}, {...submission, operation: 'voice_generate'}])('rejects malformed submission %#', value => {
  expect(heygenSubmission(value)).toBe(false);
});
it.each([
  {...view, status: 'invented'}, {...view, result: null}, {...view, result: {...view.result, operation: 'heygen_video_plan'}},
  ...[-1, 0.5, true, Number.MAX_SAFE_INTEGER + 1].map(uploadCount => ({...view, result: {...view.result, uploadCount}})),
])('rejects malformed operation %#', value => {expect(heygenOperationView(value)).toBe(false);});
