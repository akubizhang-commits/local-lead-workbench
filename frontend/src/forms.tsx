import {useEffect,useId,useRef,useState,type FormEvent} from 'react';
import {api} from './api';
import {actionMessage} from './errors';
import {Field,Alert} from './ui';
import {StatusOptions} from './CompanyStatus';
import {businessChoices} from './companyEdits';
import {label} from './helpers';
import {RegionFields} from './RegionFields';
import {MarketSocialOptions,newMarketSocialSettings} from './MarketSocialSettings';
import type {Company,IndustryPreset,Task} from './types';
import {useResource} from './hooks';
import {QueryPreview,presetTerms} from './QueryPreview';
import type {FieldError} from './validation';
const text=(form:FormData,key:string)=>String(form.get(key)||'').trim();
interface FormFailure {message:string;fields:FieldError[]}
const noFailure:FormFailure={message:'',fields:[]};
const failure=(error:unknown):FormFailure=>({message:actionMessage(error),fields:(error as {fields?:FieldError[]}).fields??[]});
/** Top-of-form alert; several field errors are listed one per line. */
function FormError({id,error}:{id:string;error:FormFailure}) {
  if(!error.message)return null;
  return <div id={id}><Alert>{error.fields.length>1?<ul>{error.fields.map(f=><li key={f.field+f.message}>「{f.label}」：{f.message}</li>)}</ul>:error.message}</Alert></div>;
}
const invalid=(error:FormFailure,field:string,id:string)=>error.fields.some(f=>f.field===field)?{'aria-invalid':true as const,'aria-describedby':id}:{};

export const KEYWORDS_REQUIRED='请填写检索关键词，或从预设行业中选择一个';
const splitKeywords=(value:string)=>value.split(/[,，\n]+/).map(item=>item.trim()).filter(Boolean);

/** Keyword textarea plus preset select; shared by the new-market form and the market edit dialog. */
export function KeywordFields({industry,onIndustry,companyTypes,keywords,onKeywords,invalidProps={}}:{
  industry:string;onIndustry:(value:string)=>void;companyTypes:string[];
  keywords?:string;onKeywords?:(value:string)=>void;invalidProps?:Record<string,unknown>;
}) {
  const presets=useResource<IndustryPreset[]>('/industry-presets');
  const preset=presets.data?.find(item=>item.id===industry);
  const terms=presetTerms(preset,companyTypes);
  return <>
    <Field label="检索关键词 · 必填，逗号或换行分隔" wide><textarea name="keywords" rows={3} placeholder="例如：metal roofing supply"
      {...(onKeywords?{value:keywords,onChange:event=>onKeywords(event.target.value)}:{})} {...invalidProps}/></Field>
    <Field label="或从预设行业选择" wide><select name="industry" value={industry} onChange={event=>onIndustry(event.target.value)}>
      <option value="">不使用预设</option>
      {(presets.data||[]).map(item=><option key={item.id} value={item.id}>{item.label}</option>)}
    </select>{presets.error&&<small className="muted">预设行业暂不可用：{presets.error}</small>}
    {!!terms.length&&<small>将使用检索词：{terms.join('、')}</small>}</Field>
  </>;
}

// The form's own window: keeps jsdom tests and the browser on the same constructor.
const formData=(form:HTMLFormElement)=>new (form.ownerDocument.defaultView?.FormData??FormData)(form);
/** The POST /tasks body of the new-market form. */
function taskBody(form:HTMLFormElement,industry:string):Record<string,unknown> {
  const data=formData(form);
  const keywords=splitKeywords(text(data,'keywords'));
  const state=text(data,'canonical_state'),city=text(data,'canonical_city');
  const identifiers=Object.fromEntries(['state_id','county_id','place_id','county']
    .map(key=>[key,text(data,key)]).filter(([,value])=>value));
  return {country:text(data,'country'),state,city,...identifiers,keywords,
    company_types:data.getAll('company_types').map(String),...(industry?{industry}:{}),mode:'market'};
}
/** Only a body with keywords (or a preset) and a region can be previewed; others go to server validation. */
const previewable=(body:Record<string,unknown>)=>
  ((body.keywords as string[]).length>0||!!body.industry)&&!!body.state&&!!body.city;

