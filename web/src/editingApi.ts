import {ApiError,campaignPath,object,post,read} from './api';
import {editProfile,newProfile} from './profileApi';
import type {AssetRef,CaptionStyle,EditDefaults,FramingStyle,HeadlineStyle,MusicStyle,OutputStyle} from './profileApi';
import type {HeyGenRenderView} from './heygenApi';

export type EditOverrides = {output?:Partial<OutputStyle>;headline?:Partial<HeadlineStyle & {text:string}>;
  captions?:Partial<CaptionStyle>;music?:Partial<MusicStyle>;framing?:Partial<FramingStyle>};
export type ProfileRef={profileId:string;version:number;hash:string};
type IdentityRef={id:string;hash:string};
type CopyRef=IdentityRef & {version:number};
type SourceRef=AssetRef & {id:string;durationSec:number};
export type EditBatchRequest={schemaVersion:'1.0';campaignId:string;renderId:string;videoId:string;profileRef:ProfileRef;
  headlineText:string;campaign:EditOverrides;video:EditOverrides;musicAccepted:boolean;
  variants:{key:string;label:string;overrides:EditOverrides}[];maxOutputs:number;timingRef:AssetRef|null};
type Origin='profile'|'input'|'source'|'campaign'|'video'|'outputVariant';
export type EditManifest=Omit<EditDefaults,'headline'> & {headline:Omit<HeadlineStyle,'endSec'> & {endSec:number;text:string;spoken:false};
  schemaVersion:'2.0';resolverVersion:'1.0';campaignId:string;videoId:string;outputVariantId:string;source:SourceRef;profileRef:ProfileRef;
  copyRef:IdentityRef|null;voiceRef:IdentityRef|null;musicAccepted:boolean;
  overrides:{campaign:EditOverrides;video:EditOverrides;outputVariant:EditOverrides};provenance:Record<string,Origin>;manifestHash:string};
type Cue={startSec:number;endSec:number;tokenStart:number;tokenEnd:number;text:string};
type CaptionInput={copyRef:CopyRef;voiceRef:IdentityRef;text:string;timingStatus:'missing'|'provided';timingRef:AssetRef|null;
  origin:'manual'|'external_alignment'|null;acceptedBy:string|null;timebase:'source_mp4'|null;cues:Cue[]};
type OutputSummary={key:string;label:string;outputVariantId:string;manifestHash:string;outputHash:string;filename:string;captionState:'disabled'|'timing_missing'|'timing_provided'};
export type EditBatchPlan={schemaVersion:'1.0';plannerVersion:'1.0';campaignId:string;renderId:string;videoId:string;source:SourceRef;
  copyRef:CopyRef;voiceRef:IdentityRef;imageRef:IdentityRef;maxOutputs:number;outputCount:number;captionInput:CaptionInput;
  outputs:(OutputSummary & {manifest:EditManifest})[];planHash:string};
export type PlanSummary={videoId:string;renderId:string;planHash:string;outputCount:number;maxOutputs:number;timingStatus:'missing'|'provided';
  copyRef:CopyRef;voiceRef:IdentityRef;source:SourceRef;outputs:OutputSummary[]};
export type EditSubmission={jobId:string;campaignId:string;operation:'edit_plan';voiceMasterId?:null};
export type EditOperationView=EditSubmission & {status:'queued'|'running'|'completed'|'failed'|'blocked'|'retry_scheduled'|'cancelled';
  errorCode:string|null;result:{operation:'edit_plan';persist:boolean;plan:EditBatchPlan}|null};

