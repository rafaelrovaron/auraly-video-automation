import {useEffect,useId,useRef,useState} from 'react';
import {ApiError} from './api';
import type {Items} from './api';
import type {HeyGenRenderView} from './heygenApi';
import type {RemoteState} from './usePolling';
import {getProfile,listProfiles} from './profileApi';
import type {ProfileView} from './profileApi';
import {EditOverridesForm,newOverrideDraft,readOverrides} from './EditOverridesForm';
import type {OverrideDraft,OverrideHints} from './EditOverridesForm';
import {editBatchRequest,getEditOperation,getEditPlan,listEditPlans,planMatchesRequest,samePlan,submitEditPlan} from './editingApi';
import type {EditBatchPlan,EditBatchRequest,EditOverrides,PlanSummary} from './editingApi';
import {useUnsavedChanges} from './useUnsavedChanges';

type Props={campaignId:string;renders:RemoteState<Items<HeyGenRenderView>>};
type Variant={row:number;key:string;label:string;draft:OverrideDraft};
type Validated={request:EditBatchRequest;plan:EditBatchPlan;render:HeyGenRenderView};
type Submission={request:EditBatchRequest;render:HeyGenRenderView;plan:EditBatchPlan|null;persist:boolean;jobId?:string};
const initialVariant=():Variant=>({row:1,key:'a',label:'A',draft:newOverrideDraft()});
function PlanDetails({plan}:{plan:EditBatchPlan}){
  return <div><p>{plan.outputCount} saídas planejadas · limite {plan.maxOutputs} · {plan.planHash}</p>
    <p>Copy vinculada: {plan.copyRef.id} · v{plan.copyRef.version}. Legendas não substituem a copy aprovada.</p>
    {plan.outputs.map(output=><article key={output.key}><h4>{output.key} · {output.label}</h4><p>{output.manifest.headline.text}</p>
      <p>Legendas: {output.captionState==='timing_missing'?'Timing pendente':output.captionState==='disabled'?'Desabilitadas':'Timing fornecido; qualidade não aprovada automaticamente'}</p>
      <p>Saída planejada: {output.filename}. Nenhum MP4 final foi renderizado.</p>
      <details><summary>Configuração resolvida e origem · {output.key}</summary><pre>{JSON.stringify(output.manifest,null,2)}</pre></details>
    </article>)}
  </div>;
}
export function EditingPanel(props:Props){return <EditorialFlow key={props.campaignId} {...props}/>;}
function EditorialFlow({campaignId,renders}:Props){
  const [profiles,setProfiles]=useState<ProfileView[]>([]),[plans,setPlans]=useState<PlanSummary[]>([]);
  const [listError,setListError]=useState<string|null>(null),[listsLoading,setListsLoading]=useState(true);
  const [renderId,setRenderId]=useState(''),[profileKey,setProfileKey]=useState(''),[profile,setProfile]=useState<ProfileView|null>(null);
  const [profileLoading,setProfileLoading]=useState(false),[videoId,setVideoId]=useState(''),[headline,setHeadline]=useState(''),[maxOutputs,setMaxOutputs]=useState('3');
  const [campaign,setCampaign]=useState(newOverrideDraft),[video,setVideo]=useState(newOverrideDraft),[variants,setVariants]=useState<Variant[]>([initialVariant()]);
  const [musicAccepted,setMusicAccepted]=useState(false),[timingPath,setTimingPath]=useState(''),[timingHash,setTimingHash]=useState('');
  const [dirty,setDirty]=useState(false),[validated,setValidated]=useState<Validated|null>(null),[submission,setSubmission]=useState<Submission|null>(null);
  const [unknown,setUnknown]=useState(false),[notice,setNotice]=useState<string|null>(null),[error,setError]=useState<string|null>(null),[saved,setSaved]=useState<EditBatchPlan|null>(null);
  const [view,setView]=useState<EditBatchPlan|null>(null),[viewLoading,setViewLoading]=useState(false);
  const alive=useRef(true),generation=useRef(0),listGeneration=useRef(0),profileGeneration=useRef(0),viewGeneration=useRef(0),nextRow=useRef(2);
  const lock=useRef(false),sent=useRef<Submission|null>(null),timer=useRef<ReturnType<typeof setTimeout>|undefined>(undefined),queryController=useRef<AbortController|null>(null);
  const form=useRef<HTMLFormElement>(null),errorId=useId();const clearUnsaved=useUnsavedChanges(dirty);
  const selectedRender=renders.data?.items.find(r=>r.renderId===renderId&&r.status==='ready'&&r.source!==null)??null;
  const latestRender=useRef(selectedRender);latestRender.current=selectedRender;
  const busy=submission!==null;
  const refresh=async()=>{
    const token=++listGeneration.current;setListsLoading(true);setListError(null);
    try{const [p,e]=await Promise.all([listProfiles(new AbortController().signal),listEditPlans(campaignId,new AbortController().signal)]);
      if(alive.current&&token===listGeneration.current){setProfiles(p);setPlans(e);}}
    catch(e){if(alive.current&&token===listGeneration.current)setListError((e instanceof ApiError?e:new ApiError('connection_lost')).message);}
    finally{if(alive.current&&token===listGeneration.current)setListsLoading(false);}
  };
  useEffect(()=>{alive.current=true;void refresh();return()=>{alive.current=false;generation.current++;clearTimeout(timer.current);queryController.current?.abort();};},[]);
  const clearErrors=()=>{setError(null);form.current?.querySelectorAll('[aria-invalid]').forEach(el=>{el.removeAttribute('aria-invalid');el.removeAttribute('aria-describedby');});};
  const change=()=>{setDirty(true);setValidated(null);setSaved(null);setNotice(null);clearErrors();};
  const discard=()=>!dirty||window.confirm('Há um rascunho não salvo. Descartar alterações?');
  const resetDraft=()=>{setHeadline('');setMaxOutputs('3');setCampaign(newOverrideDraft());setVideo(newOverrideDraft());setVariants([initialVariant()]);nextRow.current=2;
    setMusicAccepted(false);setTimingPath('');setTimingHash('');setValidated(null);setSaved(null);setDirty(false);clearUnsaved();clearErrors();setNotice(null);};
  const chooseRender=(value:string)=>{if(busy||!discard())return;generation.current++;resetDraft();setRenderId(value);setVideoId(value);};
  const chooseProfile=async(value:string)=>{
    if(busy||!discard())return;resetDraft();setProfileKey(value);setProfile(null);setProfileLoading(!!value);const token=++profileGeneration.current;
    const row=profiles.find(p=>`${p.profile.profileId}/${p.profile.version}`===value);if(!row){setProfileLoading(false);return;}
    try{const p=await getProfile(row.profile.profileId,row.profile.version,new AbortController().signal);
      if(alive.current&&token===profileGeneration.current){if(p.profileHash!==row.profileHash)throw new ApiError('invalid_response');setProfile(p);}}
    catch(e){if(alive.current&&token===profileGeneration.current)setError((e instanceof ApiError?e:new ApiError('connection_lost')).message);}
    finally{if(alive.current&&token===profileGeneration.current)setProfileLoading(false);}
  };
  const invalid=(name:string)=>{
    const input=Array.from(form.current?.elements??[]).find(el=>el instanceof HTMLElement&&el.getAttribute('name')===name) as HTMLElement|undefined;
    setError(`Revise o campo ${name}: valor, limite ou referência incompleta.`);
    if(input){input.setAttribute('aria-invalid','true');input.setAttribute('aria-describedby',errorId);
      let details=input.closest('details');while(details){details.open=true;details=details.parentElement?.closest('details')??null;}
      requestAnimationFrame(()=>input.focus());}
  };
  const request=():EditBatchRequest|null=>{
    clearErrors();if(!selectedRender){invalid('renderId');return null;}if(!profile){invalid('profile');return null;}
    const c=readOverrides(campaign),v=readOverrides(video);
    if(!c.overrides){invalid(`Campanha.${c.invalidField}`);return null;}if(!v.overrides){invalid(`Vídeo.${v.invalidField}`);return null;}
    const rows:EditBatchRequest['variants']=[];
    for(const variant of variants){const o=readOverrides(variant.draft);if(!o.overrides){invalid(`Variante ${variant.key}.${o.invalidField}`);return null;}
      rows.push({key:variant.key,label:variant.label.trim(),overrides:o.overrides});}
    const body:EditBatchRequest={schemaVersion:'1.0',campaignId,renderId,videoId,profileRef:{profileId:profile.profile.profileId,version:profile.profile.version,hash:profile.profileHash},
      headlineText:headline.trim(),campaign:c.overrides,video:v.overrides,musicAccepted,variants:rows,maxOutputs:maxOutputs.trim()?Number(maxOutputs):NaN,
      timingRef:timingPath||timingHash?{path:timingPath,sha256:timingHash}:null};
    if(!editBatchRequest(body)){
      const duplicate=rows.find((r,i)=>!r.key.trim()||rows.findIndex(other=>other.key===r.key)!==i);
      invalid(!headline.trim()?'headlineText':duplicate?`variant.${variants.find(r=>r.key===duplicate.key)!.row}.key`:
        timingPath||timingHash?'timingPath':!videoId.trim()?'videoId':'maxOutputs');return null;
    }return body;
  };
  const isCurrent=(s:Submission,token:number)=>alive.current&&generation.current===token&&sent.current===s;
  const release=()=>{clearTimeout(timer.current);sent.current=null;setSubmission(null);lock.current=false;setUnknown(false);};
  const markUnknown=(message='Resultado editorial desconhecido. Nenhum POST será reenviado.')=>{setUnknown(true);setNotice(message);lock.current=false;};
  const query=async(s:Submission,token:number)=>{
    if(!s.jobId||!isCurrent(s,token))return;
    queryController.current?.abort();const controller=new AbortController();queryController.current=controller;
    try{
      const operation=await getEditOperation(campaignId,s.jobId,controller.signal);if(!isCurrent(s,token))return;
      if(operation.status==='queued'||operation.status==='running'||operation.status==='retry_scheduled'){
        setNotice('Operação editorial na fila ou em execução. Inicie local_operations explicitamente nos controles de Worker.');setUnknown(false);
        clearTimeout(timer.current);timer.current=setTimeout(()=>{void query(s,token);},2000);return;
      }
      if(operation.status!=='completed'){setValidated(null);release();setNotice(`Operação editorial falhou ou foi bloqueada (${operation.status}). Corrija o rascunho e valide novamente.`);return;}
      const result=operation.result;
      if(!result||result.persist!==s.persist||!planMatchesRequest(result.plan,s.request,s.render)
        ||!latestRender.current||!planMatchesRequest(result.plan,s.request,latestRender.current))throw new ApiError('command_unknown');
      if(!s.persist){release();setValidated({request:s.request,render:s.render,plan:result.plan});setNotice('Plano validado. Nenhum arquivo de edição publicado.');return;}
      if(!s.plan||!samePlan(result.plan,s.plan))throw new ApiError('command_unknown');
      const confirmed=await getEditPlan(campaignId,s.request.videoId,result.plan.planHash,controller.signal);if(!isCurrent(s,token))return;
      if(!samePlan(confirmed,result.plan))throw new ApiError('command_unknown');
      release();setSaved(confirmed);setDirty(false);clearUnsaved();setNotice('Plano salvo e confirmado. Não é um vídeo renderizado.');void refresh();
    }catch{if(isCurrent(s,token))markUnknown();}
  };
  const submit=async(persist:boolean)=>{
    if(lock.current||busy||renders.error||profileLoading)return;
    const body=persist?validated?.request:request();if(!body||!selectedRender||(persist&&!validated))return;
    lock.current=true;const token=++generation.current;
    const frozen:Submission={request:structuredClone(body),render:structuredClone(selectedRender),plan:validated?.plan??null,persist};
    sent.current=frozen;setSubmission(frozen);setUnknown(false);setNotice(null);clearErrors();
    try{const accepted=await submitEditPlan(frozen.request,persist);if(!isCurrent(frozen,token))return;
      frozen.jobId=accepted.jobId;setSubmission({...frozen});setNotice('Operação editorial na fila. Inicie local_operations explicitamente nos controles de Worker.');void query(frozen,token);
    }catch(e){if(!isCurrent(frozen,token))return;
      if(e instanceof ApiError&&e.code!=='command_unknown'){setValidated(null);release();setError(e.message);}else markUnknown();}
  };
  const inspectSent=async()=>{
    const s=sent.current;if(!s)return;
    if(s.jobId){void query(s,generation.current);return;}
    if(!s.persist||!s.plan)return;
    const token=generation.current;
    try{const found=await getEditPlan(campaignId,s.request.videoId,s.plan.planHash,new AbortController().signal);
      if(isCurrent(s,token)&&samePlan(found,s.plan)){setView(found);setNotice('Resultado editorial desconhecido. Artefato encontrado; isto não confirma autoria do POST perdido.');}}
    catch{if(isCurrent(s,token))markUnknown('Resultado editorial desconhecido. Não foi possível confirmar o artefato; nenhum POST será reenviado.');}
  };
  const abandon=()=>{if(!window.confirm('Abandonar acompanhamento? A operação pode continuar no backend; isto não cancela o Job.'))return;
    generation.current++;clearTimeout(timer.current);queryController.current?.abort();release();setValidated(null);setNotice(null);setDirty(true);};
  const inspect=async(key:string)=>{
    const item=plans.find(p=>`${p.videoId}/${p.planHash}`===key);setView(null);const token=++viewGeneration.current;if(!item)return;setViewLoading(true);
    try{const p=await getEditPlan(campaignId,item.videoId,item.planHash,new AbortController().signal);if(alive.current&&token===viewGeneration.current)setView(p);}
    catch(e){if(alive.current&&token===viewGeneration.current)setError((e instanceof ApiError?e:new ApiError('connection_lost')).message);}
    finally{if(alive.current&&token===viewGeneration.current)setViewLoading(false);}
  };
  const hints:OverrideHints|null=profile?{...profile.profile.defaults,headline:{...profile.profile.defaults.headline,text:headline}}:null;
  const inherited=(parent:OverrideHints,draft:OverrideDraft):OverrideHints=>{
    const partial=readOverrides(draft).overrides;if(!partial)return parent;
    return Object.fromEntries(Object.entries(parent).map(([section,fields])=>[section,{...fields,...partial[section as keyof EditOverrides]}])) as OverrideHints;
  };
  const cHints=hints?inherited(hints,campaign):null,vHints=cHints?inherited(cHints,video):null;
  const overrideChange=(update:()=>void,field:string)=>{change();update();if(field==='music.asset')setMusicAccepted(false);};
  const canSave=!!validated&&!busy&&!renders.error&&!!selectedRender&&planMatchesRequest(validated.plan,validated.request,selectedRender);
  return <div>
    <button type="button" onClick={()=>{void refresh();}}>Atualizar edição</button> · <a href="#/profiles">Gerenciar profiles de edição</a>
    <p>Um MP4 por plano. Não gera voz, imagem ou HeyGen. Validação e publicação usam Jobs locais, sem render final.</p>
    <p>Rascunho somente em memória; reload pode perdê-lo. Inicie o worker local_operations manualmente quando houver Job na fila.</p>
    {listsLoading&&<p role="status">Carregando profiles e planos…</p>}{listError&&<p role="status">Profiles/planos indisponíveis: {listError}</p>}
    <form ref={form} noValidate onSubmit={e=>{e.preventDefault();void submit(false);}}>
      <fieldset disabled={busy}><legend>Fonte e configuração</legend>
        <label>MP4 HeyGen para edição<select aria-label="MP4 HeyGen para edição" name="renderId" value={renderId} onChange={e=>chooseRender(e.target.value)}>
          <option value="">Selecione</option>{renders.data?.items.filter(r=>r.status==='ready'&&r.source).map(r=><option key={r.renderId} value={r.renderId}>{r.renderId} · {r.sceneVariantId}</option>)}</select></label>
        {selectedRender?.source&&<p>MP4 (work root): {selectedRender.source.path} · {selectedRender.source.sha256} · {selectedRender.source.probe.durationSec}s</p>}
        <label>Profile de edição<select aria-label="Profile de edição" name="profile" value={profileKey} onChange={e=>{void chooseProfile(e.target.value);}}>
          <option value="">Selecione</option>{profiles.map(p=><option key={`${p.profile.profileId}/${p.profile.version}`} value={`${p.profile.profileId}/${p.profile.version}`}>{p.profile.profileId} · v{p.profile.version}</option>)}</select></label>
        {profileLoading&&<p role="status">Consultando profile…</p>}
        <label>ID do vídeo editorial<input name="videoId" value={videoId} onChange={e=>{change();setVideoId(e.target.value);}}/></label>
        <label>Headline base<input name="headlineText" value={headline} onChange={e=>{change();setHeadline(e.target.value);}}/></label>
        <label>Limite de saídas<input name="maxOutputs" type="number" value={maxOutputs} onChange={e=>{change();setMaxOutputs(e.target.value);}}/></label>
        <label>Aceito o uso da música nesta edição<input type="checkbox" checked={musicAccepted} onChange={e=>{change();setMusicAccepted(e.target.checked);}}/></label>
        <details><summary>Timing de legendas existente</summary>
          <label>Timing: caminho relativo ao project root<input name="timingPath" value={timingPath} onChange={e=>{change();setTimingPath(e.target.value);}}/></label>
          <label>Timing: SHA-256<input value={timingHash} onChange={e=>{change();setTimingHash(e.target.value);}}/></label>
        </details><p>Fontes/música/timing por path/hash explícitos. Sem timing, captions habilitadas ficam pendentes; não há editor de texto de legendas.</p>
      </fieldset>
      {hints&&cHints&&vHints&&<>
        <EditOverridesForm label="Campanha" draft={campaign} inherited={hints} disabled={busy} onChange={(d,f)=>overrideChange(()=>setCampaign(d),f)}/>
        <EditOverridesForm label="Vídeo" draft={video} inherited={cHints} disabled={busy} onChange={(d,f)=>overrideChange(()=>setVideo(d),f)}/>
        {variants.map(variant=><fieldset key={variant.row} disabled={busy}><legend>Variante {variant.key}</legend>
          <label>Key da variante {variant.key}<input name={`variant.${variant.row}.key`} value={variant.key} onChange={e=>{change();setVariants(old=>old.map(v=>v.row===variant.row?{...v,key:e.target.value}:v));}}/></label>
          <label>Nome da variante {variant.key}<input value={variant.label} onChange={e=>{change();setVariants(old=>old.map(v=>v.row===variant.row?{...v,label:e.target.value}:v));}}/></label>
          <EditOverridesForm label={`Variante ${variant.key}`} draft={variant.draft} inherited={vHints} disabled={busy} onChange={(d,f)=>overrideChange(()=>setVariants(old=>old.map(v=>v.row===variant.row?{...v,draft:d}:v)),f)}/>
          <button type="button" disabled={variants.length===1} onClick={()=>{change();setVariants(old=>old.filter(v=>v.row!==variant.row));if(variant.draft.music.asset.mode!=='inherit')setMusicAccepted(false);}}>Remover variante {variant.key}</button>
        </fieldset>)}
        <button type="button" disabled={busy} onClick={()=>{change();const row=nextRow.current++;let n=1;let key='b';while(variants.some(v=>v.key===key)){n++;key=n<26?String.fromCharCode(97+n):`v${row}`;}
          setVariants(old=>[...old,{row,key,label:key.toUpperCase(),draft:newOverrideDraft()}]);}}>Adicionar variante</button>
      </>}
      <p>{variants.length} variantes explícitas; sem multiplicação automática.</p>
      <button disabled={busy||profileLoading||!!renders.error} type="submit">Validar plano</button>
      <button disabled={!canSave} type="button" onClick={()=>{void submit(true);}}>Salvar plano</button>
    </form>
    {error&&<p role="alert" id={errorId}>{error}</p>}{notice&&<p role="status">{notice}</p>}
    {submission?.jobId&&<><p>Job editorial: {submission.jobId}</p><button onClick={()=>{void inspectSent();}}>Consultar operação editorial</button></>}
    {busy&&unknown&&<>{submission?.persist&&!submission.jobId&&<button onClick={()=>{void inspectSent();}}>Consultar plano enviado</button>}
      <p>Inspecione o Job conhecido ou os planos; uma lista não prova a autoria de uma resposta perdida. Não há reenvio automático.</p></>}
    {busy&&<button onClick={abandon}>Abandonar acompanhamento editorial</button>}
    {validated&&!busy&&!saved&&<PlanDetails plan={validated.plan}/>} {saved&&<PlanDetails plan={saved}/>}
    <label>Consultar plano salvo<select aria-label="Consultar plano salvo" onChange={e=>{void inspect(e.target.value);}} defaultValue=""><option value="">Selecione</option>
      {plans.map(p=><option key={`${p.videoId}/${p.planHash}`} value={`${p.videoId}/${p.planHash}`}>{p.videoId} · {p.outputCount} saídas · {p.planHash}</option>)}</select></label>
    {viewLoading&&<p role="status">Consultando plano salvo…</p>}{view&&<PlanDetails plan={view}/>}
  </div>;
}
