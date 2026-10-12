import {ApiError,campaignPath,object,post,read} from './api';
import type {HumanReviewRecord,ReviewTarget} from './reviewApi';
export type DeliveryRequest=Omit<ReviewTarget,'jobId'> & {reviewKey:string};
export type DeliveryRecord=DeliveryRequest & {deliveredAt:string;path:string;masterSha256:string;sizeBytes:number;receiptSha256:string;qcReportSha256:string;reviewSha256:string};
export type DeliveryState={request:DeliveryRequest;record:DeliveryRecord|null};
const keys=['schemaVersion','campaignId','videoId','planHash','outputVariantId','renderKey','reviewKey'] as const;
const sha=(v:unknown)=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
function request(t:ReviewTarget,r:HumanReviewRecord):DeliveryRequest{
  const {jobId:_,...context}=t;return {...context,reviewKey:r.reviewKey};
}
const matches=(v:unknown,body:DeliveryRequest)=>object(v)&&keys.every(key=>v[key]===body[key]);
function record(v:unknown,body:DeliveryRequest,r:HumanReviewRecord):v is DeliveryRecord{
  return object(v)&&matches(v,body)&&r.decision==='approved'&&r.qcStatus==='passed'
    &&(['masterSha256','receiptSha256','qcReportSha256'] as const).every(key=>sha(v[key])&&v[key]===r[key])
    &&sha(v.reviewSha256)&&Number.isSafeInteger(v.sizeBytes)&&v.sizeBytes===r.sizeBytes&&Number(v.sizeBytes)>0
    &&v.path===`${body.campaignId}/${body.outputVariantId}/${body.reviewKey}/master.mp4`
    &&typeof v.deliveredAt==='string'&&/^\d{4}-\d{2}-\d{2}T.*(?:Z|\+00:00)$/.test(v.deliveredAt)&&Number.isFinite(Date.parse(v.deliveredAt));
}
const path=(t:ReviewTarget)=>campaignPath(t.campaignId,`/editing/renders/${encodeURIComponent(t.jobId)}/outputs/${encodeURIComponent(t.outputVariantId)}/delivery`);
export function getDelivery(t:ReviewTarget,r:HumanReviewRecord,signal:AbortSignal):Promise<DeliveryState>{
  const body=request(t,r);
  return read(path(t),signal,v=>object(v)&&matches(v.request,body)&&(v.record===null||record(v.record,body,r)));
}
export async function submitDelivery(t:ReviewTarget,r:HumanReviewRecord):Promise<DeliveryRecord>{
  const body=request(t,r),value:unknown=await post(path(t),body);
  if(!record(value,body,r))throw new ApiError('invalid_response');return value;
}
