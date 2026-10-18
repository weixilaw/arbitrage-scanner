#!/usr/bin/env python3
"""套利机会扫描器：资金费率套利 + 跨所现货价差监控（仅公开行情，不下单）

数据来源（2026-10 实测可用）：
  - OKX：永续合约资金费率 + 现货 ticker（公开接口，无需 key）
  - Binance：现货 ticker（data-api.binance.vision，无地域限制）
  - Bybit / Binance 合约：当前环境被地域限制，暂不支持

用法：
    python3 arb_scan.py                      # 一次扫描：资金费率 Top + 现货价差
    python3 arb_scan.py --watch 300          # 每 300 秒循环扫描
    python3 arb_scan.py --min-apy 30         # 只显示年化资金费率 >= 30% 的
    python3 arb_scan.py --coins BTC,ETH,SOL   # 只看指定币种

策略说明（资金费率套利）：
  资金费率为正  → 多头付钱给空头。做法：现货买入等量 + 永续做空，方向对冲，收资金费。
  资金费率为负  → 反向操作（现货卖出/不持有 + 永续做多）。
  年化 = 单次资金费率 × 3（每天结算 3 次）× 365。
"""
import argparse
import json
import sys
import time
import urllib.request

OKX = "https://www.okx.com"
BN = "https://data-api.binance.vision"

WATCH_COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX",
               "LINK", "TON", "TRX", "DOT", "MATIC", "NEAR", "APT"]


def get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "arb-scanner/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def okx_funding_rates():
    """返回 {币种: (当前费率, 年化%)}，只取 USDT 永续，按 24h 成交额取前 40。"""
    tickers = get(OKX + "/api/v5/market/tickers?instType=SWAP")["data"]
    usdt = [t for t in tickers if t["instId"].endswith("-USDT-SWAP")]
    usdt.sort(key=lambda t: float(t.get("volCcy24h") or 0), reverse=True)
    out = {}
    for t in usdt[:40]:
        coin = t["instId"].split("-")[0]
        try:
            fr = get(OKX + "/api/v5/public/funding-rate?instId=" + t["instId"])["data"][0]
            rate = float(fr["fundingRate"])
            out[coin] = (rate, rate * 3 * 365 * 100)
        except Exception:
            continue
    return out


def spot_prices(coins):
    """返回 {币种: {'okx': px, 'bn': px}} 现货价。"""
    out = {}
    for c in coins:
        row = {}
        try:
            d = get(OKX + "/api/v5/market/ticker?instId=" + c + "-USDT")["data"][0]
            row["okx"] = float(d["last"])
        except Exception:
            pass
        try:
            d = get(BN + "/api/v3/ticker/price?symbol=" + c + "USDT")
            row["bn"] = float(d["price"])
        except Exception:
            pass
        if row:
            out[c] = row
    return out


def scan_funding(min_apy):
    print("=" * 64)
    print("【资金费率套利扫描】OKX 永续合约（年化 = 单次费率 x3 x365）")
    print("=" * 64)
    rates = okx_funding_rates()
    ranked = sorted(rates.items(), key=lambda kv: abs(kv[1][1]), reverse=True)
    print("%-8s%-14s%-12s%s" % ("币种", "当前费率", "年化", "方向建议"))
    print("-" * 64)
    hits = 0
    for coin, (rate, apy) in ranked:
        if abs(apy) < min_apy:
            continue
        hits += 1
        side = "现货买入 + 合约做空（收资金费）" if rate > 0 else "合约做多（收资金费，需对冲）"
        print("%-8s%+.6f    %+.1f%%  %s" % (coin, rate, apy, side))
    if not hits:
        print("年化超 %g%% 的机会暂无。" % min_apy)
    print("")
    print("注意：费率每 8 小时结算一次且会变化；做空端需留足保证金防爆仓；")
    print("手续费约 0.05%/次（一开一平约 0.1%），年化低于 10% 基本不值得做。")


def scan_spread(coins):
    print("=" * 64)
    print("【跨所现货价差】Binance vs OKX")
    print("=" * 64)
    px = spot_prices(coins)
    print("%-8s%-14s%-14s%s" % ("币种", "Binance", "OKX", "价差"))
    print("-" * 64)
    for c in coins:
        if c not in px or "bn" not in px[c] or "okx" not in px[c]:
            continue
        bn, ok = px[c]["bn"], px[c]["okx"]
        spread = (ok - bn) / bn * 100
        flag = "  <-- 可关注" if abs(spread) > 0.3 else ""
        print("%-8s%-14.4f%-14.4f%+.3f%%%s" % (c, bn, ok, spread, flag))
    print("")
    print("说明：价差 >0.3% 才值得看；0.1% 以内基本是手续费水平。")
    print("真搬砖要在两边预存资金同时对冲下单，等转账的黄花菜都凉了。")


def main():
    ap = argparse.ArgumentParser(description="套利机会扫描器（只监控，不下单）")
    ap.add_argument("--watch", type=int, default=0, help="循环秒数，0=只跑一次")
    ap.add_argument("--min-apy", type=float, default=20, help="资金费率年化阈值%%")
    ap.add_argument("--coins", default=",".join(WATCH_COINS), help="现货价差监控币种，逗号分隔")
    args = ap.parse_args()
    coins = [c.strip().upper() for c in args.coins.split(",") if c.strip()]

    def once():
        print("")
        print("### 扫描时间 " + time.strftime("%Y-%m-%d %H:%M:%S"))
        try:
            scan_funding(args.min_apy)
        except Exception as e:
            print("资金费率扫描失败：%s" % e)
        try:
            scan_spread(coins)
        except Exception as e:
            print("现货价差扫描失败：%s" % e)

    once()
    while args.watch > 0:
        time.sleep(args.watch)
        once()


if __name__ == "__main__":
    sys.exit(main())
