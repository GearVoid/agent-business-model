# 钙钛矿情报雷达 · 设计指导稿 (Playbook)

> 版本：v1.0 ｜ 日期：2026-07-10
> 性质：**决策锚点 / 防偏红线**。本文件不重复架构细节（见 `perovskite-scout-spec.md`），只固化"为什么这么做、绝不做什么、走哪条路"，供后续实现随时回看，防止越写越偏。
> 来源：与 Codex 多轮可行性讨论的收敛结论（B+ → A- → A → A 四轮迭代）。

---

## 0. 这份文件怎么用

实现过程中如果冒出"要不顺便加个 XX 功能""要不改成 XX 形态"的念头，**先回看 §3 红线**和 §2 已拍板决策。只要新想法撞红线，就砍掉或降级到对应阶段，不要塞进 MVP。

---

## 1. 目标（一句话，不可动摇）

做**可信源钙钛矿情报雷达**：自动聚合多源 → 机器可信度分级 → 去重 → 结构化 feed → 溯源投递。
**不是**泛资讯摘要器，**不是**半吊子科研数据库。

---

## 2. 已拍板的核心决策（按顺序收敛）

1. **架构 = 中央情报管线**（仿 follow-builders，非简单 skill prompt）
   `定时采集 → feed JSON → 用户端读取 → LLM 只摘要/分析 → 每条必附原始链接`。
   采集与内容处理完全分离；LLM 被禁止搜索网络 / 访问 URL / 调 API。

2. **部署平台 = openclaw 云端 agent**（已定）
   - 用 openclaw 工作流替代 GitHub Actions：定时触发 → HTTP 抓源 → 代码节点做 tier+去重 → LLM 节点摘要 → 投递节点。
   - 单一实例可简化掉"中央 repo + 用户端"两层，agent 自己跑完采集→投递。
   - 核心 Python 脚本（discover_papers.py 等）平台无关，本地验证与云端部署共用同一份代码。
   - 前置校验：openclaw 云端实例需放行出站网络（arxiv.org / api.crossref.org / api.openalex.org）。

3. **投递通道 = 个人微信**（已定，覆盖 spec 原 Telegram/Email 设想）
   - 个人微信**不渲染 HTML、不支持卡片、markdown 支持差**。
   - 呈现形态 = **图片 + 文本组合**：先发一张渲染好的简报图（最贴近设计稿、好看），再补发一条纯文本把原始链接带上（图片里的链接点不了）。
   - 文本用 `[T1]/[T2]/[T3]` 标签 + 换行表达分级。
   - ⚠️ 微信单条消息有长度上限，renderer 必须分页：核心 TOP N + 摘要，完整列表发文件/链接，或拆多条。

4. **论文发现 = 借 perla-extract 的 papersbot 思路**（MVP 只取发现层）
   不急着跑全文抽取，先借 RSS/DOI/开放 PDF 发现逻辑输出论文 feed，LLM 只摘要元数据，不碰结构化器件指标。

5. **可信度分级 = 机器硬判定 provenance tiers**（T1–T4）
   - T1（进核心）：arxiv.org / doi.org / 期刊官网 / 公司官网 / nrel.gov
   - T2（进核心）：nomad-lab.eu / perovskitedatabase.com / 机构数据库
   - T3（单列标注，不进核心）：媒体域名（pv-magazine.com / pv-tech.org / 中文行业媒体）
   - T4（默认不展示）：社媒 / 未知 / 无一手来源
   - **硬规则**：tier 由来源域名映射判定，LLM 只能解释 tier，不能决定 tier。
   - ⚠️ 边界 case：公司公告/披露常发在 prnewswire / businesswire / 巨潮 / 公司公众号（非公司官网域名）。规则：仅当能识别发布主体为目标公司或交易所披露主体时才升 T1，否则最多 T3。不做"域名=T1"粗暴映射。

6. **第二阶段复用 perla-extract，不自己发明**
   - 真正工程资产：`lamalab-org/perla-extract`（含 papersbot CLI、PDF 处理、LiteLLM 抽取、evaluate CLI、ground_truth/{dev,test}）。
   - NOMAD 作 schema anchor + 验证基准（区分 human-curated 与 LLM-extracted，不可全当 gold）。
   - 中文产业情报（产线 / GW 规模 / 认证 / 组件效率 / 融资金额 / 客户订单）**需另建中文 gold set**，不能拿 PERLA test set 背书。

7. **技术栈 = Python**（与 perla-extract 统一，便于阶段2衔接；MVP 不装 perla-extract 全包重依赖，只借发现逻辑自写轻量 discovery）。

---

## 3. 防偏红线（绝不做什么）

| # | 红线 | 为什么 |
|---|------|--------|
| R1 | MVP 不做 PDF 全文结构化指标抽取（PCE/面积/稳定性/架构/认证） | PERLA 证明可行但需物理约束+验证层，不是轻量 prompt 能搞定 |
| R2 | MVP 不做中文产业字段抽取 | 需自建中文 gold set，PERLA 覆盖不了 |
| R3 | 不把 LLM 当 tier 判定者 | LLM 天然倾向给自报高分，分级会形同虚设 |
| R4 | 不做泛资讯摘要器（不收社媒/未知源进核心） | 失去"科研情报系统"定位，变成新能源资讯号 |
| R5 | 不重复造 PERLA | 20 人团队一年的管线，重复造是最大浪费 |
| R6 | 不把 openclaw 工作流退化成"LLM 读一段 SKILL.md 自行决定" | 工作流节点要显式编排（HTTP→代码→LLM→投递），不能靠 LLM 自由发挥 |
| R7 | 不为发现层硬装 perla-extract 全包（PyMuPDF/Nougat/Marker） | 重依赖会拖住 MVP，先轻量 discovery 跑通链路 |

---

## 4. 平台无关原则（关键设计约束）

```
feed-papers.json  (平台无关，T1–T4 已判定，结构化字段)
        │
        ├─ text_renderer   → 纯文本消息（个人微信兜底）
        ├─ image_renderer  → 卡片图 (PIL/HTML 转图，个人微信主呈现)
        └─ wecom_renderer  → 企微 news/markdown（备用，若将来切企微）
```

同一份 feed 数据，三种 renderer 随便换。从个人微信切企微只换 renderer，前面采集/分级逻辑一字不改。

---

## 5. 三阶段路线（回看用）

- **阶段1 (MVP)**：发现 + 元数据摘要 + 来源分级 + 溯源提醒。验收标准：能生成真实 `feed-papers.json`，每条带 `id/url/source_domain/provenance_tier/type/published_date`。
- **阶段2**：复用 perla-extract + NOMAD schema + gold set 做结构化指标抽取（英文用 PERLA ground truth；中文自建 gold set）。
- **阶段3**：结构化记录入库 → 趋势分析（架构演化 / SAM 使用 / FA-rich 组成 / PCE-面积-稳定性时序）。

---

## 6. 待补项（实现时别忘）

- tier 边界判定规则（新闻稿平台 / 交易所 / 公众号：识别发布主体为目标公司/披露主体才升 T1，否则 T3）。
- `image_renderer` 分页逻辑（微信长度上限）。
- 中文 gold set 标注（阶段2 才启动，但阶段1 要预留 schema 字段）。
