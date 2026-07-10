# openclaw 配置说明（钙钛矿情报雷达 MVP+）

外部调度系统只需做两件事：**定时调用 `deliver.py`**，再按 manifest 状态投递。

## 1. 定时任务

- **触发**：每周一 09:00（本地时区）
- **命令**（工作目录 = 项目根目录）：
  - 生产：`python scripts/deliver.py`
  - 预览 / 调试：`python scripts/deliver.py --mode preview`
- **超时**：建议 ≥ 240s（arxiv + 行业 RSS + enrich + 渲染 + 校验）

## 2. 投递决策（读 manifest）

管线结束后读 `output/delivery/delivery-manifest.json`：

| `status` | 动作 |
|----------|------|
| `ready` | 发 `output/delivery/card.png` + `output/delivery/message.txt` 到个人微信 |
| `skipped` | 不发送（本轮无新内容，旧文件已清空） |
| 命令退出码非 0 | 不发正文，发错误通知 |

> 退出码 `0` = 成功（`ready` 或 `skipped`）；非 `0` = 校验失败，脚本已主动终止，**绝不投递**。

## 3. webhook 模式（可选）

不想读文件夹时，用 webhook 出口（POST 到环境变量 `$DELIVERY_WEBHOOK`）：

```bash
python scripts/deliver.py --transport webhook
```

最小协议（脚本自动 POST）：

```json
{
  "status": "ready",
  "mode": "production",
  "message_path": "output/delivery/message.txt",
  "card_path": "output/delivery/card.png",
  "paper_count": 3,
  "industry_count": 2
}
```

`status=skipped` 时不发 webhook。

## 4. 前置（一次性）

- Python 3.13 + Pillow：`pip install -r requirements-optional.txt`
- OpenAlex 邮箱：填 `config/enrich.json` 的 `openalex_mailto`，或设环境变量 `OPENALEX_MAILTO`
- 工作目录须含 `config/`、`scripts/`、`README-perovskite-scout.md`

## 边界（已守住，调度方无需关心）

- 校验不全绿 → 不投递
- 安静周 → `skipped` + 清旧文件，openclaw 不会误发历史图文
- `production` 正常去重只推本周期新增；`preview` 看完整本轮内容
