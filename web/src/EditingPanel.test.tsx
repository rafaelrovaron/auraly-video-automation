import {act,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {expect,it,vi} from 'vitest';
import {EditingPanel} from './EditingPanel';
import {EDIT_RENDER,editPlanFixture} from './editingTestSupport';
import {PROFILE} from './profileTestSupport';
import type {EditBatchRequest,EditBatchPlan} from './editingApi';

const renders={data:{items:[EDIT_RENDER,{...EDIT_RENDER,renderId:'render-two'}]},loading:false,error:null,lastSuccessAt:1,lastSuccessReadId:1,refresh:()=>1};
type Call={request:EditBatchRequest;persist:boolean};
function server(options:{trim?:boolean;lost?:boolean;queued?:boolean;failed?:boolean;getFail?:boolean;getDivergent?:boolean;wrongPersist?:boolean;saveHash?:boolean;wrongRender?:boolean;divergent?:boolean;postMalformed?:boolean;delay?:Promise<Response>}={}){
  const posts:Call[]=[];const saved:EditBatchPlan[]=[];let current:Call|null=null,queued=options.queued;
  vi.stubGlobal('fetch',async(path:string,init:RequestInit)=>{
    if(init.method==='POST'){
      current=JSON.parse(String(init.body));posts.push(current!);
      if(options.trim)current=JSON.parse(String(init.body),(_,v)=>typeof v==='string'?v.trim():v);
      if(current!.persist)saved.push(editPlanFixture(current!.request));
      if(options.lost&&current!.persist)return Response.json({error:{code:'storage_unavailable'}},{status:503});
      return Response.json({jobId:'edit-job',campaignId:options.postMalformed?'other':'campaign-one',operation:'edit_plan'});
    }
    if(path==='/api/v1/editing/profiles')return Response.json({items:[PROFILE]});
    if(path==='/api/v1/editing/profiles/plain/1')return Response.json(PROFILE);
    if(path.endsWith('/operations/edit-job')){
      if(options.delay)return options.delay;
      const plan=editPlanFixture(current!.request);if(options.divergent)plan.outputs[0].label='Other';
      if(options.saveHash&&current!.persist)plan.planHash='e'.repeat(64);
      if(options.wrongRender)plan.renderId='render-two';
      return Response.json({jobId:'edit-job',campaignId:'campaign-one',operation:'edit_plan',status:queued?'queued':options.failed?'failed':'completed',
        errorCode:options.failed?'artifact_invalid':null,result:queued||options.failed?null:{operation:'edit_plan',persist:options.wrongPersist?!current!.persist:current!.persist,plan}});
    }
    if(path.endsWith('/editing/plans'))return Response.json({items:saved.map(({videoId,renderId,planHash,outputCount,maxOutputs,captionInput,copyRef,voiceRef,source,outputs})=>
      ({videoId,renderId,planHash,outputCount,maxOutputs,timingStatus:captionInput.timingStatus,copyRef,voiceRef,source,outputs:outputs.map(({manifest,...out})=>{void manifest;return out;})}))});
    if(options.getFail)return Response.json({error:{code:'not_found'}},{status:404});
    return Response.json(options.getDivergent?{...saved[0],maxOutputs:4}:saved[0]);
  });
  return {posts,saved,complete:()=>{queued=false;}};
}
async function draft(){
  await screen.findByRole('option',{name:/plain.*v1/});
  fireEvent.change(screen.getByLabelText('MP4 HeyGen para edição'),{target:{value:'render-one'}});
  fireEvent.change(screen.getByLabelText('Profile de edição'),{target:{value:'plain/1'}});
  await screen.findByLabelText('Headline base');fireEvent.change(screen.getByLabelText('Headline base'),{target:{value:'Base'}});
}
const validate=()=>fireEvent.click(screen.getByRole('button',{name:'Validar plano'}));
const save=()=>screen.getByRole('button',{name:'Salvar plano'});
it('preview_selection_does_not_mutate_editorial_request',async()=>{
  const s=server();render(<EditingPanel campaignId="campaign-one" renders={renders}/>);await draft();
  fireEvent.change(screen.getByLabelText('Variante a · Headline · enabled · Modo'),{target:{value:'replace'}});
  fireEvent.click(screen.getByLabelText('Variante a · Headline · enabled'));
  fireEvent.click(screen.getByRole('button',{name:'Adicionar variante'}));
  fireEvent.change(screen.getByLabelText('Variante b · Headline · enabled · Modo'),{target:{value:'replace'}});
  fireEvent.click(screen.getByLabelText('Variante b · Headline · enabled'));
  fireEvent.change(screen.getByLabelText('Variante b · Headline · Texto · Modo'),{target:{value:'replace'}});
  fireEvent.change(screen.getByLabelText('Variante b · Headline · Texto'),{target:{value:'B'}});
  const preview=within(screen.getByRole('region',{name:'Preview da edição'}));
  expect(preview.getByText('Base')).toBeTruthy();
  fireEvent.change(screen.getByLabelText('Variante no preview'),{target:{value:'2'}});
  expect(preview.getByText('B')).toBeTruthy();expect(s.posts).toEqual([]);
  fireEvent.change(screen.getByLabelText('Key da variante b'),{target:{value:'renamed'}});
  expect(preview.getByText('B')).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:'Remover variante renamed'}));
  expect(preview.getByText('Base')).toBeTruthy();expect(s.posts).toEqual([]);
});
it('readonly_preview_uses_exact_plan_without_overwriting_draft',async()=>{
  const s=server();render(<EditingPanel campaignId="campaign-one" renders={renders}/>);await draft();
  validate();await screen.findByText('Plano validado. Nenhum arquivo de edição publicado.');
  fireEvent.click(save());await screen.findByText('Plano salvo e confirmado. Não é um vídeo renderizado.');
  fireEvent.change(screen.getByLabelText('Headline base'),{target:{value:'New draft'}});
  fireEvent.change(screen.getByLabelText('Consultar plano salvo'),{target:{value:`${s.saved[0].videoId}/${s.saved[0].planHash}`}});
  await waitFor(()=>expect(screen.getByLabelText('Origem do preview').querySelector('option[value="stored"]')).not.toBeNull());
  fireEvent.change(screen.getByLabelText('Origem do preview'),{target:{value:'stored'}});
  expect(within(screen.getByRole('region',{name:'Preview da edição'})).getByText('Plano salvo · consulta readonly')).toBeTruthy();
  expect((screen.getByLabelText('Headline base') as HTMLInputElement).value).toBe('New draft');
  fireEvent.change(screen.getByLabelText('Origem do preview'),{target:{value:'draft'}});
  expect(save().hasAttribute('disabled')).toBe(true);expect(s.posts).toHaveLength(2);
});
it('one_mp4_three_headlines',async()=>{
  const s=server();render(<EditingPanel campaignId="campaign-one" renders={renders}/>);await draft();
  for(const key of ['a','b','c']){
    if(key!=='a')fireEvent.click(screen.getByRole('button',{name:'Adicionar variante'}));
    const variant=within(screen.getByRole('group',{name:`Variante ${key}`}));
    fireEvent.change(variant.getByLabelText(`Variante ${key} · Headline · Texto · Modo`),{target:{value:'replace'}});
    fireEvent.change(variant.getByLabelText(`Variante ${key} · Headline · Texto`),{target:{value:key.toUpperCase()}});
  }
  validate();await screen.findByText('Plano validado. Nenhum arquivo de edição publicado.');
  expect(s.posts[0]).toMatchObject({persist:false,request:{maxOutputs:3,variants:[
    {key:'a',label:'A',overrides:{headline:{text:'A'}}},{key:'b',label:'B',overrides:{headline:{text:'B'}}},{key:'c',label:'C',overrides:{headline:{text:'C'}}}]}});
  fireEvent.click(save());await screen.findByText('Plano salvo e confirmado. Não é um vídeo renderizado.');
  expect(s.posts[1]).toEqual({campaignId:'campaign-one',operation:'edit_plan',request:s.posts[0].request,persist:true});
});
it('normalizes editorial text before freezing the request to match local Job storage',async()=>{
  const s=server({trim:true});render(<EditingPanel campaignId="campaign-one" renders={renders}/>);await draft();
  fireEvent.change(screen.getByLabelText('Headline base'),{target:{value:' Headline with spaces '}});
  fireEvent.change(screen.getByLabelText('Nome da variante a'),{target:{value:' Label '}});
  fireEvent.change(screen.getByLabelText('Variante a · Headline · Texto · Modo'),{target:{value:'replace'}});
  fireEvent.change(screen.getByLabelText('Variante a · Headline · Texto'),{target:{value:' Variant '}});
  validate();await screen.findByText(/Plano validado\./);
  expect(s.posts[0].request.headlineText).toBe('Headline with spaces');expect(s.posts[0].request.variants[0].label).toBe('Label');
  expect(s.posts[0].request.variants[0].overrides.headline?.text).toBe('Variant');
});
it('validation_required_for_save and queued is not validated',async()=>{
  const s=server({queued:true});render(<EditingPanel campaignId="campaign-one" renders={renders}/>);await draft();
  expect(save().matches(':disabled')).toBe(true);validate();
  await screen.findByText(/Operação editorial na fila/);expect(save().matches(':disabled')).toBe(true);
  s.complete();fireEvent.click(screen.getByRole('button',{name:'Consultar operação editorial'}));
  await screen.findByText('Plano validado. Nenhum arquivo de edição publicado.');expect(save().matches(':disabled')).toBe(false);
});
it('editing_invalidates_validation and refresh_keeps_draft',async()=>{
  server();render(<EditingPanel campaignId="campaign-one" renders={renders}/>);await draft();validate();await screen.findByText(/Plano validado\./);
  fireEvent.change(screen.getByLabelText('Headline base'),{target:{value:'Changed'}});expect(save().matches(':disabled')).toBe(true);
  fireEvent.click(screen.getByRole('button',{name:'Atualizar edição'}));expect((screen.getByLabelText('Headline base') as HTMLInputElement).value).toBe('Changed');
});
it('music_acceptance_resets_selectively',async()=>{
  server();render(<EditingPanel campaignId="campaign-one" renders={renders}/>);await draft();
  const acceptance=screen.getByLabelText('Aceito o uso da música nesta edição');fireEvent.click(acceptance);
  fireEvent.change(screen.getByLabelText('Headline base'),{target:{value:'New'}});expect((acceptance as HTMLInputElement).checked).toBe(true);
  fireEvent.change(screen.getByLabelText('Vídeo · Música · asset · Modo'),{target:{value:'clear'}});expect((acceptance as HTMLInputElement).checked).toBe(false);
});
it('removing a headline-only variant keeps music acceptance',async()=>{
  server();render(<EditingPanel campaignId="campaign-one" renders={renders}/>);await draft();
  fireEvent.click(screen.getByRole('button',{name:'Adicionar variante'}));
  fireEvent.click(screen.getByLabelText('Aceito o uso da música nesta edição'));
  fireEvent.click(screen.getByRole('button',{name:'Remover variante b'}));
  expect((screen.getByLabelText('Aceito o uso da música nesta edição') as HTMLInputElement).checked).toBe(true);
});
it('lost_post_stays_unknown even when the artifact exists',async()=>{
  const s=server({lost:true});render(<EditingPanel campaignId="campaign-one" renders={renders}/>);await draft();validate();await screen.findByText(/Plano validado\./);
  fireEvent.click(save());await screen.findByText(/Resultado editorial desconhecido/);fireEvent.click(screen.getByRole('button',{name:'Consultar plano enviado'}));
  await screen.findByText(/Artefato encontrado; isto não confirma autoria/);expect(s.posts).toHaveLength(2);expect(save().matches(':disabled')).toBe(true);
});
it.each([{divergent:true},{postMalformed:true}])('divergent_result_is_not_success %j',async options=>{
  server(options);render(<EditingPanel campaignId="campaign-one" renders={renders}/>);await draft();validate();
  await screen.findByText(/Resultado editorial desconhecido/);expect(save().matches(':disabled')).toBe(true);
});
it('failed_job_preserves_input',async()=>{
  server({failed:true});render(<EditingPanel campaignId="campaign-one" renders={renders}/>);await draft();validate();
  await screen.findByText(/Operação editorial falhou/);expect((screen.getByLabelText('Headline base') as HTMLInputElement).value).toBe('Base');expect(save().matches(':disabled')).toBe(true);
});
it('save_requires_exact_get even after acknowledged POST',async()=>{
  server({getFail:true});render(<EditingPanel campaignId="campaign-one" renders={renders}/>);await draft();validate();await screen.findByText(/Plano validado\./);fireEvent.click(save());
  await screen.findByText(/Resultado editorial desconhecido/);expect(screen.queryByText(/Plano salvo e confirmado/)).toBeNull();
});
it('late_operation_does_not_unlock_save in another campaign',async()=>{
  let finish!:(r:Response)=>void;const delay=new Promise<Response>(resolve=>{finish=resolve;});const s=server({delay});
  const view=render(<EditingPanel campaignId="campaign-one" renders={renders}/>);await draft();validate();await screen.findByText(/Operação editorial na fila/);
  view.rerender(<EditingPanel campaignId="campaign-two" renders={{...renders,data:{items:[]}}}/>);
  await act(async()=>finish(Response.json({jobId:'edit-job',campaignId:'campaign-one',operation:'edit_plan',status:'completed',errorCode:null,
    result:{operation:'edit_plan',persist:false,plan:editPlanFixture(s.posts[0].request)}})));
  expect(screen.queryByText(/Plano validado\./)).toBeNull();expect(save().matches(':disabled')).toBe(true);
});
it('discard_cancel_keeps_values',async()=>{
  server();vi.spyOn(window,'confirm').mockReturnValue(false);render(<EditingPanel campaignId="campaign-one" renders={renders}/>);await draft();
  fireEvent.change(screen.getByLabelText('MP4 HeyGen para edição'),{target:{value:'render-two'}});
  expect((screen.getByLabelText('MP4 HeyGen para edição') as HTMLSelectElement).value).toBe('render-one');expect((screen.getByLabelText('Headline base') as HTMLInputElement).value).toBe('Base');
});
it('collapsed_invalid_field_receives_focus',async()=>{
  server();render(<EditingPanel campaignId="campaign-one" renders={renders}/>);await draft();
  fireEvent.change(screen.getByLabelText('Vídeo · Headline · fontSizePx · Modo'),{target:{value:'replace'}});
  const input=screen.getByLabelText('Vídeo · Headline · fontSizePx');fireEvent.change(input,{target:{value:''}});validate();
  await waitFor(()=>expect(document.activeElement).toBe(input));expect(input.getAttribute('aria-invalid')).toBe('true');expect(input.closest('details')?.open).toBe(true);
});
it('rejects duplicate variant keys and limits before submitting',async()=>{
  const s=server();render(<EditingPanel campaignId="campaign-one" renders={renders}/>);await draft();
  fireEvent.click(screen.getByRole('button',{name:'Adicionar variante'}));fireEvent.change(screen.getByLabelText('Key da variante b'),{target:{value:'a'}});validate();
  await screen.findByRole('alert');expect(s.posts).toHaveLength(0);
});
it.each(['limit','blank','timing','id'])('rejects incomplete draft before POST: %s',async kind=>{
  const s=server();render(<EditingPanel campaignId="campaign-one" renders={renders}/>);await draft();
  if(kind==='limit')fireEvent.change(screen.getByLabelText('Limite de saídas'),{target:{value:'0'}});
  if(kind==='blank')fireEvent.change(screen.getByLabelText('Headline base'),{target:{value:' '}});
  if(kind==='timing')fireEvent.change(screen.getByLabelText('Timing: caminho relativo ao project root'),{target:{value:'timing.json'}});
  if(kind==='id')fireEvent.change(screen.getByLabelText('ID do vídeo editorial'),{target:{value:'../unsafe'}});
  validate();await screen.findByRole('alert');expect(s.posts).toHaveLength(0);
});
it.each([
  ['ID do vídeo editorial','../unsafe'],['Key da variante a','../unsafe'],['Nome da variante a',' '],
])('focuses the actual invalid input: %s',async(label,value)=>{
  const s=server();render(<EditingPanel campaignId="campaign-one" renders={renders}/>);await draft();
  const input=screen.getByLabelText(label);fireEvent.change(input,{target:{value}});validate();
  await waitFor(()=>expect(document.activeElement).toBe(input));expect(input.getAttribute('aria-invalid')).toBe('true');expect(s.posts).toHaveLength(0);
});
it('double click does not duplicate submissions',async()=>{
  const s=server({queued:true});render(<EditingPanel campaignId="campaign-one" renders={renders}/>);await draft();validate();validate();
  await screen.findByText(/Operação editorial na fila/);expect(s.posts).toHaveLength(1);
});
it.each([{wrongPersist:true},{wrongRender:true}])('rejects wrong operation identity %j',async options=>{
  server(options);render(<EditingPanel campaignId="campaign-one" renders={renders}/>);await draft();validate();
  await screen.findByText(/Resultado editorial desconhecido/);expect(save().matches(':disabled')).toBe(true);
});
it.each([{saveHash:true},{getDivergent:true}])('rejects divergent save confirmation %j',async options=>{
  server(options);render(<EditingPanel campaignId="campaign-one" renders={renders}/>);await draft();validate();await screen.findByText(/Plano validado\./);
  fireEvent.click(save());await screen.findByText(/Resultado editorial desconhecido/);expect(screen.queryByText(/Plano salvo e confirmado/)).toBeNull();
});
it('upstream changed during GET cannot unlock save',async()=>{
  let finish!:(r:Response)=>void;const delay=new Promise<Response>(resolve=>{finish=resolve;});const s=server({delay});
  const view=render(<EditingPanel campaignId="campaign-one" renders={renders}/>);await draft();validate();await screen.findByText(/Operação editorial na fila/);
  view.rerender(<EditingPanel campaignId="campaign-one" renders={{...renders,data:{items:[]}}}/>);
  await act(async()=>finish(Response.json({jobId:'edit-job',campaignId:'campaign-one',operation:'edit_plan',status:'completed',errorCode:null,
    result:{operation:'edit_plan',persist:false,plan:editPlanFixture(s.posts[0].request)}})));
  await screen.findByText(/Resultado editorial desconhecido/);expect(save().matches(':disabled')).toBe(true);
});
it('readonly saved plan does not overwrite draft',async()=>{
  const s=server();render(<EditingPanel campaignId="campaign-one" renders={renders}/>);await draft();validate();await screen.findByText(/Plano validado\./);
  fireEvent.click(save());await screen.findByText(/Plano salvo e confirmado/);
  fireEvent.change(screen.getByLabelText('Headline base'),{target:{value:'Unsaved draft'}});
  fireEvent.change(screen.getByLabelText('Consultar plano salvo'),{target:{value:`${s.saved[0].videoId}/${s.saved[0].planHash}`}});
  await screen.findByText('Base');expect((screen.getByLabelText('Headline base') as HTMLInputElement).value).toBe('Unsaved draft');expect(save().matches(':disabled')).toBe(true);
});
