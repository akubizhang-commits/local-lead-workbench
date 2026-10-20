import { useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import { listContacts } from "../contactList";
import type { Action } from "../pages/pageTypes";
import { SocialDiscovery } from "../SocialDiscovery";
import type { Contact } from "../types";
import { Alert, External, Field } from "../ui";
import { SocialAccountList } from "./SocialAccountList";
import type { Entry, SocialData } from "./types";
export function SocialList({
  companyId,
  base,
  contacts,
  data,
  error,
  failure,
  notice,
  loading,
  busy,
  act,
  onRefresh,
  onOpenAccount,
  render,
  followup,
  openDiscovery = false,
  headerAction,
  addForm,
}: {
  openDiscovery?: boolean;
  headerAction?: ReactNode;
  addForm?: ReactNode;
  companyId: string;
  base: string;
  contacts: Contact[];
  data?: SocialData;
  error: string;
  failure: string;
  notice: string;
  loading: boolean;
  busy: boolean;
  act: Action;
  onRefresh: () => void;
  onOpenAccount?: (id: string) => void;
  render: (entry: Entry) => ReactNode;
  followup: ReactNode;
}) {
  const [url, setUrl] = useState(""),
    [evidence, setEvidence] = useState("");
  const contactGroups = listContacts(contacts);
  const discovery = useRef<HTMLDetailsElement>(null);
  // Arriving with section=contacts (restore panel “前往查看”): open the discovery details once.
  // It is rendered outside the `data &&` blocks so the ref exists even before data arrives (review R3).
  useEffect(() => {
    if (openDiscovery && discovery.current) discovery.current.open = true;
  }, [openDiscovery]);
  const list = (rows: Contact[]) => (
    <SocialAccountList entries={[]} contacts={rows} pages={[]} onOpen={(id) => onOpenAccount?.(id)} />
  );
  return (
    <section className="panel social-panel" id="company-contacts" aria-labelledby="company-contacts-title">
      <div className="section-title">
        <h3 id="company-contacts-title" tabIndex={-1}>联系人与账号</h3>
        <div className="actions">
          {headerAction}
          <button
            disabled={busy || loading}
            onClick={() =>
              void act(
                () => api(`${base}/sync`, "POST"),
                "已整理当前保存的官网社交链接",
              )
            }
          >
            整理已有链接
          </button>
        </div>
      </div>
      {addForm}
      <p className="muted">采购联系人排在前面；先核对归属再跟进，打开账号或帖子不会记为已联系。</p>
      {error && (
        <Alert>
          {error}
          <button onClick={onRefresh}>重试</button>
        </Alert>
      )}
      {failure && <Alert>{failure}</Alert>}
      {notice && <p role="status">{notice}</p>}
      {loading && <p role="status">正在读取社交资料…</p>}
      {data?.do_not_contact && <Alert>此企业已标记拒绝联系，跟进录入已停用。</Alert>}
      <SocialAccountList
        entries={data?.accounts || []}
        contacts={contactGroups.active}
        pages={data?.pages || []}
        onOpen={(id) => onOpenAccount?.(id)}
      />
      {!!contactGroups.supplementary.length && (
        <details className="social-section">
          <summary>补充联系页面（{contactGroups.supplementary.length}）</summary>
          <p className="muted">
            只发现了页面链接，尚未确认存在可提交的联系表单，不作为直接联系入口展示。
          </p>
          {list(contactGroups.supplementary)}
        </details>
      )}
      {!!contactGroups.pending.length && (
        <details className="social-section">
          <summary>待确认联系人（{contactGroups.pending.length}）</summary>
          {list(contactGroups.pending)}
        </details>
      )}
      {!!contactGroups.hidden.length && (
        <details className="social-section">
          <summary>非客户开发用途 / 不采用（{contactGroups.hidden.length}）</summary>
          {list(contactGroups.hidden)}
        </details>
      )}
      {data && (
        <details className="social-section">
          <summary>已知帖子与合集（{data.posts.length}）</summary>
          <div className="social-grid">{data.posts.map(render)}</div>
        </details>
      )}
      <details className="social-section" ref={discovery}>
        <summary>查找与更新社媒资料</summary>
        <SocialDiscovery companyId={companyId} onChange={onRefresh} />
      </details>
      {data && (
        <>
          <details className="social-section">
            <summary>查找更多账号 / 添加候选</summary>
            <p className="muted">
              搜索会在新页面打开。名称、域名和公开业务电话仅用于找候选，不代表已确认匹配。
            </p>
            <div className="social-search">
              {data.search_queries.map((q) => (
                <div key={q.query}>
                  <External url={q.url}>{q.query}</External>
                </div>
              ))}
            </div>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void act(async () => {
                  await api(`${base}/links`, "POST", { url, evidence });
                  setUrl("");
                  setEvidence("");
                }, "候选链接已保存，请核对归属");
              }}
            >
              <Field label="账号或帖子链接">
                <input
                  type="url"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  required
                  maxLength={4096}
                  placeholder="粘贴企业社交主页或原帖链接"
                />
              </Field>
              <Field label="发现依据">
                <textarea
                  value={evidence}
                  onChange={(e) => setEvidence(e.target.value)}
                  maxLength={3000}
                  placeholder="例如：账号简介中的官网与企业一致"
                />
              </Field>
              <button className="primary" disabled={busy}>
                保存候选
              </button>
            </form>
          </details>
          <details className="social-section">
            <summary>不采用的账号与帖子（{data.excluded.length}）</summary>
            <div className="social-grid">{data.excluded.map(render)}</div>
          </details>
          <details className="social-section">
            <summary>已过滤的链接（{data.ignored_links.length}）</summary>
            {data.ignored_links.map((e) => (
              <p key={e.id}>
                <span className="social-raw-url">{e.original_url}</span>
                <br />
                <small>{e.reason}</small>
              </p>
            ))}
          </details>
          <details className="social-section social-followup">
            <summary>联系草稿与跟进</summary>
            {followup}
          </details>
        </>
      )}
    </section>
  );
}
