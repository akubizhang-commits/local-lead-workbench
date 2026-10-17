export function batchStatusPayload(companies:ReadonlyArray<{id:string;revision:number}>,status:string){
 if(!['candidate','verified','not_fit','do_not_contact'].includes(status))throw new Error('无效客户匹配状态');
 if(!companies.length)throw new Error('请先选择企业');
 const unique=new Map(companies.map(c=>[c.id,{id:c.id,revision:c.revision}]));
 return {companies:[...unique.values()],status};
}
export const businessTypeOptions=['wholesaler','retailer','importer','manufacturer','distributor'];
export function businessChoices(existing:string[]){return [...new Set([...businessTypeOptions,...existing])];}
