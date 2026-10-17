# 美国地区目录与市场选择设计

日期：2026-09-24。

## 研究结论与产品口径

美国不适合建模为全国一致、每节点只有一个上级的“国家—州—县—市”行政树。对本产品的市场选择，可以提供四项，但县与城市的关系必须允许例外。

- 州及同级地区：本期覆盖50州和哥伦比亚特区；不将特区称作第51个州。领地不混进这51项。
- 县及同级地区：官方县图层包括县及统计同级地区，不统一改名成County。界面保留原始名称；其中会出现 parish、borough、independent city、planning region 等不同形式。
- 地方：官方分为 incorporated places（建制市镇）和 census designated places（CDP，人口普查指定地区）。CDP是统计地理单位，不能视为有市政府的行政市。
- 一个Place可能与多个县有面积重叠，不能把它复制成多个不同城市，也不能强制只选其中一个父县。县仅作为选择器的筛选维度。
- 部分地区另有county subdivisions（县以下区划、township等）。本期没有把这些自动当作City；本期“城市/居民点”指所选官方Place图层的完整目录，不等于所有邮政城市、社区、乡镇或命名居民点。

官方数据入口：

1. https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/0 —— States及同级地区，图层元数据。
2. https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/1 —— Counties及同级地区。
3. https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Places_CouSub_ConCity_SubMCD/MapServer/4 —— Incorporated Places。
4. https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Places_CouSub_ConCity_SubMCD/MapServer/5 —— Census Designated Places。
5. https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Places_CouSub_ConCity_SubMCD/MapServer —— 同时列出County Subdivisions等其他图层，说明Place不等同所有下级区划。

本次图层元数据标明 January 1, 2026 vintage。Gazetteer下载网址返回403，ACS API匿名请求重定向为Missing Key页面；没有把这些响应当作有效数据。最终使用公开TIGERweb查询服务，无收费接口、账号或密钥。

## 数据模型

参考目录：`leadworkbench/datasets/us-geography-2026.json.gz`，随安装包发布，离线读取，不属于可清空的企业历史数据。

```mermaid
flowchart LR
  Country[US 美国] --> State[州 / 特区]
  State --> County[县 / 同级地区]
  State --> Place[建制市镇 / CDP]
  County <-->|多对多地理覆盖| Place
  Place --> Market[以完整Place为目标市场]
```

目录每条记录包含id、name、kind、parent_id、geoid和subtype；Place还包含official_name、state_id、county_ids和参考点geo。州ID沿用US-CA等邮政缩写；县用US-COUNTY-{5位GEOID}；Place用US-PLACE-{7位GEOID}。同名Place通过稳定编码区分，界面建议同时显示正式名称和类型。

州与县/Place的州归属使用源属性STATE。**county_ids并非官方直接提供的法律隶属表**：它由同版本官方县边界与Place边界的正面积相交计算。使用完整源几何，不用中心点归县、不用包围盒相交；无效几何经make_valid处理，排除零面积接触和面积不超过1e-10平方度的数值碎片。关系适用于市场目录筛选，不作为法律边界认定。极小面积跨界片段可能受数值阈值影响。

业务库增加独立 `task_geography(task_id PRIMARY KEY REFERENCES tasks(id), payload TEXT)`。payload保存state_id、county_id、place_id、county_ids、place_type、official_name、vintage及scope=whole_place。保留原tasks文字字段兼容既有任务；不会用县选择裁剪跨县城市。新目录不会重写已存企业地址。

## 接口与交互

| 请求 | 结果 |
|---|---|
| GET /api/regions | 国家 |
| GET /api/regions?parent_id=US | 州与特区 |
| GET /api/regions?parent_id=US-CA&kind=county | 加州县目录 |
| GET /api/regions?parent_id=US-CA&kind=place | 加州全部Place |
| GET /api/regions?parent_id=US-CA&kind=place&county_id=US-COUNTY-06037 | 与洛杉矶县重叠的Place |

保持返回数组兼容旧客户端。省略kind且parent为州时仍返回城市/居民点；请求其他国家没有本地目录时返回空列表并保留手工输入路径。非法跨州县筛选返回400。

POST /api/tasks新增可选state_id、county_id、place_id、county。服务端验证编码存在、类型正确、州名称与编码一致、县和Place均在州内、Place关联所选县、文字与选中Place一致。按县选择须选有效Place；不能携带旧ID提交另一处文字。

市场有有效Place编码时以Place+关键词/类型去重，不把县筛选作为市场身份的一部分。对同州名称唯一的城市保留旧文字key别名，避免旧客户端与新目录重复创建；同名不明记录不强行合并。

选择器顺序：国家、州/省、县/同级地区（可选）、城市/居民点。县默认“全州城市（不按县筛选）”。上级变化清空下级，异步旧响应不能回填到新范围。真实正式名称用于请求，修饰性标签不写进城市字段。CDP与建制市镇分别标示；列表加载失败有重试，不伪装成零城市。

## 更新、导入与验证

`scripts/import_us_geography.py`为手动导入工具，导入时需shapely；运行产品不依赖shapely。请求分页排序，核对各图层返回总数，验证所有Place存在县关联及ID唯一，完成后才原子替换压缩目录。原始响应暂存在/tmp/us-geography-2026供本次校验，目录文件metadata保留来源、数据版本、导入时间、源数量与入库数量。升级数据版本应同时升级图层版本与文件名，不混合年度边界。

回归包括51州级选项/加州58县/纽约市多县关联、跨县不重复建市场、编码不匹配拒绝、其他国家手工输入、无效父级、州县切换清空、同名Place辨识、加载失败重试。

本次选择有效地区只表示匹配本地参考目录，不保证爬虫已覆盖该城市全部企业，也不取代现有检索范围规划与来源边界核验。


## 本次入库结果及检索范围

正式目录：51州级地区（50州+DC）、3,144县/同级地区、32,071 Place（19,514建制市镇、12,557 CDP），其中1,330个Place与多个县有正面积相交。84组同州、同名、同类型地点，建议项使用县名区分，仍同名时才显示GEOID。纽约市关联5县，Baltimore city只关联独立市同级县24510，不归入相邻Baltimore County；DC与Washington的例外已验证。

Place另存官方边界包围盒bbox=[south,north,west,east]。选中有效Place时，市场规划直接使用该编码对应的参考点与包围盒，并标明Census来源；不再依赖同名文本的Nominatim定位，避免重名误定位。原有无编码任务保留旧定位流程。包围盒用于分区检索，不是对企业行政归属的精确裁剪；仍不承诺100%全城召回。

目录更新后须重启后端清理内存缓存。原始分页响应摘要及本地目录SHA256见 `../validation/2026-09-24-us-geography/dataset-manifest.json`。
