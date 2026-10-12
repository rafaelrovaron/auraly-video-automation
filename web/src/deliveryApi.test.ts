import {expect,it,vi} from 'vitest';
import {getDelivery,submitDelivery} from './deliveryApi';
import type {ReviewTarget,HumanReviewRecord} from './reviewApi';
export const target:ReviewTarget={schemaVersion:'1.0',campaignId:'campaign-one',jobId:'22222222-2222-4222-8222-222222222222',videoId:'video-one',planHash:'a'.repeat(64),outputVariantId:'variant-a',renderKey:'b'.repeat(64)};
const {jobId:_,...context}=target;
export const review:HumanReviewRecord={...context,qcKey:'c'.repeat(64),decision:'approved',reviewedBy:'Tester',comment:null,watchedAndListened:true,reviewedAt:'2026-10-12T10:00:00Z',reviewKey:'f'.repeat(64),masterPath:'masters/a.mp4',masterSha256:'d'.repeat(64),receiptSha256:'e'.repeat(64),sizeBytes:123,qcReportSha256:'a'.repeat(64),qcStatus:'passed'};
export const request={...context,reviewKey:review.reviewKey};
export const record={...request,deliveredAt:'2026-10-12T11:00:00Z',path:`${target.campaignId}/${target.outputVariantId}/${review.reviewKey}/master.mp4`,masterSha256:review.masterSha256,sizeBytes:review.sizeBytes,receiptSha256:review.receiptSha256,qcReportSha256:review.qcReportSha256,reviewSha256:'c'.repeat(64)};
const signal=()=>new AbortController().signal;
it('reads absence and existing original receipt; POST excludes job and destination',async()=>{
  const fetcher=vi.fn(async()=>Response.json({request,record:null}));vi.stubGlobal('fetch',fetcher);
  const abort=signal();expect(await getDelivery(target,review,abort)).toEqual({request,record:null});
  expect(fetcher.mock.calls[0]).toEqual([expect.stringContaining('/outputs/variant-a/delivery'),expect.objectContaining({method:'GET',signal:abort})]);
  vi.stubGlobal('fetch',async()=>Response.json({request,record}));expect((await getDelivery(target,review,signal())).record).toEqual(record);
  const sender=vi.fn(async()=>Response.json(record));vi.stubGlobal('fetch',sender);
  expect(await submitDelivery(target,review)).toEqual(record);
  expect(JSON.parse((sender.mock.calls[0] as unknown as [string,RequestInit])[1].body as string)).toEqual(request);
});
it.each(['context','review','sha','path','time','schema','size','evidence'])('rejects invalid delivery %s',async mutation=>{
  const changed:Record<string,unknown>={...record};
  if(mutation==='context')changed.videoId='foreign';
  if(mutation==='review')changed.reviewKey='a'.repeat(64);
  if(mutation==='sha')changed.reviewSha256='bad';
  if(mutation==='path')changed.path='C:/private/master.mp4';
  if(mutation==='time')changed.deliveredAt='2026-10-12T11:00:00+03:00';
  if(mutation==='schema')changed.schemaVersion='2.0';
  if(mutation==='size')changed.sizeBytes=124;
  if(mutation==='evidence')changed.qcReportSha256='b'.repeat(64);
  vi.stubGlobal('fetch',async()=>Response.json({request,record:changed}));
  await expect(getDelivery(target,review,signal())).rejects.toMatchObject({code:'invalid_response'});
  vi.stubGlobal('fetch',async()=>Response.json(changed));
  await expect(submitDelivery(target,review)).rejects.toMatchObject({code:'invalid_response'});
});
it('rejects foreign request even with an absent receipt',async()=>{
  vi.stubGlobal('fetch',async()=>Response.json({request:{...request,reviewKey:'a'.repeat(64)},record:null}));
  await expect(getDelivery(target,review,signal())).rejects.toMatchObject({code:'invalid_response'});
});
