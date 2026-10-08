import {ApiError,campaignPath,object,post,read,workerState} from './api';
import type {Items,WorkerState} from './api';
import {editAsset,editIdentifier} from './editingApi';

export type RenderJobRequest={schemaVersion:'1.0';campaignId:string;videoId:string;planHash:string;executionId:string};
export type RenderJobSubmission=RenderJobRequest & {jobId:string};
export type RenderOutput={key:string;outputVariantId:string;status:'rendered'|'reused'|'failed';renderKey:string;fitMeasured:boolean;
  path:string|null;error:{field:string;message:string}|null};
export type RenderBatchResult={schemaVersion:'1.0';planHash:string;dryRun:false;outputs:RenderOutput[];hasFailures:boolean};
export type RenderJobView=RenderJobSubmission & {status:'queued'|'running'|'completed'|'failed'|'blocked'|'retry_scheduled'|'cancelled';
  result:RenderBatchResult|null;renderStatus:'succeeded'|'partial_failure'|'failed'|null;errorCode:string|null};
const sha=(v:unknown)=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const uuid=(v:unknown)=>typeof v==='string'&&/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(v);
function request(v:unknown):boolean{
  return object(v)&&v.schemaVersion==='1.0'&&editIdentifier(v.campaignId)&&editIdentifier(v.videoId)&&sha(v.planHash)&&uuid(v.executionId);
}
function submission(v:unknown):boolean{return request(v)&&object(v)&&uuid(v.jobId);}
function output(v:unknown):v is RenderOutput{
  if(!object(v)||!editIdentifier(v.key)||!editIdentifier(v.outputVariantId)||!sha(v.renderKey)||typeof v.fitMeasured!=='boolean'
    ||!(v.path===null||editAsset({path:v.path,sha256:'a'.repeat(64)})))return false;
  if(v.status==='failed')return object(v.error)&&typeof v.error.field==='string'&&typeof v.error.message==='string';
  return (v.status==='rendered'||v.status==='reused')&&v.error===null&&v.fitMeasured&&v.path!==null;
}
export function renderJobView(v:unknown):v is RenderJobView{
  if(!submission(v)||!object(v)||!['queued','running','completed','failed','blocked','retry_scheduled','cancelled'].includes(String(v.status))
    ||!(v.errorCode===null||typeof v.errorCode==='string'))return false;
  if(v.status!=='completed')return v.result===null&&v.renderStatus===null;
  const result=v.result;
  if(v.errorCode!==null||!object(result)||result.schemaVersion!=='1.0'||result.planHash!==v.planHash||result.dryRun!==false
    ||!Array.isArray(result.outputs)||!result.outputs.length||!result.outputs.every(output))return false;
  if(new Set(result.outputs.map(o=>o.key)).size!==result.outputs.length||new Set(result.outputs.map(o=>o.outputVariantId)).size!==result.outputs.length)return false;
  const failures=result.outputs.filter(o=>o.status==='failed').length;
  return result.hasFailures===(failures>0)&&v.renderStatus===(failures===0?'succeeded':failures===result.outputs.length?'failed':'partial_failure');
}
const path=(campaignId:string,jobId?:string)=>campaignPath(campaignId,'/editing/renders'+(jobId?`/${encodeURIComponent(jobId)}`:''));
export async function submitRender(body:RenderJobRequest):Promise<RenderJobSubmission>{
  const value:unknown=await post(path(body.campaignId),body);
  if(!submission(value)||!object(value)||value.campaignId!==body.campaignId||value.videoId!==body.videoId||value.planHash!==body.planHash||value.executionId!==body.executionId)throw new ApiError('invalid_response');
  return value as RenderJobSubmission;
}
export function getRender(campaignId:string,jobId:string,signal:AbortSignal):Promise<RenderJobView>{
  return read(path(campaignId,jobId),signal,v=>renderJobView(v)&&v.campaignId===campaignId&&v.jobId===jobId);
}
export function listRenders(campaignId:string,videoId:string,planHash:string,signal:AbortSignal):Promise<Items<RenderJobView>>{
  const query=new URLSearchParams({videoId,planHash});
  return read(`${path(campaignId)}?${query}`,signal,v=>object(v)&&Array.isArray(v.items)
    &&v.items.every(item=>renderJobView(item)&&item.campaignId===campaignId&&item.videoId===videoId&&item.planHash===planHash));
}
export function renderMediaUrl(campaignId:string,jobId:string,outputVariantId:string,download=false):string{
  return `${path(campaignId,jobId)}/outputs/${encodeURIComponent(outputVariantId)}/media${download?'?download=true':''}`;
}
export async function startRenderWorker(campaignId:string):Promise<WorkerState>{
  const value:unknown=await post(campaignPath(campaignId,'/worker/start'),{campaignId,kind:'editing_render'});
  if(!workerState(value)||value.campaignId!==campaignId||value.kind!=='editing_render'||value.state!=='running')throw new ApiError('command_unknown');
  return value;
}
