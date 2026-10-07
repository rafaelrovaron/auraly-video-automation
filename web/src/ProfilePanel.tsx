import { useEffect, useRef, useState } from 'react';
import { ApiError } from './api';
import { ProfileForm } from './ProfileForm';
import { getProfile, listProfiles, newProfile, publishProfile, sameProfileContent, type EditProfile, type ProfileView } from './profileApi';
import { useUnsavedChanges } from './useUnsavedChanges';

type Draft={initial:EditProfile; creating:boolean; baseVersion?:number; key:number};
type Submission={profile:EditProfile; baseVersion?:number; state:'unknown'|'absent'|'conflict'};
const message=(e:unknown)=>e instanceof ApiError?`${e.message} (${e.code})`:'A operação local falhou com segurança.';
export function ProfilePanel() {
  const [items,setItems]=useState<ProfileView[]>([]),[listLoading,setListLoading]=useState(true);
  const [listError,setListError]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [selected,setSelected]=useState(''),[view,setView]=useState<ProfileView|null>(null);
  const [draft,setDraft]=useState<Draft|null>(null),[submission,setSubmission]=useState<Submission|null>(null);
  const [dirty,setDirty]=useState(false),[busy,setBusy]=useState(false),[loading,setLoading]=useState(false);
  const clearUnsaved=useUnsavedChanges(dirty);
  const listRequest=useRef<AbortController|null>(null),detailRequest=useRef<AbortController|null>(null);
  const alive=useRef(true),locked=useRef(false),generation=useRef(0),draftKey=useRef(0);
  async function refresh() {
    listRequest.current?.abort(); const c=new AbortController();listRequest.current=c;
    setListLoading(true);setListError('');
    try { const values=await listProfiles(c.signal);if(alive.current&&!c.signal.aborted)setItems(values); }
    catch(e){if(alive.current&&!c.signal.aborted)setListError(message(e));}
    finally{if(alive.current&&!c.signal.aborted)setListLoading(false);}
  }
  useEffect(()=>{
    alive.current=true;void refresh();
    return()=>{alive.current=false;generation.current++;listRequest.current?.abort();detailRequest.current?.abort();};
  },[]);
  function discard():boolean {
    if(locked.current)return false;
    if(dirty&&!window.confirm('Há um rascunho não salvo ou publicação incerta. Descartar e continuar?'))return false;
    clearUnsaved();setDirty(false);return true;
  }
  function reset() {
    detailRequest.current?.abort();generation.current++;setLoading(false);setError('');setNotice('');setSubmission(null);
  }
  async function select(key:string) {
    if(!discard())return;reset();setSelected(key);setDraft(null);setView(null);
    if(!key)return;
    const [id,version]=key.split('/'),token=generation.current,c=new AbortController();detailRequest.current=c;setLoading(true);
    try{const v=await getProfile(id,Number(version),c.signal);if(alive.current&&!c.signal.aborted&&token===generation.current)setView(v);}
    catch(e){if(alive.current&&!c.signal.aborted&&token===generation.current)setError(message(e));}
    finally{if(alive.current&&!c.signal.aborted&&token===generation.current)setLoading(false);}
  }
  function create() {
    if(!discard())return;reset();setView(null);setSelected('');
    setDraft({initial:newProfile('','',new Date().toISOString()),creating:true,key:++draftKey.current});
  }
  function version() {
    if(!view||!discard())return;reset();
    setDraft({initial:{...structuredClone(view.profile),version:view.profile.version+1,createdAt:new Date().toISOString()},
      creating:false,baseVersion:view.profile.version,key:++draftKey.current});
  }
  function confirmed(v:ProfileView) {
    if(!alive.current)return;
    clearUnsaved();setDirty(false);setSubmission(null);setDraft(null);setView(v);
    setSelected(`${v.profile.profileId}/${v.profile.version}`);setError('');setNotice('Versão publicada e confirmada.');
    void refresh();
  }
  async function write(profile:EditProfile,baseVersion?:number) {
    let acknowledged=false;
    try {
      await publishProfile(profile,baseVersion);
      acknowledged=true;
      const c=new AbortController();detailRequest.current=c;
      const v=await getProfile(profile.profileId,profile.version,c.signal);
      if(!alive.current)return;
      if(sameProfileContent(v.profile,profile))confirmed(v);
      else{setSubmission({profile,baseVersion,state:'conflict'});setError('A versão existente tem conteúdo diferente. Não será sobrescrita.');}
    }catch(e){
      if(!alive.current)return;
      setError(message(e));setDirty(true);
      if(acknowledged||!(e instanceof ApiError)||e.code==='command_unknown'||e.status===null||e.status>=500)
        setSubmission({profile,baseVersion,state:'unknown'});
      else if(e.code==='artifact_invalid')setSubmission({profile,baseVersion,state:'conflict'});
      else setSubmission(null);
    }
  }
  async function save(profile:EditProfile) {
    if(locked.current||submission||!draft)return;
    locked.current=true;setBusy(true);setError('');setNotice('');
    const frozen={...structuredClone(profile),createdAt:new Date().toISOString()};setDirty(true);
    try{await write(frozen,draft.baseVersion);}finally{locked.current=false;if(alive.current)setBusy(false);}
  }
  async function query(retry=false) {
    if(locked.current||!submission)return;
    locked.current=true;setBusy(true);setError('');setNotice('');
    const sent=submission,c=new AbortController();detailRequest.current=c;
    try{
      const v=await getProfile(sent.profile.profileId,sent.profile.version,c.signal);
      if(!alive.current)return;
      if(sameProfileContent(v.profile,sent.profile))confirmed(v);
      else{setSubmission({...sent,state:'conflict'});setError('A versão existente tem conteúdo diferente. Não será sobrescrita.');}
    }catch(e){
      if(!alive.current)return;
      if(e instanceof ApiError&&e.code==='not_found'&&e.status===404){
        if(retry)await write(sent.profile,sent.baseVersion);
        else{setSubmission({...sent,state:'absent'});setNotice('A versão enviada não foi encontrada nesta consulta.');}
      }else{setSubmission({...sent,state:'unknown'});setError(message(e));}
    }finally{locked.current=false;if(alive.current)setBusy(false);}
  }
  return <section className="profile-panel" aria-label="Profiles de edição">
    <h1>Profiles de edição</h1><p>Estilos reutilizáveis. Versões publicadas são imutáveis; alterações criam outra versão.</p>
    <div className="actions"><button onClick={()=>void refresh()} disabled={listLoading}>Atualizar profiles</button>
      <button onClick={create} disabled={busy}>Novo profile</button></div>
    {listLoading&&<p role="status">Carregando profiles…</p>}
    {listError&&<p role="alert">{listError} {items.length>0?'Lista desatualizada.':'Lista indisponível.'}</p>}
    {!listLoading&&!listError&&items.length===0&&<p>Nenhum profile publicado.</p>}
    <label>Consultar profile e versão<select aria-label="Consultar profile e versão" value={selected} disabled={busy} onChange={e=>void select(e.target.value)}>
      <option value="">Selecione uma versão</option>
      {items.map(v=><option key={`${v.profile.profileId}/${v.profile.version}`} value={`${v.profile.profileId}/${v.profile.version}`}>
        {v.profile.profileId} · v{v.profile.version} · {v.profile.name}
      </option>)}
    </select></label>
    {loading&&<p role="status">Consultando versão…</p>}
    {busy&&<p role="status">Aguardando confirmação da API…</p>}
    {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
    {submission&&<div className="confirmation"><p>{submission.state==='unknown'?'Resultado da publicação desconhecido. Nenhum POST será repetido automaticamente.':
      submission.state==='conflict'?'Publicação rejeitada ou versão em conflito. Consulte o conteúdo existente.':'Ausência observada; nova tentativa exige consulta prévia.'}</p>
      <button disabled={busy} onClick={()=>void query()}>Consultar versão enviada</button>
      {submission.state==='absent'&&<button disabled={busy} onClick={()=>void query(true)}>Tentar novamente com o mesmo conteúdo</button>}
      <button disabled={busy} onClick={()=>void select(`${submission.profile.profileId}/${submission.profile.version}`)}>Abrir versão existente</button>
    </div>}
    {draft?<><h2>Rascunho · v{draft.initial.version}</h2>
      <button disabled={busy} onClick={()=>{if(discard()){reset();setDraft(null);}}}>Descartar rascunho</button>
      <ProfileForm key={draft.key} initial={draft.initial} creating={draft.creating} readOnly={false}
        disabled={busy||submission!==null} onSubmit={p=>void save(p)} onDirtyChange={setDirty}/></>:
      view&&<><p>Versão publicada · Hash: <code>{view.profileHash}</code></p><button disabled={busy} onClick={version}>Criar nova versão</button>
        <ProfileForm key={`${view.profile.profileId}/${view.profile.version}`} initial={view.profile} creating={false}
          readOnly={true} disabled={false} onSubmit={()=>{}} onDirtyChange={()=>{}}/></>}
  </section>;
}
