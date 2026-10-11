import {ApiError,campaignPath,object,post,read,workerState} from './api';
import type {WorkerState} from './api';
import {editAsset,editIdentifier} from './editingApi';

export type ReviewTarget={schemaVersion:'1.0';campaignId:string;jobId:string;videoId:string;planHash:string;outputVariantId:string;renderKey:string};
export type EditQcOperation=ReviewTarget & {operation:'edit_qc';executionId:string};
export type OperationSubmission={jobId:string;campaignId:string;operation:'edit_qc';voiceMasterId:null};
type Finding={code:string;field:string;message:string};
type Check={name:'integrity'|'audio'|'text';status:'passed'|'blocked'|'not_applicable'|'skipped';findings:Finding[]};
type Evidence=Omit<ReviewTarget,'jobId'> & {masterPath:string;masterSha256:string;receiptSha256:string;sizeBytes:number};
export type QcReport=Evidence & {qcVersion:'1.0';manifestHash:string;outputHash:string;qcKey:string;policyHash:string;
  policy:{version:'final-qc-v1';minLoudnessLufs:-30;maxTruePeakDbtp:0};runtime:{fingerprint:string;ffmpegVersion:string;libassVersion:string;encoding:Record<string,string>};
  probe:Record<string,unknown>|null;checks:Check[];audio:{integratedLoudnessLufs:number|null;truePeakDbtp:number|null}|null;
  status:'passed'|'blocked';humanReviewRequired:true;textMeasurementScope:'manifest_layout_not_burned_frames'};
