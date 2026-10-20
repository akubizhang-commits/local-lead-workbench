import { useState, type ReactNode } from "react";
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
}: {
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
  return (
    <section className="panel social-panel">
      <div className="section-title">
        <h3>联系人与账号</h3>
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
      <p className="muted">
        官网更新会同步补充链接。先核对归属，再记录跟进；打开账号或帖子不会记为已联系。
      </p>
      <details className="social-section">
        <summary>查找与更新社媒资料</summary>
        <SocialDiscovery companyId={companyId} onChange={onRefresh} />
      </details>
      {error && (
        <Alert>
          {error}
          <button onClick={onRefresh}>重试</button>
        </Alert>
      )}
      {failure && <Alert>{failure}</Alert>}
      {notice && <p role="status">{notice}</p>}
      {loading && <p role="status">正在读取社交资料…</p>}
      {!data && (
        <SocialAccountList
          entries={[]}
          contacts={contactGroups.active}
          pages={[]}
          onOpen={(id) => onOpenAccount?.(id)}
        />
      )}
      {data && (
        <>
          {data.do_not_contact && (
            <Alert>此企业已标记拒绝联系，跟进录入已停用。</Alert>
          )}
          <p className="muted">
            采购联系人排在前面；其余为可尝试联系的入口，点击查看资料和来源。
          </p>
          <SocialAccountList
            entries={data.accounts}
            contacts={contactGroups.active}
            pages={data.pages || []}
            onOpen={(id) => onOpenAccount?.(id)}
          />
          {!!contactGroups.supplementary.length && (
            <details className="social-section">
              <summary>
                补充联系页面（{contactGroups.supplementary.length}）
              </summary>
              <p className="muted">
                只发现了页面链接，尚未确认存在可提交的联系表单，不作为直接联系入口展示。
              </p>
              <SocialAccountList
                entries={[]}
                contacts={contactGroups.supplementary}
                pages={[]}
                onOpen={(id) => onOpenAccount?.(id)}
              />
            </details>
          )}
          {!!contactGroups.pending.length && (
            <details className="social-section">
              <summary>待确认联系人（{contactGroups.pending.length}）</summary>
              <SocialAccountList
                entries={[]}
                contacts={contactGroups.pending}
                pages={[]}
                onOpen={(id) => onOpenAccount?.(id)}
              />
            </details>
          )}
          {!!contactGroups.hidden.length && (
            <details className="social-section">
              <summary>
                非客户开发用途 / 不采用（{contactGroups.hidden.length}）
              </summary>
              <SocialAccountList
                entries={[]}
                contacts={contactGroups.hidden}
                pages={[]}
                onOpen={(id) => onOpenAccount?.(id)}
              />
            </details>
          )}
          <section className="social-section">
            <h4>已知帖子与合集（{data.posts.length}）</h4>
            <div className="social-grid">{data.posts.map(render)}</div>
          </section>
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
            <summary>已过滤的非账号链接（{data.ignored_links.length}）</summary>
            {data.ignored_links.map((e) => (
              <p key={e.id}>
                <span className="social-raw-url">{e.original_url}</span>
                <br />
                <small>{e.reason}</small>
              </p>
            ))}
          </details>
          {followup}
        </>
      )}
    </section>
  );
}
