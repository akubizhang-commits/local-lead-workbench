import { useState } from "react";
import { CompanyStatus } from "../CompanyStatus";
import { ContactEntrySummary } from "../ContactEntrySummary";
import { ManualContactForm } from "../ManualContactForm";
import { date, display, label } from "../helpers";
import { type View } from "../navigation";
import { RelatedLocations } from "../RelatedLocations";
import { Schedules } from "../Schedules";
import { SocialPanel } from "../SocialPanel";
import type { Company, CompanyDetail } from "../types";
import { Badge, Empty, External, Icon } from "../ui";

import { Resource, type ResourceState } from "../Resource";
import { ChangeList } from "./ChangeList";
export function CompanyDetailPage({
  selected,
  detail,
  account,
  version,
  busy,
  refresh,
  backToCompany,
  quickStatus,
  openCompany,
  openAccount,
  setView,
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
}) {
  const [adding, setAdding] = useState(false);
  return selected ? (
    <Resource {...detail} retry={refresh}>
      {detail.data &&
        (account ? (
          <SocialPanel
            key={selected}
            companyId={selected}
            version={version}
            accountId={account}
            onBack={backToCompany}
            contacts={detail.data.contacts}
          />
        ) : (
          <>
            <div className="panel company-hero">
              <span className="avatar large">
                {detail.data.company.name.slice(0, 1)}
              </span>
              <div>
                <h2>{detail.data.company.name}</h2>
                <p>
                  {[detail.data.company.city, detail.data.company.country]
                    .filter(Boolean)
                    .join(" / ") || "地区未记录"}{" "}
                  · 建档于 {date(detail.data.company.created_at)}
                </p>
              </div>
              <CompanyStatus
                company={detail.data.company}
                busy={busy}
                onChange={quickStatus}
              />
            </div>
            <ContactEntrySummary
              company={detail.data.company}
              contacts={detail.data.contacts}
              adding={adding}
              onAdd={() => setAdding((v) => !v)}
            />
            {adding && (
              <section className="panel">
                <ManualContactForm
                  companyId={detail.data.company.id}
                  revision={detail.data.company.revision}
                  onSaved={() => {
                    setAdding(false);
                    refresh();
                  }}
                  onCancel={() => setAdding(false)}
                  onRefresh={refresh}
                />
              </section>
            )}
            <div className="detail-grid">
              <section className="panel">
                <div className="section-title">
                  <h3>企业资料</h3>
                  <span className="muted">
                    修订 {detail.data.company.revision}
                  </span>
                </div>
                <dl className="facts">
                  <dt>官方网站</dt>
                  <dd>
                    <External url={detail.data.company.website} />
                  </dd>
                  <dt>营业状态</dt>
                  <dd>
                    {detail.data.company.operating_status &&
                    detail.data.company.operating_status !== "unknown" ? (
                      <>
                        <Badge value={detail.data.company.operating_status} />
                        <div>
                          <External
                            url={detail.data.company.operating_source_url}
                          >
                            查看状态来源
                          </External>{" "}
                          · {date(detail.data.company.operating_observed_at)}
                        </div>
                      </>
                    ) : (
                      "尚无明确状态证据"
                    )}
                  </dd>
                  <dt>联系电话</dt>
                  <dd>{detail.data.company.phone || "未记录"}</dd>
                  <dt>地址</dt>
                  <dd>{detail.data.company.address || "未记录"}</dd>
                  <dt>业务类型</dt>
                  <dd>
                    {detail.data.company.business_types.map(label).join("、") ||
                      [...new Set((detail.data.company.type_hints || []).map((h) => h.business_type))]
                        .map((t) => `${label(t)}（来自市场定义，待核实）`).join("、") ||
                      "待核实"}
                  </dd>
                  <dt>来源类别</dt>
                  <dd>{(detail.data.company.categories || []).join("、") || "—"}</dd>
                  <dt>官网采集时间</dt>
                  <dd>{date(detail.data.company.last_checked_at)}</dd>
                  <dt>人工确认时间</dt>
                  <dd>{date(detail.data.company.manual_checked_at)}</dd>
                  <dt>跟进备注</dt>
                  <dd className="pre-wrap">
                    {detail.data.company.notes || "尚无备注"}
                  </dd>
                  <dt>企业编号</dt>
                  <dd className="muted">{detail.data.company.id}</dd>
                </dl>
              </section>
              <Schedules companyId={selected} version={version} />
            </div>
            <RelatedLocations
              key={`locations:${selected}`}
              companies={detail.data.related_locations || []}
              onOpen={openCompany}
            />
            <SocialPanel
              key={`social:${selected}`}
              companyId={selected}
              version={version}
              onOpenAccount={openAccount}
              contacts={detail.data.contacts}
            />
            <section className="panel">
              <div className="section-title">
                <h3>来源与字段证据</h3>
                <span className="muted">观察事实与当前资料分开保存</span>
              </div>
              {detail.data.observations.length ? (
                <div className="evidence-list">
                  {detail.data.observations.map((o) => (
                    <article key={o.id}>
                      <div className="row between">
                        <strong>
                          {label(o.field)} · {display(o.value)}
                        </strong>
                        <Badge value={o.method} />
                      </div>
                      <blockquote>{o.evidence || "未提供证据片段"}</blockquote>
                      <div className="row between">
                        <External url={o.source_url} />
                        <time>{date(o.observed_at)}</time>
                      </div>
                    </article>
                  ))}
                </div>
              ) : (
                <Empty title="暂无来源证据">
                  人工录入不等同于自动发现。来源不明确时不推定发现渠道。
                </Empty>
              )}
            </section>
            <section className="panel">
              <h3>资料变化</h3>
              {<ChangeList items={detail.data.changes} onOpen={openCompany} />}
            </section>
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
        从企业库打开记录，查看资料、联系人、证据与复核计划。
      </Empty>
    </section>
  );
}
