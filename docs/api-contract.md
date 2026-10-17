# UI/API contract

> 历史参考：本文为早期契约，部分路由和行为已变化。当前协议见 [技术文档/API 协议](technical/api-contract.md)，接口快照见 [OpenAPI](technical/openapi.current.json)。

Base `/api`. JSON responses. Error body `{detail: string}`. Arrays returned directly unless described.

`GET /api/session` -> `{token: string}`. Frontend fetch with same-origin credentials, use `X-Local-Token` for ALL other API requests. Server validates Origin (same host) and Host; session route rejects cross-site fetch. No account login.

`GET /api/overview` -> `{companies,contacts,tasks,pending_reviews,running_runs,source_status:{maps:{available,message},website:{available,message}}}`.

`GET /api/regions?parent_id=` -> `[{id,name,parent_id,kind}]`. Without parent returns countries. Include USA/California/Los Angeles/San Francisco/New York/NYC initially; custom region task fields allowed.

`GET /api/tasks` -> `[{id,name,country,state,city,keywords: string[],company_types: string[],limit,auto_enrich,created_at,latest_run: Run|null}]`.
`POST /api/tasks` body `{name,country:"US",state,city,keywords: string[],company_types:string[],limit:20,auto_enrich:true}` -> task.
`POST /api/tasks/{id}/runs` -> Run.
`GET /api/runs` -> Run[]. `GET /api/runs/{id}` -> Run.
Run `{id,task_id,kind,status,progress:{done,total,discovered},message,created_at,finished_at,error}`.
`POST /api/runs/{id}/{pause|resume|cancel}` -> Run. Statuses queued/running/paused/completed/partial/blocked/failed/cancelled.

`GET /api/companies?q=&status=&type=` -> Company[] (up to 500).
Company `{id,name,address,phone,website,city,country,business_types:string[],status,notes,revision,created_at,last_checked_at,contact_count}`. Status candidate/verified/not_fit/do_not_contact.
`GET /api/companies/{id}` -> `{company:Company,contacts:Contact[],observations:Observation[],changes:Change[],social_links:string[]}`.
`PATCH /api/companies/{id}` body `{revision,fields:{name?,address?,phone?,website?},notes?,status?}` -> Company.
`POST /api/companies/{id}/refresh` -> Run (website enrichment).
`POST /api/companies` body `{name,website,city?,country?}` -> Company. Mark manual record explicitly; no invented discovery provenance.
Contact `{id,name,role,email,url,kind,role_status,association_status,source_url,evidence,observed_at,review_status}`.
Observation `{id,field,value,source_url,evidence,observed_at,method}`.
Change `{id,company_id,company_name,field,old_value,new_value,created_at,reason}`.

`GET /api/reviews` -> `[{id,company_id,company_name,kind,field,old_value,new_value,source_url,evidence,status,created_at}]`.
`POST /api/reviews/{id}/decision` body `{decision:"accept"|"reject"}` -> `{ok:true}`.
`GET /api/changes` -> Change[].

`GET /api/schedules` -> `[{id,task_id,company_id,kind,interval_months,timezone,next_due_at,enabled}]`.
`POST /api/schedules` body `{task_id?:string,company_id?:string,kind:"discovery"|"refresh",interval_months:3,timezone:"Asia/Shanghai",enabled:true}` -> schedule. Exactly one target.
`PATCH /api/schedules/{id}` body `{enabled:boolean}` -> schedule.

`GET /api/settings` -> `{data_dir,version,network_mode:"direct-public",paid_services:false,ai_enabled:false}`.
`POST /api/backups` -> `{name,created_at}`; `GET /api/backups` -> list; `POST /api/backups/restore` body `{name}` -> `{ok:true}`; application refuses restore while active runs exist.
`GET /api/exports?format=csv|xlsx&q=&status=&type=` -> download; browser use authenticated fetch then blob. CSV contains companies; XLSX companies/contacts/locations; do_not_contact excluded by default.

UI fully Chinese, clean desktop workspace, navigable details and readable evidence. Empty production database initially. User-visible distinction: actual discovered records vs manual entries; role verification vs email presence; no data vs failed source.
