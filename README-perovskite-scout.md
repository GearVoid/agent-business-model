# Perovskite Scout — 钙钛矿情报雷达 v0.1.0

可信源钙钛矿科研情报雷达。定时抓取 arXiv 论文 → 机器相关性过滤 → 机器可信度分级 → 输出纯文本简报 + 微信图片卡片。

**全链路不调用 LLM**（红线 R3）：发现、过滤、分级、渲染全部为确定性规则。

---

## 三条命令即可运行

```bash
# 1) 跑全链路：抓取 -> 过滤 -> 去重 -> enrich -> 文本/图片渲染
python scripts/run_pipeline.py [--rebuild | --ignore-state]

# 2) 校验产物完整性 + Top5 一致性 (+ feed-industry / 跨 feed 去重)
python scripts/validate_outputs.py

# 3) 投递闭环：跑管线 -> 校验 -> 组装投递包 -> 推送到出口 (详见下文「投递闭环」)
python scripts/deliver.py [--mode production|preview] [--transport local|webhook] [--allow-local-fallback]
```

`validate_outputs.py` 退出码：`0` = 全绿，`1` = 有失败项。
`deliver.py` 退出码：`0` = 已投递或确认无新内容(跳过)，`1` = 管线/校验失败未投递。

---

## 依赖

- **必需**：Python 3.10+，仅标准库（`json` / `urllib` / `pathlib` 等）。发现、过滤、分级、文本渲染、校验都不需第三方包。
- **可选**：`Pillow>=12`，仅用于生成 PNG 图片卡片。见 `requirements-optional.txt`。

```bash
pip install -r requirements-optional.txt   # 仅当需要 PNG 卡片
```

未安装 Pillow 时，`image_renderer` 可生成 HTML 供人工预览；但个人微信 `deliver.py` 的 `ready` 契约必须有 PNG，因此生产投递需安装 Pillow。

---

## 运行环境

- 任意 **Python 3.10+** 解释器即可；本地用系统 `python` 或项目托管 Python 都可。
- **openclaw 部署时，由运行环境自带的 Python 执行 `deliver.py`**，确保校验、manifest 与投递门禁全部生效。
- 终端编码问题：脚本会在入口把 stdout/stderr 重设为 UTF-8（errors=replace），即使 Windows GBK 控制台也不会因中文/希腊字母/下标符号崩溃。

---

## 投递闭环（deliver.py）

把「跑管线 → 校验 → 组装投递包 → 推送到出口」串成一条命令，让 openclaw 定时任务能直接调用，无需人工干预。

### 两种运行模式（对齐去重语义）

| 模式 | 命令 | 行为 | 何时用 |
|------|------|------|--------|
| `production`（默认） | `python scripts/deliver.py` | 正常去重，只推本周期新增 | **每周定时跑的正确用法** |
| `preview` | `python scripts/deliver.py --mode preview` | 等价于 `--ignore-state`，每次生成完整本轮内容 | 现在看效果 / 调试（注意会重复发历史，别接生产出口） |

### 出口（transport）

| transport | 行为 |
|-----------|------|
| `local`（默认） | 校验全绿后写入长版 `message.txt`、微信短版 `message-compact.txt`、`card.png` 与 manifest。新消费者优先发短版，旧消费者路径不变 |
| `webhook` | 严格远端出口：保留 `{text, image_path, manifest}`，并新增 `compact_text`、`delivery_id` 与幂等请求头；未配置或 POST 失败即非零退出并回滚去重 state。仅显式加 `--allow-local-fallback` 才保留本地包，且 manifest 仍为 `failed` |

### 安全红线（已守住）

- **校验不全绿，绝不投递**：`validate_outputs.py` 任一检查失败即写 `status: failed`、清理上一轮可发送文件并退出，避免旧 ready 包误发。
- **安静周自动跳过**：`production` 模式下若本轮论文与行业都为空（无新增），写入 `status: skipped` 并清掉上次的长版、短版与卡片，**不会发旧内容或空消息**。
- **定时模式容忍空 feed**：`deliver.py` 调用校验时自动设 `ALLOW_EMPTY_FEED=1`，把「feed 非空」从硬失败降级为通过；但字段/乱码/tier/跨 feed 去重/卡片/邮箱等检查仍严格。**手动跑 `validate_outputs.py`（不设该变量）仍保持非空硬要求**，开发与 CI 不被弱化。

