import {useEffect,useRef,useState} from 'react';
import {ApiError,readWorker,statusLabel} from './api';
import {getReviewState,startQcWorker,submitQc,submitReview} from './reviewApi';
import type {EditQcOperation,FinalReviewState,HumanReviewRequest,ReviewTarget} from './reviewApi';
import {renderMediaUrl} from './renderApi';
import {DeliveryPanel} from './DeliveryPanel';
const active=(status:string)=>['queued','running','retry_scheduled'].includes(status);
const safe=(e:unknown)=>e instanceof ApiError?e:new ApiError('connection_lost');
const uncertain=(e:ApiError)=>['command_unknown','invalid_response','connection_lost','operation_conflict'].includes(e.code);
const findings:Record<string,string>={audio_silent:'Áudio silencioso.',audio_loudness_unmeasurable:'Volume não mensurável.',audio_low_loudness:'Volume baixo.',audio_peak_risk:'Risco de clipping.',artifact_invalid:'Artefato inválido.',runtime_unavailable:'Runtime indisponível.',audio_measurement_failed:'Falha na medição de áudio.'};
export function ReviewPanel({target}:{target:ReviewTarget}){return <ReviewFlow key={JSON.stringify(target)} target={target}/>;}
function ReviewFlow({target}:{target:ReviewTarget}){
  const [state,setState]=useState<FinalReviewState|null>(null),[selected,setSelected]=useState(''),[notice,setNotice]=useState<string|null>(null);
  const [busy,setBusy]=useState(false),[reading,setReading]=useState(false),[unknownQc,setUnknownQc]=useState<EditQcOperation|null>(null),[absentQc,setAbsentQc]=useState(false);
  const [pendingReview,setPendingReview]=useState<HumanReviewRequest|null>(null),[retryReview,setRetryReview]=useState(false),[workerUnknown,setWorkerUnknown]=useState(false);
  const [name,setName]=useState(''),[comment,setComment]=useState(''),[watched,setWatched]=useState(false);
  const alive=useRef(true),lock=useRef(false),controller=useRef<AbortController|null>(null),version=useRef(0);
  const reportSet=useRef('');
  const timer=useRef<ReturnType<typeof setTimeout>|undefined>(undefined),inspectRef=useRef<(recoverQc?:boolean)=>Promise<void>>(async()=>{});
  const qcPending=state?.runs.some(run=>active(run.status))??false;
  const report=state?.reports.find(item=>item.qcKey===selected);
  const blocked=!state||busy||reading||qcPending||!!unknownQc||!!pendingReview||!!state.review;
  const inspect=async(recoverQc=true)=>{
    const token=++version.current;controller.current?.abort();clearTimeout(timer.current);
    const abort=new AbortController();controller.current=abort;setReading(true);setAbsentQc(false);setRetryReview(false);
    try{const fresh=await getReviewState(target,abort.signal);if(!alive.current||token!==version.current)return;
      const keys=fresh.reports.map(r=>r.qcKey).sort().join(','),sameReports=keys===reportSet.current;reportSet.current=keys;
      setState(fresh);setSelected(previous=>fresh.review?.qcKey??(fresh.reports.length===1?fresh.reports[0].qcKey:sameReports&&fresh.reports.some(r=>r.qcKey===previous)?previous:''));
      if(unknownQc&&recoverQc){if(fresh.runs.some(run=>run.request.executionId===unknownQc.executionId)){setUnknownQc(null);setNotice('Execução QC encontrada. Nenhum reenvio.');}
        else{setAbsentQc(true);setNotice('Execução QC ainda não encontrada. Nenhum reenvio automático.');}}
      if(pendingReview){if(fresh.review){setPendingReview(null);setRetryReview(false);setNotice('Decisão definitiva recuperada do servidor.');}else setRetryReview(true);}
      if(fresh.runs.some(run=>active(run.status)))timer.current=setTimeout(()=>{void inspectRef.current();},2000);
    }catch(e){if(alive.current&&token===version.current){setState(null);setSelected('');setWatched(false);setNotice(safe(e).message);}}
    finally{if(alive.current&&token===version.current)setReading(false);}
  };
  inspectRef.current=inspect;
  useEffect(()=>{alive.current=true;void inspectRef.current();return()=>{alive.current=false;++version.current;controller.current?.abort();clearTimeout(timer.current);};},[]);
  const start=async()=>{
    if(workerUnknown)return;
    try{await startQcWorker(target.campaignId);if(alive.current)setNotice('Worker local iniciado; isto não confirma QC aprovado.');}
    catch(e){if(alive.current){const error=safe(e);setNotice(error.message);if(uncertain(error)&&error.code!=='operation_conflict')setWorkerUnknown(true);}}
  };
  const startPending=async()=>{if(lock.current||workerUnknown||!state)return;lock.current=true;setBusy(true);try{await start();}finally{if(alive.current){lock.current=false;setBusy(false);}}};
  const consultWorker=async()=>{if(lock.current)return;lock.current=true;setBusy(true);
    try{await readWorker(target.campaignId,new AbortController().signal);if(alive.current){setWorkerUnknown(false);setNotice('Worker consultado. Se ocupado, espere antes de iniciar.');}}
    catch(e){if(alive.current)setNotice(safe(e).message);}finally{if(alive.current){lock.current=false;setBusy(false);}}};
  const qc=async()=>{
    if(blocked||lock.current)return;lock.current=true;setBusy(true);setNotice(null);
    const request:EditQcOperation={...target,operation:'edit_qc',executionId:crypto.randomUUID()};setUnknownQc(request);setAbsentQc(false);
    try{await submitQc(request);if(!alive.current)return;setUnknownQc(null);await start();await inspectRef.current(false);}
    catch(e){if(alive.current){const error=safe(e);setNotice(error.message);if(!uncertain(error))setUnknownQc(null);
      if(error.code==='artifact_invalid'){++version.current;controller.current?.abort();clearTimeout(timer.current);setReading(false);setState(null);setSelected('');setWatched(false);reportSet.current='';}}}
    finally{if(alive.current){lock.current=false;setBusy(false);}}
  };
  const sendReview=async(request:HumanReviewRequest)=>{
    if(lock.current)return;lock.current=true;setBusy(true);setPendingReview(request);setRetryReview(false);setNotice(null);
    // Invalidate pending reads before changing authority; no stale GET can undo a save.
    ++version.current;controller.current?.abort();clearTimeout(timer.current);setReading(false);
    try{const saved=await submitReview(target,request);if(!alive.current)return;setState(previous=>previous?{...previous,review:saved}:null);setPendingReview(null);}
    catch(e){if(alive.current){const error=safe(e);setNotice(error.code==='operation_conflict'?'Consulte a decisão definitiva salva por outro cliente.':error.message);
      if(!uncertain(error)){setPendingReview(null);setState(null);setSelected('');setWatched(false);}}}
    finally{if(alive.current){lock.current=false;setBusy(false);}}
  };
  const decide=(decision:'approved'|'rejected')=>{
    if(blocked||!report||!name.trim()||(decision==='approved'?(report.status!=='passed'||!watched):!comment.trim()))return;
    if(!window.confirm('Esta decisão é definitiva para esta versão. Confirmar?'))return;
    const {jobId:_,...context}=target;
    void sendReview({...context,qcKey:report.qcKey,decision,reviewedBy:name.trim(),comment:comment.trim()||null,watchedAndListened:watched});
  };
  const abandon=()=>{if(busy||reading||!absentQc||!unknownQc||!window.confirm('Abandonar envio desconhecido? Não cancela nenhum Job e uma nova execução pode duplicar trabalho.'))return;
    setUnknownQc(null);setAbsentQc(false);setNotice('Envio desconhecido abandonado; não cancela o Job.');};
  return <section aria-label={`QC e review ${target.outputVariantId}`}>
    <h5>QC e review humano</h5><p>QC técnico aprovado não significa aprovação humana.</p>
    <p>O worker local_operations pode processar outras operações locais pendentes desta campanha.</p>
    <video aria-label="Master final" controls preload="metadata" style={{width:'100%',maxHeight:480,background:'#111'}} src={renderMediaUrl(target.campaignId,target.jobId,target.outputVariantId)}/>
    <button disabled={blocked} onClick={()=>{void qc();}}>Executar QC</button>
    <button disabled={busy||reading} onClick={()=>{void inspectRef.current();}}>Consultar QC e review</button>
    {unknownQc&&<><p>Envio QC desconhecido: {unknownQc.executionId}. Consulte antes de nova execução.</p><button disabled={busy||reading||!absentQc} onClick={abandon}>Abandonar envio QC desconhecido</button></>}
    {state?.runs.map(run=><p key={run.jobId}>Execução QC {run.request.executionId}: {run.status==='queued'?'QC na fila':statusLabel(run.status)}
      {run.result?.result.outputs[0].status==='error'?' · QC não concluído; repare os inputs/runtime antes de nova execução.':''}
      {run.errorCode?' · '+new ApiError(run.errorCode).message:''}</p>)}
    {qcPending&&<button disabled={busy||reading||workerUnknown} onClick={()=>{void startPending();}}>Iniciar QC pendente</button>}
    {(qcPending||workerUnknown)&&<button disabled={busy||reading} onClick={()=>{void consultWorker();}}>Consultar worker QC</button>}
    {!!state?.reports.length&&<label>Relatório QC<select aria-label="Relatório QC" value={selected} disabled={busy||!!pendingReview||!!state.review} onChange={e=>{setSelected(e.target.value);setWatched(false);}}>
      <option value="">Selecione um QC</option>{state.reports.map(r=><option key={r.qcKey} value={r.qcKey}>{r.qcKey} · {r.status} · {r.policy.version} · {r.runtime.fingerprint}</option>)}
    </select></label>}
    {report&&<><p>QC técnico: {report.status==='passed'?'Aprovado':'Bloqueado'}</p>
      {report.checks.map(check=><div key={check.name}><p>{check.name}: {check.status}</p>{check.findings.map((f,index)=><p key={index}>{Object.hasOwn(findings,f.code)?findings[f.code]:'Verificação bloqueada; confira os inputs e o plano salvo.'}</p>)}</div>)}
      {report.audio&&<p>Volume: {report.audio.integratedLoudnessLufs??'não mensurável'} LUFS · Pico: {report.audio.truePeakDbtp??'não mensurável'} dBTP</p>}
      <p>Texto medido no layout do manifest, não nos frames gravados.</p></>}
    {state?.review?<><p>Decisão definitiva: {statusLabel(state.review.decision)}</p><p>Revisor: {state.review.reviewedBy} · {state.review.reviewedAt} (UTC)</p><p>Comentário: {state.review.comment??'—'}</p>
      {state.review.decision==='rejected'&&<p>Edite e salve outro plano para renderizar e revisar uma nova versão.</p>}<p>Somente leitura; sem edição ou revogação.</p>
      {state.review.decision==='approved'&&<DeliveryPanel target={target} review={state.review}/>}</>:<>
      <label>Nome do revisor<input aria-label="Nome do revisor" value={name} disabled={busy||!!pendingReview} onChange={e=>setName(e.target.value)}/></label>
      <label>Comentário<textarea aria-label="Comentário" value={comment} disabled={busy||!!pendingReview} onChange={e=>setComment(e.target.value)}/></label>
      <label><input type="checkbox" checked={watched} disabled={busy||!!pendingReview} onChange={e=>setWatched(e.target.checked)}/>Assisti e ouvi este master</label>
      <p>A decisão é definitiva para esta versão.</p>
      <button disabled={blocked||!report||report.status!=='passed'||!name.trim()||!watched} onClick={()=>decide('approved')}>Aprovar master</button>
      <button disabled={blocked||!report||!name.trim()||!comment.trim()} onClick={()=>decide('rejected')}>Rejeitar master</button>
      {pendingReview&&<p>Envio de decisão pendente de confirmação. Consulte o servidor; não será reenviado automaticamente.</p>}
      {pendingReview&&retryReview&&<button disabled={busy||reading||!state||qcPending} onClick={()=>{void sendReview(pendingReview);}}>Reenviar a mesma decisão</button>}
    </>}
    {notice&&<p role="status">{notice}</p>}
  </section>;
}
