export type Filters = {q:string; status:string; type:string; market_id?:string; run_id?:string; segment?:string; contact?:''|'email'|'procurement'; sort?:'newest'|'procurement'|'email'|'updated'};
export function filtersQuery(filters:Filters,page?:number) { const query = new URLSearchParams(Object.entries(filters).filter((entry):entry is [string,string]=>typeof entry[1] === 'string' && entry[1].trim() !== '')); if(page !== undefined) {query.set('page',String(page));query.set('page_size','50');}return query.toString(); }
export function safeUrl(value?:string|null) { try {const url = new URL(value || ''); return ['http:','https:'].includes(url.protocol) ? url.href : undefined;} catch {return undefined;} }
export function runActions(status:string,coverage?:{capability_exhausted?:boolean;retryable?:boolean}):string[] { const actions=['paused','blocked'].includes(status) ? ['resume','cancel'] : ['failed','partial'].includes(status) ? ['resume'] : ['running','queued'].includes(status) ? ['pause','cancel'] : []; return coverage?.capability_exhausted&&coverage.retryable===false?actions.filter(action=>action!=='resume'):actions; }
export const labels:Record<string,string> = {queued:'排队中',running:'运行中',paused:'已暂停',completed:'已完成',partial:'部分完成',blocked:'需要处理',failed:'失败',cancelled:'已取消',candidate:'候选企业',verified:'符合目标客户',not_fit:'不符合目标客户',do_not_contact:'拒绝联系',wholesale:'批发商',retail:'零售商',import:'进口贸易商',manufacturer:'制造商',pending:'待审核',accepted:'已接受',rejected:'已拒绝',manual:'人工录入',rule:'规则提取',discovery:'市场检索',refresh:'资料复核',confirmed:'已确认',unknown:'待核实',department:'部门入口',company_only:'仅企业入口',person:'人员',explicit:'证据明确',purchasing:'采购岗位',other:'其他',email:'公开邮箱',supplier_form:'供应商入口',contact_form:'联系表单',name:'企业名称',website:'官方网站',phone:'联系电话',address:'地址',business_types:'业务类型',notes:'跟进备注',status:'客户匹配状态'};
Object.assign(labels,{auto_recorded:'自动收录',role:'岗位',role_status:'岗位身份依据',association_status:'邮箱归属依据',kind:'联系方式类型',evidence:'来源原文',operating_status:'营业状态',permanently_closed:'来源标记永久停业',temporarily_closed:'来源标记暂时停业',operational:'来源显示营业',auto_separate:'已自动区分门店',market:'目标市场',new:'本轮新增',existing:'存量企业',changed:'资料变化',not_seen:'未再次找到',accept:'已接受',reject:'已拒绝',superseded:'已被新证据替代',procurement:'采购入口',general:'通用入口',historical:'历史任职',same_block:'同一资料区块对应',wholesaler:'批发商',retailer:'零售商',importer:'进口商',distributor:'分销商',maps:'Maps 实际发现',website:'官网观察',contact:'联系人证据',field_update:'字段变化'});
Object.assign(labels,{auto_closed:'已自动关闭',skipped_legacy_query:'已按新规则跳过（与关键词无关）',
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

Object.assign(labels,{social:'社媒补充',manual_checked_at:'人工确认时间',last_checked_at:'官网采集时间',excluded:'不采用',automatic:'自动收录',accepted:'已采用'});
