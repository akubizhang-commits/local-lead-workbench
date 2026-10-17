export type ContentSource = {
  observed_at?: string;
  source_url?: string;
  retained?: boolean;
};
export type SocialField = ContentSource & {
  key: string;
  value: string;
  evidence: string;
};
export type Evidence = {
  id: string;
  original_url: string;
  source_url: string;
  evidence: string;
  method: string;
  observed_at: string;
  reason: string;
};
export type Entry = {
  id: string;
  platform: string;
  kind: string;
  url: string;
  label: string;
  status: string;
  scope: string;
  revision: number;
  linked_company_count: number;
  last_observed_at: string | null;
  evidence: Evidence[];
};
export type Outreach = {
  id: string;
  company_name: string;
  platform: string;
  label: string;
  url: string;
  state: string;
  note: string;
  created_at: string;
};
export type SocialPage = {
  match_signals?: { text: string; source_url: string }[];
  provider?: string;
  posts?: {
    url: string;
    text: string;
    published_at?: string | null;
    observed_at?: string;
  }[];
  content_observed_at?: string;
  entity_id: string;
  status: string;
  message: string;
  title: string;
  description: string;
  fields: SocialField[];
  field_history?: SocialField[];
  content_sources?: { title?: ContentSource; description?: ContentSource };
  observed_at: string;
  source_url: string;
};
export type SocialData = {
  pages?: SocialPage[];
  accounts: Entry[];
  posts: Entry[];
  excluded: Entry[];
  ignored_links: Evidence[];
  outreach: Outreach[];
  do_not_contact: boolean;
  search_queries: { platform: string; query: string; url: string }[];
};
