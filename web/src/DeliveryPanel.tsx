import {useEffect,useRef,useState} from 'react';
import {ApiError} from './api';
import {getDelivery,submitDelivery} from './deliveryApi';
import type {DeliveryRecord} from './deliveryApi';
import type {HumanReviewRecord,ReviewTarget} from './reviewApi';
export function DeliveryPanel({target,review}:{target:ReviewTarget;review:HumanReviewRecord}){
  return review.decision==='approved'?<DeliveryFlow key={JSON.stringify([target,review.reviewKey])} target={target} review={review}/>:null;
}
function DeliveryFlow({target,review}:{target:ReviewTarget;review:HumanReviewRecord}){
  const [record,setRecord]=useState<DeliveryRecord|null>(null),[ready,setReady]=useState(false);
  const [busy,setBusy]=useState(false),[reading,setReading]=useState(false),[notice,setNotice]=useState<string|null>(null);
  const alive=useRef(true),lock=useRef(false),version=useRef(0),controller=useRef<AbortController|null>(null);
  const safe=(e:unknown)=>e instanceof ApiError?e:new ApiError('connection_lost');
  const inspect=async()=>{
    if(lock.current)return;const token=++version.current;controller.current?.abort();
    const abort=new AbortController();controller.current=abort;setReading(true);setReady(false);setRecord(null);setNotice(null);
    try{const fresh=await getDelivery(target,review,abort.signal);if(!alive.current||token!==version.current)return;
      setRecord(fresh.record);setReady(true);setNotice(fresh.record?null:'Entrega não encontrada. Nenhum envio automático.');}
    catch(e){if(alive.current&&token===version.current){setRecord(null);setReady(false);setNotice(safe(e).message);}}
    finally{if(alive.current&&token===version.current)setReading(false);}
  };
  useEffect(()=>{alive.current=true;void inspect();return()=>{alive.current=false;++version.current;controller.current?.abort();};},[]);
  const send=async()=>{
    if(lock.current||!ready||reading||record)return;lock.current=true;setBusy(true);setReady(false);setNotice(null);
    ++version.current;controller.current?.abort();setReading(false);
    try{const saved=await submitDelivery(target,review);if(alive.current){setRecord(saved);setReady(true);}}
    catch(e){if(alive.current){setRecord(null);setReady(false);setNotice(safe(e).message);}}
    finally{if(alive.current){lock.current=false;setBusy(false);}}
  };
  return <section aria-label={`Entrega local ${target.outputVariantId}`}>
    <h5>Entrega do master aprovado</h5>
    <p>Cópia local verificada; não é upload para a nuvem. Sem nova renderização.</p>
    <p>Destino padrão: work/deliveries. Configure AURALY_DELIVERY_ROOT antes de iniciar a API para usar outra pasta.</p>
    <button disabled={!ready||busy||reading||!!record} onClick={()=>{void send();}}>Entregar master aprovado</button>
    <button disabled={busy||reading} onClick={()=>{void inspect();}}>Consultar entrega</button>
    {record&&<><p>Entrega local verificada</p><p>{record.path}</p><p>SHA-256: {record.masterSha256}</p><p>{record.deliveredAt} (UTC)</p><p>Recibo original, somente leitura.</p></>}
    {notice&&<p role="status">{notice}</p>}
  </section>;
}
