export interface Incompleteness {incomplete?:boolean;incomplete_reason?:'geocode_failed'|'catalogue_no_bounds'|null;geometry_source?:string|null;geocoder_retry_at?:string|null;relocatable?:boolean;relocate_block_reason?:string|null;query_terms?:string[];run_id?:string}
export interface Run {company_id?:string|null;company_name?:string|null;coverage?:{retryable:boolean;capability_exhausted:boolean;unresolved:string[]}&Incompleteness;id:string;task_id:string;kind:string;status:string;progress:{social_total?:number;social_done?:number;social_accounts?:number;done:number;total:number;discovered:number;new?:number;existing?:number;changed?:number;market_total?:number;website_pages_checked?:number;website_links_remaining?:number;enrichment_done?:number;enrichment_total?:number;discovery_done?:number;discovery_total?:number};message:string;created_at:string;finished_at:string|null;error:string|null}
export interface TaskGeography {
  state_id:string;
  county_id:string|null;
  place_id:string|null;
  county:string;
  county_ids:string[];
  official_name:string|null;
  place_type:string|null;
  scope:'whole_place'|'whole_municipality';
  verified_selection:boolean;
  vintage:string;
}
export type SocialPlatform = 'facebook'|'instagram'|'x';
export interface TaskSocialSettings {auto_social:boolean;social_platforms:SocialPlatform[];social_read_posts:boolean}
export interface Task extends Partial<TaskSocialSettings> {id:string;name:string;created_by_user_id?:string|null;country:string;state:string;city:string;county?:string;geography?:TaskGeography;keywords:string[];company_types:string[];mode?:'market';market_stats?:{total:number;new:number;existing:number;changed:number;not_seen:number;excluded?:number};tier_stats?:Partial<Record<TierCode,number>>;coverage?:{scope_label:string;bbox?:number[]|null;planned:number;done:number;unresolved:string[];attribution?:string;query_basis?:'keywords'|'company_types';query_terms?:string[]}&Incompleteness;auto_enrich:boolean;created_at:string;latest_run:Run|null;query_estimate?:QueryEstimate;needs_keywords?:boolean;legacy_terms?:string[]}
export interface QueryEstimate {terms:string[];query_terms:string[];geometry:'catalogue'|'geocoder'|'text_only';initial_min:number;initial_max:number;max:number|null}
export interface QueryPreviewResult extends QueryEstimate {keywords:string[];existing_market:{id:string;name:string;archived:boolean}|null;existing_market_scope?:'create'|'edit'}
export interface IndustryPreset {id:string;label:string;terms:Record<'wholesale'|'retail'|'import',string>}
export interface TypeHint {business_type:string;market_id:string;market_name:string}
export interface Exclusion {rule_id:string;kind:'category'|'keyword';value:string;scope:'global'|'market'}
export interface ExclusionRule {id:string;scope:'global'|'market';market_id:string|null;kind:'category'|'keyword';value:string;value_key:string;created_at:string;matched:number;reused?:boolean}
export interface CategoryCount {category:string;category_key:string;count:number}
export interface Gap {code:string;reason:string;next_step:string}
export type TierCode='named_procurement'|'named_role'|'named_unknown'|'department_email'|'general_email'|'form_phone'|'company_phone'|'none';
export interface Company {best_tier?:TierCode;best_tier_label?:string;best_tier_rank?:number;gap?:Gap|null;categories?:string[];type_hints?:TypeHint[];exclusion?:Exclusion|null;operating_status?:string;operating_source_url?:string;operating_observed_at?:string;id:string;name:string;address:string;phone:string;website:string;city:string;country:string;business_types:string[];status:string;notes:string;revision:number;created_at:string;last_checked_at:string|null;manual_checked_at?:string|null;procurement_count?:number;email_count?:number;social_count?:number;contact_count:number;market_segment?:string;market_changed?:boolean;first_seen?:string;last_seen?:string}
export interface ContactField {key:string;label:string;value:string;source_url:string;source_label:string;observed_at:string}
export interface ContactSource {source_url:string;evidence:string;observed_at:string}
export interface Contact {tier?:TierCode;tier_label?:string;origin?:'manual';phone?:string;outreach_category?:string;id:string;name:string;role:string;email:string;url:string;kind:string;role_status:string;association_status:string;source_url:string;evidence:string;observed_at:string;review_status:string;adoption_status?:'excluded'|'accepted'|'automatic';fields:ContactField[];sources?:ContactSource[];record_ids?:string[]}
export interface Observation {id:string;field:string;value:unknown;source_url:string;evidence:string;observed_at:string;method:string}
export interface Change {id:string;company_id:string;company_name:string;field:string;old_value:unknown;new_value:unknown;created_at:string;reason:string}
export type CompanyBrief = Pick<Company,'id'|'name'|'address'|'phone'|'website'|'city'|'country'>;
export interface CompanyDetail {related_locations?:CompanyBrief[];company:Company;contacts:Contact[];observations:Observation[];changes:Change[];social_links:string[]}
export interface Review {contact?:Contact|null;previous_contact?:Record<string,string>;contact_change?:'proposal'|'legacy'|'new';comparison?:{left:CompanyBrief|null;right:CompanyBrief|null;same_website:boolean;website_group:string};id:string;company_id:string;company_name:string;kind:string;field:string;old_value:unknown;new_value:unknown;source_url:string;evidence:string;status:string;created_at:string;evidence_count?:number;evidence_items?:{source_url:string;evidence:string;observed_at:string}[]}
export interface Schedule {id:string;task_id:string|null;company_id:string|null;kind:string;interval_months:number;timezone:string;next_due_at:string;enabled:boolean;waiting_reason?:string|null}
export interface Region {id:string;name:string;parent_id:string|null;kind:string;official_name?:string;subtype?:string;county_ids?:string[];state_id?:string;geoid?:string}
export interface RegionConfig {
  country:string;
  name:string;
  mode:'county_filter'|'municipal_branch';
  labels:{state:string;county:string;place:string};
  levels:string[];
  terminal_subtypes?:string[];
  scope?:'whole_place'|'whole_municipality';
  scope_label?:string;
  attribution:string;
  hint:string;
}
export interface Overview {maps_policy?:{manual_required:boolean;retry_after_seconds:number;message:string};companies:number;contacts:number;tasks:number;pending_reviews:number;running_runs:number;queued_runs?:number;worker?:{alive:boolean;last_seen:string|null;problem?:{code:string;message:string}};source_status:Record<'maps'|'website',{available:boolean;message:string}>;retention?:{state:'awaiting_confirmation'|'active';pending_files:number|null;pending_bytes:number|null;export_days?:number}}
export interface Settings {data_dir:string;version:string;network_mode:string;paid_services:boolean;ai_enabled:boolean}
export interface Backup {name:string;created_at:string;kind?:'manual'|'auto'|'other';reason?:'pre-restore'|'pre-repair'|null;size_bytes?:number}
export interface RetentionPolicy {maps_raw_days:number;auto_backup_keep:number;export_days:number}
export interface RetentionItem {category:'maps_raw'|'auto_backup'|'export';path:string;size_bytes:number;reason:string}
export interface RetentionView {policy:RetentionPolicy;state:'awaiting_confirmation'|'active';confirmed_at:string|null;confirmed_policy:RetentionPolicy|null;
 history:{at:string;trigger:'confirm'|'auto';deleted:number;freed_bytes:number;errors:number}[];
 usage:{runs_bytes:number;backups_bytes:number;exports_bytes:number;logs_bytes:number};
 preview:{plan_id:string;total_files:number;total_bytes:number;items:RetentionItem[];truncated:boolean}}

export interface CompanyPage {items:Company[];total:number;page:number;page_size:number;excluded_total?:number}
export interface ReviewPage {items:Review[];total:number;page:number;page_size:number}
