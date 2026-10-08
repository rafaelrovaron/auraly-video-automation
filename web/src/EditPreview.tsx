import {useEffect,useLayoutEffect,useRef,useState} from 'react';
import type {CSSProperties} from 'react';
import {readOverrides} from './EditOverridesForm';
import type {OverrideDraft,OverrideHints} from './EditOverridesForm';
import type {TextStyle} from './profileApi';
import {getRenderPoster} from './heygenApi';

export type PreviewSource={campaignId:string;renderId:string;sha256:string};
export function previewSettings(base:OverrideHints,campaign:OverrideDraft,video:OverrideDraft,variant:OverrideDraft):OverrideHints|null{
  let result=base;
  for(const draft of [campaign,video,variant]){
    const {overrides}=readOverrides(draft);if(!overrides)return null;
    result=Object.fromEntries(Object.entries(result).map(([section,fields])=>[section,{...fields,...overrides[section as keyof typeof overrides]}])) as OverrideHints;
  }
  return result;
}
function PreviewText({text,style,label}:{text:string;style:TextStyle;label:string}){
  const element=useRef<HTMLDivElement>(null),[overflow,setOverflow]=useState(false);
  useLayoutEffect(()=>{
    const measure=()=>{const el=element.current;if(el)setOverflow(el.scrollHeight>style.maxLines*style.fontSizePx*style.lineHeight+1||el.scrollWidth>el.clientWidth+1);};
    measure();const observer=typeof ResizeObserver==='undefined'?null:new ResizeObserver(measure);if(element.current)observer?.observe(element.current);
    return()=>observer?.disconnect();
  },[text,style]);
  if(!style.enabled)return null;
  const y=style.anchor==='top'?'0%':style.anchor==='bottom'?'-100%':'-50%';
  const width=Math.max(0,Math.min(style.x-style.safeLeft,1-style.safeRight-style.x)*2);
  const css:CSSProperties={left:`${style.x*100}%`,top:`${style.y*100}%`,width:`${width*100}%`,transform:`translate(-50%,${y})`,
    fontFamily:'system-ui, sans-serif',fontSize:style.fontSizePx,fontWeight:style.fontWeight,lineHeight:style.lineHeight,color:style.color,
    WebkitTextStroke:style.strokeWidthPx?`${style.strokeWidthPx}px ${style.strokeColor}`:undefined,
    textShadow:style.shadowEnabled?`${style.shadowOffsetX}px ${style.shadowOffsetY}px 0 ${style.shadowColor}`:undefined,
    backgroundColor:style.backgroundEnabled?style.backgroundColor:undefined,padding:style.backgroundEnabled?style.backgroundPaddingPx:0};
  return <><div ref={element} className="preview-text" aria-label={label} style={css}>{text}</div>
    {overflow&&<span className="preview-overflow">{label}: overflow aproximado — conferir no render</span>}</>;
}
export function EditPreview({source,settings,captionText,mode,timingMissing}:{source:PreviewSource|null;settings:OverrideHints|null;captionText:string;mode:'draft'|'validated'|'saved';timingMissing:boolean}){
  const identity=source?`${source.campaignId}/${source.renderId}/${source.sha256}`:'', [reload,setReload]=useState(0);
  const [frame,setFrame]=useState<{identity:string;url:string}|null>(null),[failed,setFailed]=useState(false),[loading,setLoading]=useState(false);
  const viewport=useRef<HTMLDivElement>(null),[width,setWidth]=useState(320);
  useEffect(()=>{
    setFrame(null);setFailed(false);setLoading(!!source);if(!source)return;
    const controller=new AbortController();let url:string|null=null,active=true;
    void getRenderPoster(source.campaignId,source.renderId,source.sha256,controller.signal).then(blob=>{
      if(!active)return;url=URL.createObjectURL(blob);setFrame({identity,url});setLoading(false);
    }).catch(()=>{if(active){setFailed(true);setLoading(false);}});
    return()=>{active=false;controller.abort();if(url)URL.revokeObjectURL(url);};
  },[identity,reload]);
  useLayoutEffect(()=>{
    const measure=()=>setWidth(viewport.current?.getBoundingClientRect().width||320);
    measure();const observer=typeof ResizeObserver==='undefined'?null:new ResizeObserver(measure);if(viewport.current)observer?.observe(viewport.current);
    return()=>observer?.disconnect();
  },[!!settings]);
  const current=frame?.identity===identity?frame.url:null;
  return <section aria-label="Preview da edição" className="edit-preview">
    <p>Preview aproximado — render final é a referência.</p>
    <p>{mode==='draft'?'Rascunho não validado':mode==='validated'?'Plano validado · amostra estática':'Plano salvo · consulta readonly'}</p>
    <p>Fonte de sistema (fallback); fonte real e quebra devem ser conferidas no render. Sem sincronização ou áudio.</p>
    {mode==='draft'&&<p>Legenda: texto demonstrativo, não copy aprovada.</p>}
    {timingMissing&&<p>Timing pendente; legenda é amostra de layout.</p>}
    {loading&&<p role="status">Carregando frame…</p>}{failed&&<p role="status">Frame indisponível — fundo neutro, não representa mídia real.</p>}
    <button type="button" disabled={!source||loading} onClick={()=>setReload(n=>n+1)}>Recarregar frame</button>
    {!settings?<p>Preview indisponível: selecione profile e revise os valores do rascunho.</p>:<>
      {settings.output.width/settings.output.height!==9/16&&<p>Proporção diferente de 9:16: preservando canvas configurado.</p>}
      {settings.framing.zoomStart!==settings.framing.zoomEnd&&<p>Zoom estático: usando zoomStart, sem animar zoomEnd.</p>}
      {(settings.headline.fitPolicy!=='wrap'||settings.captions.fitPolicy!=='wrap')&&<p>Fit shrink/error não simulado; apenas wrap aproximado.</p>}
      {settings.captions.highlightEnabled&&<p>Highlight não simulado; sem animação por palavra.</p>}
      <div ref={viewport} className="preview-viewport" style={{aspectRatio:`${settings.output.width}/${settings.output.height}`}}>
        <div className="preview-canvas" style={{width:settings.output.width,height:settings.output.height,transform:`scale(${width/settings.output.width})`}}>
          {current&&<img alt="Frame do MP4 selecionado" src={current} onError={()=>{setFrame(null);setFailed(true);}} style={{objectFit:settings.framing.fit,
            objectPosition:`${settings.framing.x*100}% ${settings.framing.y*100}%`,transform:`scale(${settings.framing.scale*settings.framing.zoomStart})`,
            transformOrigin:`${settings.framing.x*100}% ${settings.framing.y*100}%`}}/>}
          <PreviewText label="Headline no preview" text={settings.headline.text} style={settings.headline}/>
          <PreviewText label="Legenda no preview" text={captionText} style={settings.captions}/>
        </div>
      </div>
    </>}
  </section>;
}