### openclaw 定时任务接缝

openclaw 侧只需做两件事（本仓库不管凭证）：

1. **定时触发**：例如每周一 09:00 执行 `python scripts/deliver.py`（不加 `--mode` 即生产模式）。
2. **微信出口**：先发 `card.png`，紧接着发 `message-compact.txt`；图中的 01–07 与短版中的可点击原文链接一一对应。短版不存在时回退 `message.txt`。也可设置 `$DELIVERY_WEBHOOK` 直接 POST。

> 官方 newsroom（html-monitor）、NREL 效率图（monitored-asset）、社交/博主层均**未做**，按计划等投递闭环先稳定跑 1–2 期再加。

---

## OpenAlex 联系邮箱（配置化）

OpenAlex 鼓励在请求里带 `mailto` 以进入“礼貌池”（更高限速）。默认占位为 `perovskite-scout@example.com`。

改成真实邮箱有两种方式（**优先级：环境变量 > 配置文件**）：

```bash
# 方式 1: 环境变量 (推荐用于 openclaw / CI, 不落盘)
export OPENALEX_MAILTO=you@example.com

# 方式 2: 配置文件 config/enrich.json
{ "openalex_mailto": "you@example.com" }
```

`enrich_metadata.py` 会在每次运行时读取；改完无需动代码。`validate_outputs.py` 也会检查该邮箱是否已配置。

---

## `--rebuild` 与 `--ignore-state` 的区别

| 参数 | 行为 | 何时用 |
|------|------|--------|
| （默认，无参数） | 正常去重：已见过的 arXiv id 不再输出，只吐新增 | **每周定时跑的正确用法**，不要加任何参数 |
| `--ignore-state` | 忽略去重判定且**不修改** state；按 `preview_lookback_days` 回看窗口输出本轮内容 | 调试 / 看完整预览，不污染 production 去重记忆 |
| `--rebuild` | 先清空 `state-feed.json` 再正常去重 = 从头生成 | 改了过滤规则后重置基线 |

> 注意：`--rebuild` 会在成功扫描后替换 `state-feed.json`（去重记忆）。改 `relevance_filter.py` 规则后用它重跑，才能看到新的过滤结果。首次运行和 `--rebuild` 严格按 `bootstrap_lookback_days` 回看 14 天；将该值设为 `0` 才会显式进行无界历史回填。已持久化的生产水位线仍保留 `watermark_overlap_days` 重叠，以避免边界时刻的漏抓。
>
> 定时任务（openclaw）**只调用 `python scripts/deliver.py`，不加任何参数**。它会在校验或组包失败时回滚去重 state，避免未投递内容被提前“吃掉”。

---

## 输出产物

| 文件 | 说明 |
|------|------|
| `feed-papers.json` | 本轮命中过滤的论文（每条含 tier / score / reason） |
| `feed-industry.json` | 行业门户/专业媒体动态（与论文分开放，tier 多为 T3 + curated-media 子级） |
| `rejected-papers.json` | 被相关性过滤拒绝的论文 + `reject_reason`（审计用） |
| `rejected-industry.json` | 未命中关键词 / fetch 失败 / 被跨 feed 去重剔除的行业条目 + `reject_reason` |
| `output/perovskite-scout-digest.txt` | 纯文本简报，可直接复制到微信（含「产业动态」区） |
| `output/perovskite-scout-digest-compact.txt` | 微信短版（Top5 + 产业 Top2 标题与完整链接） |
| `output/perovskite-scout-card.png` | 微信图片卡片（研究 Top3 + 产业 Top1；编号、来源、tier、日期和确定性主题标签；`1080px` 宽；2× 超采样；需 Pillow） |
| `output/perovskite-scout-card.html` | 无 Pillow 时的图片卡片回退产物 |
| `state-feed.json` | 论文去重状态（已见 arXiv id），勿手动编辑 |
| `state-industry.json` | 行业源去重状态（已见标题/URL），勿手动编辑 |

