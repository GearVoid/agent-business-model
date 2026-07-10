"""deliver.py — 钙钛矿情报雷达 投递闭环 (MVP+ 最后一公里).

把「跑管线 → 校验 → 组装投递包 → 推送到出口」串成一条命令，
让 openclaw 定时任务能直接调用，无需人工干预。

用法:
  python scripts/deliver.py                      # 生产: 正常去重, 只推本周期新增
  python scripts/deliver.py --mode preview       # 预览: --ignore-state, 看完整本轮内容
  python scripts/deliver.py --transport webhook  # 推送到一个 HTTP 出口(需 $DELIVERY_WEBHOOK)

两种运行模式 (对齐 run_pipeline 的去重语义):
  production  默认。run_pipeline 不带 --ignore-state, 已见过的 arXiv id / 行业条目
              不再重复推送, 只发本周期新增。适合长期每周定时跑。
  preview     等价于 run_pipeline --ignore-state。每次生成完整本轮内容 (忽略 state),
              适合你现在看效果 / 调试。注意: preview 会重复发历史内容, 不要接生产出口。

出口 (transport):
  local    默认。校验全绿后, 把投递包写到 output/delivery/ :
              message.txt         微信文本正文 (digest 内容 + 头部一行)
              card.png            图片卡片副本 (直接可发)
              delivery-manifest.json  元数据 (模式/时间/各 feed 条数/文件路径)
            openclaw 侧只要读取 output/delivery/ 这两个文件, 推到个人微信即可。
  webhook  可选。若环境变量 $DELIVERY_WEBHOOK 存在, 把 {text, image_path, manifest}
            以 JSON POST 出去。用于你已有 HTTP 推送端点 (openclaw / 自建 bot) 的情况。

安全红线:
  - 校验 (validate_outputs) 不全绿, 绝不投递 (避免把坏数据推到你微信)。
  - 无新增内容 (production 模式下 papers+industry 都为 0 新增) 时, 跳过投递并提示,
    不会发一条空消息刷屏。

退出码: 0=已投递(或确认无新内容跳过); 1=管线/校验失败未投递。
"""

import argparse
import json
import os
import shutil
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
BASE = Path(__file__).resolve().parent.parent
OUTPUT = BASE / "output"
DELIVERY_DIR = OUTPUT / "delivery"

import run_pipeline  # noqa: E402
import validate_outputs  # noqa: E402

# 复用 feed 路径常量
FEED_PAPERS = BASE / "feed-papers.json"
FEED_INDUSTRY = BASE / "feed-industry.json"
STATE_PAPERS = BASE / "state-feed.json"
STATE_INDUSTRY = BASE / "state-industry.json"
DIGEST = OUTPUT / "perovskite-scout-digest.txt"
CARD_PNG = OUTPUT / "perovskite-scout-card.png"


def new_count(state_path: Path) -> int:
    """估算本周期新增条数 = 当前 feed 总条数 (state 文件不一定含增量标记, 用全量近似)。

    说明: 去重后的 feed 就是「当前已发现全部」。真正「本周期新增」需要 diff state,
    但 MVP 阶段我们用更稳妥的策略: 若 feed 非空就投递 (preview 永远投递; production
    由 run_pipeline 的去重保证只含新增)。这里返回 feed 条数仅用于 manifest 展示。
    """
    if not state_path.exists():
        return 0
    return 0  # 详见 run_pipeline 去重; 实际是否投递由下方 has_content 决定


def feed_len(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return int(json.loads(path.read_text(encoding="utf-8")).get("count", 0))
    except Exception:  # noqa: BLE001
        return 0


def has_new_content(mode: str) -> bool:
    """是否值得投递: 至少 feed 之一有内容。

    preview 模式: 有内容就投。
    production 模式: 同样有内容就投 —— run_pipeline 已保证只含去重后的结果;
                    若某周 arXiv/行业都无新命中, feed 为空, 自然跳过。
    """
    return feed_len(FEED_PAPERS) > 0 or feed_len(FEED_INDUSTRY) > 0


def build_message(mode: str) -> str:
    """组装微信文本正文。直接复用 digest.txt (它本就是微信可复制格式)。"""
    if not DIGEST.exists():
        return ""
    body = DIGEST.read_text(encoding="utf-8")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header = (
        f"# 钙钛矿情报雷达 · {stamp}\n"
        f"# 模式: {mode}\n"
        f"# 论文 {feed_len(FEED_PAPERS)} 条 · 产业 {feed_len(FEED_INDUSTRY)} 条\n"
        f"{'-' * 24}\n"
    )
    return header + body


def write_local(message: str, mode: str) -> Path:
    """transport=local: 写 output/delivery/。返回 manifest 路径。"""
    DELIVERY_DIR.mkdir(parents=True, exist_ok=True)
    # 清理旧卡片副本, 避免发错版本 (sandbox 下 unlink 可能被拦截, best-effort)
    for old in DELIVERY_DIR.glob("card*.png"):
        try:
            old.unlink()
        except OSError:
            pass
    (DELIVERY_DIR / "message.txt").write_text(message, encoding="utf-8")
    if CARD_PNG.exists():
        shutil.copy2(CARD_PNG, DELIVERY_DIR / "card.png")
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "papers_count": feed_len(FEED_PAPERS),
        "industry_count": feed_len(FEED_INDUSTRY),
        "text_file": "message.txt",
        "image_file": "card.png" if CARD_PNG.exists() else None,
        "delivery_dir": str(DELIVERY_DIR),
    }
    mpath = DELIVERY_DIR / "delivery-manifest.json"
    mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return mpath


