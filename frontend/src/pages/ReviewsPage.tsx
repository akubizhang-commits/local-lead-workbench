import { useEffect, useState } from "react";
import { api } from "../api";
import { useResource } from "../hooks";
import { ReviewList, isContactChange } from "../ReviewList";
import { ReviewCleanup } from "../ReviewCleanup";
import type { ReviewPage } from "../types";
import { Empty } from "../ui";

import { Resource } from "../Resource";
import type { Action } from "./pageTypes";
/** Append how many same-value suggestions the decision closed. */
export function decisionNotice(base: string) {
  return (result: unknown) => {
    const closed = (result as { auto_closed?: number } | null)?.auto_closed ?? 0;
    return closed ? `${base}，已同时关闭 ${closed} 条相同建议` : base;
  };
}
export const REVIEW_PAGE_SIZE = 20;
export function ReviewsPage({
  version,
  busy,
  act,
  refresh,
  openCompany,
}: {
  version: number;
  busy: boolean;
  act: Action;
  refresh: () => void;
  openCompany: (id: string) => void;
}) {
  const [page, setPage] = useState(1);
  const reviews = useResource<ReviewPage>(`/reviews?page=${page}&page_size=${REVIEW_PAGE_SIZE}`, version);
  const data = reviews.data;
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  // Deciding the last item of a later page leaves it empty: go back one page.
  useEffect(() => {
    if (data && !data.items.length && page > 1) setPage((p) => Math.max(1, p - 1));
  }, [data, page]);
  return (
    <>
    <ReviewCleanup version={version} onDone={refresh} />
    <Resource {...reviews} retry={refresh}>
      {data && data.total > 0 && (
        <nav className="review-pagination" aria-label="审核分页">
          <span className="muted">共 {data.total} 条待审核 · 第 {page} / {pages} 页</span>
          <div className="actions">
            <button disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>上一页</button>
            <button disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>下一页</button>
          </div>
        </nav>
      )}
      {data?.items.length ? (
        <ReviewList
          reviews={data.items}
          busy={busy}
          onOpen={openCompany}
          onDecision={(r, decision) =>
            act(
              () =>
                api(`/reviews/${encodeURIComponent(r.id)}/decision`, "POST", {
                  decision,
                }),
              decisionNotice(
                r.kind === "duplicate_candidate"
                  ? "已保留两条独立记录，双方资料未覆盖"
                  : isContactChange(r)
                    ? decision === "reject"
                      ? "已保留此前记录，本次新信息不会再提醒"
                      : "已采用新的联系人信息"
                    : r.kind === "contact"
                    ? decision === "reject"
                      ? "已标记该联系信息为不采用"
                      : "已确认联系信息"
                    : decision === "reject"
                      ? "已拒绝此次变更，保留原值"
                      : "审核决定已保存",
              ),
            )
          }
        />
      ) : (
        <section className="panel">
          <Empty title="没有待处理的审核项">
            来源冲突、身份不明与新的候选值会进入这里，等待你的判断。
          </Empty>
        </section>
      )}
    </Resource>
    </>
  );
}
