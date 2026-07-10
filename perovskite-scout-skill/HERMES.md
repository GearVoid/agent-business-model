# perovskite-scout（HermesAgent 入口）

钙钛矿光伏情报雷达。论文(arXiv) + 产业(行业 RSS) 双 feed，机器判级后生成微信图文并校验投递。

## 运行（从项目根目录，含 scripts/ 的目录）

```bash
python scripts/deliver.py                      # 生产：去重，只推新增
python scripts/deliver.py --mode preview       # 预览：忽略去重，看完整本轮
python scripts/validate_outputs.py             # 仅校验，不投递
python scripts/run_pipeline.py [--rebuild | --ignore-state]  # 手动全链路
```

## 产物

`output/delivery/message.txt`（文本）、`output/delivery/card.png`（图片）、`output/delivery/delivery-manifest.json`（决策依据）。

## 红线（不可违反）

1. tier 只能由 `scripts/tier_mapper.py` 判定（T1–T4）。
2. relevance 只能由 `scripts/relevance_filter.py` 判定。
3. LLM 不得决定可信度、相关性、是否进入 feed。
4. 投递前必须 `python scripts/validate_outputs.py` 全绿。
5. validate 失败不得投递。
6. production 安静周 `status=skipped`，不发旧内容。
7. 社交 / 博主层继续 DEFER（不接 X / LinkedIn / 公众号 / 个人动态）。

## 说明

本目录不复制 `scripts/`，只引用项目仓库中的脚本。完整设计见 `references/`。