export type HumanReviewRequest=Omit<ReviewTarget,'jobId'> & {qcKey:string;decision:'approved'|'rejected';reviewedBy:string;comment:string|null;watchedAndListened:boolean};
export type HumanReviewRecord=HumanReviewRequest & Evidence & {reviewedAt:string;reviewKey:string;qcReportSha256:string;qcStatus:'passed'|'blocked'};
type QcOutput={outputVariantId:string;renderKey:string;status:'passed'|'blocked'|'error';reused:boolean;qcKey:string|null;path:string|null;error:Finding|null};
type QcResult={operation:'edit_qc';request:EditQcOperation;result:Omit<ReviewTarget,'jobId'|'outputVariantId'|'renderKey'> & {outputs:QcOutput[];hasFailures:boolean}};
export type QcRunView=OperationSubmission & {request:EditQcOperation;status:'queued'|'running'|'completed'|'failed'|'blocked'|'retry_scheduled'|'cancelled';result:QcResult|null;errorCode:string|null};
export type FinalReviewState=ReviewTarget & {reports:QcReport[];review:HumanReviewRecord|null;runs:QcRunView[]};
const sha=(v:unknown)=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const uuid=(v:unknown)=>typeof v==='string'&&/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(v);
const number=(v:unknown)=>typeof v==='number'&&Number.isFinite(v);
const contextKeys=['schemaVersion','campaignId','videoId','planHash','outputVariantId','renderKey'] as const;
const matches=(v:Record<string,unknown>,t:ReviewTarget)=>contextKeys.every(key=>v[key]===t[key]);
const path=(t:ReviewTarget)=>campaignPath(t.campaignId,`/editing/renders/${encodeURIComponent(t.jobId)}/outputs/${encodeURIComponent(t.outputVariantId)}`);
function target(v:unknown):v is ReviewTarget & Record<string,unknown>{return object(v)&&v.schemaVersion==='1.0'&&editIdentifier(v.campaignId)&&editIdentifier(v.videoId)&&editIdentifier(v.outputVariantId)&&uuid(v.jobId)&&sha(v.planHash)&&sha(v.renderKey);}
function localPath(v:unknown):v is string{return typeof v==='string'&&editAsset({path:v,sha256:'a'.repeat(64)});}
function evidence(v:Record<string,unknown>,t:ReviewTarget):boolean{return matches(v,t)&&localPath(v.masterPath)&&sha(v.masterSha256)&&sha(v.receiptSha256)&&Number.isInteger(v.sizeBytes)&&Number(v.sizeBytes)>0;}
function finding(v:unknown):v is Finding{return object(v)&&editIdentifier(v.code)&&typeof v.field==='string'&&typeof v.message==='string';}
function report(v:unknown,t:ReviewTarget):v is QcReport{
  if(!object(v)||!evidence(v,t)||v.qcVersion!=='1.0'||!['passed','blocked'].includes(String(v.status))||v.humanReviewRequired!==true||v.textMeasurementScope!=='manifest_layout_not_burned_frames'
    ||!['manifestHash','outputHash','qcKey','policyHash'].every(key=>sha(v[key]))||!object(v.policy)||v.policy.version!=='final-qc-v1'||v.policy.minLoudnessLufs!==-30||v.policy.maxTruePeakDbtp!==0
    ||!object(v.runtime)||!sha(v.runtime.fingerprint)||typeof v.runtime.ffmpegVersion!=='string'||typeof v.runtime.libassVersion!=='string'||!object(v.runtime.encoding)||!Object.values(v.runtime.encoding).every(item=>typeof item==='string')
    ||!(v.probe===null||object(v.probe))||!Array.isArray(v.checks)||v.checks.length!==3)return false;
  if(!v.checks.every((c,index)=>object(c)&&c.name===['integrity','audio','text'][index]&&['passed','blocked','not_applicable','skipped'].includes(String(c.status))
    &&Array.isArray(c.findings)&&c.findings.every(finding)&&!!c.findings.length===(c.status==='blocked'||c.status==='skipped')))return false;
  const [integrity,audio,text]=v.checks as Check[];
  if(integrity.status==='blocked')return v.status==='blocked'&&audio.status==='skipped'&&text.status==='skipped'&&v.audio===null;
  if(integrity.status!=='passed'||v.probe===null||!object(v.audio)||!['passed','blocked','not_applicable'].includes(text.status)
    ||!(v.audio.integratedLoudnessLufs===null||number(v.audio.integratedLoudnessLufs))||!(v.audio.truePeakDbtp===null||number(v.audio.truePeakDbtp)))return false;
  const loudness=v.audio.integratedLoudnessLufs,peak=v.audio.truePeakDbtp;
  if(loudness!==null&&peak===null)return false;
  const codes:string[]=[];
  if(loudness===null)codes.push(peak===null?'audio_silent':'audio_loudness_unmeasurable');
  else if(Number(loudness)<-30)codes.push('audio_low_loudness');
  if(peak!==null&&Number(peak)>=0)codes.push('audio_peak_risk');
  return audio.status===(codes.length?'blocked':'passed')&&JSON.stringify(audio.findings.map(f=>f.code))===JSON.stringify(codes)
    &&v.status===(codes.length||text.status==='blocked'?'blocked':'passed');
}
function human(v:unknown,t:ReviewTarget):v is HumanReviewRecord{
  return object(v)&&evidence(v,t)&&sha(v.qcKey)&&sha(v.reviewKey)&&sha(v.qcReportSha256)&&typeof v.reviewedBy==='string'&&!!v.reviewedBy.trim()
    &&(v.comment===null||typeof v.comment==='string'&&!!v.comment.trim())&&typeof v.watchedAndListened==='boolean'
    &&typeof v.reviewedAt==='string'&&/^\d{4}-\d{2}-\d{2}T.*(?:Z|\+00:00)$/.test(v.reviewedAt)&&Number.isFinite(Date.parse(v.reviewedAt))
    &&['passed','blocked'].includes(String(v.qcStatus))&&((v.decision==='approved'&&v.watchedAndListened&&v.qcStatus==='passed')||(v.decision==='rejected'&&!!v.comment));
}
function submission(v:unknown,campaignId:string):v is OperationSubmission & Record<string,unknown>{return object(v)&&uuid(v.jobId)&&v.campaignId===campaignId&&v.operation==='edit_qc'&&v.voiceMasterId===null;}
function run(v:unknown,t:ReviewTarget):v is QcRunView{
  if(!submission(v,t.campaignId)||!object(v)||!target(v.request)||!object(v.request)||!matches(v.request,t)||v.request.jobId!==t.jobId||v.request.operation!=='edit_qc'||!uuid(v.request.executionId)
    ||!['queued','running','completed','failed','blocked','retry_scheduled','cancelled'].includes(String(v.status))||!(v.errorCode===null||typeof v.errorCode==='string'))return false;
  if(v.status!=='completed')return v.result===null;
  const result=v.result;
  const request=v.request;
  if(v.errorCode!==null||!object(result)||result.operation!=='edit_qc'||!object(result.request)||!Object.keys(request).every(key=>result.request&&object(result.request)&&result.request[key]===request[key])
    ||!object(result.result))return false;
  const batch=result.result;
  if(batch.schemaVersion!==t.schemaVersion||batch.campaignId!==t.campaignId||batch.videoId!==t.videoId||batch.planHash!==t.planHash||!Array.isArray(batch.outputs)||batch.outputs.length!==1)return false;
  const output=batch.outputs[0];
  if(!object(output)||output.outputVariantId!==t.outputVariantId||output.renderKey!==t.renderKey||typeof output.reused!=='boolean'||!['passed','blocked','error'].includes(String(output.status))||batch.hasFailures!==(output.status!=='passed'))return false;
  return output.status==='error'?finding(output.error)&&output.qcKey===null&&output.path===null&&!output.reused:output.error===null&&sha(output.qcKey)&&localPath(output.path);
}
export function finalReviewState(v:unknown,t:ReviewTarget):v is FinalReviewState{
  if(!target(v)||!object(v)||!matches(v,t)||v.jobId!==t.jobId||!Array.isArray(v.reports)||!v.reports.every(item=>report(item,t))
    ||!(v.review===null||human(v.review,t))||!Array.isArray(v.runs)||!v.runs.every(item=>run(item,t)))return false;
  if(new Set(v.reports.map(item=>item.qcKey)).size!==v.reports.length||new Set(v.runs.map(item=>item.request.executionId)).size!==v.runs.length)return false;
  const review=v.review;
  if(review!==null){const original=v.reports.find(item=>object(review)&&item.qcKey===review.qcKey);if(!original||!object(review)
    ||original.status!==review.qcStatus||(['masterPath','masterSha256','receiptSha256','sizeBytes'] as const).some(key=>original[key]!==review[key]))return false;}
  return true;
}
export function getReviewState(t:ReviewTarget,signal:AbortSignal):Promise<FinalReviewState>{return read(path(t)+'/review-state',signal,v=>finalReviewState(v,t));}
export async function submitQc(body:EditQcOperation):Promise<OperationSubmission>{const value:unknown=await post(path(body)+'/qc',body);if(!submission(value,body.campaignId))throw new ApiError('invalid_response');return value;}
export async function submitReview(t:ReviewTarget,body:HumanReviewRequest):Promise<HumanReviewRecord>{
  const value:unknown=await post(path(t)+'/review',body);if(!human(value,t)||!Object.keys(body).every(key=>value[key as keyof HumanReviewRequest]===body[key as keyof HumanReviewRequest]))throw new ApiError('invalid_response');return value;
}
export async function startQcWorker(campaignId:string):Promise<WorkerState>{const value:unknown=await post(campaignPath(campaignId,'/worker/start'),{campaignId,kind:'local_operations'});
  if(!workerState(value)||value.campaignId!==campaignId||value.kind!=='local_operations'||value.state!=='running')throw new ApiError('command_unknown');return value;}
