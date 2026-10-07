import {expect,it,vi} from 'vitest';
import {editOverrides,editBatchRequest,editBatchPlan,planMatchesRequest,samePlan,submitEditPlan,getEditOperation,listEditPlans,getEditPlan} from './editingApi';
import {EDIT_RENDER,EDIT_REQUEST,editPlanFixture} from './editingTestSupport';

it('preserves explicit values and does not impose defaults on partials',()=>{
  expect(editOverrides({music:{volumeDb:0,loop:false,asset:null},headline:{color:'#FFFFFF80',safeLeft:0.98}})).toBe(true);
  expect(editOverrides({})).toBe(true);
});
it.each([{headline:{anchor:['top']}},{framing:{fit:['cover']}},{captions:{text:'replacement'}},{output:{fps:NaN}},
  {music:{volumeDb:Infinity}},{headline:{fontSizePx:null}},{headline:{font:{path:'../a',sha256:'a'.repeat(64)}}},{extra:{}}])('rejects malformed partial %j',value=>expect(editOverrides(value)).toBe(false));
it('enforces strict request identity, keys and output limit',()=>{
  expect(editBatchRequest(EDIT_REQUEST)).toBe(true);
  expect(editBatchRequest({...EDIT_REQUEST,maxOutputs:0})).toBe(false);
  expect(editBatchRequest({...EDIT_REQUEST,maxOutputs:1,variants:[...EDIT_REQUEST.variants,...EDIT_REQUEST.variants]})).toBe(false);
  expect(editBatchRequest({...EDIT_REQUEST,videoId:'CON'})).toBe(false);
  expect(editBatchRequest({...EDIT_REQUEST,headlineText:' '})).toBe(false);
});
it('correlates roots by bindings instead of comparing path prefixes',()=>{
  const plan=editPlanFixture();
  expect(editBatchPlan(plan)).toBe(true);
  expect(planMatchesRequest(plan,EDIT_REQUEST,EDIT_RENDER)).toBe(true);
  expect(planMatchesRequest({...plan,renderId:'other'},EDIT_REQUEST,EDIT_RENDER)).toBe(false);
  expect(planMatchesRequest({...plan,voiceRef:{id:'other',hash:plan.voiceRef.hash}},EDIT_REQUEST,EDIT_RENDER)).toBe(false);
  expect(planMatchesRequest({...plan,maxOutputs:9},EDIT_REQUEST,EDIT_RENDER)).toBe(false);
});
it('normalizes empty sections but not null or scalar omissions',()=>{
  const plan=editPlanFixture(); plan.outputs[0].manifest.overrides.campaign={headline:{},music:{}};
  expect(planMatchesRequest(plan,EDIT_REQUEST,EDIT_RENDER)).toBe(true);
  plan.outputs[0].manifest.overrides.campaign={music:{asset:null}};
  expect(planMatchesRequest(plan,EDIT_REQUEST,EDIT_RENDER)).toBe(false);
});
it.each(['schemaVersion','source','captionInput','outputs','planHash'])('rejects missing plan %s',key=>{
  const plan:Record<string,unknown>={...editPlanFixture()};delete plan[key];expect(editBatchPlan(plan)).toBe(false);
});
it('rejects caption/output inconsistencies and detects complete plan differences',()=>{
  const plan=editPlanFixture();const bad=structuredClone(plan);bad.outputs[0].captionState='timing_provided';
  expect(editBatchPlan(bad)).toBe(false);
  bad.outputs[0].captionState='disabled';bad.outputs[0].manifest.voiceRef={id:'other',hash:'d'.repeat(64)};
  expect(editBatchPlan(bad)).toBe(false);
  expect(samePlan(plan,structuredClone(plan))).toBe(true);
  expect(samePlan(plan,bad)).toBe(false);
});
it('does not reject inactive music fades exceeding source duration',()=>{
  const plan=editPlanFixture();plan.outputs[0].manifest.music.fadeOutSec=9;
  expect(editBatchPlan(plan)).toBe(true);
});
it('checks timing enums as strings and cue coverage',()=>{
  const plan=editPlanFixture();plan.captionInput={...plan.captionInput,timingStatus:'provided',timingRef:{path:'timing/a.json',sha256:'a'.repeat(64)},
    origin:'manual',acceptedBy:'tester',timebase:'source_mp4',cues:[{startSec:0,endSec:1,tokenStart:0,tokenEnd:3,text:'Hook Body CTA'}]};
  expect(editBatchPlan(plan)).toBe(true);
  expect(editBatchPlan({...plan,captionInput:{...plan.captionInput,acceptedBy:'tester:local'}})).toBe(true);
  expect(editBatchPlan({...plan,captionInput:{...plan.captionInput,origin:['manual']}})).toBe(false);
  plan.captionInput.cues[0].tokenEnd=2;expect(editBatchPlan(plan)).toBe(false);
});
it('rejects mismatched request bindings and explicit overrides',()=>{
  const plan=editPlanFixture();
  expect(planMatchesRequest(plan,{...EDIT_REQUEST,headlineText:'unused base'},EDIT_RENDER)).toBe(true);
  expect(planMatchesRequest(plan,{...EDIT_REQUEST,profileRef:{...EDIT_REQUEST.profileRef,hash:'f'.repeat(64)}},EDIT_RENDER)).toBe(false);
  expect(planMatchesRequest(plan,{...EDIT_REQUEST,video:{music:{loop:false}}},EDIT_RENDER)).toBe(false);
  expect(planMatchesRequest(plan,EDIT_REQUEST,{...EDIT_RENDER,imageSha256:'f'.repeat(64)})).toBe(false);
  expect(planMatchesRequest(plan,EDIT_REQUEST,{...EDIT_RENDER,source:{...EDIT_RENDER.source!,sha256:'f'.repeat(64)}})).toBe(false);
});
it('submits the exact persist operation and classifies a wrong 202 as unknown',async()=>{
  const posts:unknown[]=[];
  vi.stubGlobal('fetch',async(_path:string,init:RequestInit)=>{posts.push(JSON.parse(String(init.body)));return Response.json({jobId:'job-one',campaignId:'campaign-one',operation:'edit_plan'});});
  expect(await submitEditPlan(EDIT_REQUEST,false)).toMatchObject({jobId:'job-one'});
  expect(posts).toEqual([{campaignId:'campaign-one',operation:'edit_plan',request:EDIT_REQUEST,persist:false}]);
  vi.stubGlobal('fetch',async()=>Response.json({jobId:'job-one',campaignId:'other',operation:'edit_plan'}));
  await expect(submitEditPlan(EDIT_REQUEST,true)).rejects.toMatchObject({code:'command_unknown'});
});
it('reads exact operation and artifact, rejecting divergent GET identity',async()=>{
  const plan=editPlanFixture(); const signal=new AbortController().signal;
  vi.stubGlobal('fetch',async(path:string)=>Response.json(path.includes('/operations/')
    ?{jobId:'job-one',campaignId:'campaign-one',operation:'edit_plan',status:'completed',errorCode:null,result:{operation:'edit_plan',persist:false,plan}}
    :plan));
  expect((await getEditOperation('campaign-one','job-one',signal)).result?.plan).toEqual(plan);
  expect(await getEditPlan('campaign-one','render-one',plan.planHash,signal)).toEqual(plan);
  await expect(getEditPlan('campaign-one','wrong',plan.planHash,signal)).rejects.toMatchObject({code:'invalid_response'});
  vi.stubGlobal('fetch',async()=>Response.json({items:[]}));expect(await listEditPlans('campaign-one',signal)).toEqual([]);
});
