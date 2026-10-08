import {useEffect,useRef,useState} from 'react';
import {ApiError,readWorker,statusLabel} from './api';
import type {EditBatchPlan} from './editingApi';
import {getRender,listRenders,renderMediaUrl,startRenderWorker,submitRender} from './renderApi';
import type {RenderJobRequest,RenderJobView} from './renderApi';
type Props={campaignId:string;plan:EditBatchPlan|null};
const active=(v:RenderJobView)=>['queued','running','retry_scheduled'].includes(v.status);
const safe=(e:unknown)=>e instanceof ApiError?e:new ApiError('connection_lost');
export function RenderPanel(props:Props){
  return <RenderFlow key={`${props.campaignId}/${props.plan?.videoId}/${props.plan?.planHash}`} {...props}/>;
}
function RenderFlow({campaignId,plan}:Props){
  const [runs,setRuns]=useState<RenderJobView[]>([]),[job,setJob]=useState<RenderJobView|null>(null);
  const [pending,setPending]=useState<RenderJobRequest|null>(null),[sending,setSending]=useState(false),[listing,setListing]=useState(false);
  const [notice,setNotice]=useState<string|null>(null),[workerUnknown,setWorkerUnknown]=useState(false),[monitor,setMonitor]=useState(true),[readId,setReadId]=useState(0);
  const alive=useRef(true),lock=useRef(false),listController=useRef<AbortController|null>(null),listVersion=useRef(0);
  const current=(v:RenderJobRequest)=>!!plan&&v.campaignId===campaignId&&v.videoId===plan.videoId&&v.planHash===plan.planHash;
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;listController.current?.abort();};},[]);
  const inspect=async()=>{
    if(!plan)return;const token=++listVersion.current;listController.current?.abort();
    const controller=new AbortController();listController.current=controller;setListing(true);
    try{const result=await listRenders(campaignId,plan.videoId,plan.planHash,controller.signal);
      if(!alive.current||token!==listVersion.current)return;setRuns(result.items);
      if(pending&&!job){const found=result.items.find(v=>current(v)&&v.executionId===pending.executionId);
        if(found){setJob(found);setPending(null);setMonitor(true);setNotice('Execução enviada encontrada pelo executionId.');}
        else setNotice('Execução enviada ainda não encontrada. Nenhum POST será reenviado.');}}
    catch(e){if(alive.current&&token===listVersion.current)setNotice(safe(e).message);}
    finally{if(alive.current&&token===listVersion.current)setListing(false);}
  };
  useEffect(()=>{void inspect();},[]);
  useEffect(()=>{
    if(!job||!monitor||!active(job))return;
    const frozen=job,controller=new AbortController();let stopped=false,timer:ReturnType<typeof setTimeout>|undefined;
    const query=async()=>{
      try{const result=await getRender(campaignId,frozen.jobId,controller.signal);
        if(stopped)return;
        if(!current(result)||result.executionId!==frozen.executionId)throw new ApiError('invalid_response');
        setJob(result);if(active(result))timer=setTimeout(()=>{void query();},2000);
      }catch(e){if(!stopped)setNotice(safe(e).message);}
    };
    void query();return()=>{stopped=true;clearTimeout(timer);controller.abort();};
  },[job?.jobId,monitor,readId]);
  const reconcileWorker=async()=>{
    try{await readWorker(campaignId,new AbortController().signal);if(alive.current){setWorkerUnknown(false);setNotice('Estado do worker consultado. Inicie somente se o Job continuar na fila.');}}
    catch(e){if(alive.current){setWorkerUnknown(true);setNotice(safe(e).message);}}
  };
  const start=async()=>{
    if(workerUnknown)return;
    try{await startRenderWorker(campaignId);if(alive.current)setNotice('Worker de render iniciado. Isto não confirma conclusão.');}
    catch(e){if(!alive.current)return;const error=safe(e);setNotice(error.message);
      if(error.code==='command_unknown'){setWorkerUnknown(true);await reconcileWorker();}}
  };
  const startPending=async()=>{if(lock.current||workerUnknown)return;lock.current=true;setSending(true);
    try{await start();}finally{if(alive.current){lock.current=false;setSending(false);}}};
  const submit=async()=>{
    if(!plan||plan.campaignId!==campaignId||lock.current||pending||(job&&active(job)))return;
    lock.current=true;setSending(true);setNotice(null);setWorkerUnknown(false);
    const request:RenderJobRequest={schemaVersion:'1.0',campaignId,videoId:plan.videoId,planHash:plan.planHash,executionId:crypto.randomUUID()};
    setPending(request);setJob(null);
    try{const accepted=await submitRender(request);if(!alive.current)return;
      setPending(null);setJob({...accepted,status:'queued',result:null,renderStatus:null,errorCode:null});setMonitor(true);
      await start();
    }catch(e){if(alive.current){const error=safe(e);setNotice(error.message);
      if(!['command_unknown','invalid_response','connection_lost'].includes(error.code))setPending(null);}}
    finally{if(alive.current){lock.current=false;setSending(false);}}
  };
  const choose=(id:string)=>{if(pending||sending)return;const chosen=runs.find(v=>v.jobId===id);setJob(chosen??null);setMonitor(true);setNotice(null);setWorkerUnknown(false);};
  return <section aria-label="Render final">
    <h3>Render final</h3>
    {plan?<p>Plano salvo para render: {plan.videoId} · {plan.planHash}</p>:<p>Salve ou consulte um plano confirmado para renderizar.</p>}
    <p>Usa somente o plano salvo, não o rascunho ou o preview. Processa sequencialmente as variantes; não gera voz, imagem ou HeyGen.</p>
    <p>Inicia editing_render para os Jobs deste tipo na campanha. Renderizado não significa aprovado ou entregue.</p>
    <button disabled={!plan||plan.campaignId!==campaignId||sending||!!pending||!!job&&active(job)} onClick={()=>{void submit();}}>{job&&!active(job)?'Nova execução':'Renderizar'}</button>
    <p>Podem ser reaproveitados masters válidos já existentes; nova execução não garante nova codificação.</p>
    <button disabled={!plan||listing} onClick={()=>{void inspect();}}>Consultar execuções</button>
    <label>Execução de render<select aria-label="Execução de render" value={job?.jobId??''} disabled={sending||!!pending} onChange={e=>choose(e.target.value)}>
      <option value="">Selecione</option>{runs.map(v=><option key={v.jobId} value={v.jobId}>{v.jobId} · {statusLabel(v.status)}</option>)}
    </select></label>
    {pending&&<p>Execução enviada: {pending.executionId}. Consulte antes de decidir outra ação; nenhum reenvio automático.</p>}
    {job&&<><p>Job de render: {job.jobId} · {statusLabel(job.status)}</p>
      {job.status==='queued'&&<button disabled={sending||workerUnknown} onClick={()=>{void startPending();}}>Iniciar render pendente</button>}
      {workerUnknown&&<button disabled={sending} onClick={()=>{void reconcileWorker();}}>Consultar worker de render</button>}
      <button disabled={sending} onClick={()=>{setMonitor(true);setReadId(n=>n+1);}}>Consultar render</button>
      {monitor&&active(job)&&<button onClick={()=>{setMonitor(false);setNotice('Acompanhamento abandonado. Não cancela o Job no backend.');}}>Abandonar acompanhamento do render</button>}
      {job.renderStatus&&<p role="status">{job.renderStatus==='succeeded'?'Render concluído':job.renderStatus==='partial_failure'?'Falha parcial':'Todas as variantes falharam'}</p>}
      {job.errorCode&&<p role="status">{new ApiError(job.errorCode).message}</p>}
      {job.result?.outputs.map(o=><article key={o.key}><h4>{o.key} · {o.status}</h4>
        {o.status==='failed'?<p>Variante falhou. Revise o plano; nenhuma nova tentativa automática.</p>:<p>
          <a href={renderMediaUrl(campaignId,job.jobId,o.outputVariantId)} target="_blank" rel="noreferrer">Abrir MP4</a> · {' '}
          <a href={renderMediaUrl(campaignId,job.jobId,o.outputVariantId,true)}>Baixar MP4</a></p>}
      </article>)}
    </>}
    {notice&&<p role="status">{notice}</p>}
  </section>;
}
