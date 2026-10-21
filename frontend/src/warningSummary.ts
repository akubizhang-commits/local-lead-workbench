/** server: wording for a server deployment (the network is the server's). */
export function warningReasons(error: string, server = false): {reason:string;action:string}[] {
 const rules: [RegExp,string,string][] = [
  [/byte limit|body.*limit/i,'页面过大','已保留读取上限；优先读取联系、团队等相关页面。'],
  [/robots/i,'网站访问规则或规则文件读取受限','检查网站可访问性；明确禁止的页面不会继续抓取。'],
  [/cross-site redirect/i,'跳转到其他网站','核对企业官网或登录入口，确认目标后再更新。'],
  [/页面被意外关闭|浏览器意外关闭|browser_closed|target.*closed|browser.*(closed|disconnected)|crashed/i,'采集程序异常：地图页面被意外关闭','系统会按计划自动重试，重试用尽后可点“继续”；不能据此判断当地没有企业。'],
  [/采集程序异常退出|采集程序报告查询失败|未找到可执行的地图采集程序|无法解析的结果|Maps adapter failed|Process cleanup failed|Final output read failed/,'采集程序异常','展开原始日志核对原因；已取得的结果会保留。'],
  [/没有找到结果|no validated|empty_output/i,'确实没有结果','采集程序正常结束但没有返回企业；可调整关键词或范围。'],
  [/timeout|timed out|time budget|超时|时间上限|无响应/i,'读取超时或时间预算耗尽','稍后重试；分批处理会保留已取得的结果。'],
  [/page limit/i,'旧版单次页面上限','新的轮次已支持分批继续；以前未完成的轮次可以继续处理。'],
  [/depth limit|queue limit|request limit/i,'达到扫描范围或请求上限','保留当前结果，可核对是否遗漏重要联系页面。'],
  [/HTTP\s*404|HTTP\s*410/i,'网页已不存在','核对官网入口；重试不能保证恢复失效页面。'],
  [/HTTP\s*403|HTTP\s*429|captcha|access denied/i,'网站拒绝访问或限流','等待访问恢复；需要验证时由用户在平台处理。'],
  [/ssl|certificate|tls/i,'网站证书或安全连接失败',`核对网站证书及${server?'服务器':'本地'}网络，修复后重试。`],
  [/connection|connector|dns|network_error/i,'网络连接失败','检查网络或代理后重试。'],
  [/unsupported content type|application\/pdf/i,'文件格式未解析','PDF及非网页资源不能按普通网页提取，已读资料仍保留。'],
  [/provider_unavailable|searxng|source_paused/i,'搜索来源暂不可用','先恢复搜索服务，再继续受影响的轮次。'],
  [/auth_required|login required/i,'平台需要登录','在设置中完成对应平台登录后重试。'],
  [/^已暂停：/,'已按用户操作暂停','已取得的结果会保留，可点“继续”恢复。'],
 ];
 const matches=rules.filter(([pattern])=>pattern.test(error)).map(([,reason,action])=>({reason,action}));
 return matches.length?matches:[{reason:'读取未完成',action:'展开原始日志核对具体原因，已采集资料会保留。'}];
}
export function warningSummary(error: string): string {
 return warningReasons(error).map(item=>item.reason).join('；');
}
