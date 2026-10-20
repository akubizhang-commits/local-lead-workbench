import { useEffect, useRef, useState } from "react";
import { CompanyAnchors, jumpTo } from "../CompanyAnchors";
import { ContactEntrySummary } from "../ContactEntrySummary";
import { EvidenceGroups, evidenceSummary } from "../EvidenceGroups";
import { ManualContactForm } from "../ManualContactForm";
import { date, label, safeUrl } from "../helpers";
import { type View } from "../navigation";
import { RelatedLocations } from "../RelatedLocations";
import { Schedules } from "../Schedules";
import { SocialPanel } from "../SocialPanel";
import type { Company, CompanyDetail } from "../types";
import { Badge, Empty, External, Icon } from "../ui";
import { Resource, type ResourceState } from "../Resource";
import { ChangeList } from "./ChangeList";

/** Host name without "www." for the summary card; the full URL stays in “资料”. */
export function websiteHost(url?: string | null): string {
  const href = safeUrl(url);
  if (!href) return "";
  return new URL(href).hostname.replace(/^www\./, "");
}
function businessTypes(company: Company) {
  return company.business_types.map(label).join("、") ||
    [...new Set((company.type_hints || []).map((h) => h.business_type))]
      .map((t) => `${label(t)}（来自市场定义，待核实）`).join("、") ||
    "待核实";
}
export function CompanyDetailPage({
  selected,
  detail,
  account,
  version,
  refresh,
  backToCompany,
  openCompany,
  openAccount,
  setView,
  section,
  onSectionHandled,
}: {
  selected: string | null;
  detail: ResourceState<CompanyDetail>;
  account?: string;
  version: number;
  busy: boolean;
  refresh: () => void;
  backToCompany: () => void;
  quickStatus: (company: Company, status: string) => void;
  openCompany: (id: string) => void;
  openAccount: (id: string) => void;
  setView: (view: View) => void;
  /** “前往查看” from the restore panel: jump to 联系入口 once the data is available (design §6.2). */
  section?: "contacts";
  onSectionHandled?: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const [openDiscovery, setOpenDiscovery] = useState(false);
  const handled = useRef(false);
  useEffect(() => {
    handled.current = false;
    setOpenDiscovery(false);
    setAdding(false);
  }, [selected]);
  const ready = !!detail.data && !account;
  useEffect(() => {
    if (section !== "contacts" || !ready || handled.current) return;
    handled.current = true;
    setOpenDiscovery(true);
    jumpTo("company-contacts");
    onSectionHandled?.();
  }, [section, ready, onSectionHandled]);
  const data = detail.data;
  return selected ? (
    <Resource
      {...detail}
      loading={detail.loading && !detail.data}
      retry={refresh}
      notFoundAction={{ label: "返回企业库", onClick: () => setView("companies") }}
    >
      {data &&
        (account ? (
          <SocialPanel
            key={selected}
            companyId={selected}
            version={version}
            accountId={account}
            onBack={backToCompany}
            contacts={data.contacts}
          />
        ) : (
          <>
            <CompanyAnchors contacts={data.company.contact_count} evidence={data.observations.length} history={data.changes.length} />
            <section className="panel detail-summary" id="company-summary" aria-label="概要">
              <ContactEntrySummary company={data.company} contacts={data.contacts} onJump={() => jumpTo("company-contacts")} />
              <div className="key-facts">
                <h3>关键资料</h3>
                <dl className="facts">
                  <dt>电话</dt>
                  <dd>{data.company.phone || "未记录"}</dd>
                  <dt>官网</dt>
                  <dd>{websiteHost(data.company.website) ? <External url={data.company.website}>{websiteHost(data.company.website)}</External> : "未记录"}</dd>
                  <dt>地址</dt>
                  <dd className="clamp-2" title={data.company.address}>{data.company.address || "未记录"}</dd>
                  <dt>营业状态</dt>
                  <dd>{data.company.operating_status && data.company.operating_status !== "unknown" ? <Badge value={data.company.operating_status} /> : "尚无明确状态证据"}</dd>
                </dl>
              </div>
            </section>
            <SocialPanel
              key={`social:${selected}`}
              companyId={selected}
              version={version}
              onOpenAccount={openAccount}
              contacts={data.contacts}
              openDiscovery={openDiscovery}
              headerAction={<button type="button" onClick={() => setAdding((v) => !v)} aria-expanded={adding}>新增联系人</button>}
              addForm={adding && (
                <ManualContactForm
                  companyId={data.company.id}
                  revision={data.company.revision}
                  onSaved={() => {
                    setAdding(false);
                    refresh();
                  }}
                  onCancel={() => setAdding(false)}
                  onRefresh={refresh}
                />
              )}
            />
            <section className="panel" id="company-facts" aria-labelledby="company-facts-title">
              <div className="section-title">
                <h3 id="company-facts-title" tabIndex={-1}>资料</h3>
              </div>
              <dl className="facts">
                <dt>官方网站</dt>
                <dd><External url={data.company.website} /></dd>
                <dt>营业状态</dt>
                <dd>
                  {data.company.operating_status && data.company.operating_status !== "unknown" ? (
                    <>
                      <Badge value={data.company.operating_status} />
                      <div>
                        <External url={data.company.operating_source_url}>查看状态来源</External>{" "}
                        · {date(data.company.operating_observed_at)}
                      </div>
                    </>
                  ) : (
                    "尚无明确状态证据"
                  )}
                </dd>
                <dt>联系电话</dt>
                <dd>{data.company.phone || "未记录"}</dd>
                <dt>地址</dt>
                <dd>{data.company.address || "未记录"}</dd>
                <dt>业务类型</dt>
                <dd>{businessTypes(data.company)}</dd>
                <dt>来源类别</dt>
                <dd>{(data.company.categories || []).join("、") || "—"}</dd>
                <dt>建档时间</dt>
                <dd>{date(data.company.created_at)}</dd>
                <dt>官网采集时间</dt>
                <dd>{date(data.company.last_checked_at)}</dd>
                <dt>人工确认时间</dt>
                <dd>{date(data.company.manual_checked_at)}</dd>
                <dt>跟进备注</dt>
                <dd className="pre-wrap">{data.company.notes || "尚无备注"}</dd>
              </dl>
              <div className="facts-more">
              <RelatedLocations
                key={`locations:${selected}`}
                companies={data.related_locations || []}
                onOpen={openCompany}
              />
              <Schedules companyId={selected} version={version} collapsible />
              <details className="diagnostics">
                <summary>诊断信息</summary>
                <p className="muted">企业编号 <code>{data.company.id}</code> · 修订 {data.company.revision}</p>
              </details>
              </div>
            </section>
            {data.observations.length ? (
              <details className="panel detail-collapsible" id="company-evidence">
                <summary>来源证据 · {data.observations.length} 条 <span className="muted">{evidenceSummary(data.observations)}</span></summary>
                <p className="muted">来源证据和当前资料分开保存。</p>
                <EvidenceGroups items={data.observations} />
              </details>
            ) : (
              <section className="panel" id="company-evidence" aria-labelledby="company-evidence-title">
                <h3 id="company-evidence-title" tabIndex={-1}>来源证据</h3>
                <Empty title="暂无来源证据">
                  人工录入不等同于自动发现。来源不明确时不推定发现渠道。
                </Empty>
              </section>
            )}
            <details className="panel detail-collapsible" id="company-history">
              <summary>历史 · 资料变化 {data.changes.length} 条{data.changes[0] && <span className="muted">最近：{label(data.changes[0].field)} · {date(data.changes[0].created_at)}</span>}</summary>
              <ChangeList items={data.changes} onOpen={openCompany} limit={10} />
            </details>
          </>
        ))}
    </Resource>
  ) : (
    <section className="panel">
      <Empty
        title="先选择一家企业"
        action={
          <button className="primary" onClick={() => setView("companies")}>
            前往企业库 <Icon name="arrow" size={16} />
          </button>
        }
      >
        从企业库打开一家企业，查看资料、联系人、来源证据和定时计划。
      </Empty>
    </section>
  );
}
