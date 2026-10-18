#!/usr/bin/env python3
"""资金费率套利回测器：回答"这个策略过去 N 天到底赚不赚钱"

只用公开行情（OKX 公开接口，无需 key，不下单）。
逻辑：
  - 拉取每个币种过去 N 天的资金费率结算历史（OKX 永续每 8 小时结算一次）
  - 做空永续+现货买入（delta 中性）时，每期收益 = +fundingRate；
    做多永续时，每期收益 = -fundingRate。取两者中总收益高的方向。
  - 年化 = 区间总收益率 × 365 / N。
  - 额外给出：费率翻转次数（方向突变的风险）、单期最大反向扣款、
    累计收益最大回撤（拿着不动时账面最难受的时刻）。

用法：
    python3 funding_backtest.py                # 默认 30 天，主流币
    python3 funding_backtest.py --days 90      # 回测 90 天
    python3 funding_backtest.py --coins BTC,ETH,SOL
    python3 funding_backtest.py --min-apy 10   # 只显示年化 >= 10% 的

结论怎么看：
  - best_apy < 10%：扣掉手续费和滑点基本白干，别碰
  - flips 很多：费率反复横跳，今天收钱明天倒贴，持仓体验极差
"""
import argparse
import datetime
import json
import sys
import time
import urllib.request

OKX = "https://www.okx.com"

TOP_COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX",
             "LINK", "TON", "TRX", "DOT", "NEAR", "APT", "SUI", "ARB"]


def get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "arb-scanner/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def funding_history(inst_id, days):
    """返回 [(ts_ms, rate)] 按时间升序，覆盖过去 days 天。"""
    out = []
    after = ""  # 分页游标：取更早的数据
    need = days * 3 + 5
    while len(out) < need:
        url = OKX + f"/api/v5/public/funding-rate-history?instId={inst_id}&limit=100"
        if after:
            url += f"&after={after}"
        try:
            data = get(url)["data"]
        except Exception:
            break
        if not data:
            break
        for d in data:
            out.append((int(d["fundingTime"]), float(d["fundingRate"])))
        after = data[-1]["fundingTime"]
        if len(data) < 100:
            break
        time.sleep(0.15)
    out.sort()
    cutoff = int(time.time() * 1000) - days * 86400 * 1000
    return [(ts, r) for ts, r in out if ts >= cutoff]


def backtest(coin, days):
    inst_id = f"{coin}-USDT-SWAP"
    hist = funding_history(inst_id, days)
    if len(hist) < days * 2:
        return None
    rates = [r for _, r in hist]
    total = sum(rates)
    # 方向：short_perp 收益 = +total；long_perp 收益 = -total
    if total >= 0:
        direction, pnl = "做空永续+现货买入", total
    else:
        direction, pnl = "做多永续（现货不持有）", -total
    apy = pnl * 365 / days * 100
    flips = sum(1 for a, b in zip(rates, rates[1:]) if (a > 0) != (b > 0))
    # 累计收益曲线的最大回撤（相对已实现高点）
    cum, peak, maxdd = 0.0, 0.0, 0.0
    for r in rates:
        cum += r if total >= 0 else -r
        peak = max(peak, cum)
        maxdd = max(maxdd, peak - cum)
    worst_single = min(rates) if total >= 0 else min(-r for r in rates)
    return {
        "coin": coin, "n": len(hist), "days": days,
        "direction": direction, "apy": apy,
        "flips": flips, "maxdd_pct": maxdd * 100,
        "worst_single_pct": worst_single * 100,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--coins", default=",".join(TOP_COINS))
    ap.add_argument("--min-apy", type=float, default=None)
    args = ap.parse_args()

    coins = [c.strip().upper() for c in args.coins.split(",") if c.strip()]
    rows = []
    for c in coins:
        try:
            r = backtest(c, args.days)
        except Exception as e:
            print(f"{c}: 拉取失败（{e}），跳过", file=sys.stderr)
            continue
        if r:
            rows.append(r)
        time.sleep(0.2)

    rows.sort(key=lambda r: r["apy"], reverse=True)
    if args.min_apy is not None:
        rows = [r for r in rows if r["apy"] >= args.min_apy]

    print(f"\n资金费率套利回测（OKX 永续，过去 {args.days} 天，8h 结算一次）")
    print("=" * 96)
    print(f"{'币种':<6}{'方向':<20}{'年化%':>8}{'翻转次':>7}{'最大回撤%':>10}{'单期最惨%':>10}  结论")
    print("-" * 96)
    for r in rows:
        if r["apy"] >= 10 and r["flips"] <= args.days:
            verdict = "可看"
        elif r["apy"] >= 10:
            verdict = "收益够但翻转多，难受"
        elif r["apy"] > 0:
            verdict = "不够手续费"
        else:
            verdict = "倒贴，别碰"
        print(f"{r['coin']:<6}{r['direction']:<20}{r['apy']:>8.1f}{r['flips']:>7}"
              f"{r['maxdd_pct']:>10.2f}{r['worst_single_pct']:>10.3f}  {verdict}")
    print("=" * 96)
    print("口径：年化=区间总费率×365/天数；未扣手续费/滑点/资金费之外的基差偏离。")
    print("翻转次 = 相邻两期费率正负号变化次数，越多说明今天收钱明天可能倒贴。")


if __name__ == "__main__":
    main()
