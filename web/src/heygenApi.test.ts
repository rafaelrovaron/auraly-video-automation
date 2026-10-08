import { expect, it, vi } from 'vitest';
import { getRenderPoster, heygenOperationView, heygenSubmission } from './heygenApi';

it('poster_returns_only_bounded_png',async()=>{
  const bytes=new Uint8Array([137,80,78,71,13,10,26,10,0,0,0,0,73,72,68,82,0,0,0,32,0,0,0,64]);
  const calls:Array<[string,RequestInit]>=[];
  vi.stubGlobal('fetch',async(url:string,init:RequestInit)=>{calls.push([url,init]);return new Response(bytes,{headers:{'Content-Type':'image/png'}});});
  const signal=new AbortController().signal;
  expect((await getRenderPoster('campaign-one','render-one','a'.repeat(64),signal)).size).toBe(24);
  expect(calls[0][0]).toBe('/api/v1/campaigns/campaign-one/heygen/renders/render-one/poster/'+'a'.repeat(64));
  expect(calls[0][1]).toMatchObject({method:'GET',signal});
  for(const response of [Response.json({secret:'private'}),new Response('',{headers:{'Content-Type':'image/png'}}),
    new Response('bad png',{headers:{'Content-Type':'image/png'}}),new Response(new Uint8Array(4*1024*1024+1),{headers:{'Content-Type':'image/png'}}),
    Response.json({secret:'private'},{status:503})]){
    vi.stubGlobal('fetch',async()=>response);
    await expect(getRenderPoster('campaign-one','render-one','a'.repeat(64),signal)).rejects.toThrow(/local|inválida/i);
  }
});

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

const plan = {operation: 'heygen_video_plan', newCount: 1, reusedCount: 0, reservedCount: 2, maxPaidRenders: 3, totalAudioSeconds: 1, sceneVariantIds: ['scene']};
it('accepts valid informative plan', () => {expect(heygenOperationView({...view, operation: plan.operation, result: plan})).toBe(true);});
it.each([0, -1, 0.5, true, Number.MAX_SAFE_INTEGER + 1])('rejects invalid plan limit %s', maxPaidRenders => {
  expect(heygenOperationView({...view, operation: plan.operation, result: {...plan, maxPaidRenders}})).toBe(false);
});
it.each([{sceneVariantIds: ['scene', 'scene']}, {sceneVariantIds: ['']}, {sceneVariantIds: [true]}])('rejects nonunique or malformed plan scenes %#', ({sceneVariantIds}) => {
  expect(heygenOperationView({...view, operation: plan.operation, result: {...plan, sceneVariantIds}})).toBe(false);
});

it('rejects incomplete media result in submit and reconcile', () => {
  const render = {renderId: 'render', campaignId: 'campaign', sceneVariantId: 'scene', imageCandidateId: 'image', voiceMasterId: 'voice',
    jobId: 'child', status: 'planned', remoteVideoId: null, source: null, errorCode: null};
  expect(heygenOperationView({...view, operation: 'heygen_video_submit', result: {operation: 'heygen_video_submit', renders: [render]}})).toBe(false);
  expect(heygenOperationView({...view, operation: 'heygen_reconcile', result: {operation: 'heygen_reconcile', render}})).toBe(false);
});
