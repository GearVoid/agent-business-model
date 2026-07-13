# webhook 投递协议（最小约定）

`scripts/deliver.py --transport webhook` 在投递就绪时，向 `$DELIVERY_WEBHOOK` 发送一次 JSON `POST`。每次投递会生成稳定的 `delivery_id`：相同 mode 与相同 canonical feed 会得到相同 ID，接收端可据此去重重试。

`delivery_id` 同时位于 manifest、POST 顶层字段和 `Idempotency-Key` 请求头。接收端应以它作为幂等键；超时不表示接收端一定未收到请求。

Webhook 是严格出口：缺少 URL、连接/超时错误或非 2xx 响应都会使命令非零退出，写入 `status=failed`，并恢复 production dedup state。默认会清理本地 payload，避免另一个调度器误发未远端送达的内容。只有显式传入 `--allow-local-fallback` 才保留本地 payload；此时 manifest 仍为 `failed`，`reason=webhook_failed_local_fallback_available`，`remote_delivery_status=failed`，不得视作已远端送达。

整个管线、state、feed 和 delivery 目录的写入期都受跨平台单实例锁 `output/delivery/deliver.lock` 保护。锁记录 PID、开始时间和到期时间；默认一小时后才恢复过期锁（可用 `--lock-ttl-seconds` 调整）。活跃锁会使第二个进程立即非零退出，且不写 manifest 或 state。

## 状态机

| status | 含义 | 是否 POST | 调度器动作 |
|--------|------|-----------|------------|
| `ready` | 校验全绿且本地 payload 已原子组包 | 是 | webhook 成功后须同时为 `remote_delivery_status=delivered`；本地模式为 `not_requested` |
| `skipped` | 本轮无新内容（安静周） | 否 | 不发；旧文件已清空，不要发历史图文 |
| `failed` | 管线、校验或 webhook 未完成 | 否 | 不发正文；改发错误通知 |
| `preparing` | 组包中的瞬时状态 | 否 | 不发、不告警；等待命令结束后重读 manifest |

## 载荷字段（最小协议）

```json
{
  "status": "ready",
  "mode": "production",
  "delivery_id": "dly_<sha256>",
  "remote_delivery_status": "pending",
  "message_path": "output/delivery/message.txt",
  "compact_message_path": "output/delivery/message-compact.txt",
  "card_path": "output/delivery/card.png",
  "paper_count": 3,
  "industry_count": 2
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `status` | string | 稳定终态为 `ready` / `skipped` / `failed`；组包时可能短暂为 `preparing` |
| `mode` | string | `production`（默认）或 `preview` |
| `delivery_id` | string | 稳定投递幂等键；也作为 `Idempotency-Key` 请求头发送 |
| `remote_delivery_status` | string | `not_requested`（local）、`pending`（POST 前）、`delivered`（2xx）或 `failed` |
| `message_path` | string | 微信文本正文路径（相对项目根） |
| `compact_message_path` | string | 微信短版路径（相对项目根；新接收端优先） |
| `card_path` | string | 微信图片卡片路径（相对项目根） |
| `paper_count` | int | 本周期进入 feed-papers 的新论文数 |
| `industry_count` | int | 本周期进入 feed-industry 的新产业动态数 |

## 调度器实现要点

- 读 `status` 决定动作，不要自行判断内容是否值得发。
- `ready`：本地目录模式读取 `card_path` 与 `compact_message_path` 并发到个人微信；短版字段不存在时回退 `message_path`。Webhook 模式还应确认 `remote_delivery_status=delivered`。
- `skipped`：结束，不发任何东西。
- `failed`：发错误告警（不要发 `message_path` 正文，可能不完整）。
- 路径为相对项目根的路径，但它们是运行 `deliver.py` 的机器上的本地文件，不会随 webhook 上传。远端接收器只有在共享挂载并能解析相同路径时才能使用这些字段。
- 若走「读目录」而非 webhook：`python scripts/deliver.py` 默认写 `output/delivery/`，按 `delivery-manifest.json` 的 `status` 同样决策（见 openclaw-manual.md）。

为兼容已有接收端，实际 POST 保留 `text`、`image_path`、嵌套 `manifest`；新增 `compact_text` 与 `delivery_id` 可直接消费，无需接收端再次读取文件。`card_path` / `image_path` 不上传图片二进制；远端 webhook 接收器必须有共享挂载或另行实现上传层。
