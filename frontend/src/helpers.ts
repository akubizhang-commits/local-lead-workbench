export type Filters = {q:string; status:string; type:string; market_id?:string; run_id?:string; segment?:string; contact?:''|'email'|'procurement'; sort?:'newest'|'procurement'|'email'|'updated'|'tier'; category?:string[]; excluded?:''|'only'|'include'; best_tier?:string; actual_city?:string; nearby?:''|'only'|'hide'};
/** Query string for list/export URLs; array filters (category) repeat their key, empty values are dropped. */
export function filtersQuery(filters:Filters,page?:number) { const query = new URLSearchParams(); for(const [key,value] of Object.entries(filters)) {for(const item of Array.isArray(value)?value:[value]) if(typeof item === 'string' && item.trim() !== '') query.append(key,item);} if(page !== undefined) {query.set('page',String(page));query.set('page_size','50');}return query.toString(); }
/** Export download path with the current filters (category/excluded/best_tier included). */
export function exportUrl(filters:Filters,format:'csv'|'xlsx',layout:'entries'|'companies') { const query=new URLSearchParams(filtersQuery(filters)); query.set('format',format); query.set('layout',layout); return `/exports?${query}`; }
export function safeUrl(value?:string|null) { try {const url = new URL(value || ''); return ['http:','https:'].includes(url.protocol) ? url.href : undefined;} catch {return undefined;} }
export function runActions(status:string,coverage?:{capability_exhausted?:boolean;retryable?:boolean}):string[] { const actions=['paused','blocked'].includes(status) ? ['resume','cancel'] : ['failed','partial'].includes(status) ? ['resume'] : ['running','queued'].includes(status) ? ['pause','cancel'] : []; return coverage?.capability_exhausted&&coverage.retryable===false?actions.filter(action=>action!=='resume'):actions; }
export const labels:Record<string,string> = {queued:'排队中',running:'运行中',paused:'已暂停',completed:'已完成',partial:'部分完成',blocked:'需要处理',failed:'失败',cancelled:'已取消',candidate:'候选企业',verified:'符合目标客户',not_fit:'不符合目标客户',do_not_contact:'拒绝联系',wholesale:'批发商',retail:'零售商',import:'进口贸易商',manufacturer:'制造商',pending:'待审核',accepted:'已接受',rejected:'已拒绝',manual:'人工录入',rule:'规则提取',discovery:'市场检索',refresh:'资料复核',confirmed:'已确认',unknown:'待核实',department:'部门入口',company_only:'仅企业入口',person:'人员',explicit:'证据明确',purchasing:'采购岗位',other:'其他',email:'公开邮箱',supplier_form:'供应商入口',contact_form:'联系表单',name:'企业名称',website:'官方网站',phone:'联系电话',address:'地址',business_types:'业务类型',notes:'跟进备注',status:'客户匹配状态'};
Object.assign(labels,{auto_recorded:'自动收录',role:'岗位',role_status:'岗位身份依据',association_status:'邮箱归属依据',kind:'联系方式类型',evidence:'来源原文',operating_status:'营业状态',permanently_closed:'来源标记永久停业',temporarily_closed:'来源标记暂时停业',operational:'来源显示营业',auto_separate:'已自动区分门店',market:'目标市场',new:'本轮新增',existing:'存量企业',changed:'资料变化',not_seen:'未再次找到',accept:'已接受',reject:'已拒绝',superseded:'已被新证据替代',procurement:'采购入口',general:'通用入口',historical:'历史任职',same_block:'同一资料区块对应',wholesaler:'批发商',retailer:'零售商',importer:'进口贸易商',distributor:'分销商',maps:'Maps 实际发现',website:'官网观察',contact:'联系人证据',field_update:'字段变化'});
Object.assign(labels,{auto_closed:'已自动关闭',business_types_evidence:'业务类型线索（未采用）',auto_fill:'自动补全',skipped_legacy_query:'已按新规则跳过（与关键词无关）',
 browser_closed:'采集程序异常：地图页面被意外关闭',empty_output:'没有找到结果：采集程序正常结束但没有返回企业',
 nonzero_exit:'采集程序异常：采集程序异常退出',timeout:'采集程序异常：查询超时，结果不完整',
 inactivity_timeout:'采集程序异常：采集程序长时间无响应',adapter_error:'采集程序异常：采集程序报告查询失败',
 invalid_output:'采集程序异常：部分结果无法解析'});
/** Legacy English Maps messages fall back to the Chinese label of their stop reason. */
export function mapsReasonText(text:string|null|undefined,stopReason?:string|null):string {
 const value=text||'';
 if(!/[\u4e00-\u9fff]/.test(value)&&stopReason&&labels[stopReason])return labels[stopReason];
 return value;
}
export const label = (value?:string|null) => value ? labels[value] || value : '未记录';
export const date = (value?:string|null) => value ? new Date(value).toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}) : '尚未记录';
export const display = (value:unknown):string => value == null || value === '' ? '未记录' : typeof value === 'object' ? JSON.stringify(value) : String(value);
const CONTACT_KEYS:Record<string,string>={name:'姓名',role:'岗位',email:'邮箱',url:'链接',phone:'电话'};
const parsed=(value:unknown):unknown=>{if(typeof value!=='string')return value;try {return JSON.parse(value);} catch {return undefined;}};
/** Review / change values in Chinese (design §4.7); anything unreadable falls back to the raw text. */
export function fieldValue(field:string,value:unknown):string {
 if(value==null||value==='')return '未记录';
 if(field==='business_types'||field==='business_types_evidence'){
  const list=parsed(value);
  if(!Array.isArray(list))return display(value);
  return list.length?list.map(item=>label(String(item))).join('、'):'未记录';
 }
 if(field==='status')return label(String(value));
 if(field==='contact'||field==='contact_adoption'){
  const object=parsed(value);
  if(!object||typeof object!=='object'||Array.isArray(object))return display(value);
  const parts=Object.entries(object as Record<string,unknown>).filter(([,v])=>v!=null&&v!=='')
   .map(([key,v])=>`${CONTACT_KEYS[key]||label(key)} ${['role_status','association_status','kind','adoption_status'].includes(key)?label(String(v)):String(v)}`);
  return parts.length?parts.join(' · '):'未记录';
 }
 return display(value);
}

/** Entry tiers, best first (design §6.1); labels match the backend tier_label. */
export const TIERS:[string,string][]=[['named_procurement','具名采购'],['named_role','具名岗位'],['named_unknown','具名·岗位未明确'],['department_email','部门邮箱'],['general_email','通用邮箱'],['form_phone','表单 / 联系页面 / 电话'],['company_phone','仅企业电话'],['none','无可用入口']];
export const tierRank=(code?:string|null)=>{const index=TIERS.findIndex(([key])=>key===code);return index<0?null:index+1;};
export const tierLabel=(code?:string|null)=>TIERS.find(([key])=>key===code)?.[1]||'未记录';
/** Colour group by rank: 1–2 green, 3–4 blue, 5–6 grey, 7–8 light grey (label text always carries the meaning). */
export const tierClass=(rank?:number|null)=>`tier tier-${!rank?'none':rank<=2?'top':rank<=4?'mid':rank<=6?'low':'none'}`;
/** Business type from the market definition, not yet verified. */
export const hintLabel = (type:string) => `${label(type)} · 待核实`;
Object.assign(labels,{social:'社媒补充',manual_checked_at:'人工确认时间',last_checked_at:'官网采集时间',excluded:'不采用',automatic:'自动收录',accepted:'已采用'});
