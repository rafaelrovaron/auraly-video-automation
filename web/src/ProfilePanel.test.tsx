import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { ProfilePanel } from './ProfilePanel';
import { profileFixture } from './profileTestSupport';
import type { ProfileView } from './profileApi';

const root='/api/v1/editing/profiles';
function server(mode='ok') {
  const stored=new Map<string,ProfileView>([['plain/1',profileFixture()]]),posts:string[]=[];
  let queryMode='ok';
  vi.stubGlobal('fetch',async(path:string,opts:RequestInit)=>{
    const key=path.slice(root.length+1);
    if(opts.method==='POST') {
      posts.push(path); const profile=JSON.parse(opts.body as string);
      const identity=`${profile.profileId}/${profile.version}`;
      if(mode==='conflict') return Response.json({error:{code:'artifact_invalid'}},{status:422});
      if(mode!=='absent') stored.set(identity,{profile,profileHash:'b'.repeat(64)});
      return mode==='ok'?Response.json(stored.get(identity),{status:201}):Response.json({error:{code:'storage_unavailable'}},{status:503});
    }
    if(path===root)return Response.json({items:[...stored.values()]});
    if(queryMode==='failed')return Response.json({error:{code:'storage_unavailable'}},{status:503});
    return stored.has(key)?Response.json(stored.get(key)):Response.json({error:{code:'not_found'}},{status:404});
  });
  return {stored,posts,setQueryMode:(v:string)=>{queryMode=v;}};
}
async function select(key='plain/1') {
  await screen.findByRole('option',{name:/plain · v1/});
  fireEvent.change(screen.getByLabelText('Consultar profile e versão'),{target:{value:key}});
  await screen.findByRole('button',{name:'Criar nova versão'});
}
async function version() {
  await select(); fireEvent.click(screen.getByRole('button',{name:'Criar nova versão'}));
  fireEvent.change(screen.getByLabelText('Nome do profile'),{target:{value:'Changed'}});
  fireEvent.click(screen.getByRole('button',{name:'Salvar versão'}));
}
it('lists_and_versions_profiles',async()=>{
  const s=server(); render(<ProfilePanel/>); await version();
  await screen.findByText('Versão publicada e confirmada.');
  expect(s.posts).toEqual([root+'/plain/1/versions']);
  expect(s.stored.get('plain/1')?.profile.name).toBe('Plain');
  expect(s.stored.get('plain/2')?.profile.name).toBe('Changed');
});
it('unknown_queries_exact_version_without_repost',async()=>{
  const s=server('lost'); render(<ProfilePanel/>); await version();
  fireEvent.click(await screen.findByRole('button',{name:'Consultar versão enviada'}));
  await screen.findByText('Versão publicada e confirmada.'); expect(s.posts).toHaveLength(1);
});
it('failed_query_does_not_release_save',async()=>{
  const s=server('lost'); render(<ProfilePanel/>); await version();
  s.setQueryMode('failed'); fireEvent.click(await screen.findByRole('button',{name:'Consultar versão enviada'}));
  await screen.findByText(/storage_unavailable/);
  expect((screen.getByRole('button',{name:'Salvar versão'}) as HTMLButtonElement).disabled).toBe(true);
  expect(s.posts).toHaveLength(1);
});
it('not_found_retry_rechecks',async()=>{
  const s=server('absent'); render(<ProfilePanel/>); await version();
  fireEvent.click(await screen.findByRole('button',{name:'Consultar versão enviada'}));
  await screen.findByRole('button',{name:'Tentar novamente com o mesmo conteúdo'});
  const v=profileFixture();v.profile.version=2;v.profile.name='Other';s.stored.set('plain/2',v);
  fireEvent.click(screen.getByRole('button',{name:'Tentar novamente com o mesmo conteúdo'}));
  await screen.findByText(/A versão existente tem conteúdo diferente/);
  expect(s.posts).toHaveLength(1);expect(s.stored.get('plain/2')?.profile.name).toBe('Other');
});
it('conflict_never_overwrites',async()=>{
  const s=server('conflict');render(<ProfilePanel/>);await version();
  await screen.findByText(/artifact_invalid/);expect(s.posts).toHaveLength(1);
  expect(s.stored.get('plain/1')?.profile.name).toBe('Plain');
});
it('refresh_preserves_draft',async()=>{
  server();render(<ProfilePanel/>);await select();
  fireEvent.click(screen.getByRole('button',{name:'Criar nova versão'}));
  fireEvent.change(screen.getByLabelText('Nome do profile'),{target:{value:'Keep'}});
  fireEvent.click(screen.getByRole('button',{name:'Atualizar profiles'}));
  await waitFor(()=>expect((screen.getByLabelText('Nome do profile') as HTMLInputElement).value).toBe('Keep'));
});
it('discard_cancel_keeps_draft',async()=>{
  server();vi.spyOn(window,'confirm').mockReturnValue(false);render(<ProfilePanel/>);await select();
  fireEvent.click(screen.getByRole('button',{name:'Criar nova versão'}));
  fireEvent.change(screen.getByLabelText('Nome do profile'),{target:{value:'Keep'}});
  fireEvent.click(screen.getByRole('button',{name:'Novo profile'}));
  expect((screen.getByLabelText('Nome do profile') as HTMLInputElement).value).toBe('Keep');
});
it('published_content_is_readonly',async()=>{
  server();render(<ProfilePanel/>);await select();expect(screen.queryByRole('button',{name:'Salvar versão'})).toBeNull();
  expect(screen.getByLabelText('Nome do profile').matches(':disabled')).toBe(true);
});
it('late_get_does_not_replace_selection',async()=>{
  const a=profileFixture(),b=profileFixture();b.profile.profileId='other';b.profile.name='Other';
  let finish:(r:Response)=>void=()=>{};
  vi.stubGlobal('fetch',async(path:string)=>{
    if(path===root)return Response.json({items:[a,b]});
    if(path.endsWith('/plain/1'))return new Promise<Response>(resolve=>{finish=resolve;});
    return Response.json(b);
  });
  render(<ProfilePanel/>);await screen.findByRole('option',{name:/plain · v1/});
  fireEvent.change(screen.getByLabelText('Consultar profile e versão'),{target:{value:'plain/1'}});
  fireEvent.change(screen.getByLabelText('Consultar profile e versão'),{target:{value:'other/1'}});
  await screen.findByRole('button',{name:'Criar nova versão'});
  await act(async()=>{finish(Response.json(a));});
  expect((screen.getByLabelText('Nome do profile') as HTMLInputElement).value).toBe('Other');
});
it('successful_post_with_unconfirmed_get_stays_unknown',async()=>{
  let posts=0;
  vi.stubGlobal('fetch',async(path:string,opts:RequestInit)=>{
    if(path===root&&opts.method==='GET')return Response.json({items:[profileFixture()]});
    if(opts.method==='POST'){posts++;return Response.json({profile:JSON.parse(opts.body as string),profileHash:'b'.repeat(64)},{status:201});}
    if(path.endsWith('/plain/1'))return Response.json(profileFixture());
    return Response.json({error:{code:'not_found'}},{status:404});
  });
  render(<ProfilePanel/>);await version();
  await screen.findByRole('button',{name:'Consultar versão enviada'});
  expect((screen.getByRole('button',{name:'Salvar versão'}) as HTMLButtonElement).disabled).toBe(true);
  expect(posts).toBe(1);
});
it('creates_new_profile_without_campaign',async()=>{
  const s=server();render(<ProfilePanel/>);await screen.findByRole('option',{name:/plain · v1/});
  fireEvent.click(screen.getByRole('button',{name:'Novo profile'}));
  fireEvent.change(screen.getByLabelText('ID do profile'),{target:{value:'organic'}});
  fireEvent.change(screen.getByLabelText('Nome do profile'),{target:{value:'Organic'}});
  fireEvent.click(screen.getByRole('button',{name:'Salvar versão'}));
  await screen.findByText('Versão publicada e confirmada.');
  expect(s.posts).toEqual([root]);expect(s.stored.get('organic/1')?.profile.version).toBe(1);
});
