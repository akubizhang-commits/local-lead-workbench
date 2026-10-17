import { useState } from "react";
import { date } from "../helpers";
import { External } from "../ui";
import { discoveryName, platformNames, scopeNames } from "./presentation";
import { SocialAccountSummary } from "./SocialAccountSummary";
import { SocialPagePreview } from "./SocialPagePreview";
import { SocialPosts } from "./SocialPosts";
import type { Entry, SocialPage } from "./types";
export function SocialEntry({
  entry,
  pages,
  busy,
  decide,
  onFollow,
  blocked,
}: {
  entry: Entry;
  pages: SocialPage[];
  busy: boolean;
  decide: (e: Entry, status: string, scope: string) => void;
  onFollow: (id: string) => void;
  blocked: boolean;
}) {
  const [scope, setScope] = useState(entry.scope);
  return (
    <article className="social-card">
      {entry.kind === "account" ? (
        <SocialAccountSummary entry={entry} pages={pages} />
      ) : (
        <>
          <div className="row between">
            <strong>
              数据来源：{platformNames[entry.platform] || entry.platform}
            </strong>
            <span className="badge">
              {entry.status === "confirmed"
                ? "人工已确认"
                : entry.status === "excluded"
                  ? "不采用"
                  : "待核对"}
            </span>
          </div>
          <p>
            <External url={entry.url}>
              {entry.kind === "collection" ? "打开合集" : "打开原帖"}
            </External>
          </p>
        </>
      )}
      <p className="muted">
        {scopeNames[entry.scope]} · {entry.linked_company_count}{" "}
        家企业记录关联同一链接
      </p>
      {pages.map((page, index) => (
        <SocialPosts
          key={index}
          page={page}
          platform={platformNames[entry.platform] || entry.platform}
        />
      ))}
      {pages.length ? (
        <details>
          <summary>查看采集详情</summary>
          {pages.map((page, index) => (
            <SocialPagePreview
              key={`${page.entity_id}:${page.observed_at}:${index}`}
              page={page}
              showPosts={false}
            />
          ))}
        </details>
      ) : (
        entry.kind !== "account" && (
          <p className="muted">仅有原帖链接，作者与内容未自动核实</p>
        )
      )}
      <details>
        <summary>查看发现证据（{entry.evidence.length}）</summary>
        {entry.evidence.map((e) => (
          <div className="social-evidence" key={e.id}>
            <p>
              {discoveryName(
                e.method,
                platformNames[entry.platform] || entry.platform,
              )}
            </p>
            {e.source_url && (
              <External url={e.source_url}>查看发现来源</External>
            )}
            <blockquote>{e.evidence || "未提供证据说明"}</blockquote>
            <small>
              {e.method === "legacy" ? "历史建档于" : "观察于"}{" "}
              {date(e.observed_at)}
            </small>
          </div>
        ))}
      </details>
      <div className="social-decision">
        <label>
          关联范围
          <select
            value={scope}
            disabled={busy}
            onChange={(e) => setScope(e.target.value)}
          >
            {Object.entries(scopeNames).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </label>
        <div className="actions">
          <button
            disabled={busy || scope === "unknown"}
            onClick={() => decide(entry, "confirmed", scope)}
          >
            确认归属
          </button>
          {entry.status !== "excluded" ? (
            <button
              disabled={busy}
              onClick={() => decide(entry, "excluded", scope)}
            >
              不采用
            </button>
          ) : (
            <button
              disabled={busy}
              onClick={() => decide(entry, "candidate", "unknown")}
            >
              恢复待核对
            </button>
          )}
          {entry.status === "confirmed" && !blocked && (
            <button onClick={() => onFollow(entry.id)}>记录跟进 / 草稿</button>
          )}
        </div>
      </div>
    </article>
  );
}
