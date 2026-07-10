# webhook 投递协议（最小约定）

`scripts/deliver.py --transport webhook` 在投递就绪时，向环境变量 `$DELIVERY_WEBHOOK` 指向的地址发送一次 `POST`（JSON，`Content-Type: application/json`）。

> 仅在 `status=ready` 时发送。`skipped` 与 `failed` 都**不**发 webhook（对应「安静周不刷屏」「校验失败不发坏内容」）。

## 状态机

| status | 含义 | 是否 POST | 调度器动作 |
|--------|------|-----------|------------|
| `ready` | 校验全绿且有新内容 | 是 | 发 `card_path` + `message_path` 到个人微信 |
| `skipped` | 本轮无新内容（安静周） | 否 | 不发；旧文件已清空，不要发历史图文 |
| `failed` | 校验未全绿（命令退出码非 0） | 否 | 不发正文；改发错误通知 |

## 载荷字段（最小协议）

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

| 字段 | 类型 | 说明 |
|------|------|------|
| `status` | string | `ready` / `skipped` / `failed` |
| `mode` | string | `production`（默认）或 `preview` |
| `message_path` | string | 微信文本正文路径（相对项目根） |
| `card_path` | string | 微信图片卡片路径（相对项目根） |
| `paper_count` | int | 本周期进入 feed-papers 的新论文数 |
| `industry_count` | int | 本周期进入 feed-industry 的新产业动态数 |

## 调度器实现要点

- 读 `status` 决定动作，不要自行判断内容是否值得发。
- `ready`：读取 `message_path` / `card_path` 两个文件并发到个人微信（图片 + 文本组合）。
- `skipped`：结束，不发任何东西。
- `failed`：发错误告警（不要发 `message_path` 正文，可能不完整）。
- 路径为相对项目根的路径，调度器需以项目根为基准解析。
- 若走「读目录」而非 webhook：`python scripts/deliver.py` 默认写 `output/delivery/`，按 `delivery-manifest.json` 的 `status` 同样决策（见 openclaw-manual.md）。
