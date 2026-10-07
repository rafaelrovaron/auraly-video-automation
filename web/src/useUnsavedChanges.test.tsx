import {act,fireEvent,render} from '@testing-library/react';
import {expect,it,vi} from 'vitest';
import {useUnsavedChanges} from './useUnsavedChanges';

function Draft({dirty=true}:{dirty?:boolean}){useUnsavedChanges(dirty);return <input defaultValue="Keep draft"/>;}
it('refused browser Back restores hash before route listeners can unmount drafts',()=>{
  window.history.replaceState(null,'','#/campaigns/campaign-one');
  vi.spyOn(window,'confirm').mockReturnValue(false);
  render(<Draft/>);
  const navigate=vi.fn();window.addEventListener('hashchange',navigate);
  try{
    window.history.replaceState(null,'','#/campaigns');
    fireEvent(window,new HashChangeEvent('hashchange'));
    expect(window.location.hash).toBe('#/campaigns/campaign-one');expect(navigate).not.toHaveBeenCalled();
  }finally{window.removeEventListener('hashchange',navigate);}
});
it.each(['click','popstate'])('another draft refusal does not advance the first guard: %s',async kind=>{
  window.history.replaceState(null,'','#/campaigns/campaign-one');
  const confirm=vi.spyOn(window,'confirm').mockReturnValueOnce(true).mockReturnValueOnce(false).mockReturnValue(false);
  const view=render(<><Draft/><Draft/><a href="#/profiles">Profiles</a></>);
  if(kind==='click')fireEvent.click(view.getByText('Profiles'));
  else{window.history.replaceState(null,'','#/profiles');fireEvent(window,new PopStateEvent('popstate'));}
  await act(async()=>{});
  view.rerender(<><Draft/><Draft dirty={false}/><a href="#/profiles">Profiles</a></>);
  confirm.mockClear();window.history.replaceState(null,'','#/profiles');fireEvent(window,new PopStateEvent('popstate'));
  expect(confirm).toHaveBeenCalledTimes(1);expect(window.location.hash).toBe('#/campaigns/campaign-one');
});
it('refused popstate restores hash before the subsequent hashchange',()=>{
  window.history.replaceState(null,'','#/campaigns/campaign-one');
  vi.spyOn(window,'confirm').mockReturnValue(false);render(<Draft/>);
  window.history.replaceState(null,'','#/campaigns');
  fireEvent(window,new PopStateEvent('popstate'));
  expect(window.location.hash).toBe('#/campaigns/campaign-one');
});
