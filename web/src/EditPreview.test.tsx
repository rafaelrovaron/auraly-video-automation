import {act,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {expect,it,vi} from 'vitest';
import {EditPreview,previewSettings} from './EditPreview';
import {newOverrideDraft} from './EditOverridesForm';
import {newProfile} from './profileApi';

const base=()=>({...newProfile('plain','Plain','2026-10-08T00:00:00Z').defaults,headline:{...newProfile('plain','Plain','2026-10-08T00:00:00Z').defaults.headline,enabled:true,text:'A'}});
const source={campaignId:'campaign-one',renderId:'render-one',sha256:'a'.repeat(64)};
it('warns_outside_canvas_when_text_crosses_vertical_safe_area',()=>{
  const rect=vi.spyOn(HTMLElement.prototype,'getBoundingClientRect').mockImplementation(function(this:HTMLElement){
    return {x:0,y:0,left:0,right:320,width:320,top:0,bottom:600,height:600,toJSON:()=>({})} as DOMRect;
  });
  const settings=base();settings.headline.y=1;settings.headline.anchor='top';
  const ui=render(<EditPreview source={null} settings={settings} captionText="Sample" mode="draft" timingMissing={false}/>);
  expect(screen.getByText(/Headline no preview: overflow aproximado/).closest('.preview-canvas')).toBeNull();
  ui.unmount();rect.mockRestore();
});
it('bounds_extreme_ratio_preview_height_without_changing_logical_canvas',()=>{
  const settings=base();settings.output.width=16;settings.output.height=1024;
  const ui=render(<EditPreview source={null} settings={settings} captionText="Sample" mode="draft" timingMissing={false}/>);
  const viewport=ui.container.querySelector('.preview-viewport') as HTMLElement;
  expect(viewport.style.width).toBe('min(100%, 10px)');
  expect((ui.container.querySelector('.preview-canvas') as HTMLElement).style.height).toBe('1024px');
});
const bytes=new Uint8Array([137,80,78,71,13,10,26,10,0,0,0,0,73,72,68,82,0,0,0,32,0,0,0,64]);
function browser(){
  const create=vi.fn(()=>`blob:poster-${Math.random()}`),revoke=vi.fn();
  vi.stubGlobal('URL',Object.assign(class extends URL{},{createObjectURL:create,revokeObjectURL:revoke}));
  return {create,revoke};
}
it('draft_preview_preserves_layers_and_updates_without_posts',async()=>{
  browser();const calls:RequestInit[]=[];vi.stubGlobal('fetch',async(_:string,init:RequestInit)=>{calls.push(init);return new Response(bytes,{headers:{'Content-Type':'image/png'}});});
  const props={source,settings:base(),captionText:'Texto demonstrativo de legenda',mode:'draft' as const,timingMissing:false};
  const ui=render(<EditPreview {...props}/>);
  await screen.findByAltText('Frame do MP4 selecionado');
  expect(screen.getByText('Preview aproximado — render final é a referência.')).toBeTruthy();
  expect(screen.getByText('Rascunho não validado')).toBeTruthy();
  expect(screen.getByText('A')).toBeTruthy();
  ui.rerender(<EditPreview {...props} settings={{...props.settings,headline:{...props.settings.headline,text:'B'}}}/>);
  expect(screen.getByText('B')).toBeTruthy();expect(calls).toHaveLength(1);
  expect(calls.every(c=>c.method==='GET')).toBe(true);
});
it('visual_layers_preserve_null_false_zero_rgba',()=>{
  const c=newOverrideDraft(),v=newOverrideDraft(),o=newOverrideDraft();
  c.headline.enabled={mode:'replace',value:true};v.headline.enabled={mode:'replace',value:false};
  o.headline.color={mode:'replace',value:'#11223344'};o.headline.x={mode:'replace',value:'0'};
  o.headline.font={mode:'clear',value:{path:'',sha256:''}};
  expect(previewSettings(base(),c,v,o)?.headline).toMatchObject({enabled:false,color:'#11223344',x:0,font:null,text:'A'});
  o.headline.x={mode:'replace',value:''};expect(previewSettings(base(),c,v,o)).toBeNull();
});
it('preview_clears_stale_frame_and_revokes_urls',async()=>{
  const urls=browser();let finish!:(r:Response)=>void;let n=0;const signals:AbortSignal[]=[];
  vi.stubGlobal('fetch',async(_:string,init:RequestInit)=>{signals.push(init.signal as AbortSignal);return ++n===1?new Response(bytes,{headers:{'Content-Type':'image/png'}}):new Promise<Response>(resolve=>{finish=resolve;});});
  const props={source,settings:base(),captionText:'Sample',mode:'draft' as const,timingMissing:false};
  const ui=render(<EditPreview {...props}/>);await screen.findByAltText('Frame do MP4 selecionado');
  ui.rerender(<EditPreview {...props} source={{...source,sha256:'b'.repeat(64)}}/>);
  expect(screen.queryByAltText('Frame do MP4 selecionado')).toBeNull();expect(urls.revoke).toHaveBeenCalledTimes(1);
  ui.unmount();await act(async()=>{finish(new Response(bytes,{headers:{'Content-Type':'image/png'}}));});
  expect(signals.every(s=>s.aborted)).toBe(true);expect(urls.create).toHaveBeenCalledTimes(1);
});
it('preview_warns_instead_of_hiding_overflow',async()=>{
  browser();let gets=0;vi.stubGlobal('fetch',async()=>{gets++;return Response.json({error:{}},{status:503});});
  const settings=base();settings.headline.fitPolicy='shrink';settings.captions.highlightEnabled=true;settings.framing.zoomEnd=1.1;
  render(<EditPreview source={source} settings={settings} captionText="Sample" mode="saved" timingMissing/>);
  await screen.findByText(/Frame indisponível/);
  expect(screen.getByText(/fallback/i)).toBeTruthy();expect(screen.getByText(/Timing pendente/)).toBeTruthy();
  expect(screen.getByText(/fit.*não simulado/i)).toBeTruthy();expect(screen.getByText(/highlight.*não simulado/i)).toBeTruthy();
  expect(screen.getByText(/zoom.*estático/i)).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:'Recarregar frame'}));await waitFor(()=>expect(gets).toBe(2));
});
