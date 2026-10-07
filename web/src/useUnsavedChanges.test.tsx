import {fireEvent,render} from '@testing-library/react';
import {expect,it,vi} from 'vitest';
import {useUnsavedChanges} from './useUnsavedChanges';

function Draft(){useUnsavedChanges(true);return <input defaultValue="Keep draft"/>;}
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
it('refused popstate restores hash before the subsequent hashchange',()=>{
  window.history.replaceState(null,'','#/campaigns/campaign-one');
  vi.spyOn(window,'confirm').mockReturnValue(false);render(<Draft/>);
  window.history.replaceState(null,'','#/campaigns');
  fireEvent(window,new PopStateEvent('popstate'));
  expect(window.location.hash).toBe('#/campaigns/campaign-one');
});