`digest-compact.txt` 是卡片的链接伴侣：卡片显示研究 Top3 与产业 Top1，短版保留论文 Top5 与产业 Top2，使用同一组 01–07 编号。`card.png` 为排版美观**不放链接**；长版仍保留完整摘要与更多产业动态。

---

## 文件结构

```
config/sources.json          # 数据源（当前仅 arXiv）
config/sources-industry.json # 行业源（type: rss/html-monitor; source_type: curated-media/official-newsroom）
config/enrich.json           # OpenAlex 联系邮箱等 enrich 配置
scripts/discover_papers.py   # 抓取 + 去重（含 429 退避重试 + 文本清洗）
scripts/discover_industry.py # 行业源 RSS 抓取；调用统一 relevance/tier 规则入口
scripts/feed_dedup.py        # 跨 feed 去重（论文 vs 行业, 按 归一标题/URL/DOI）
scripts/relevance_filter.py  # 论文 + 产业相关性唯一判定入口
scripts/tier_mapper.py       # 机器可信度分级 T1-T4
scripts/enrich_metadata.py   # Crossref/OpenAlex 字段补全（只补字段, 不新增发现源）
scripts/text_renderer.py     # 长版 + 微信 compact 文本（含「产业动态」区）
scripts/image_renderer.py    # 2× 超采样微信卡片（研究 Top3 + 产业 Top1；编号/来源/tier/主题标签；Pillow / HTML fallback）
scripts/text_utils.py        # 共享文本卫生（防乱码 / 终端 UTF-8 重设）
scripts/run_pipeline.py      # 一键串联（论文 → 行业 → 跨 feed 去重 → 渲染）
scripts/validate_outputs.py  # 产出校验（含 tier/relevance 复算、compact、PNG 完整性）
scripts/deliver.py           # 投递闭环（跑管线→校验→组装投递包→推出口；production/preview + local/webhook）
requirements-optional.txt    # 仅 Pillow
```

`output/delivery/`：投递产物目录（由 `deliver.py` 生成）。保留兼容长版 `message.txt`，新增推荐发送的 `message-compact.txt`，再加 `card.png` 与带明确 `status` 的 manifest。

> **两条主线 + 跨 feed 去重**：`feed-papers.json`（论文）与 `feed-industry.json`（行业）刻意分开，避免把行业媒体混进科研结论。管线在 `discover_industry` 之后自动做跨 feed 去重——同一主体（如 Oxford PV）既发论文又发公告时，行业条目若与论文的**归一标题 / URL / DOI** 任一相同即被剔除，不会在微信里重复出现。标题相似度（模糊匹配）留到后续阶段。
>
> **微信图文分区**：长版最多放 5 条产业动态；图片只做可扫读的研究 Top3 + 产业 Top1，不放 URL、score、OpenAlex 或原始摘要；compact 保留论文 Top5 + 产业 Top2 的原题和可点击链接。按同一组 01–07 编号先图后文发送。

---

## 设计锚点

完整决策与红线见 `perovskite-scout-playbook.md`，架构细节见 `perovskite-scout-spec.md`。

---

## 暂未做（阶段规划）

- Crossref / OpenAlex **作为发现源**接入（当前仅用于 enrich；arXiv 仍是唯一发现源）
- 微信实际投递通道（openclaw + 个人微信）
- PDF 指标抽取（第二阶段，复用 perla-extract + NOMAD gold set）
- 抽象为通用"可信源情报雷达" skill（待多领域验证后再做）

> 阶段 1.5 已完成：`enrich_metadata.py` 用 OpenAlex（优先，给机构 + openalex_id + doi）与 Crossref（回退，补 DOI）补全 `doi / openalex_id / institutions / corresponding_source / enrich_errors`。不新增发现源、不影响 keep/reject/tier/rank。
>
> 注意：arXiv **预印本**在 OpenAlex 通常不携带机构信息，故 `institutions` 对新鲜预印本多为空列表；发表后的期刊论文才会有机构。`doi` 字段对预印本为 `10.48550/arxiv.{id}`。OpenAlex 查询必须用 `filter=doi:https://doi.org/10.48550/arxiv.{id}`（`arxiv` / `arxiv_id` 均不是合法 filter 字段）。
