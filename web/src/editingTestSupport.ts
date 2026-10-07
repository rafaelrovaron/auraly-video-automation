import type {EditBatchPlan, EditBatchRequest} from './editingApi';
import type {HeyGenRenderView} from './heygenApi';
import {PROFILE} from './profileTestSupport';

const sha = 'b'.repeat(64), time = '2026-10-07T00:00:00Z';
export const EDIT_RENDER: HeyGenRenderView = {
  renderId:'render-one',campaignId:'campaign-one',sceneVariantId:'scene-one',imageCandidateId:'image-one',voiceMasterId:'voice-one',
  jobId:'upstream-one',status:'ready',remoteVideoId:'remote-one',manualBinding:false,imageSha256:'c'.repeat(64),audioSha256:'d'.repeat(64),
  errorCode:null,createdAt:time,updatedAt:time,source:{path:'videos/a.mp4',sha256:sha,sizeBytes:100,probe:{formatName:'mp4',durationSec:5,sizeBytes:100,
    video:{codec:'h264',width:1080,height:1920,fps:30,nominalFps:30,isVfr:false,rotation:0},
    audio:{codec:'aac',sampleRate:48000,channels:1},warnings:[],hasAudio:true}},
};
export const EDIT_REQUEST: EditBatchRequest = {schemaVersion:'1.0',campaignId:'campaign-one',renderId:'render-one',videoId:'render-one',
  profileRef:{profileId:'plain',version:1,hash:'a'.repeat(64)},headlineText:'Base',campaign:{},video:{},musicAccepted:false,
  variants:[{key:'a',label:'A',overrides:{headline:{text:'Headline A'}}}],maxOutputs:3,timingRef:null};
// A complete synthetic boundary response; fake hashes are never used as real asset validation.
export function editPlanFixture(request: EditBatchRequest = EDIT_REQUEST): EditBatchPlan {
  const source = {id:request.renderId,path:'work/videos/a.mp4',sha256:sha,durationSec:5};
  const copyRef = {id:'copy-one',version:1,hash:'e'.repeat(64)}, voiceRef = {id:'voice-one',hash:'d'.repeat(64)};
  const defaults = structuredClone(PROFILE.profile.defaults);
  const outputs = [...request.variants].sort((a,b)=>a.key.localeCompare(b.key)).map(variant=>{
    const text = variant.overrides.headline?.text ?? request.video.headline?.text ?? request.campaign.headline?.text ?? request.headlineText;
    const headline = {...defaults.headline,endSec:5,text};
    const provenance: Record<string,'profile'|'input'|'source'|'outputVariant'> = {};
    for(const [section,style] of Object.entries({...defaults,headline})) for(const field of Object.keys(style)) provenance[`${section}.${field}`]='profile';
    provenance['headline.text'] = variant.overrides.headline?.text ? 'outputVariant' : 'input';
    provenance['headline.endSec']='source';
    const outputVariantId = `ab-${variant.key}`, digest = 'f'.repeat(64);
    return {key:variant.key,label:variant.label,outputVariantId,manifestHash:digest,outputHash:sha,filename:`${outputVariantId}-${sha}.mp4`,captionState:'disabled' as const,
      manifest:{schemaVersion:'2.0' as const,resolverVersion:'1.0' as const,campaignId:request.campaignId,videoId:request.videoId,outputVariantId,
        source,profileRef:request.profileRef,copyRef:{id:copyRef.id,hash:copyRef.hash},voiceRef,musicAccepted:request.musicAccepted,
        ...defaults,headline,overrides:{campaign:request.campaign,video:request.video,outputVariant:variant.overrides},provenance,manifestHash:digest}};
  });
  return {schemaVersion:'1.0',plannerVersion:'1.0',campaignId:request.campaignId,renderId:request.renderId,videoId:request.videoId,source,copyRef,voiceRef,
    imageRef:{id:'image-one',hash:'c'.repeat(64)},maxOutputs:request.maxOutputs,outputCount:outputs.length,
    captionInput:{copyRef,voiceRef,text:'Hook Body CTA',timingStatus:'missing',timingRef:null,origin:null,acceptedBy:null,timebase:null,cues:[]},outputs,planHash:sha};
}
