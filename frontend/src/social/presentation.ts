import { cleanAccountTitle } from "../socialDisplay";
import type { Entry, SocialPage } from "./types";
export const platformNames: Record<string, string> = {
  instagram: "Instagram",
  facebook: "Facebook",
  linkedin: "LinkedIn",
  youtube: "YouTube",
  tiktok: "TikTok",
  x: "Twitter（X）",
  pinterest: "Pinterest",
  bluesky: "Bluesky",
};
export const scopeNames: Record<string, string> = {
  unknown: "归属范围未确认",
  brand: "品牌统一账号",
  location: "此门店",
  employee: "企业员工",
  third_party: "第三方提及",
};
export const stateNames: Record<string, string> = {
  draft: "草稿 · 未发送",
  contacted: "人工确认已联系",
  replied: "已收到回复",
  closed: "结束跟进",
};

export function discoveryName(method: string, platform: string) {
  return (
    (
      {
        platform_search: `${platform} 站内搜索`,
        search_snippet: "搜索引擎结果",
        manual: "人工添加",
        legacy: "原始来源未记录",
        website: "企业官网",
        browser: `${platform} 页面读取`,
      } as Record<string, string>
    )[method] || "来源页面发现"
  );
}

export function accountTitle(entry: Entry, pages: SocialPage[]) {
  const page = pages.find(
    (p) => p.entity_id === entry.id && (p.title || p.description),
  );
  const search = entry.evidence.find(
    (e) =>
      e.method === "platform_search" &&
      /；结果：(.+?)。名称匹配候选/.test(e.evidence),
  );
  return (
    cleanAccountTitle(page?.title) ||
    cleanAccountTitle(
      search?.evidence.match(/；结果：(.+?)。名称匹配候选/)?.[1],
    ) ||
    entry.label
  );
}
