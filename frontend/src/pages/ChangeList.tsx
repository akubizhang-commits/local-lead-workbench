import { date, fieldValue, label } from "../helpers";
import type { Change } from "../types";
import { Badge, Empty, Icon } from "../ui";
export function ChangeList({
  items,
  onOpen,
}: {
  items: Change[];
  onOpen: (id: string) => void;
}) {
  return items.length ? (
    <div className="timeline">
      {items.map((c) => (
        <article key={c.id}>
          <span className="timeline-mark" />
          <div className="row between">
            <button
              className="text-button"
              onClick={() => onOpen(c.company_id)}
            >
              {c.company_name || c.company_id}
            </button>
            <time>{date(c.created_at)}</time>
          </div>
          <h3>
            {label(c.field)}
            {c.auto_fill && <> <Badge value="auto_fill" /></>}
          </h3>
          <div className="comparison">
            <div>
              <small>原记录</small>
              <p>{fieldValue(c.field, c.old_value)}</p>
            </div>
            <Icon name="arrow" />
            <div>
              <small>新观察</small>
              <p>{fieldValue(c.field, c.new_value)}</p>
            </div>
          </div>
          <p className="muted">
            {c.auto_fill
              ? "自动补全（原值为空，未进入审核）"
              : c.reason || "未记录原因"}
          </p>
        </article>
      ))}
    </div>
  ) : (
    <Empty title="尚无更新记录">资料发生变化后，可在这里对比新旧值。</Empty>
  );
}
