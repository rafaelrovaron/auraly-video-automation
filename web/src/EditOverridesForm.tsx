import {editOverrides} from './editingApi';
import type {EditOverrides} from './editingApi';
import {newProfile} from './profileApi';
import type {EditDefaults,HeadlineStyle} from './profileApi';

export type FieldDraft={mode:'inherit'|'replace'|'clear';value:string|boolean|{path:string;sha256:string}};
export type OverrideDraft={[S in keyof EditOverrides]-?:{[F in keyof NonNullable<EditOverrides[S]>]-?:FieldDraft}};
export type OverrideHints=EditDefaults & {headline:HeadlineStyle & {text:string}};
type Section=keyof EditOverrides;
const sections:Section[]=['output','headline','captions','music','framing'];
const names:Record<Section,string>={output:'Output',headline:'Headline',captions:'Legendas',music:'Música',framing:'Enquadramento'};
const defaults:OverrideHints={...newProfile('plain','Plain','2026-10-07T00:00:00Z').defaults,
  headline:{...newProfile('plain','Plain','2026-10-07T00:00:00Z').defaults.headline,text:''}};
const nullable=new Set(['headline.font','captions.font','headline.endSec','music.asset','music.trimEndSec']);
const choices:Record<string,string[]>={'output.format':['mp4'],'output.codec':['h264'],
  'headline.anchor':['top','center','bottom'],'captions.anchor':['top','center','bottom'],
  'headline.fitPolicy':['wrap','shrink','error'],'captions.fitPolicy':['wrap','shrink','error'],'framing.fit':['cover','contain']};
function raw(value:unknown,field:string):FieldDraft['value']{
  if(field.endsWith('.font')||field==='music.asset')return value&&typeof value==='object'?{...(value as {path:string;sha256:string})}:{path:'',sha256:''};
  return typeof value==='boolean'?value:String(value??'');
}
export function newOverrideDraft():OverrideDraft{
  return Object.fromEntries(sections.map(section=>[section,Object.fromEntries(Object.entries(defaults[section]).map(([field,value])=>
    [field,{mode:'inherit',value:raw(value,`${section}.${field}`)}]))])) as OverrideDraft;
}
export function readOverrides(draft:OverrideDraft):{overrides:EditOverrides|null;invalidField:string|null}{
  const result:Record<string,Record<string,unknown>>={};
  for(const section of sections){
    for(const [field,item] of Object.entries(draft[section])){
      const name=`${section}.${field}`;if(item.mode==='inherit')continue;
      let value:unknown=item.value;
      if(typeof value==='string')value=value.trim();
      else if(value&&typeof value==='object')value={path:(value as {path:string}).path.trim(),sha256:(value as {sha256:string}).sha256.trim()};
      if(item.mode==='clear'){if(!nullable.has(name))return {overrides:null,invalidField:name};value=null;}
      else if(typeof (defaults[section] as unknown as Record<string,unknown>)[field]==='number'||name==='headline.endSec'||name==='music.trimEndSec'){
        value=typeof item.value==='string'&&item.value.trim()?Number(item.value):NaN;
      }
      if(!editOverrides({[section]:{[field]:value}}))return {overrides:null,invalidField:name};
      (result[section]??={})[field]=value;
    }
  }
  return {overrides:result as EditOverrides,invalidField:null};
}
export function EditOverridesForm({draft,inherited,disabled,label,onChange}:{draft:OverrideDraft;inherited:OverrideHints;disabled:boolean;label:string;
  onChange:(draft:OverrideDraft,field:string)=>void}){
  const control=(section:Section,field:string,item:FieldDraft)=>{
    const name=`${section}.${field}`,caption=`${label} · ${names[section]} · ${field==='text'?'Texto':field}`;
    const hint=(inherited[section] as unknown as Record<string,unknown>)[field];
    const change=(next:FieldDraft)=>{const copy=structuredClone(draft);(copy[section] as Record<string,FieldDraft>)[field]=next;onChange(copy,name);};
    const numeric=typeof (defaults[section] as unknown as Record<string,unknown>)[field]==='number'||name==='headline.endSec'||name==='music.trimEndSec';
    return <div key={name} className="override-field">
      <label>{caption} · Modo<select aria-label={`${caption} · Modo`} value={item.mode} name={`${label}.${name}.mode`} onChange={e=>{
        const mode=e.target.value as FieldDraft['mode'];change({mode,value:mode==='replace'&&item.mode==='inherit'?raw(hint,name):item.value});
      }}><option value="inherit">Herdar</option><option value="replace">Substituir</option>{nullable.has(name)&&<option value="clear">Limpar</option>}</select></label>
      <small>Herdado: {typeof hint==='object'?JSON.stringify(hint):String(hint??'Não definido')} (não validado)</small>
      {item.mode==='replace'&&(typeof item.value==='object'?<>
        <label>{caption} · Caminho<input name={`${label}.${name}`} value={item.value.path} onChange={e=>change({...item,value:{...(item.value as {path:string;sha256:string}),path:e.target.value}})}/></label>
        <label>{caption} · SHA-256<input name={`${label}.${name}.sha256`} value={item.value.sha256} onChange={e=>change({...item,value:{...(item.value as {path:string;sha256:string}),sha256:e.target.value}})}/></label>
      </>:typeof item.value==='boolean'?<label>{caption}<input name={`${label}.${name}`} type="checkbox" checked={item.value} onChange={e=>change({...item,value:e.target.checked})}/></label>
        :choices[name]?<label>{caption}<select aria-label={caption} name={`${label}.${name}`} value={item.value} onChange={e=>change({...item,value:e.target.value})}>
          {choices[name].map(value=><option key={value}>{value}</option>)}</select></label>
          :<label>{caption}<input name={`${label}.${name}`} type={numeric?'number':'text'} step="any" value={item.value} onChange={e=>change({...item,value:e.target.value})}/></label>)}
    </div>;
  };
  return <fieldset disabled={disabled}><legend>{label} · Overrides</legend>
    {control('headline','text',draft.headline.text)}
    <details><summary>{label} · Opções avançadas</summary>{sections.map(section=><details key={section}><summary>{names[section]}</summary>
      {Object.entries(draft[section]).filter(([field])=>section!=='headline'||field!=='text').map(([field,item])=>control(section,field,item))}
    </details>)}</details>
  </fieldset>;
}