const text=(v:unknown):v is string=>typeof v==='string'&&!!v.trim()&&!v.includes('\0');
const sha=(v:unknown):v is string=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const num=(v:unknown):v is number=>typeof v==='number'&&Number.isFinite(v);
const positive=(v:unknown):v is number=>num(v)&&v>0;
const integer=(v:unknown):v is number=>positive(v)&&Number.isSafeInteger(v);
const id=(v:unknown):v is string=>text(v)&&/^[a-z0-9][a-z0-9_-]{0,63}$/.test(v)&&!/^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/.test(v);
const path=(v:unknown)=>text(v)&&!/[\\:]/.test(v)&&v.split('/').every(p=>!!p&&p!=='.'&&p!=='..'&&!/[ .]$/.test(p)&&!/^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(p));
function keys(v:unknown,names:string[],optional:string[]=[]):v is Record<string,unknown>{return object(v)&&names.every(k=>Object.hasOwn(v,k))&&Object.keys(v).every(k=>names.includes(k)||optional.includes(k));}
const asset=(v:unknown):v is AssetRef=>keys(v,['path','sha256'])&&path(v.path)&&sha(v.sha256);
export const editIdentifier=id;
export const editAsset=asset;
const identity=(v:unknown):v is IdentityRef=>keys(v,['id','hash'])&&text(v.id)&&sha(v.hash);
const copy=(v:unknown):v is CopyRef=>keys(v,['id','hash','version'])&&text(v.id)&&sha(v.hash)&&integer(v.version);
const profile=(v:unknown):v is ProfileRef=>keys(v,['profileId','version','hash'])&&id(v.profileId)&&integer(v.version)&&sha(v.hash);
const source=(v:unknown):v is SourceRef=>keys(v,['id','path','sha256','durationSec'])&&id(v.id)&&path(v.path)&&sha(v.sha256)&&positive(v.durationSec);
const sections=['output','headline','captions','music','framing'] as const;
const base=newProfile('plain','Plain','2026-10-07T00:00:00Z');
const origins=['profile','input','source','campaign','video','outputVariant'];
function equal(a:unknown,b:unknown):boolean{
  if(a===b)return true;
  if(Array.isArray(a)&&Array.isArray(b))return a.length===b.length&&a.every((v,i)=>equal(v,b[i]));
  return object(a)&&object(b)&&Object.keys(a).length===Object.keys(b).length&&Object.keys(a).every(k=>Object.hasOwn(b,k)&&equal(a[k],b[k]));
}
function normalized(v:EditOverrides):EditOverrides{return Object.fromEntries(sections.map(s=>[s,v[s]??{}]));}
export function editOverrides(v:unknown):v is EditOverrides{
  if(!keys(v,[],[...sections]))return false;
  return Object.entries(v).every(([s,fields])=>{
    const section=s as keyof EditDefaults;
    if(!object(fields))return false;
    return Object.entries(fields).every(([field,value])=>{
      if(section==='headline'&&field==='text')return text(value);
      if(!Object.hasOwn(base.defaults[section],field))return false;
      const candidate=structuredClone(base);
      // Partials cannot be checked against invented combined safe zones/intervals.
      candidate.defaults.headline.safeTop=candidate.defaults.headline.safeRight=candidate.defaults.headline.safeBottom=candidate.defaults.headline.safeLeft=0;
      candidate.defaults.captions.safeTop=candidate.defaults.captions.safeRight=candidate.defaults.captions.safeBottom=candidate.defaults.captions.safeLeft=0;
      candidate.defaults.headline.startSec=0;candidate.defaults.music.trimStartSec=0;
      const style:Record<string,unknown>={...candidate.defaults[section],[field]:value};
      const check={...candidate,defaults:{...candidate.defaults,[section]:style}};
      return editProfile(check);
    });
  });
}
export function editBatchRequest(v:unknown):v is EditBatchRequest{
  return keys(v,['schemaVersion','campaignId','renderId','videoId','profileRef','headlineText','campaign','video','musicAccepted','variants','maxOutputs','timingRef'])
    &&v.schemaVersion==='1.0'&&id(v.campaignId)&&id(v.renderId)&&id(v.videoId)&&profile(v.profileRef)&&text(v.headlineText)
    &&editOverrides(v.campaign)&&editOverrides(v.video)&&typeof v.musicAccepted==='boolean'&&integer(v.maxOutputs)
    &&(v.timingRef===null||asset(v.timingRef))&&Array.isArray(v.variants)&&v.variants.length>0&&v.variants.length<=v.maxOutputs
    &&v.variants.every(item=>keys(item,['key','label','overrides'])&&id(item.key)&&text(item.label)&&editOverrides(item.overrides))
    &&new Set(v.variants.map(item=>item.key)).size===v.variants.length;
}
function manifest(v:unknown):v is EditManifest{
  if(!keys(v,['schemaVersion','resolverVersion','campaignId','videoId','outputVariantId','source','profileRef','copyRef','voiceRef','musicAccepted',...sections,'overrides','provenance','manifestHash'])
    ||v.schemaVersion!=='2.0'||v.resolverVersion!=='1.0'||!id(v.campaignId)||!id(v.videoId)||!id(v.outputVariantId)||!source(v.source)||!profile(v.profileRef)
    ||!(v.copyRef===null||identity(v.copyRef))||!(v.voiceRef===null||identity(v.voiceRef))||typeof v.musicAccepted!=='boolean'||!sha(v.manifestHash)
    ||!object(v.headline)||!text(v.headline.text)||!positive(v.headline.endSec)||v.headline.spoken!==false)return false;
  const {text:headlineText,spoken,...headline}=v.headline;void headlineText;void spoken;
  if(!editProfile({...base,defaults:{output:v.output,headline,captions:v.captions,music:v.music,framing:v.framing}}))return false;
  if(!keys(v.overrides,['campaign','video','outputVariant'])||!Object.values(v.overrides).every(editOverrides)||!object(v.provenance))return false;
  const expected=sections.flatMap(s=>Object.keys(v[s] as object).map(f=>`${s}.${f}`));
  if(!keys(v.provenance,expected)||!Object.values(v.provenance).every(o=>typeof o==='string'&&origins.includes(o)))return false;
  const m=v as unknown as EditManifest;
  return m.headline.startSec<m.headline.endSec&&m.headline.endSec<=m.source.durationSec
    &&(!m.headline.enabled||m.headline.font!==null)&&(!m.captions.enabled||m.captions.font!==null)
    &&(!m.music.enabled||(m.music.asset!==null&&m.musicAccepted&&m.music.fadeInSec<=m.source.durationSec&&m.music.fadeOutSec<=m.source.durationSec));
}
function outputSummary(v:unknown,hasManifest=false):v is OutputSummary{
  return keys(v,['key','label','outputVariantId','manifestHash','outputHash','filename','captionState'],hasManifest?['manifest']:[])
    &&id(v.key)&&text(v.label)&&id(v.outputVariantId)&&sha(v.manifestHash)&&sha(v.outputHash)
    &&v.filename===`${v.outputVariantId}-${v.outputHash}.mp4`&&typeof v.captionState==='string'&&['disabled','timing_missing','timing_provided'].includes(v.captionState);
}
function captions(v:unknown,duration:number):v is CaptionInput{
  if(!keys(v,['copyRef','voiceRef','text','timingStatus','timingRef','origin','acceptedBy','timebase','cues'])||!copy(v.copyRef)||!identity(v.voiceRef)||!text(v.text)||!Array.isArray(v.cues))return false;
  if(v.timingStatus==='missing')return v.timingRef===null&&v.origin===null&&v.acceptedBy===null&&v.timebase===null&&v.cues.length===0;
  if(v.timingStatus!=='provided'||!asset(v.timingRef)||typeof v.origin!=='string'||!['manual','external_alignment'].includes(v.origin)||!text(v.acceptedBy)
    ||!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$/.test(v.acceptedBy)||v.timebase!=='source_mp4'||v.cues.length===0)return false;
  const tokens=v.text.split(/\s+/);let index=0,end=0;
  for(const cue of v.cues){
    if(!keys(cue,['startSec','endSec','tokenStart','tokenEnd','text'])||!num(cue.startSec)||cue.startSec<end||!positive(cue.endSec)||cue.endSec<=cue.startSec||cue.endSec>duration
      ||typeof cue.tokenStart!=='number'||!Number.isSafeInteger(cue.tokenStart)||cue.tokenStart!==index||!integer(cue.tokenEnd)||cue.tokenEnd<=index||cue.tokenEnd>tokens.length
      ||cue.text!==tokens.slice(index,cue.tokenEnd).join(' '))return false;
    index=cue.tokenEnd;end=cue.endSec;
  }
  return index===tokens.length;
}
export function editBatchPlan(v:unknown):v is EditBatchPlan{
  if(!keys(v,['schemaVersion','plannerVersion','campaignId','renderId','videoId','source','copyRef','voiceRef','imageRef','maxOutputs','outputCount','captionInput','outputs','planHash'])
    ||v.schemaVersion!=='1.0'||v.plannerVersion!=='1.0'||!id(v.campaignId)||!id(v.renderId)||!id(v.videoId)||!source(v.source)||v.source.id!==v.renderId
    ||!copy(v.copyRef)||!identity(v.voiceRef)||!identity(v.imageRef)||!integer(v.maxOutputs)||!integer(v.outputCount)||!sha(v.planHash)
    ||!captions(v.captionInput,v.source.durationSec)||!equal(v.copyRef,v.captionInput.copyRef)||!equal(v.voiceRef,v.captionInput.voiceRef)
    ||!Array.isArray(v.outputs)||v.outputCount!==v.outputs.length||v.outputCount>v.maxOutputs)return false;
  let previous='';const ids=new Set<string>();
  for(const out of v.outputs){
    if(!object(out))return false;
    const m:unknown=out.manifest;
    if(!outputSummary(out,true)||!manifest(m)||out.key<=previous||ids.has(out.outputVariantId))return false;
    if(m.campaignId!==v.campaignId||m.videoId!==v.videoId||m.outputVariantId!==out.outputVariantId||m.manifestHash!==out.manifestHash
      ||!equal(m.source,v.source)||!equal(m.voiceRef,v.voiceRef)||!equal(m.copyRef,{id:v.copyRef.id,hash:v.copyRef.hash})
      ||out.captionState!==(!m.captions.enabled?'disabled':v.captionInput.timingStatus==='provided'?'timing_provided':'timing_missing'))return false;
    previous=out.key;ids.add(out.outputVariantId);
  }
  return true;
}
export function planMatchesRequest(plan:EditBatchPlan,request:EditBatchRequest,render:HeyGenRenderView):boolean{
  if(!editBatchPlan(plan)||!editBatchRequest(request)||render.status!=='ready'||!render.source||request.campaignId!==render.campaignId
    ||plan.campaignId!==request.campaignId||plan.renderId!==request.renderId||render.renderId!==request.renderId||plan.videoId!==request.videoId
    ||plan.source.sha256!==render.source.sha256||plan.source.durationSec!==render.source.probe.durationSec
    ||!equal(plan.voiceRef,{id:render.voiceMasterId,hash:render.audioSha256})||!equal(plan.imageRef,{id:render.imageCandidateId,hash:render.imageSha256})
    ||plan.maxOutputs!==request.maxOutputs||plan.outputCount!==request.variants.length||!equal(plan.captionInput.timingRef,request.timingRef))return false;
  return plan.outputs.every(out=>{
    const variant=request.variants.find(v=>v.key===out.key);if(!variant)return false;
    const m=out.manifest;
    return variant.label===out.label&&equal(m.profileRef,request.profileRef)&&m.musicAccepted===request.musicAccepted
      &&equal(normalized(m.overrides.campaign),normalized(request.campaign))&&equal(normalized(m.overrides.video),normalized(request.video))
      &&equal(normalized(m.overrides.outputVariant),normalized(variant.overrides))
      &&m.headline.text===(variant.overrides.headline?.text??request.video.headline?.text??request.campaign.headline?.text??request.headlineText);
  });
}
export function samePlan(a:EditBatchPlan,b:EditBatchPlan):boolean{return equal(a,b);}
function submission(v:unknown):v is EditSubmission{return keys(v,['jobId','campaignId','operation'],['voiceMasterId'])&&text(v.jobId)&&id(v.campaignId)&&v.operation==='edit_plan'&&(!Object.hasOwn(v,'voiceMasterId')||v.voiceMasterId===null);}
function operation(v:unknown):v is EditOperationView{
  if(!object(v)||!submission({jobId:v.jobId,campaignId:v.campaignId,operation:v.operation,...(Object.hasOwn(v,'voiceMasterId')?{voiceMasterId:v.voiceMasterId}:{})})
    ||!keys(v,['jobId','campaignId','operation','status','result','errorCode'],['voiceMasterId'])||typeof v.status!=='string'
    ||!['queued','running','completed','failed','blocked','retry_scheduled','cancelled'].includes(v.status)||!(v.errorCode===null||text(v.errorCode)))return false;
  return v.result===null?v.status!=='completed':v.status==='completed'&&keys(v.result,['operation','persist','plan'])&&v.result.operation==='edit_plan'&&typeof v.result.persist==='boolean'&&editBatchPlan(v.result.plan)&&v.result.plan.campaignId===v.campaignId;
}
function summary(v:unknown):v is PlanSummary{
  return keys(v,['videoId','renderId','planHash','outputCount','maxOutputs','timingStatus','copyRef','voiceRef','source','outputs'])
    &&id(v.videoId)&&id(v.renderId)&&sha(v.planHash)&&integer(v.outputCount)&&integer(v.maxOutputs)&&v.outputCount<=v.maxOutputs
    &&(v.timingStatus==='missing'||v.timingStatus==='provided')&&copy(v.copyRef)&&identity(v.voiceRef)&&source(v.source)&&v.source.id===v.renderId
    &&Array.isArray(v.outputs)&&v.outputs.length===v.outputCount&&v.outputs.every(o=>outputSummary(o))&&new Set(v.outputs.map(o=>o.key)).size===v.outputCount;
}
export async function submitEditPlan(request:EditBatchRequest,persist:boolean):Promise<EditSubmission>{
  const value:unknown=await post(campaignPath(request.campaignId,'/editing/plans'),{campaignId:request.campaignId,operation:'edit_plan',request,persist});
  if(!submission(value)||value.campaignId!==request.campaignId)throw new ApiError('command_unknown');return value;
}
export function getEditOperation(campaignId:string,jobId:string,signal:AbortSignal):Promise<EditOperationView>{
  return read(campaignPath(campaignId,`/operations/${encodeURIComponent(jobId)}`),signal,v=>operation(v)&&v.campaignId===campaignId&&v.jobId===jobId);
}
export async function listEditPlans(campaignId:string,signal:AbortSignal):Promise<PlanSummary[]>{
  const result=await read<{items:PlanSummary[]}>(campaignPath(campaignId,'/editing/plans'),signal,v=>keys(v,['items'])&&Array.isArray(v.items)&&v.items.every(summary));return result.items;
}
export function getEditPlan(campaignId:string,videoId:string,planHash:string,signal:AbortSignal):Promise<EditBatchPlan>{
  return read(campaignPath(campaignId,`/editing/plans/${encodeURIComponent(videoId)}/${encodeURIComponent(planHash)}`),signal,v=>editBatchPlan(v)&&v.campaignId===campaignId&&v.videoId===videoId&&v.planHash===planHash);
}
