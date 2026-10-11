import {expect,it,vi} from 'vitest';
import {getReviewState,submitQc,submitReview,startQcWorker} from './reviewApi';
import type {ReviewTarget} from './reviewApi';
const target:ReviewTarget={schemaVersion:'1.0',campaignId:'campaign-one',videoId:'video-one',planHash:'a'.repeat(64),
  jobId:'22222222-2222-4222-8222-222222222222',outputVariantId:'variant-a',renderKey:'b'.repeat(64)};
const empty={...target,reports:[],review:null,runs:[]};
const request={...target,operation:'edit_qc' as const,executionId:'11111111-1111-4111-8111-111111111111'};
const signal=()=>new AbortController().signal;
it.each(['foreign','schema','hash','runs','result','duplicate'])('rejects_foreign_or_malformed_state %s',async mutation=>{
  const state:Record<string,unknown>=structuredClone(empty);
  const run={jobId:'33333333-3333-4333-8333-333333333333',campaignId:target.campaignId,operation:'edit_qc',voiceMasterId:null,
    request,status:'queued',result:null,errorCode:null};
  if(mutation==='foreign')state.planHash='c'.repeat(64);
  if(mutation==='schema')state.schemaVersion='2.0';
  if(mutation==='hash')state.renderKey='invalid';
  if(mutation==='runs')state.runs=[{...run,request:{...request,jobId:'44444444-4444-4444-8444-444444444444'}}];
  if(mutation==='result')state.runs=[{...run,status:'completed'}];
  if(mutation==='duplicate')state.runs=[run,run];
  vi.stubGlobal('fetch',async()=>Response.json(state));
  await expect(getReviewState(target,signal())).rejects.toMatchObject({code:'invalid_response'});
});
it('rejects_mismatched_qc_submission',async()=>{
  const submission={jobId:'33333333-3333-4333-8333-333333333333',campaignId:'foreign',operation:'edit_qc',voiceMasterId:null};
  vi.stubGlobal('fetch',async()=>Response.json(submission));
  await expect(submitQc(request)).rejects.toMatchObject({code:'invalid_response'});
});
it('rejects_mismatched_review_record',async()=>{
  const {jobId:_,...context}=target;
  const body={...context,qcKey:'c'.repeat(64),decision:'approved' as const,reviewedBy:'Tester',comment:null,watchedAndListened:true};
  vi.stubGlobal('fetch',async()=>Response.json({...body,reviewedBy:'Other'}));
  await expect(submitReview(target,body)).rejects.toMatchObject({code:'invalid_response'});
});
it('preserves abort signal and escapes output URLs; worker uses existing local_operations',async()=>{
  const fetcher=vi.fn(async(_url:string,_init?:RequestInit)=>Response.json(empty));vi.stubGlobal('fetch',fetcher);
  const controller=new AbortController();expect(await getReviewState(target,controller.signal)).toEqual(empty);
  expect(fetcher.mock.calls[0][1]?.signal).toBe(controller.signal);
  await expect(getReviewState({...target,outputVariantId:'variant/a'},signal())).rejects.toMatchObject({code:'invalid_response'});
  expect(fetcher.mock.calls[1][0]).toContain('/outputs/variant%2Fa/review-state');
  const worker={state:'running',campaignId:target.campaignId,kind:'local_operations',errorCode:null};
  vi.stubGlobal('fetch',async()=>Response.json(worker));expect(await startQcWorker(target.campaignId)).toEqual(worker);
});
