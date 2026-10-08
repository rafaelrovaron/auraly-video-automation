import {act,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {beforeEach,expect,it,vi} from 'vitest';
import {RenderPanel} from './RenderPanel';
import {ApiError,readWorker} from './api';
import {getRender,listRenders,startRenderWorker,submitRender} from './renderApi';
import type {RenderJobRequest,RenderJobView} from './renderApi';
import {editPlanFixture} from './editingTestSupport';
vi.mock('./renderApi',async original=>({...await original<typeof import('./renderApi')>(),getRender:vi.fn(),listRenders:vi.fn(),startRenderWorker:vi.fn(),submitRender:vi.fn()}));
vi.mock('./api',async original=>({...await original<typeof import('./api')>(),readWorker:vi.fn()}));
const plan=editPlanFixture(),jobId='22222222-2222-4222-8222-222222222222';
const body:RenderJobRequest={schemaVersion:'1.0',campaignId:plan.campaignId,videoId:plan.videoId,planHash:plan.planHash,executionId:'11111111-1111-4111-8111-111111111111'};
const queued:RenderJobView={...body,jobId,status:'queued',result:null,renderStatus:null,errorCode:null};
const completed:RenderJobView={...queued,status:'completed',renderStatus:'partial_failure',result:{schemaVersion:'1.0',planHash:plan.planHash,dryRun:false,hasFailures:true,outputs:[
  {key:'a',outputVariantId:'ab-a',renderKey:'a'.repeat(64),status:'rendered',fitMeasured:true,path:'masters/a.mp4',error:null},
  {key:'b',outputVariantId:'ab-b',renderKey:'b'.repeat(64),status:'failed',fitMeasured:false,path:null,error:{field:'captionInput',message:'private-path-secret'}}]}};
beforeEach(()=>{
  vi.spyOn(crypto,'randomUUID').mockReturnValue(body.executionId as `${string}-${string}-${string}-${string}-${string}`);
  vi.mocked(listRenders).mockResolvedValue({items:[]});vi.mocked(getRender).mockResolvedValue(queued);
  vi.mocked(submitRender).mockResolvedValue({...body,jobId});
  vi.mocked(startRenderWorker).mockResolvedValue({state:'running',campaignId:plan.campaignId,kind:'editing_render',errorCode:null});
});
it('requires a confirmed plan and freezes one double-click submission',async()=>{
  const view=render(<RenderPanel campaignId={plan.campaignId} plan={null}/>);
  expect(screen.getByRole('button',{name:'Renderizar'}).hasAttribute('disabled')).toBe(true);
  view.rerender(<RenderPanel campaignId={plan.campaignId} plan={plan}/>);
  const button=screen.getByRole('button',{name:'Renderizar'});fireEvent.click(button);fireEvent.click(button);
  await waitFor(()=>expect(startRenderWorker).toHaveBeenCalledOnce());
  expect(submitRender).toHaveBeenCalledExactlyOnceWith(body);expect(screen.getByText(plan.planHash,{exact:false})).toBeTruthy();
});
it('busy worker retains queued job and starting later never resubmits',async()=>{
  vi.mocked(startRenderWorker).mockRejectedValueOnce(new ApiError('operation_conflict',409));
  render(<RenderPanel campaignId={plan.campaignId} plan={plan}/>);fireEvent.click(screen.getByRole('button',{name:'Renderizar'}));
  await screen.findByText(/Outro worker/);
  fireEvent.click(screen.getByRole('button',{name:'Iniciar render pendente'}));
  await waitFor(()=>expect(startRenderWorker).toHaveBeenCalledTimes(2));expect(submitRender).toHaveBeenCalledOnce();
});
it('lost POST reconciles only exact execution identity',async()=>{
  vi.mocked(submitRender).mockRejectedValue(new ApiError('command_unknown'));
  const other={...queued,executionId:'33333333-3333-4333-8333-333333333333'};
  render(<RenderPanel campaignId={plan.campaignId} plan={plan}/>);fireEvent.click(screen.getByRole('button',{name:'Renderizar'}));
  await screen.findByText(/Resultado do comando desconhecido/);
  vi.mocked(listRenders).mockResolvedValueOnce({items:[other]});fireEvent.click(screen.getByRole('button',{name:'Consultar execuções'}));
  await screen.findByText(/Execução enviada ainda não encontrada/);expect(getRender).not.toHaveBeenCalled();
  vi.mocked(listRenders).mockResolvedValueOnce({items:[queued]});fireEvent.click(screen.getByRole('button',{name:'Consultar execuções'}));
  await waitFor(()=>expect(getRender).toHaveBeenCalled());expect(submitRender).toHaveBeenCalledOnce();expect(startRenderWorker).not.toHaveBeenCalled();
});
it('shows partial outcomes, safe media links and explicit new execution',async()=>{
  vi.mocked(getRender).mockResolvedValue(completed);
  render(<RenderPanel campaignId={plan.campaignId} plan={plan}/>);fireEvent.click(screen.getByRole('button',{name:'Renderizar'}));
  await screen.findByText('Falha parcial');expect(screen.getAllByRole('link',{name:'Abrir MP4'})).toHaveLength(1);
  expect(screen.getByRole('link',{name:'Baixar MP4'}).getAttribute('href')).toContain('/outputs/ab-a/media?download=true');
  expect(screen.queryByText(/private-path-secret/)).toBeNull();expect(screen.getByText(/Podem ser reaproveitados/)).toBeTruthy();
  vi.mocked(crypto.randomUUID).mockReturnValue('44444444-4444-4444-8444-444444444444');
  vi.mocked(submitRender).mockResolvedValue({...body,executionId:'44444444-4444-4444-8444-444444444444',jobId:'55555555-5555-4555-8555-555555555555'});
  fireEvent.click(screen.getByRole('button',{name:'Nova execução'}));expect(screen.queryByText('Falha parcial')).toBeNull();
  await waitFor(()=>expect(submitRender).toHaveBeenCalledTimes(2));
  expect(vi.mocked(submitRender).mock.calls[1][0]).toMatchObject({...body,executionId:'44444444-4444-4444-8444-444444444444'});
});
it('reload lists persisted runs and abandoning observation never cancels',async()=>{
  vi.mocked(listRenders).mockResolvedValue({items:[queued]});
  render(<RenderPanel campaignId={plan.campaignId} plan={plan}/>);
  await screen.findByRole('option',{name:new RegExp(jobId)});
  fireEvent.change(screen.getByLabelText('Execução de render'),{target:{value:jobId}});
  await waitFor(()=>expect(getRender).toHaveBeenCalledOnce());
  fireEvent.click(screen.getByRole('button',{name:'Abandonar acompanhamento do render'}));
  expect(submitRender).not.toHaveBeenCalled();expect(startRenderWorker).not.toHaveBeenCalled();
  expect(screen.getByText(/Não cancela o Job/)).toBeTruthy();
});
it('polls serially, aborts stale reads and discards late results on plan change',async()=>{
  vi.useFakeTimers();let resolve!: (v:RenderJobView)=>void;
  vi.mocked(getRender).mockImplementationOnce(()=>new Promise(r=>{resolve=r;}));
  const view=render(<RenderPanel campaignId={plan.campaignId} plan={plan}/>);
  await act(async()=>{fireEvent.click(screen.getByRole('button',{name:'Renderizar'}));});
  await act(async()=>{await vi.advanceTimersByTimeAsync(10000);});expect(getRender).toHaveBeenCalledOnce();
  await act(async()=>{resolve(queued);});
  await act(async()=>{await vi.advanceTimersByTimeAsync(1999);});expect(getRender).toHaveBeenCalledOnce();
  await act(async()=>{await vi.advanceTimersByTimeAsync(1);});expect(getRender).toHaveBeenCalledTimes(2);
  let late!: (v:RenderJobView)=>void;vi.mocked(getRender).mockImplementationOnce(()=>new Promise(r=>{late=r;}));
  await act(async()=>{await vi.advanceTimersByTimeAsync(2000);});
  const lastSignal=vi.mocked(getRender).mock.calls[2][2];
  view.rerender(<RenderPanel campaignId={plan.campaignId} plan={{...plan,planHash:'e'.repeat(64)}}/>);
  expect(lastSignal.aborted).toBe(true);await act(async()=>{late(completed);});
  expect(screen.queryByText('Falha parcial')).toBeNull();view.unmount();
});
it('uncertain worker start reads fresh state before offering another start',async()=>{
  vi.mocked(startRenderWorker).mockRejectedValue(new ApiError('command_unknown'));
  vi.mocked(readWorker).mockRejectedValueOnce(new ApiError('connection_lost'));
  render(<RenderPanel campaignId={plan.campaignId} plan={plan}/>);fireEvent.click(screen.getByRole('button',{name:'Renderizar'}));
  await screen.findByRole('button',{name:'Consultar worker de render'});
  expect(screen.getByRole('button',{name:'Iniciar render pendente'}).hasAttribute('disabled')).toBe(true);
  vi.mocked(readWorker).mockResolvedValueOnce({scope:'known',value:{state:'idle',campaignId:plan.campaignId,kind:'editing_render',errorCode:null}});
  fireEvent.click(screen.getByRole('button',{name:'Consultar worker de render'}));
  await waitFor(()=>expect(screen.getByRole('button',{name:'Iniciar render pendente'}).hasAttribute('disabled')).toBe(false));
  expect(startRenderWorker).toHaveBeenCalledOnce();
});
it('unknown submission can be explicitly abandoned only after a successful consultation',async()=>{
  vi.mocked(submitRender).mockRejectedValueOnce(new ApiError('command_unknown'));
  vi.spyOn(window,'confirm').mockReturnValue(false);
  render(<RenderPanel campaignId={plan.campaignId} plan={plan}/>);
  fireEvent.click(screen.getByRole('button',{name:'Renderizar'}));await screen.findByText(/Resultado do comando desconhecido/);
  const abandon=screen.getByRole('button',{name:'Abandonar acompanhamento do render'});
  expect(abandon.hasAttribute('disabled')).toBe(true);
  vi.mocked(listRenders).mockRejectedValueOnce(new ApiError('connection_lost'));
  fireEvent.click(screen.getByRole('button',{name:'Consultar execuções'}));await screen.findByText(/Sem conexão/);
  expect(abandon.hasAttribute('disabled')).toBe(true);
  fireEvent.click(screen.getByRole('button',{name:'Consultar execuções'}));await screen.findByText(/Execução enviada ainda não encontrada/);
  expect(abandon.hasAttribute('disabled')).toBe(false);
  fireEvent.click(abandon);expect(screen.getByRole('button',{name:'Renderizar'}).hasAttribute('disabled')).toBe(true);
  vi.mocked(window.confirm).mockReturnValue(true);fireEvent.click(abandon);
  expect(screen.getByRole('button',{name:'Renderizar'}).hasAttribute('disabled')).toBe(false);
  expect(submitRender).toHaveBeenCalledOnce();expect(startRenderWorker).not.toHaveBeenCalled();
  vi.mocked(crypto.randomUUID).mockReturnValue('44444444-4444-4444-8444-444444444444');
  vi.mocked(submitRender).mockResolvedValue({...body,executionId:'44444444-4444-4444-8444-444444444444',jobId});
  fireEvent.click(screen.getByRole('button',{name:'Renderizar'}));
  await waitFor(()=>expect(submitRender).toHaveBeenCalledTimes(2));
  expect(vi.mocked(submitRender).mock.calls[1][0].executionId).toBe('44444444-4444-4444-8444-444444444444');
});
it.each([
  ['captionInput','captionInput: accepted caption timing required','Timing de legendas ausente'],
  ['text.fit','text.fit: text does not fit safe zones and maxLines','Texto não cabe'],
  ['output','output: orphan render; manual artifact repair required','Master órfão'],
  ['text.font','text.font: local font selection or glyph coverage failed','Confira a fonte local'],
])('shows actionable safe reason for %s',async(field,message,expected)=>{
  const result=structuredClone(completed);if(!result.result)throw new Error('fixture');
  result.result.outputs[1].error={field,message};vi.mocked(getRender).mockResolvedValue(result);
  render(<RenderPanel campaignId={plan.campaignId} plan={plan}/>);fireEvent.click(screen.getByRole('button',{name:'Renderizar'}));
  await screen.findByText(new RegExp(expected));
  expect(screen.getAllByRole('link',{name:'Abrir MP4'})).toHaveLength(1);
});
