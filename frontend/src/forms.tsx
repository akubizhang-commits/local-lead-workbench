import {useId,useState,type FormEvent} from 'react';
import {api} from './api';
import {Field,Alert} from './ui';
import {StatusOptions} from './CompanyStatus';
import {businessChoices} from './companyEdits';
import {label} from './helpers';
import {RegionFields} from './RegionFields';
import {MarketSocialOptions,newMarketSocialSettings} from './MarketSocialSettings';
import type {Company,Task} from './types';
import type {FieldError} from './validation';
const text=(form:FormData,key:string)=>String(form.get(key)||'').trim();
interface FormFailure {message:string;fields:FieldError[]}
const noFailure:FormFailure={message:'',fields:[]};
const failure=(error:unknown):FormFailure=>({message:(error as Error).message,fields:(error as {fields?:FieldError[]}).fields??[]});
/** Top-of-form alert; several field errors are listed one per line. */
function FormError({id,error}:{id:string;error:FormFailure}) {
  if(!error.message)return null;
  return <div id={id}><Alert>{error.fields.length>1?<ul>{error.fields.map(f=><li key={f.field+f.message}>「{f.label}」：{f.message}</li>)}</ul>:error.message}</Alert></div>;
}
const invalid=(error:FormFailure,field:string,id:string)=>error.fields.some(f=>f.field===field)?{'aria-invalid':true as const,'aria-describedby':id}:{};

export function TaskForm({onDone,onCancel}:{onDone:(task:Task)=>void;onCancel:()=>void}) {
  const [error,setFailure]=useState<FormFailure>(noFailure);
  const setError=(message:string)=>setFailure({message,fields:[]});
  const errorId=useId();
  const [busy,setBusy]=useState(false);
  const [social,setSocial]=useState(newMarketSocialSettings);
  async function submit(event:FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const form=new FormData(event.currentTarget);
    if (text(form,'county_id')&&!text(form,'place_id')) {
      setError('请选择所属地区内的有效城市或自治市');
      return;
    }
    const keywords=text(form,'keywords').split(/[,，\n]+/).map(value=>value.trim()).filter(Boolean);
    if (!keywords.length&&!form.getAll('company_types').length) {
      setFailure({message:'请选择目标企业类型或填写关键词',fields:[]});
      return;
    }
    if(social.auto_social&&!social.social_platforms.length){
      setError('请至少选择一个社媒平台');return;
    }
    const identifiers=Object.fromEntries(['state_id','county_id','place_id','county']
      .map(key=>[key,text(form,key)]).filter(([,value])=>value));
    setBusy(true);setError('');
    try {
      onDone(await api<Task>('/tasks','POST',{
        country:text(form,'country'),
        state:text(form,'canonical_state'),city:text(form,'canonical_city'),
        ...identifiers,keywords,company_types:form.getAll('company_types'),
        mode:'market',auto_enrich:form.has('auto_enrich'),...social,
      }));
    } catch(error) {setFailure(failure(error));}
    finally {setBusy(false);}
  }
  return <form onSubmit={submit}>
    <div className="form-body">
      <FormError id={errorId} error={error}/>
      <div className="form-grid">
        <p className="hint wide">任务名称将根据地区、企业类型和关键词自动生成。</p>
        <RegionFields/>
        <fieldset className="wide" {...invalid(error,'company_types',errorId)}><legend>目标企业类型</legend><div className="checks">
          {[['wholesale','批发商'],['retail','零售商'],['import','进口贸易商']].map(([value,title])=>
            <label key={value}><input type="checkbox" name="company_types" value={value} defaultChecked={value==='wholesale'}/>{title}</label>)}
        </div><p className="hint">填写关键词时只按关键词检索，企业类型用于命名和筛选；不填关键词时，按所选企业类型使用服装行业默认检索词（如 clothing wholesaler）。</p></fieldset>
        <Field label="检索关键词 · 选填，逗号或换行分隔" wide><textarea name="keywords" rows={3} placeholder="metal roofing supply" {...invalid(error,'keywords',errorId)}/></Field>
      </div>
      <label className="check-line"><input type="checkbox" name="auto_enrich" defaultChecked/>发现后自动读取官网，补全公开联系方式</label>
      <MarketSocialOptions value={social} onChange={setSocial} disabled={busy}/>
      <div className="hint">企业类型与关键词至少填写一项。地区查询采用近似范围；检索完成不表示已覆盖全部企业。未再次找到的记录仍保留在市场中。</div>
    </div>
    <footer><button type="button" onClick={onCancel}>取消</button><button className="primary" disabled={busy}>{busy?'保存中…':'创建市场'}</button></footer>
  </form>;
}
export function CompanyForm({company,onDone,onCancel}:{company?:Company;onDone:(company:Company)=>void;onCancel:()=>void}) {const [busy,setBusy]=useState(false);const [error,setError]=useState<FormFailure>(noFailure);const errorId=useId();const bad=(field:string)=>invalid(error,field,errorId);async function submit(e:FormEvent<HTMLFormElement>){e.preventDefault();const f=new FormData(e.currentTarget);setBusy(true);setError(noFailure);try{const body=company?{revision:company.revision,fields:{name:text(f,'name'),website:text(f,'website'),address:text(f,'address'),phone:text(f,'phone')},notes:text(f,'notes'),status:text(f,'status'),business_types:f.getAll('business_types').map(String)}:{name:text(f,'name'),website:text(f,'website'),city:text(f,'city'),country:text(f,'country')};onDone(await api<Company>(company?`/companies/${encodeURIComponent(company.id)}`:'/companies',company?'PATCH':'POST',body));}catch(e){const result=failure(e);setError({...result,message:`${result.message}${(e as {status?:number}).status===409?'。请关闭表单并刷新详情后重新编辑，避免覆盖新修订。':''}`});}finally{setBusy(false);}}
return <form onSubmit={submit}><div className="form-body"><div className="hint">{company?'人工修正随修订号保存，采集更新不会直接覆盖人工确认值。':'此记录将明确标记为人工录入，不生成自动发现来源。'}</div><FormError id={errorId} error={error}/><div className="form-grid"><Field label="企业名称" wide><input autoFocus name="name" defaultValue={company?.name} required maxLength={200} {...bad('name')}/></Field><Field label="官方网站" wide><input name="website" type="url" pattern="https?://.*" defaultValue={company?.website} placeholder="https://" {...bad('website')}/></Field>{company?<><Field label="地址" wide><input name="address" defaultValue={company.address||''} {...bad('address')}/></Field><Field label="联系电话"><input name="phone" defaultValue={company.phone||''} {...bad('phone')}/></Field><Field label="客户匹配状态"><select name="status" defaultValue={company.status} {...bad('status')}><StatusOptions/></select></Field><fieldset className="wide" {...bad('business_types')}><legend>业务类型 · 可多选</legend><div className="checks">{businessChoices(company.business_types).map(value=><label key={value}><input type="checkbox" name="business_types" value={value} defaultChecked={company.business_types.includes(value)}/>{label(value)}</label>)}</div><p className="muted">按已确认的业务选择；清空表示尚未确定。人工修改会保留记录。</p></fieldset><Field label="跟进备注" wide><textarea name="notes" rows={4} defaultValue={company.notes||''} {...bad('notes')}/></Field></>:<><Field label="城市"><input name="city" {...bad('city')}/></Field><Field label="国家代码"><input name="country" defaultValue="US" {...bad('country')}/></Field></>}</div></div><footer><button type="button" onClick={onCancel}>取消</button><button className="primary" disabled={busy}>{busy?'保存中…':company?'保存修正':'保存人工记录'}</button></footer></form>}
