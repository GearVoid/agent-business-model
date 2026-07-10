---
name: perovskite-scout
description: >-
  钙钛矿光伏情报雷达。从 arXiv（论文）与行业 RSS（产业动态）抓取，机器判定可信度(tier)与相关性，过滤、跨 feed 去重、生成微信可发的文本简报与图片卡片，经校验后投递到个人微信。当用户的意图是运行/调度/调试 perovskite-scout、生成「钙钛矿情报雷达」周报、查看投了哪些内容、或在 openclaw 等调度器接入投递时使用。注意：脚本位于被引用项目仓库的 scripts/ 下，本技能包不复制、只引用。
---

# perovskite-scout

钙钛矿光伏情报雷达：**论文（arXiv）+ 产业（行业 RSS）双 feed**，机器判定可信度与相关性，生成微信可发的图文，经校验后安全投递。

## 项目根（重要）

本技能操作的是 **perovskite-scout 项目仓库**。`scripts/` 与 `config/` 就在该仓库根目录。
**所有命令都必须从项目根目录运行**（即包含 `scripts/` 的那个目录），不要从本 `perovskite-scout-skill/` 子目录运行。

> 本技能包只做入口说明与引用，**不复制 `scripts/`**。任何抓取 / 过滤 / 渲染 / 校验逻辑都以项目仓库里的脚本为唯一可信源。

## 触发场景

- 用户要生成 / 调度「钙钛矿情报雷达」周报
- 用户要调试 `deliver` / `validate` / 抓取 / 渲染
- 用户问「这周钙钛矿有什么新进展 / 投了什么」
- 用户要在 openclaw / 其他调度器里接入定时投递

## 默认命令（务必从项目根运行）

```bash
# 生产：每周定时跑，正常去重，只推本周期新增
python scripts/deliver.py

# 预览：调试用，--ignore-state 忽略去重，看完整本轮内容
python scripts/deliver.py --mode preview

# 仅校验（不投递）：检查产物完整性，全绿才允许投递
python scripts/validate_outputs.py

# 手动全链路（生成 feed + 图文，不改投递）：
python scripts/run_pipeline.py [--rebuild | --ignore-state]
```

## 产物路径（相对项目根）

| 路径 | 说明 |
|------|------|
| `output/delivery/message.txt` | 微信文本正文（可直接复制发送） |
| `output/delivery/card.png` | 微信图片卡片（Top5 论文 + 产业动态 2 条，1080px 宽） |
| `output/delivery/delivery-manifest.json` | 投递决策依据（status 见下） |
| `feed-papers.json` / `feed-industry.json` | 结构化数据（论文 / 产业两条主线） |

`delivery-manifest.json` 的 `status` 取值与动作：

- `ready` → 发 `card.png` + `message.txt`
- `skipped` → 本轮无新内容，**不发送**（旧文件已清空）
- 命令退出码非 0 → 校验失败，**不发正文**，改发错误通知

## 红线（不可违反）

1. **tier 只能由 `scripts/tier_mapper.py` 判定**（T1–T4）。
2. **relevance 只能由 `scripts/relevance_filter.py` 判定**（相关性分数 / 是否进入 feed）。
3. **LLM 不得决定可信度、相关性、是否进入 feed**——以上全部由规则管线判定。
4. **投递前必须 `python scripts/validate_outputs.py` 全绿。**
5. **validate 失败不得投递**（deliver.py 已内置该闸门，不要绕过）。
6. **production 安静周 `status=skipped`，不发旧内容**（deliver.py 会清空上次 message/card）。
7. **社交 / 博主层继续 DEFER**——本轮不接 X / LinkedIn / 公众号 / 个人动态。

## 引用（不要复制脚本）

- `scripts/deliver.py` —— 唯一投递入口（production / preview，local / webhook）
- `scripts/validate_outputs.py` —— 校验闸门（24 项）
- `scripts/run_pipeline.py` —— 全链路串联
- 详细设计：`references/perovskite-scout-spec.md`
- 设计纪律 / 反漂移：`references/perovskite-scout-playbook.md`
- openclaw 定时配置：`references/openclaw-manual.md`
- webhook 投递协议：`references/webhook-contract.md`
