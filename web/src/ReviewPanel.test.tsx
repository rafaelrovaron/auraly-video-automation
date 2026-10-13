import {act,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {beforeEach,expect,it,vi} from 'vitest';
import {ReviewPanel} from './ReviewPanel';
import {ApiError,readWorker} from './api';
import {getReviewState,submitQc,submitReview,startQcWorker} from './reviewApi';
import type {ReviewTarget,FinalReviewState,QcReport,HumanReviewRecord,HumanReviewRequest} from './reviewApi';
vi.mock('./reviewApi',async original=>({...await original<typeof import('./reviewApi')>(),getReviewState:vi.fn(),submitQc:vi.fn(),submitReview:vi.fn(),startQcWorker:vi.fn()}));
vi.mock('./api',async original=>({...await original<typeof import('./api')>(),readWorker:vi.fn()}));
vi.mock('./DeliveryPanel',()=>({DeliveryPanel:()=> <p>Approved delivery controls</p>}));
const target:ReviewTarget={schemaVersion:'1.0',campaignId:'campaign-one',videoId:'video-one',planHash:'a'.repeat(64),jobId:'22222222-2222-4222-8222-222222222222',outputVariantId:'variant-a',renderKey:'b'.repeat(64)};
const executionId='11111111-1111-4111-8111-111111111111',qcJobId='33333333-3333-4333-8333-333333333333';
const {jobId:_,...context}=target;
const report={...context,qcKey:'c'.repeat(64),status:'passed',qcVersion:'1.0',humanReviewRequired:true,masterPath:'masters/a.mp4',masterSha256:'d'.repeat(64),receiptSha256:'e'.repeat(64),sizeBytes:123,
  manifestHash:'f'.repeat(64),outputHash:'a'.repeat(64),policyHash:'b'.repeat(64),policy:{version:'final-qc-v1',minLoudnessLufs:-30,maxTruePeakDbtp:0},
  runtime:{fingerprint:'a'.repeat(64),ffmpegVersion:'test',libassVersion:'test',encoding:{}},probe:null,
  checks:[{name:'integrity',status:'passed',findings:[]},{name:'audio',status:'passed',findings:[]},{name:'text',status:'not_applicable',findings:[]}],
  audio:{integratedLoudnessLufs:-16,truePeakDbtp:-2},textMeasurementScope:'manifest_layout_not_burned_frames'} as QcReport;
const empty:FinalReviewState={...target,reports:[],review:null,runs:[]};
const ready:FinalReviewState={...empty,reports:[report]};
const queued:FinalReviewState={...empty,runs:[{jobId:qcJobId,campaignId:target.campaignId,operation:'edit_qc',voiceMasterId:null,
  request:{...target,operation:'edit_qc',executionId},status:'queued',result:null,errorCode:null}]};
function record(body:HumanReviewRequest):HumanReviewRecord{return {...body,reviewedAt:'2026-10-11T10:00:00Z',reviewKey:'f'.repeat(64),masterPath:report.masterPath,masterSha256:report.masterSha256,
  sizeBytes:report.sizeBytes,receiptSha256:report.receiptSha256,qcReportSha256:'a'.repeat(64),qcStatus:report.status};}
beforeEach(()=>{
  vi.mocked(getReviewState).mockResolvedValue(empty);
  vi.mocked(submitQc).mockResolvedValue({jobId:qcJobId,campaignId:target.campaignId,operation:'edit_qc',voiceMasterId:null});
  vi.mocked(startQcWorker).mockResolvedValue({state:'running',campaignId:target.campaignId,kind:'local_operations',errorCode:null});
  vi.mocked(submitReview).mockImplementation(async(_target,body)=>record(body));
  vi.spyOn(crypto,'randomUUID').mockReturnValue(executionId);vi.spyOn(window,'confirm').mockReturnValue(true);
});
async function mount(state=empty){vi.mocked(getReviewState).mockResolvedValue(state);const view=render(<ReviewPanel target={target}/>);await waitFor(()=>expect(screen.getByRole('button',{name:'Consultar QC e review'}).hasAttribute('disabled')).toBe(false));return view;}
it('labels QC reports by result and keeps hashes in technical details',async()=>{
  await mount(ready);
  const select=screen.getByLabelText('Relatório QC') as HTMLSelectElement;
  expect(select.options[1].value).toBe(report.qcKey);
  expect(select.options[1].textContent).not.toContain(report.qcKey);
  expect(select.options[1].textContent).not.toContain(report.runtime.fingerprint);
  expect(screen.getByText(report.qcKey).closest('details')?.querySelector('summary')?.textContent).toBe('Detalhes técnicos do QC');
});
it('shows QC execution status without the full execution ID',async()=>{
  await mount(queued);
  const section=screen.getByRole('region',{name:'QC e review variant-a'});
  const visible=[...section.querySelectorAll('p')].filter(node=>!node.closest('details')).map(node=>node.textContent).join(' ');
  expect(visible).toContain('QC na fila');
  expect(visible).not.toContain(executionId);
  expect(screen.getByText(executionId).closest('details')?.querySelector('summary')?.textContent).toBe('Detalhes técnicos da execução');
});
it('keeps an uncertain QC request ID available without showing it in the normal view',async()=>{
  await mount();vi.mocked(submitQc).mockRejectedValueOnce(new ApiError('command_unknown'));
  fireEvent.click(screen.getByRole('button',{name:'Executar QC'}));
  await screen.findByText(/Resultado do comando desconhecido/);
  expect(screen.getByText(executionId).closest('details')?.querySelector('summary')?.textContent).toBe('Detalhes técnicos do envio');
});
it('mounts delivery only for a definitive approved review',async()=>{
  const approved=record({...context,qcKey:report.qcKey,decision:'approved',reviewedBy:'Tester',comment:null,watchedAndListened:true});
  const view=await mount({...ready,review:approved});expect(screen.getByText('Approved delivery controls')).toBeTruthy();view.unmount();
  await mount({...ready,review:{...approved,decision:'rejected',comment:'Change headline'}});
  expect(screen.queryByText('Approved delivery controls')).toBeNull();
});
function fill(){fireEvent.change(screen.getByLabelText('Nome do revisor'),{target:{value:'Tester'}});fireEvent.click(screen.getByLabelText('Assisti e ouvi este master'));}
it('qc_is_explicit_and_completed_error_is_not_passed',async()=>{
  await mount();expect(submitQc).not.toHaveBeenCalled();expect(startQcWorker).not.toHaveBeenCalled();
  const player=screen.getByLabelText('Master final') as HTMLVideoElement;expect(player.controls).toBe(true);expect(player.preload).toBe('metadata');expect(player.autoplay).toBe(false);expect(player.muted).toBe(false);
  expect(screen.getByText(/QC técnico aprovado não significa aprovação humana/)).toBeTruthy();
  const completed={...queued.runs[0],status:'completed' as const,result:{operation:'edit_qc' as const,request:queued.runs[0].request,result:{...context,outputs:[{outputVariantId:target.outputVariantId,renderKey:target.renderKey,status:'error' as const,reused:false,qcKey:null,path:null,error:{code:'artifact_invalid',field:'artifact',message:'private-secret'}}],hasFailures:true}}};
  vi.mocked(getReviewState).mockResolvedValue({...empty,runs:[completed]});fireEvent.click(screen.getByRole('button',{name:'Executar QC'}));
  await screen.findByText(/QC não concluído/);expect(screen.getByRole('button',{name:'Aprovar master'}).hasAttribute('disabled')).toBe(true);expect(screen.queryByText(/private-secret/)).toBeNull();
});
it('worker_busy_preserves_queued_qc',async()=>{
  await mount();vi.mocked(startQcWorker).mockRejectedValueOnce(new ApiError('operation_conflict',409));vi.mocked(getReviewState).mockResolvedValue(queued);
  fireEvent.click(screen.getByRole('button',{name:'Executar QC'}));await screen.findByText(/Outro worker/);
  fireEvent.click(screen.getByRole('button',{name:'Iniciar QC pendente'}));await waitFor(()=>expect(startQcWorker).toHaveBeenCalledTimes(2));expect(submitQc).toHaveBeenCalledOnce();
});
it('lost_qc_response_is_consulted_not_reposted',async()=>{
  await mount();vi.mocked(submitQc).mockRejectedValueOnce(new ApiError('command_unknown'));
  fireEvent.click(screen.getByRole('button',{name:'Executar QC'}));await screen.findByText(/Resultado do comando desconhecido/);
  expect(screen.getByRole('button',{name:'Abandonar envio QC desconhecido'}).hasAttribute('disabled')).toBe(true);
  vi.mocked(getReviewState).mockRejectedValueOnce(new ApiError('connection_lost'));
  fireEvent.click(screen.getByRole('button',{name:'Consultar QC e review'}));await screen.findByText(/Sem conexão/);
  expect(screen.getByRole('button',{name:'Abandonar envio QC desconhecido'}).hasAttribute('disabled')).toBe(true);
  vi.mocked(getReviewState).mockResolvedValue(queued);fireEvent.click(screen.getByRole('button',{name:'Consultar QC e review'}));await screen.findByText(/QC na fila/);
  expect(submitQc).toHaveBeenCalledOnce();expect(startQcWorker).not.toHaveBeenCalled();
});
it('lost_review_response_recovers_original_or_retries_frozen_request',async()=>{
  await mount(ready);fill();vi.mocked(submitReview).mockRejectedValueOnce(new ApiError('command_unknown'));
  fireEvent.click(screen.getByRole('button',{name:'Aprovar master'}));await screen.findByText(/Resultado do comando desconhecido/);
  const frozen=vi.mocked(submitReview).mock.calls[0][1];
  expect(screen.getByRole('button',{name:'Aprovar master'}).hasAttribute('disabled')).toBe(true);
  fireEvent.click(screen.getByRole('button',{name:'Consultar QC e review'}));await screen.findByRole('button',{name:'Reenviar a mesma decisão'});
  fireEvent.click(screen.getByRole('button',{name:'Reenviar a mesma decisão'}));await screen.findByText('Decisão definitiva: Aprovado');
  expect(vi.mocked(submitReview).mock.calls[1][1]).toEqual(frozen);expect(window.confirm).toHaveBeenCalledOnce();
});
it('reports a pending human decision to the parent before plan switching',async()=>{
  vi.mocked(getReviewState).mockResolvedValue(ready);
  vi.mocked(submitReview).mockImplementationOnce(()=>new Promise(()=>{}));
  const changes:boolean[]=[];
  render(<ReviewPanel target={target} onBusyChange={(_key,busy)=>changes.push(busy)}/>);
  await screen.findByRole('button',{name:'Aprovar master'});
  fill();fireEvent.click(screen.getByRole('button',{name:'Aprovar master'}));
  expect(changes.at(-1)).toBe(true);
});
it('another_client_decision_is_readonly',async()=>{
  await mount(ready);fill();vi.mocked(submitReview).mockRejectedValueOnce(new ApiError('operation_conflict',409));
  fireEvent.click(screen.getByRole('button',{name:'Aprovar master'}));await screen.findByText(/Consulte a decisão/);
  const winner=record({...context,qcKey:report.qcKey,decision:'rejected',reviewedBy:'Other',comment:'Timing',watchedAndListened:false});
  vi.mocked(getReviewState).mockResolvedValue({...ready,review:winner});fireEvent.click(screen.getByRole('button',{name:'Consultar QC e review'}));await screen.findByText('Decisão definitiva: Rejeitado');
  expect(screen.queryByRole('button',{name:'Aprovar master'})).toBeNull();expect(screen.getByText(/Other/)).toBeTruthy();
});
it('multiple_qc_requires_selection',async()=>{
  await mount({...ready,reports:[report,{...report,qcKey:'f'.repeat(64)}]});fill();
  expect(screen.getByRole('button',{name:'Aprovar master'}).hasAttribute('disabled')).toBe(true);
  fireEvent.change(screen.getByLabelText('Relatório QC'),{target:{value:report.qcKey}});
  fireEvent.click(screen.getByLabelText('Assisti e ouvi este master'));
  expect(screen.getByRole('button',{name:'Aprovar master'}).hasAttribute('disabled')).toBe(false);
});
it('QC integrity failure clears the prior actionable evidence',async()=>{
  await mount(ready);fill();fireEvent.change(screen.getByLabelText('Comentário'),{target:{value:'Review'}});
  vi.mocked(submitQc).mockRejectedValueOnce(new ApiError('artifact_invalid',409));
  fireEvent.click(screen.getByRole('button',{name:'Executar QC'}));await screen.findByText(/Artefato armazenado inválido/);
  expect(screen.queryByText('QC técnico: Aprovado')).toBeNull();
  for(const name of ['Executar QC','Aprovar master','Rejeitar master'])expect(screen.getByRole('button',{name}).hasAttribute('disabled')).toBe(true);
  expect((screen.getByLabelText('Assisti e ouvi este master') as HTMLInputElement).checked).toBe(false);
  expect(startQcWorker).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'Consultar QC e review'}));await screen.findByText('QC técnico: Aprovado');
  expect(screen.getByRole('button',{name:'Executar QC'}).hasAttribute('disabled')).toBe(false);
  expect(screen.getByRole('button',{name:'Aprovar master'}).hasAttribute('disabled')).toBe(true);
});
it('one report becoming multiple after QC requires a fresh explicit selection',async()=>{
  await mount(ready);fill();
  vi.mocked(getReviewState).mockResolvedValue({...queued,reports:[report]});
  fireEvent.click(screen.getByRole('button',{name:'Executar QC'}));await screen.findByText(/QC na fila/);
  vi.mocked(getReviewState).mockResolvedValue({...ready,reports:[report,{...report,qcKey:'f'.repeat(64),status:'blocked'}]});
  fireEvent.click(screen.getByRole('button',{name:'Consultar QC e review'}));
  await waitFor(()=>expect(screen.getByRole('button',{name:'Executar QC'}).hasAttribute('disabled')).toBe(false));
  expect((screen.getByLabelText('Relatório QC') as HTMLSelectElement).value).toBe('');
  expect(screen.getByRole('button',{name:'Aprovar master'}).hasAttribute('disabled')).toBe(true);
  fireEvent.change(screen.getByLabelText('Relatório QC'),{target:{value:report.qcKey}});
  fireEvent.click(screen.getByLabelText('Assisti e ouvi este master'));
  expect(screen.getByRole('button',{name:'Aprovar master'}).hasAttribute('disabled')).toBe(false);
});
it('stale_response_or_target_change_clears_old_approval',async()=>{
  const saved=record({...context,qcKey:report.qcKey,decision:'approved',reviewedBy:'Tester',comment:null,watchedAndListened:true});
  const view=await mount({...ready,review:saved});expect(screen.getByText('Decisão definitiva: Aprovado')).toBeTruthy();
  vi.mocked(getReviewState).mockRejectedValueOnce(new ApiError('artifact_invalid',409));fireEvent.click(screen.getByRole('button',{name:'Consultar QC e review'}));await screen.findByText(/Artefato armazenado inválido/);
  expect(screen.queryByText('Decisão definitiva: Aprovado')).toBeNull();expect(screen.getByRole('button',{name:'Aprovar master'}).hasAttribute('disabled')).toBe(true);
  let resolve!:(s:FinalReviewState)=>void;vi.mocked(getReviewState).mockImplementationOnce(()=>new Promise(r=>{resolve=r;}));fireEvent.click(screen.getByRole('button',{name:'Consultar QC e review'}));
  const signal=vi.mocked(getReviewState).mock.calls.at(-1)![1];vi.mocked(getReviewState).mockResolvedValue({...empty,planHash:'e'.repeat(64)});
  view.rerender(<ReviewPanel target={{...target,planHash:'e'.repeat(64)}}/>);await act(async()=>{resolve({...ready,review:saved});});expect(signal.aborted).toBe(true);expect(screen.queryByText('Decisão definitiva: Aprovado')).toBeNull();
});
it('human_gate_and_rejection_comment',async()=>{
  await mount(ready);expect(screen.getByRole('button',{name:'Aprovar master'}).hasAttribute('disabled')).toBe(true);fill();
  expect(screen.getByRole('button',{name:'Aprovar master'}).hasAttribute('disabled')).toBe(false);expect(screen.getByRole('button',{name:'Rejeitar master'}).hasAttribute('disabled')).toBe(true);
  fireEvent.change(screen.getByLabelText('Comentário'),{target:{value:'Bad text'}});fireEvent.click(screen.getByLabelText('Assisti e ouvi este master'));
  expect(screen.getByRole('button',{name:'Rejeitar master'}).hasAttribute('disabled')).toBe(false);vi.mocked(window.confirm).mockReturnValueOnce(false);fireEvent.click(screen.getByRole('button',{name:'Rejeitar master'}));expect(submitReview).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'Rejeitar master'}));await screen.findByText('Decisão definitiva: Rejeitado');expect(screen.getByText(/nova versão/)).toBeTruthy();
});
it('unknown QC absence only permits confirmed abandonment, never automatic resend',async()=>{
  await mount();vi.mocked(submitQc).mockRejectedValueOnce(new ApiError('command_unknown'));
  fireEvent.click(screen.getByRole('button',{name:'Executar QC'}));await screen.findByText(/Resultado do comando desconhecido/);
  fireEvent.click(screen.getByRole('button',{name:'Consultar QC e review'}));await screen.findByText(/Execução QC ainda não encontrada/);
  vi.mocked(window.confirm).mockReturnValueOnce(false);fireEvent.click(screen.getByRole('button',{name:'Abandonar envio QC desconhecido'}));
  expect(screen.getByRole('button',{name:'Executar QC'}).hasAttribute('disabled')).toBe(true);
  fireEvent.click(screen.getByRole('button',{name:'Abandonar envio QC desconhecido'}));expect(screen.getByRole('button',{name:'Executar QC'}).hasAttribute('disabled')).toBe(false);
  expect(submitQc).toHaveBeenCalledOnce();expect(startQcWorker).not.toHaveBeenCalled();
});
it('lost review response recovers exact original without a retry',async()=>{
  await mount(ready);fill();vi.mocked(submitReview).mockRejectedValueOnce(new ApiError('command_unknown'));
  fireEvent.click(screen.getByRole('button',{name:'Aprovar master'}));await screen.findByText(/Resultado do comando desconhecido/);
  const original=record(vi.mocked(submitReview).mock.calls[0][1]);vi.mocked(getReviewState).mockResolvedValue({...ready,review:original});
  fireEvent.click(screen.getByRole('button',{name:'Consultar QC e review'}));await screen.findByText('Decisão definitiva: Aprovado');
  expect(screen.queryByRole('button',{name:'Reenviar a mesma decisão'})).toBeNull();expect(submitReview).toHaveBeenCalledOnce();
});
it('blocked QC permits rejection but not approval',async()=>{
  await mount({...ready,reports:[{...report,status:'blocked'}]});fill();fireEvent.change(screen.getByLabelText('Comentário'),{target:{value:'Volume'}});
  expect(screen.getByRole('button',{name:'Aprovar master'}).hasAttribute('disabled')).toBe(true);
  expect(screen.getByRole('button',{name:'Rejeitar master'}).hasAttribute('disabled')).toBe(false);
});
it('polls only active QC serially and aborts late reads',async()=>{
  vi.useFakeTimers();vi.mocked(getReviewState).mockResolvedValue(queued);
  const view=render(<ReviewPanel target={target}/>);await act(async()=>{});
  let resolve!:(s:FinalReviewState)=>void;vi.mocked(getReviewState).mockImplementationOnce(()=>new Promise(r=>{resolve=r;}));
  await act(async()=>{await vi.advanceTimersByTimeAsync(2000);});expect(getReviewState).toHaveBeenCalledTimes(2);
  await act(async()=>{await vi.advanceTimersByTimeAsync(10000);});expect(getReviewState).toHaveBeenCalledTimes(2);
  await act(async()=>{resolve(ready);});await act(async()=>{await vi.advanceTimersByTimeAsync(10000);});expect(getReviewState).toHaveBeenCalledTimes(2);
  view.unmount();expect(vi.mocked(getReviewState).mock.calls[1][1].aborted).toBe(true);
});
