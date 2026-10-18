import {useState} from 'react';
import {ApiError, api} from './api';
import type {CompanyDetail} from './types';

export interface ManualContactValues {name:string;role:string;email:string;phone:string;url:string;procurement:boolean;note:string}
export const emptyManualContact=():ManualContactValues=>({name:'',role:'',email:'',phone:'',url:'',procurement:false,note:''});
type Errors=Partial<Record<keyof ManualContactValues|'channel',string>>;

/** Same shape rules as the API (ManualContactInput); the server still decides. */
export function validateManualContact(values:ManualContactValues):Errors {
 const errors:Errors={};
 const email=values.email.trim(),phone=values.phone.trim(),url=values.url.trim();
 if(values.name.trim().length>120)errors.name='不能超过 120 个字符';
 if(values.role.trim().length>120)errors.role='不能超过 120 个字符';
 if(email&&(email.length>254||!/^[^\s@<>]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}$/.test(email)))errors.email='邮箱格式无效';
 const digits=phone.replace(/\D/g,'');
 if(phone&&(phone.length>40||/[^0-9+\s().-]/.test(phone)||digits.length<7||digits.length>15))errors.phone='电话格式无效';
 if(url&&(url.length>2048||!/^https?:\/\/[^\s\\]+$/i.test(url)))errors.url='网址必须以 http:// 或 https:// 开头';
 if(values.note.length>1000)errors.note='不能超过 1000 个字符';
 if(!email&&!phone&&!url)errors.channel='请至少填写邮箱、电话或联系页面中的一项';
 return errors;
}

const FIELDS:[keyof ManualContactValues,string,string][]=[['name','姓名','text'],['role','岗位','text'],['email','邮箱','email'],['phone','电话','tel'],['url','联系页面','url']];

export function ManualContactForm({companyId,revision,onSaved,onCancel,onRefresh}:{companyId:string;revision:number;onSaved:(detail:CompanyDetail)=>void;onCancel:()=>void;onRefresh:()=>void}) {
 const [values,setValues]=useState(emptyManualContact);
 const [errors,setErrors]=useState<Errors>({});
 const [serverError,setServerError]=useState<{message:string;conflict:boolean}|null>(null);
 const [saving,setSaving]=useState(false);
 const set=(key:keyof ManualContactValues,value:string|boolean)=>setValues(v=>({...v,[key]:value}));
 async function submit(e:React.FormEvent) {
  e.preventDefault();
  const found=validateManualContact(values);
  setErrors(found);setServerError(null);
  if(Object.keys(found).length)return;
  setSaving(true);
  try {
   const detail=await api<CompanyDetail>(`/companies/${encodeURIComponent(companyId)}/contacts`,'POST',{revision,...values});
   setValues(emptyManualContact());
   onSaved(detail);
  } catch(error) {
   setServerError({message:error instanceof Error?error.message:'保存失败',conflict:error instanceof ApiError&&error.status===409});
  } finally {setSaving(false);}
 }
 const described=(key:string)=>errors[key as keyof Errors]?`manual-${key}-error`:undefined;
 return <form className="manual-contact-form" onSubmit={submit} noValidate aria-label="新增联系人">
  <p className="hint">人工录入的联系人来源标记为“人工”，之后的自动采集不会覆盖。</p>
  <div className="form-grid">
   {FIELDS.map(([key,title,type])=><label className="field" key={key}><span>{title}</span>
    <input type={type} value={String(values[key])} aria-invalid={!!errors[key]} aria-describedby={described(key)} onChange={e=>set(key,e.target.value)}/>
    {errors[key]&&<small className="field-error" id={`manual-${key}-error`}>{errors[key]}</small>}
   </label>)}
   <label className="check-line wide"><input type="checkbox" checked={values.procurement} onChange={e=>set('procurement',e.target.checked)}/>此人负责采购</label>
   <label className="field wide"><span>备注</span>
    <textarea value={values.note} rows={2} aria-invalid={!!errors.note} aria-describedby={described('note')} onChange={e=>set('note',e.target.value)}/>
    {errors.note&&<small className="field-error" id="manual-note-error">{errors.note}</small>}
   </label>
  </div>
  {errors.channel&&<p className="field-error" role="alert">{errors.channel}</p>}
  {serverError&&<div className="alert" role="alert">{serverError.message}{serverError.conflict&&<> <button type="button" onClick={onRefresh}>刷新</button></>}</div>}
  <div className="actions"><button type="button" onClick={onCancel}>取消</button><button className="primary" type="submit" disabled={saving}>保存联系人</button></div>
 </form>;
}