def send_webhook(message: str, manifest: dict) -> bool:
    """transport=webhook: POST 到 $DELIVERY_WEBHOOK。返回是否成功。"""
    url = os.environ.get("DELIVERY_WEBHOOK")
    if not url:
        print("[SKIP] webhook 未配置 $DELIVERY_WEBHOOK, 退回 local 模式写入")
        return False
    payload = {
        "text": message,
        "image_path": str(DELIVERY_DIR / "card.png"),
        "manifest": manifest,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f"[OK] webhook POST -> {resp.status}")
            return True
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] webhook POST 失败: {e}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="钙钛矿情报雷达 投递闭环")
    ap.add_argument(
        "--mode",
        choices=["production", "preview"],
        default="production",
        help="production=正常去重只推新增(默认); preview=--ignore-state 看完整内容",
    )
    ap.add_argument(
        "--transport",
        choices=["local", "webhook"],
        default="local",
        help="local=写 output/delivery/(默认); webhook=POST 到 $DELIVERY_WEBHOOK",
    )
    args = ap.parse_args()

    print(f"\n=== deliver [{args.mode}] transport={args.transport} ===")

    # 1) 跑管线
    pipeline_args = ["--ignore-state"] if args.mode == "preview" else []
    saved = sys.argv
    try:
        sys.argv = ["run_pipeline.py"] + pipeline_args
        rc = run_pipeline.main()
    finally:
        sys.argv = saved
    if rc != 0:
        print("[FAIL] 管线失败, 终止投递")
        return 1

    # 2) 校验 (全绿才投)
    #    定时投递模式下允许 feed 为空 (安静周不报错), 但其它检查 (字段/乱码/tier/
    #    跨 feed 去重/卡片/邮箱) 仍严格。这等同于手动跑 validate_outputs 时设
    #    ALLOW_EMPTY_FEED=1。开发/CI 直接跑 validate 仍保持非空硬要求。
    os.environ["ALLOW_EMPTY_FEED"] = "1"
    saved = sys.argv
    try:
        sys.argv = ["validate_outputs.py"]
        vrc = validate_outputs.main()
    finally:
        sys.argv = saved
    if vrc != 0:
        print("[FAIL] 校验未全绿, 终止投递 (不把坏数据推到微信)")
        return 1

    # 3) 是否值得投 (安静周: 两 feed 都为空 -> 跳过, 不刷屏)
    if not has_new_content(args.mode):
        print("[OK] 本轮无新内容 (论文0 产业0), 跳过投递 (不刷屏)")
        DELIVERY_DIR.mkdir(parents=True, exist_ok=True)
        # 清掉上次的旧投递产物, 避免 openclaw "看到文件就发" 误发旧内容
        for stale in ("message.txt", "card.png"):
            try:
                (DELIVERY_DIR / stale).unlink()
            except OSError:
                pass
        skip = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": args.mode,
            "status": "skipped",
            "reason": "no_new_content",
            "papers_count": feed_len(FEED_PAPERS),
            "industry_count": feed_len(FEED_INDUSTRY),
        }
        (DELIVERY_DIR / "delivery-manifest.json").write_text(
            json.dumps(skip, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return 0

    # 4) 组装投递包
    message = build_message(args.mode)
    mpath = write_local(message, args.mode)
    print(f"[OK] 本地投递包已生成: {DELIVERY_DIR}")
    print(f"     - message.txt ({len(message)} 字)")
    if (DELIVERY_DIR / 'card.png').exists():
        print(f"     - card.png")

    # 5) 推送出口
    if args.transport == "webhook":
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
        ok = send_webhook(message, manifest)
        if not ok:
            # 已经写好了 local 包, 这里只提示
            print("[NOTE] 已退回 local 包, openclaw 可读 output/delivery/ 推送")
    else:
        print("[NOTE] transport=local: openclaw 读取 output/delivery/{message.txt,card.png} 推到个人微信")

    print("\n[OK] 投递闭环完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
