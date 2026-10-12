import {act,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {beforeEach,expect,it,vi} from 'vitest';
import {DeliveryPanel} from './DeliveryPanel';
import {getDelivery,submitDelivery} from './deliveryApi';
import {ApiError} from './api';
import type {ReviewTarget,HumanReviewRecord} from './reviewApi';
vi.mock('./deliveryApi',()=>({getDelivery:vi.fn(),submitDelivery:vi.fn()}));
const target:ReviewTarget={schemaVersion:'1.0',campaignId:'campaign-one',jobId:'22222222-2222-4222-8222-222222222222',videoId:'video-one',planHash:'a'.repeat(64),outputVariantId:'variant-a',renderKey:'b'.repeat(64)};
const {jobId:_,...context}=target;
const review:HumanReviewRecord={...context,qcKey:'c'.repeat(64),decision:'approved',reviewedBy:'Tester',comment:null,watchedAndListened:true,reviewedAt:'2026-10-12T10:00:00Z',reviewKey:'f'.repeat(64),masterPath:'masters/a.mp4',masterSha256:'d'.repeat(64),receiptSha256:'e'.repeat(64),sizeBytes:123,qcReportSha256:'a'.repeat(64),qcStatus:'passed'};
const request={...context,reviewKey:review.reviewKey};
const record={...request,deliveredAt:'2026-10-12T11:00:00Z',path:`${target.campaignId}/${target.outputVariantId}/${review.reviewKey}/master.mp4`,masterSha256:review.masterSha256,sizeBytes:review.sizeBytes,receiptSha256:review.receiptSha256,qcReportSha256:review.qcReportSha256,reviewSha256:'c'.repeat(64)};
const send=()=>screen.getByRole('button',{name:'Entregar master aprovado'}) as HTMLButtonElement;
const consult=()=>fireEvent.click(screen.getByRole('button',{name:'Consultar entrega'}));
beforeEach(()=>{vi.mocked(getDelivery).mockResolvedValue({request,record:null});vi.mocked(submitDelivery).mockResolvedValue(record);});
async function mount(){const view=render(<DeliveryPanel target={target} review={review}/>);await waitFor(()=>expect(send().disabled).toBe(false));return view;}
it('never sends automatically; explicit double click sends once; reload original read-only receipt',async()=>{
  const view=await mount();expect(submitDelivery).not.toHaveBeenCalled();expect(screen.getByText(/não é upload/)).toBeTruthy();
  fireEvent.click(send());fireEvent.click(send());await screen.findByText('Entrega local verificada');expect(submitDelivery).toHaveBeenCalledOnce();expect(send().disabled).toBe(true);
  expect(screen.getByText(record.path)).toBeTruthy();view.unmount();
  vi.mocked(getDelivery).mockResolvedValue({request,record});render(<DeliveryPanel target={target} review={review}/>);
  await screen.findByText('Entrega local verificada');expect(screen.getByText(/2026-10-12T11:00:00Z/)).toBeTruthy();expect(submitDelivery).toHaveBeenCalledOnce();
});
it('unknown POST requires successful GET; found receipt never resends',async()=>{
  await mount();vi.mocked(submitDelivery).mockRejectedValueOnce(new ApiError('command_unknown'));
  fireEvent.click(send());await screen.findByText(/Resultado do comando desconhecido/);expect(send().disabled).toBe(true);
  vi.mocked(getDelivery).mockRejectedValueOnce(new ApiError('connection_lost'));consult();await screen.findByText(/Sem conexão/);expect(send().disabled).toBe(true);
  vi.mocked(getDelivery).mockResolvedValue({request,record});consult();await screen.findByText('Entrega local verificada');expect(send().disabled).toBe(true);expect(submitDelivery).toHaveBeenCalledOnce();
});
it('successful absent GET allows only an explicit idempotent retry',async()=>{
  await mount();vi.mocked(submitDelivery).mockRejectedValueOnce(new ApiError('command_unknown'));
  fireEvent.click(send());await screen.findByText(/Resultado do comando desconhecido/);consult();
  await waitFor(()=>expect(send().disabled).toBe(false));expect(submitDelivery).toHaveBeenCalledOnce();
  fireEvent.click(send());await screen.findByText('Entrega local verificada');expect(submitDelivery).toHaveBeenCalledTimes(2);
});
it('destination corruption clears receipt and blocks delivery',async()=>{
  vi.mocked(getDelivery).mockResolvedValue({request,record});render(<DeliveryPanel target={target} review={review}/>);
  await screen.findByText('Entrega local verificada');vi.mocked(getDelivery).mockRejectedValueOnce(new ApiError('artifact_invalid',409));
  consult();await screen.findByText(/Artefato armazenado inválido/);expect(screen.queryByText('Entrega local verificada')).toBeNull();expect(send().disabled).toBe(true);expect(submitDelivery).not.toHaveBeenCalled();
});
it('changed target and unmount abort reads and ignore late responses',async()=>{
  let resolve!:(v:{request:typeof request;record:typeof record})=>void;
  vi.mocked(getDelivery).mockImplementationOnce(()=>new Promise(r=>{resolve=r;}));
  const view=render(<DeliveryPanel target={target} review={review}/>);
  const oldSignal=vi.mocked(getDelivery).mock.calls[0][2];
  view.rerender(<DeliveryPanel target={{...target,outputVariantId:'variant-b'}} review={{...review,outputVariantId:'variant-b'}}/>);
  await waitFor(()=>expect(send().disabled).toBe(false));expect(oldSignal.aborted).toBe(true);
  await act(async()=>resolve({request,record}));expect(screen.queryByText('Entrega local verificada')).toBeNull();
  view.unmount();expect(vi.mocked(getDelivery).mock.calls[1][2].aborted).toBe(true);
});
it('rejected review has no delivery controls',()=>{render(<DeliveryPanel target={target} review={{...review,decision:'rejected'}}/>);expect(screen.queryByRole('button',{name:'Entregar master aprovado'})).toBeNull();expect(getDelivery).not.toHaveBeenCalled();});