export function TaskForm({onDone,onCancel}:{onDone:(task:Task)=>void;onCancel:()=>void}) {
  const [error,setFailure]=useState<FormFailure>(noFailure);
  const setError=(message:string)=>setFailure({message,fields:[]});
  const errorId=useId();
  const [busy,setBusy]=useState(false);
  const [social,setSocial]=useState(newMarketSocialSettings);
  const [industry,setIndustry]=useState('');
  const [watched,setWatched]=useState<{body:Record<string,unknown>|null;types:string[]}>({body:null,types:['wholesale']});
  const [readyKey,setReadyKey]=useState<string|null>(null);
  const formRef=useRef<HTMLFormElement>(null);
  // Region fields keep their own state and update hidden inputs; watch the whole form.
  useEffect(()=>{
    const form=formRef.current;
    if(!form)return;
    const read=()=>{
      const full=taskBody(form,industry);
      const body=previewable(full)?full:null;
      const types=formData(form).getAll('company_types').map(String);
      setWatched(prev=>JSON.stringify(prev)===JSON.stringify({body,types})?prev:{body,types});
    };
    read();
    const observer=new (form.ownerDocument.defaultView?.MutationObserver??MutationObserver)(read);
    observer.observe(form,{subtree:true,childList:true,attributes:true,attributeFilter:['value','name']});
    form.addEventListener('input',read);form.addEventListener('change',read);
    return ()=>{observer.disconnect();form.removeEventListener('input',read);form.removeEventListener('change',read);};
  },[industry]);
  const previewKey=watched.body?JSON.stringify(watched.body):'';
  const ready=!watched.body||readyKey===previewKey;
  async function submit(event:FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const form=new FormData(event.currentTarget);
    if (text(form,'county_id')&&!text(form,'place_id')) {
      setError('请选择所属地区内的有效城市或自治市');
      return;
    }
    const keywords=splitKeywords(text(form,'keywords'));
    if (!keywords.length&&!industry) {
      setFailure({message:KEYWORDS_REQUIRED,fields:[{field:'keywords',label:'关键词',message:KEYWORDS_REQUIRED}]});
      return;
    }
    const body=taskBody(event.currentTarget,industry);
    if(previewable(body)&&JSON.stringify(body)!==readyKey){setError('请先等待检索预览');return;}
    if(social.auto_social&&!social.social_platforms.length){
      setError('请至少选择一个社媒平台');return;
    }
    setBusy(true);setError('');
    try {
      onDone(await api<Task>('/tasks','POST',{...body,auto_enrich:form.has('auto_enrich'),...social}));
    } catch(error) {setFailure(failure(error));}
    finally {setBusy(false);}
  }
  return <form ref={formRef} onSubmit={submit}>
    <div className="form-body">
      <FormError id={errorId} error={error}/>
      <div className="form-grid">
        <p className="hint wide">任务名称将根据地区、企业类型和关键词自动生成。</p>
        <RegionFields/>
        <fieldset className="wide" {...invalid(error,'company_types',errorId)}><legend>目标企业类型</legend><div className="checks">
          {[['wholesale','批发商'],['retail','零售商'],['import','进口贸易商']].map(([value,title])=>
            <label key={value}><input type="checkbox" name="company_types" value={value} defaultChecked={value==='wholesale'}/>{title}</label>)}
        </div><p className="hint">企业类型用于市场命名和筛选，不会自动生成检索词。</p></fieldset>
        <KeywordFields industry={industry} onIndustry={setIndustry} companyTypes={watched.types}
          invalidProps={invalid(error,'keywords',errorId)}/>
        <QueryPreview body={watched.body} onReady={setReadyKey}/>
      </div>
      <label className="check-line"><input type="checkbox" name="auto_enrich" defaultChecked/>发现后自动读取官网，补全公开联系方式</label>
      <MarketSocialOptions value={social} onChange={setSocial} disabled={busy}/>
      <div className="hint">关键词或预设行业至少选择一项。地区查询采用近似范围；检索完成不表示已覆盖全部企业。未再次找到的记录仍保留在市场中。</div>
    </div>
    <footer><button type="button" onClick={onCancel}>取消</button>{!ready&&<small className="muted">请先等待检索预览</small>}<button className="primary" disabled={busy||!ready}>{busy?'保存中…':'创建市场'}</button></footer>
  </form>;
}
export function CompanyForm({company,onDone,onCancel}:{company?:Company;onDone:(company:Company)=>void;onCancel:()=>void}) {const [busy,setBusy]=useState(false);const [error,setError]=useState<FormFailure>(noFailure);const errorId=useId();const bad=(field:string)=>invalid(error,field,errorId);async function submit(e:FormEvent<HTMLFormElement>){e.preventDefault();const f=new FormData(e.currentTarget);setBusy(true);setError(noFailure);try{const body=company?{revision:company.revision,fields:{name:text(f,'name'),website:text(f,'website'),address:text(f,'address'),phone:text(f,'phone')},notes:text(f,'notes'),status:text(f,'status'),business_types:f.getAll('business_types').map(String)}:{name:text(f,'name'),website:text(f,'website'),city:text(f,'city'),country:text(f,'country')};onDone(await api<Company>(company?`/companies/${encodeURIComponent(company.id)}`:'/companies',company?'PATCH':'POST',body));}catch(e){const result=failure(e);setError((e as {status?:number}).status===409?{...result,message:`${(e as Error).message}。请关闭表单并刷新详情后重新编辑，避免覆盖新修订。`}:result);}finally{setBusy(false);}}
return <form onSubmit={submit}><div className="form-body"><div className="hint">{company?'人工修正随修订号保存，采集更新不会直接覆盖人工确认值。':'此记录将明确标记为人工录入，不生成自动发现来源。'}</div><FormError id={errorId} error={error}/><div className="form-grid"><Field label="企业名称" wide><input autoFocus name="name" defaultValue={company?.name} required maxLength={200} {...bad('name')}/></Field><Field label="官方网站" wide><input name="website" type="url" pattern="https?://.*" defaultValue={company?.website} placeholder="https://" {...bad('website')}/></Field>{company?<><Field label="地址" wide><input name="address" defaultValue={company.address||''} {...bad('address')}/></Field><Field label="联系电话"><input name="phone" defaultValue={company.phone||''} {...bad('phone')}/></Field><Field label="客户匹配状态"><select name="status" defaultValue={company.status} {...bad('status')}><StatusOptions/></select></Field><fieldset className="wide" {...bad('business_types')}><legend>业务类型 · 可多选</legend><div className="checks">{businessChoices(company.business_types).map(value=><label key={value}><input type="checkbox" name="business_types" value={value} defaultChecked={company.business_types.includes(value)}/>{label(value)}</label>)}</div><p className="muted">按已确认的业务选择；清空表示尚未确定。人工修改会保留记录。</p></fieldset><Field label="跟进备注" wide><textarea name="notes" rows={4} defaultValue={company.notes||''} {...bad('notes')}/></Field></>:<><Field label="城市"><input name="city" {...bad('city')}/></Field><Field label="国家代码"><input name="country" defaultValue="US" {...bad('country')}/></Field></>}</div></div><footer><button type="button" onClick={onCancel}>取消</button><button className="primary" disabled={busy}>{busy?'保存中…':company?'保存修正':'保存人工记录'}</button></footer></form>}
