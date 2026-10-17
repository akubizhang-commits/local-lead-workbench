import { api } from "../api";
import { useResource } from "../hooks";
import { ReviewList } from "../ReviewList";
import type { Review } from "../types";
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
  const reviews = useResource<Review[]>("/reviews", version);
  return (
    <Resource {...reviews} retry={refresh}>
      {reviews.data?.length ? (
        <ReviewList
          reviews={reviews.data}
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
  );
}
