# 钙钛矿情报雷达 Skill — 架构 Spec (perovskite-scout)

> 版本：v0.1 (MVP 设计) ｜ 日期：2026-07-10
> 来源：仿 `zarazhangrui/follow-builders` 架构 + 借 `lamalab-org/perla-extract` 发现层 + 参考 PERLA 方法论

## 0. 一句话定位
第一版做**「可信源钙钛矿情报雷达」**——仿 follow-builders 的中央情报管线，借 perla-extract 的发现层，用机器规则做可信度分级。不做泛资讯摘要器，不做半吊子科研数据库。

## 1. follow-builders 可复用部分
- **架构**：openclaw 云端工作流定时采集 → `feed-*.json`（纯数据，agent 端读取）→ agent 读取 `prepare-digest` 等价脚本 → LLM 只做摘要/remix（**被禁止**搜索网络 / 访问 URL / 调 API）
- **prompt 三级优先级**：用户自定义 > GitHub 远程 > 本地 shipped（用户可改风格且不丢中央更新）
- **数据源中央维护**（`config/sources.json`），用户不可增删，仅能提 issue 建议
- **去重**：`state-feed.json` 记录 seen IDs，7 天清理
- **投递**：openclaw 云端工作流 → 个人微信（图片+文本组合）；详见 `perovskite-scout-playbook.md` §2.3

## 2. perla-extract / PERLA 可借鉴部分
- 论文发现借 `perla-extract papersbot` 逻辑（RSS / DOI / 开放 PDF 发现）；**MVP 只取发现层，不跑全文抽取**
- 第二阶段复用/改造 perla-extract：`src/perla_extract/` 含 PDF 处理 (PyMuPDF/Nougat/Marker)、LiteLLM、Pydantic 校验、单位归一化、NOMAD 上传
- 评估基准：`ground_truth/{dev,test}` + `evaluate` CLI（算 precision/recall）
- NOMAD 作 **schema anchor + 验证基准**（区分 human-curated entries 与 LLM-extracted entries，不可全当 gold）
- 参考文档：`references/perla-method.md`（方法论：LLM 抽 + 物理/领域规则验证，非正则/自由摘要）

## 3. MVP 暂不做
- 不跑 PDF 全文结构化指标抽取（PCE / 面积 / 稳定性协议 / 架构 / 认证状态）
- 不碰中文产业字段抽取（产线 / GW 规模 / 认证 / 组件效率 / 融资金额 / 客户订单）——需另建**中文 gold set**，不能拿 PERLA 的 test set 背书
- 不做趋势数据库 / 聚合分析
- 不接入社媒与未知来源

## 4. 钙钛矿专用 feed schema（每条情报）
```json
{
  "id": "arxiv:2601.17807",
  "title": "...",
  "url": "https://arxiv.org/abs/2601.17807",
  "source_domain": "arxiv.org",
  "published_date": "2026-01-25",
  "provenance_tier": "T1",
  "type": "paper",
  "category": null,
  "summary": "LLM 基于元数据+摘要生成",
  "raw_metrics": null
}
```
- `type`: `paper | efficiency_record | company_news | patent | policy`
- `category`: 单结 / 钙钛矿-硅叠层 / 全钙钛矿叠层 / 室内光伏 / 柔性（MVP 仅可选标注）
- `raw_metrics`: MVP 留 `null`，第二阶段填

## 5. Provenance Tiers（机器判定，硬编码）
| Tier | 来源 | 处理方式 |
|------|------|---------|
| **T1** | arxiv.org, doi.org, 期刊官网, 公司官网, nrel.gov（Best Research-Cell Efficiency Chart） | 进核心结论 |
| **T2** | nomad-lab.eu, perovskitedatabase.com, 机构数据库 | 进核心结论 |
| **T3** | 媒体域名（pv-magazine.com, pv-tech.org, 中文行业媒体） | 单列标注，不进核心结论 |
| **T4** | 社媒、未知/无一手来源 | 默认不展示 |

**规则**：核心结论仅允许 T1+T2；T3 单列；T4 默认不展示。
**关键约束**：tier 由来源域名映射**硬判定**，LLM 只能解释 tier，不能决定 tier。

> ⚠️ 边界 case（待补判定规则）：公司公告/披露常发在 prnewswire / businesswire / globenewswire / 交易所 / 巨潮 / 公司公众号，**非公司官网域名**。规则：**仅当能识别发布主体为目标公司或交易所披露主体时才升 T1，否则最多 T3**（媒体/转载）。不做"域名=T1"的粗暴映射。

## 6. 技术栈建议
- **语言**：Python（与 perla-extract 统一栈，便于第二阶段衔接；避免 Node 仿 follow-builders 又切 Python 接 perla-extract）
- **采集**：openclaw 工作流定时跑 discovery（papersbot 逻辑 + arXiv API + Crossref + OpenAlex）→ 写 feed JSON（中央 repo raw / 对象存储）。核心 Python 脚本平台无关，本地可先验证再上云端。
- **用户端**：`prepare-digest` 等价脚本 fetch feed + prompts → 打包给 LLM → 摘要
- **部署**：openclaw 云端 agent 工作流（调度+采集+LLM+投递一体），个人微信通道；核心 Python 脚本平台无关，本地验证与云端共用

## 7. 三阶段路线
- **阶段1 (MVP)**：发现 + 元数据摘要 + 来源分级 + 溯源提醒（仿 follow-builders + papersbot 发现层 + 机器 tier）
- **阶段2**：复用 perla-extract + NOMAD schema + gold set 做结构化器件/产业指标抽取（英文论文用 PERLA ground truth；中文产业自建 gold set）
- **阶段3**：结构化记录入库 → 趋势分析（架构演化 / SAM 使用 / FA-rich 组成 / PCE-面积-稳定性时序）

## 8. 待定项 / 风险
- 技术栈已拍板 **Python**（见 playbook §2.7），与 perla-extract 统一栈
- 中文 gold set 标注成本高，阶段2 才启动
- tier 边界判定规则（新闻稿平台 / 交易所 / 公众号：识别发布主体为目标公司/披露主体才升 T1，否则 T3）
- MVP 若装 perla-extract 全包偏重，可只借发现逻辑自写轻量 discovery
