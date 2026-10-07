import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { ProfileForm } from './ProfileForm';
import { profileFixture } from './profileTestSupport';

function form(readOnly=false) {
  const onSubmit=vi.fn(),onDirtyChange=vi.fn();
  render(<ProfileForm initial={profileFixture().profile} creating={true} readOnly={readOnly} disabled={false} onSubmit={onSubmit} onDirtyChange={onDirtyChange}/>);
  return {onSubmit,onDirtyChange};
}
const fill=(label:string,value:string)=>fireEvent.change(screen.getByLabelText(label),{target:{value}});
const save=()=>fireEvent.submit(screen.getByRole('form',{name:'Configuração do profile'}));
it('submits_all_style_fields',()=>{
  const {onSubmit}=form(); save(); expect(onSubmit).toHaveBeenCalledWith(profileFixture().profile);
});
it('preserves_disabled_values_and_alpha',()=>{
  const {onSubmit,onDirtyChange}=form();
  fill('Headline · Cor (RGB/RGBA)','#FFFFFF80'); fill('Música · Volume (dB)','0');
  fireEvent.click(screen.getByLabelText('Música · Loop')); save();
  expect(onDirtyChange).toHaveBeenCalledWith(true);
  expect(onSubmit.mock.calls[0][0].defaults).toMatchObject({headline:{enabled:false,color:'#FFFFFF80'},music:{volumeDb:0,loop:false}});
});
it('blank_required_number_is_not_zero',()=>{
  const {onSubmit}=form(); fill('Headline · Tamanho (px)',''); save();
  expect(onSubmit).not.toHaveBeenCalled(); expect(screen.getByRole('alert')).toBeTruthy();
});
it('nullable_empty_is_null',()=>{
  const {onSubmit}=form(); fill('Headline · Fim (s)','2'); fill('Headline · Fim (s)',''); save();
  expect(onSubmit.mock.calls[0][0].defaults.headline.endSec).toBeNull();
});
it('rejects_partial_asset_reference',()=>{
  const {onSubmit}=form(); fill('Headline · Fonte: caminho relativo','fonts/plain.ttf'); save();
  expect(onSubmit).not.toHaveBeenCalled();
});
it.each([['Headline · X (0–1)','1.1'],['Headline · Margem esquerda (0–1)','0.95'],
  ['Enquadramento · Escala (1–1.25)','1.3'],['Headline · Início (s)','-1'],['Música · Fade-in (s)','-1']])('rejects limits %s',(label,value)=>{
  const {onSubmit}=form(); fill(label,value); save(); expect(onSubmit).not.toHaveBeenCalled();
});
it('readonly_cannot_submit',()=>{const {onSubmit}=form(true); save(); expect(onSubmit).not.toHaveBeenCalled();});
it('opens_advanced_section_and_focuses_first_invalid_field',async()=>{
  const {onSubmit}=form(); fill('Headline · Peso (100–900)','999'); save();
  const field=screen.getByLabelText('Headline · Peso (100–900)');
  await new Promise(resolve=>requestAnimationFrame(resolve));
  expect(document.activeElement).toBe(field);
  expect(field.closest('details')?.open).toBe(true);
  expect(screen.getByRole('alert').textContent).toContain('Headline · Peso (100–900)');
  expect(onSubmit).not.toHaveBeenCalled();
  fill('Headline · Peso (100–900)','700');save();
  expect(onSubmit).toHaveBeenCalled();expect(field.hasAttribute('aria-invalid')).toBe(false);
});
