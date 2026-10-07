import { useId, useState } from 'react';
import { editProfile, newProfile, type EditProfile, type TextStyle } from './profileApi';

type Props={initial:EditProfile; creating:boolean; readOnly:boolean; disabled:boolean;
  onSubmit:(profile:EditProfile)=>void; onDirtyChange:(dirty:boolean)=>void};
export function ProfileForm({initial,creating,readOnly,disabled,onSubmit,onDirtyChange}:Props) {
  const [error,setError]=useState('');
  const errorId=useId();
  const value=(section:keyof EditProfile['defaults'],key:string):unknown =>
    (initial.defaults[section] as unknown as Record<string,unknown>)[key];
  function input(section:keyof EditProfile['defaults'],key:string,label:string,type='text',nullable=false) {
    const name=`${section}.${key}`;
    return <label key={name}>{label}<input name={name} type={type} step={type==='number'?'any':undefined}
      required={!nullable} defaultValue={String(value(section,key)??'')}/></label>;
  }
  function checkbox(section:keyof EditProfile['defaults'],key:string,label:string) {
    return <label key={`${section}.${key}`}><input name={`${section}.${key}`} type="checkbox" defaultChecked={Boolean(value(section,key))}/>{label}</label>;
  }
  function select(section:keyof EditProfile['defaults'],key:string,label:string,options:string[]) {
    return <label key={`${section}.${key}`}>{label}<select aria-label={label} name={`${section}.${key}`} defaultValue={String(value(section,key))}>
      {options.map(v=><option key={v} value={v}>{v}</option>)}
    </select></label>;
  }
  function asset(section:'headline'|'captions'|'music',key:'font'|'asset',prefix:string) {
    const ref=key==='asset'?initial.defaults.music.asset:(initial.defaults[section as 'headline'|'captions'] as TextStyle).font;
    return <><label>{prefix}: caminho relativo<input name={`${section}.${key}.path`} defaultValue={ref?.path??''}/></label>
      <label>{prefix}: SHA-256<input name={`${section}.${key}.sha256`} defaultValue={ref?.sha256??''}/></label></>;
  }
  function textFields(section:'headline'|'captions',label:string) {
    const p=`${label} · `;
    return <>
      {checkbox(section,'enabled',p+'Habilitar')}{input(section,'styleId',p+'Style ID')}
      {asset(section,'font',p+'Fonte')}
      {input(section,'fontSizePx',p+'Tamanho (px)','number')}
      {input(section,'color',p+'Cor (RGB/RGBA)')}
      {select(section,'anchor',p+'Âncora',['top','center','bottom'])}
      {input(section,'x',p+'X (0–1)','number')}{input(section,'y',p+'Y (0–1)','number')}
      <details><summary>{label} · Opções avançadas</summary>
        {input(section,'fontWeight',p+'Peso (100–900)','number')}
        {input(section,'lineHeight',p+'Altura de linha','number')}
        {input(section,'strokeWidthPx',p+'Contorno (px)','number')}{input(section,'strokeColor',p+'Cor do contorno')}
        {checkbox(section,'shadowEnabled',p+'Sombra')}{input(section,'shadowColor',p+'Cor da sombra')}
        {input(section,'shadowOffsetX',p+'Sombra X (px)','number')}{input(section,'shadowOffsetY',p+'Sombra Y (px)','number')}
        {checkbox(section,'backgroundEnabled',p+'Fundo')}{input(section,'backgroundColor',p+'Cor do fundo')}
        {input(section,'backgroundPaddingPx',p+'Padding do fundo (px)','number')}
        {input(section,'safeTop',p+'Margem superior (0–1)','number')}{input(section,'safeRight',p+'Margem direita (0–1)','number')}
        {input(section,'safeBottom',p+'Margem inferior (0–1)','number')}{input(section,'safeLeft',p+'Margem esquerda (0–1)','number')}
        {input(section,'maxLines',p+'Máximo de linhas','number')}{select(section,'fitPolicy',p+'Ajuste do texto',['wrap','shrink','error'])}
      </details>
    </>;
  }
  function submit(form:HTMLFormElement) {
    if(readOnly||disabled) return;
    const data=new FormData(form), profile=structuredClone(initial);
    profile.profileId=String(data.get('profileId')??''); profile.name=String(data.get('name')??'');
    // Serialize only the known complete profile fields, never arbitrary FormData keys.
    for(const section of Object.keys(profile.defaults) as (keyof EditProfile['defaults'])[]) {
      const values=profile.defaults[section] as unknown as Record<string,unknown>;
      for(const key of Object.keys(values)) {
        const name=`${section}.${key}`,current=values[key];
        if(key==='font'||key==='asset') {
          const path=String(data.get(name+'.path')??''),sha256=String(data.get(name+'.sha256')??'');
          values[key]=!path&&!sha256?null:{path,sha256};
        } else if(typeof current==='boolean') values[key]=data.has(name);
        else if(typeof current==='number'||key==='endSec'||key==='trimEndSec') {
          const raw=String(data.get(name)??'');
          values[key]=!raw.trim()?(key==='endSec'||key==='trimEndSec'?null:NaN):Number(raw);
        } else values[key]=String(data.get(name)??'');
      }
    }
    const h=profile.defaults.headline,m=profile.defaults.music;
    if(!editProfile(profile)||(h.endSec!==null&&h.endSec<=h.startSec)||(m.trimEndSec!==null&&m.trimEndSec<=m.trimStartSec)) {
      const invalid=new Set<string>(),baseline=newProfile('plain','Profile',initial.createdAt);
      // Reuse the contract validator for scalar/ref errors; check related fields together below.
      for(const s of ['headline','captions'] as const)
        Object.assign(baseline.defaults[s],{safeTop:0,safeBottom:0,safeLeft:0,safeRight:0});
      for(const key of ['profileId','name'] as const)
        if(!editProfile({...baseline,[key]:profile[key]}))invalid.add(key);
      for(const section of Object.keys(profile.defaults) as (keyof EditProfile['defaults'])[]) {
        const values=profile.defaults[section] as unknown as Record<string,unknown>;
        for(const [key,current] of Object.entries(values)) {
          const probe=structuredClone(baseline);
          (probe.defaults[section] as unknown as Record<string,unknown>)[key]=current;
          if(!editProfile(probe))invalid.add(`${section}.${key}${key==='font'||key==='asset'?'.path':''}`);
        }
      }
      for(const s of ['headline','captions'] as const) {
        const t=profile.defaults[s];
        if(t.safeTop+t.safeBottom>=1)invalid.add(`${s}.safeTop`);
        if(t.safeLeft+t.safeRight>=1)invalid.add(`${s}.safeRight`);
      }
      if(h.endSec!==null&&h.endSec<=h.startSec)invalid.add('headline.endSec');
      if(m.trimEndSec!==null&&m.trimEndSec<=m.trimStartSec)invalid.add('music.trimEndSec');
      const controls=Array.from(form.querySelectorAll<HTMLInputElement|HTMLSelectElement>('input,select'));
      controls.forEach(c=>{c.removeAttribute('aria-invalid');c.removeAttribute('aria-describedby');});
      const field=controls.find(c=>invalid.has(c.name));
      const label=field?.labels?.[0];
      const title=label?Array.from(label.childNodes).filter(n=>n.nodeType===3).map(n=>n.textContent).join('').trim():'Configuração';
      setError(`${title}: revise o valor, limites, intervalo ou referência completa.`);
      if(field){field.setAttribute('aria-invalid','true');field.setAttribute('aria-describedby',errorId);
        const details=field.closest('details');if(details)details.open=true;
        requestAnimationFrame(()=>field.focus());}return;
    }
    form.querySelectorAll('[aria-invalid]').forEach(c=>{c.removeAttribute('aria-invalid');c.removeAttribute('aria-describedby');});
    setError(''); onSubmit(profile);
  }
  return <form aria-label="Configuração do profile" noValidate onChange={()=>onDirtyChange(true)}
    onSubmit={e=>{e.preventDefault();submit(e.currentTarget);}}>
    <p>Profile salvo não confirma disponibilidade de fontes ou música. Os arquivos serão verificados ao preparar a edição.</p>
    <p>Música exige aceitação por edição; timing das legendas não é definido neste profile.</p>
    {error&&<p role="alert" id={errorId}>{error}</p>}
    <fieldset disabled={readOnly||disabled}><legend>Identidade</legend>
      <label>ID do profile<input name="profileId" defaultValue={initial.profileId} readOnly={!creating} required/></label>
      <label>Nome do profile<input name="name" defaultValue={initial.name} required/></label>
      <p>Versão: {initial.version} · Data: {initial.createdAt}</p>
    </fieldset>
    <fieldset disabled={readOnly||disabled}><legend>Output</legend>
      {input('output','width','Output · Largura (px)','number')}{input('output','height','Output · Altura (px)','number')}
      {input('output','fps','Output · FPS','number')}
      <input type="hidden" name="output.format" value="mp4"/><input type="hidden" name="output.codec" value="h264"/><p>MP4 / H.264</p>
    </fieldset>
    <fieldset disabled={readOnly||disabled}><legend>Headline</legend>{textFields('headline','Headline')}
      {input('headline','startSec','Headline · Início (s)','number')}{input('headline','endSec','Headline · Fim (s)','number',true)}
    </fieldset>
    <fieldset disabled={readOnly||disabled}><legend>Legendas</legend>{textFields('captions','Legendas')}
      {checkbox('captions','highlightEnabled','Legendas · Highlight')}{input('captions','highlightColor','Legendas · Cor do highlight')}
    </fieldset>
    <fieldset disabled={readOnly||disabled}><legend>Música</legend>
      {checkbox('music','enabled','Música · Habilitar')}{asset('music','asset','Música · Asset')}
      {input('music','volumeDb','Música · Volume (dB)','number')}{input('music','duckUnderVoiceDb','Música · Redução sob voz (dB)','number')}
      {checkbox('music','loop','Música · Loop')}
      {input('music','trimStartSec','Música · Início do corte (s)','number')}{input('music','trimEndSec','Música · Fim do corte (s)','number',true)}
      {input('music','fadeInSec','Música · Fade-in (s)','number')}{input('music','fadeOutSec','Música · Fade-out (s)','number')}
    </fieldset>
    <fieldset disabled={readOnly||disabled}><legend>Enquadramento</legend>
      {select('framing','fit','Enquadramento · Fit',['cover','contain'])}{input('framing','scale','Enquadramento · Escala (1–1.25)','number')}
      {input('framing','x','Enquadramento · X (0–1)','number')}{input('framing','y','Enquadramento · Y (0–1)','number')}
      {input('framing','zoomStart','Enquadramento · Zoom inicial (1–1.25)','number')}{input('framing','zoomEnd','Enquadramento · Zoom final (1–1.25)','number')}
    </fieldset>
    {!readOnly&&<button disabled={disabled} type="submit">Salvar versão</button>}
  </form>;
}
