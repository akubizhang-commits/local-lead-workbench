# Maps 实测报告

验证日期：2026-09-22。实际 Google Maps 查询、适配器解析、数据库入库与浏览器展示均已通过一次有界验证。不是全城覆盖率或长期稳定性证明。

## 安装与来源

- 开源程序：gosom/google-maps-scraper，锁定提交 `549e4b5e61c7103685ef8392f246ebdba783ed03`（版本 1.18.1）。
- 已检查该提交的 main.go、runner 参数、JSON 条目结构和浏览器安装入口；Go 模块校验通过，源码在本机编译。
- Chromium 149 / Playwright chromium v1228 已下载到项目 tools/bin。禁用上游遥测；无付费 API、代理池或个人浏览器资料。
- 安装命令：`bash scripts/install_maps.sh`。安装器固定提交并安装浏览器，manifest 保存在 tools/bin。

## 实际结果

- 查询：`clothing wholesaler in Los Angeles, California, US`。
- 由工作台创建并启动，最大接受 5 家，并发 1，滚动深度 1，超时 180 秒。
- 开始：`2026-09-22T03:25:58.691887+00:00`；完成：`2026-09-22T03:26:09.128513+00:00`。
- 状态：`completed`；停止原因：`company_limit`；入库 5 家候选。
- 退出码 `-9` 是达到数量上限后主动停止子进程的结果，不表示此次采集失败。
- 真实证据目录：`data/runs/741fa660928346db960d9f9b823644b3/6db857941430448fba85af96e21af496/maps-9115c406d7f74d5ca741bff72423e50c`，含 query.txt、results.jsonl、normalized.json、metadata.json、日志。
- 数据库 run ID：`741fa660928346db960d9f9b823644b3`。

| 实际返回企业名称 | 去重来源键 |
| --- | --- |
| Ora Industries Wholesale vintage clothing Los Angeles California | `google_maps:cid:10509340861462641400` |
| Tasha Apparel Wholesale | `google_maps:cid:7392432269035187707` |
| San Pedro Wholesale Mart | `google_maps:cid:8668276779738470071` |
| Vocal Apparel | `google_maps:cid:4309696537092464831` |
| CORNER 123, Wholesale Women's Clothing in Los Angeles, Fashion District | `google_maps:cid:9162311114070991682` |

每条记录另存 Maps 原始链接、观察时间、电话、官网与位置（来源有值时）。企业均为待核验候选：搜索返回不等于业务类型、城市行政边界或公司身份已经确认。

## 验证中修复的问题

早期独立预检因没有可执行文件而阻塞。安装后第一次联网取得 5 条原始记录，但重复清理已退出的进程组触发 macOS PermissionError，尚未入库。已增加回归测试，清理改为单次且保留异常元数据，再通过界面的“继续”成功执行。旧原始日志仍保留，未改写成成功记录。

本次续跑的排序与第一次不同，符合实时搜索结果可能变化的事实。仅记录实际返回内容，没有从模型记忆补齐企业。

## 边界

当前为城市文字 + 关键词搜索，不保证“所有企业”；未启用网格覆盖。缺少二进制、空结果、异常退出和超时均有明确状态。没有自动解验证码。不能将本次成功外推为长期无阻塞运行。

离线 Maps 回归 23 项通过，包括 JSONL 去重、来源验证、限额、取消、超时、进程组清理和异常证据保存。合成 fixture 仅用于临时测试目录。

## 官网字段修复与重解析

实测 JSON 使用 `web_site`。已修复适配器字段映射并增加回归测试；对上述五条原始 JSONL 重新解析补回四个官网值，保留原始观察时间、run ID 和来源键。原 normalized.json 未改写，修复结果另存同目录 reparsed-web-site.json。第五家来源未给官网，保留为空。
