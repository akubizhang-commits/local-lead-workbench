import {filtersQuery,type Filters} from './helpers.ts';
export type View='tasks'|'companies'|'detail'|'reviews'|'changes'|'settings';
export interface Route {view:View;selected:string|null;filters:Filters;page:number;account?:string;task?:string;run?:string;item?:string;activityStatus?:string;listRun?:string}
export const defaultFilters=():Filters=>({q:'',status:'',type:'',market_id:'',run_id:'',segment:'',contact:'',sort:'newest'});
export function parseRoute(value:string):Route {
 const params=new URL(value,'http://local.invalid').searchParams;
 const views:View[]=['tasks','companies','detail','reviews','changes','settings'];
 let view=views.includes(params.get('view') as View)?params.get('view') as View:'tasks';
 const selected=view==='detail'?(params.get('company')||null):null;
 if(view==='detail'&&!selected)view='companies';
 const filters=defaultFilters();
 for(const key of ['q','status','type','market_id','run_id','segment'] as const)filters[key]=params.get(key)||'';
 const contact=params.get('contact');if(contact==='email'||contact==='procurement')filters.contact=contact;
 const sort=params.get('sort');if(sort==='newest'||sort==='procurement'||sort==='email'||sort==='updated')filters.sort=sort;
 const page=Number(params.get('page')||1);
 const activity = view==='tasks' ? Object.fromEntries(['task','run','item','activityStatus','listRun'].filter(key=>params.get(key)).map(key=>[key,params.get(key)!])) : {};
 if(!activity.run)delete activity.item;
 if(activity.activityStatus&&!['pending','running','succeeded','partial','failed','blocked'].includes(activity.activityStatus))delete activity.activityStatus;
 return {...activity,view,selected,filters,page:Number.isSafeInteger(page)&&page>0?page:1,...(view==='detail'&&params.get('account')?{account:params.get('account')!}:{})};
}
export function routeUrl(route:Route){
 const query=new URLSearchParams(filtersQuery(route.filters));
 query.set('view',route.view);query.set('page',String(route.page));
 if(route.view==='detail'&&route.selected)query.set('company',route.selected);
 if(route.view==='detail'&&route.selected&&route.account)query.set('account',route.account);
 if(route.view==='tasks')for(const key of ['task','run','item','activityStatus','listRun'] as const)if(route[key])query.set(key,route[key]!);
 return `/?${query}`;
}
export function updateListFilters(route:Route,patch:Partial<Filters>):Route {
 return {...route,filters:{...route.filters,...patch},page:1};
}

export function openRefreshDetail(route:Route,item:{run_id:string;id:string}):Route {
 return {...route,listRun:route.run||'all',run:item.run_id,item:item.id};
}
export function backFromRefreshDetail(route:Route):Route {
 return {...route,item:undefined,run:route.listRun&&route.listRun!=='all'?route.listRun:undefined,listRun:undefined};
}
