import {expect,it,vi} from 'vitest';
import {getRender,listRenders,renderMediaUrl,startRenderWorker,submitRender} from './renderApi';
import {readWorker} from './api';

const request={schemaVersion:'1.0' as const,campaignId:'campaign-one',videoId:'video-one',planHash:'a'.repeat(64),executionId:'11111111-1111-4111-8111-111111111111'};
const submission={...request,jobId:'22222222-2222-4222-8222-222222222222'};
const output={key:'a',outputVariantId:'variant-a',status:'rendered',renderKey:'b'.repeat(64),fitMeasured:true,path:'campaigns/campaign-one/master.mp4',error:null};
function view(status='completed',outputs=[output],renderStatus='succeeded'){
  return {...submission,status,result:status==='completed'?{schemaVersion:'1.0',planHash:request.planHash,dryRun:false,outputs,hasFailures:outputs.some(o=>o.status==='failed')}:null,
    renderStatus:status==='completed'?renderStatus:null,errorCode:null};
}
const signal=()=>new AbortController().signal;
it.each(['succeeded','partial_failure','failed'])('accepts %s without hiding failed variants',async aggregate=>{
  const failed={...output,key:'b',outputVariantId:'variant-b',status:'failed',fitMeasured:false,path:null,error:{field:'captionInput',message:'accepted timing required'}};
  const outputs=aggregate==='succeeded'?[output]:aggregate==='failed'?[failed]:[output,failed];
  vi.stubGlobal('fetch',async()=>Response.json(view('completed',outputs as typeof output[],aggregate)));
  expect((await getRender(request.campaignId,submission.jobId,signal())).renderStatus).toBe(aggregate);
});
it.each(['dryRun','planned','empty','error','uuid','path','aggregate','duplicate','hash'])('rejects inconsistent %s',async mutation=>{
  const body=view();
  if(!body.result)throw new Error('fixture');
  if(mutation==='dryRun')body.result.dryRun=true;
  if(mutation==='planned')body.result.outputs[0].status='planned';
  if(mutation==='empty')body.result.outputs=[];
  if(mutation==='error')body.errorCode='internal_error' as never;
  if(mutation==='uuid')body.executionId='invalid';
  if(mutation==='path')body.result.outputs[0].path='https://private.example/master.mp4';
  if(mutation==='aggregate')body.renderStatus='failed';
  if(mutation==='duplicate')body.result.outputs.push({...body.result.outputs[0]});
  if(mutation==='hash')body.result.planHash='c'.repeat(64);
  vi.stubGlobal('fetch',async()=>Response.json(body));
  await expect(getRender(request.campaignId,submission.jobId,signal())).rejects.toMatchObject({code:'invalid_response'});
});
it('preserves identity, abort signal and encoded media URLs',async()=>{
  const fetcher=vi.fn(async(_url:string,_init?:RequestInit)=>Response.json(submission));vi.stubGlobal('fetch',fetcher);
  expect(await submitRender(request)).toEqual(submission);
  expect(JSON.parse(fetcher.mock.calls[0][1]?.body as string)).toEqual(request);
  vi.stubGlobal('fetch',async()=>Response.json({...submission,executionId:'33333333-3333-4333-8333-333333333333'}));
  await expect(submitRender(request)).rejects.toMatchObject({code:'invalid_response'});
  const controller=new AbortController();
  const reads=vi.fn(async(_url:string,_init?:RequestInit)=>Response.json(view('queued')));vi.stubGlobal('fetch',reads);
  await getRender(request.campaignId,submission.jobId,controller.signal);
  expect(reads.mock.calls[0][1]?.signal).toBe(controller.signal);
  vi.stubGlobal('fetch',async()=>Response.json({items:[view('queued')]}));
  expect((await listRenders(request.campaignId,request.videoId,request.planHash,signal())).items).toHaveLength(1);
  expect(renderMediaUrl('campaign-one','job-one','variant-a',true)).toBe('/api/v1/campaigns/campaign-one/editing/renders/job-one/outputs/variant-a/media?download=true');
  expect(renderMediaUrl('campaign one','job/one','variant a')).toContain('campaign%20one/editing/renders/job%2Fone/outputs/variant%20a/media');
});
it('starts only the editorial worker and validates its state',async()=>{
  const state={state:'running',campaignId:request.campaignId,kind:'editing_render',errorCode:null};
  const fetcher=vi.fn(async(_url:string,_init?:RequestInit)=>Response.json(state));vi.stubGlobal('fetch',fetcher);
  expect(await startRenderWorker(request.campaignId)).toEqual(state);
  expect(JSON.parse(fetcher.mock.calls[0][1]?.body as string)).toEqual({campaignId:request.campaignId,kind:'editing_render'});
  expect(await readWorker(request.campaignId,signal())).toEqual({scope:'known',value:state});
  vi.stubGlobal('fetch',async()=>Response.json({...state,kind:'bogus'}));
  await expect(readWorker(request.campaignId,signal())).rejects.toMatchObject({code:'invalid_response'});
});
