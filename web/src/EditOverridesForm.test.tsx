import {useState} from 'react';
import {fireEvent,render,screen} from '@testing-library/react';
import {expect,it} from 'vitest';
import {EditOverridesForm,newOverrideDraft,readOverrides} from './EditOverridesForm';
import type {FieldDraft} from './EditOverridesForm';
import {PROFILE} from './profileTestSupport';
const hints={...PROFILE.profile.defaults,headline:{...PROFILE.profile.defaults.headline,text:'Base'}};
function Harness({disabled=false}:{disabled?:boolean}){
  const [draft,setDraft]=useState(newOverrideDraft());
  return <><EditOverridesForm draft={draft} inherited={hints} label="Camada" disabled={disabled} onChange={setDraft}/>
    <output data-testid="result">{JSON.stringify(readOverrides(draft))}</output></>;
}
const result=()=>JSON.parse(screen.getByTestId('result').textContent!);
it('omits inherited fields',()=>expect(readOverrides(newOverrideDraft()).overrides).toEqual({}));
it('preserves false zero and alpha through native controls',()=>{
  render(<Harness/>);
  for(const name of ['Música · volumeDb','Música · loop','Headline · color'])fireEvent.change(screen.getByLabelText(`Camada · ${name} · Modo`),{target:{value:'replace'}});
  fireEvent.change(screen.getByLabelText('Camada · Música · volumeDb'),{target:{value:'0'}});
  fireEvent.click(screen.getByLabelText('Camada · Música · loop'));
  fireEvent.change(screen.getByLabelText('Camada · Headline · color'),{target:{value:'#FFFFFF80'}});
  expect(result().overrides).toEqual({headline:{color:'#FFFFFF80'},music:{volumeDb:0,loop:false}});
});
it('clear is not inherit',()=>{
  render(<Harness/>);const mode=screen.getByLabelText('Camada · Música · asset · Modo');
  fireEvent.change(mode,{target:{value:'clear'}});expect(result().overrides).toEqual({music:{asset:null}});
  fireEvent.change(mode,{target:{value:'inherit'}});expect(result().overrides).toEqual({});
  expect(screen.getByLabelText('Camada · Headline · fontSizePx · Modo').querySelector('[value="clear"]')).toBeNull();
});
it('roundtrips every existing style field without inventing nullable values',()=>{
  const draft=newOverrideDraft();
  for(const section of ['output','headline','captions','music','framing'] as const){
    for(const [key,value] of Object.entries(hints[section])){
      const field=(draft[section] as Record<string,FieldDraft>)[key];
      field.mode=value===null?'clear':'replace';field.value=typeof value==='boolean'?value:String(value??'');
    }
  }
  expect(readOverrides(draft).overrides).toEqual(hints);
});
it.each(['','NaN','Infinity','-1'])('rejects invalid required number %s',value=>{
  const draft=newOverrideDraft();draft.headline.fontSizePx={mode:'replace',value};
  expect(readOverrides(draft)).toEqual({overrides:null,invalidField:'headline.fontSizePx'});
});
it('rejects partial assets and unsupported clear',()=>{
  const draft=newOverrideDraft();draft.music.asset={mode:'replace',value:{path:'music/a.wav',sha256:''}};
  expect(readOverrides(draft).invalidField).toBe('music.asset');
  draft.music.asset.mode='inherit';draft.music.loop.mode='clear';expect(readOverrides(draft).invalidField).toBe('music.loop');
});
it('has accessible disabled controls and no nested form',()=>{
  const {container}=render(<Harness disabled/>);
  expect(screen.getByLabelText('Camada · Headline · Texto · Modo').matches(':disabled')).toBe(true);
  expect(container.querySelector('form')).toBeNull();
});
