import { date } from "../helpers";
import { External } from "../ui";
import type { ContentSource } from "./types";

/** A missing field timestamp must not borrow the latest page attempt's date. */
export function SourceStamp({ source }: { source?: ContentSource }) {
  if (!source) return null;
  return (
    <small className="muted">
      {source.observed_at ? (
        <>采集于 {date(source.observed_at)}</>
      ) : (
        "采集时间未记录"
      )}
      {source.retained && " · 保留旧资料（非本轮读取）"}
      {source.source_url && (
        <>
          {" "}
          · <External url={source.source_url}>字段来源</External>
        </>
      )}
    </small>
  );
}
