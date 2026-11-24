import {el} from './dom.js';
import {status} from './widgets.js';

export const write=role=>role==='operator'||role==='reviewer';
export function field(name,label,attrs={}) {
  return el('label',{},el('span',{},label),el('input',{name,required:'',...attrs}));
}
export function choices(name,label,rows,selected,multiple=false) {
  const select=el('select',{name,required:''},rows.map(([value,text])=>
    el('option',{value,selected:(multiple?(selected||[]):[selected]).includes(value)?'':undefined},text)));
  if(multiple){select.multiple=true;select.size=Math.min(6,rows.length||1);}
  return el('label',{},el('span',{},label),select);
}
export function run(id,fields,text,action) {
  const button=el('button',{type:'submit'},text);
  const notice=status('');
  const form=el('form',{id},fields,button,notice);
  form.addEventListener('submit',async event=>{
    event.preventDefault();
    notice.textContent='';
    button.disabled=true;
    try {
      const message=await action(new FormData(form));
      notice.className='status ok';
      notice.textContent=message || '';
    } catch(error) {
      notice.className='status error';
      notice.textContent=error.message;
    } finally {button.disabled=false;}
  });
  return form;
}
