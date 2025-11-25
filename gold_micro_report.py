import requests
import time
from datetime import datetime, timedelta, timezone
import math
import yfinance as yf

# ====== 基本配置 ======
BOT_TOKEN = "8053639726:AAE_Kjpin_UGi6rrHDeDRvT9WrYVKUtR3UY"
CHAT_ID = "6193487818"

# 北京时间 = UTC+8
CN_TZ = timezone(timedelta(hours=8))

# GLD → XAU 换算系数（经验值，大约 1 股 GLD ≈ 0.093 盎司黄金）
GLD_TO_XAU_FACTOR = 10.75  # 仅用于区间参考，不作为精确报价

def send_telegram_message(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(url, data={"chat_id": CHAT_ID, "text": text})
    resp.raise_for_status()

# ========== CME 成交量 / 持仓量（带重试 + 优雅降级） ==========

def fetch_cme_oi():
    """
    抓取 CME 黄金期货（GC）持仓量 OI / 成交量 Vol
    增加重试机制：最多尝试 3 次，每次超时 15 秒
    返回 dict:
        {
            "volume": ...,
            "oi": ...,
            "change_oi": ...,
            "ok": True/False,
            "error": Optional[str]
        }
    """
    url = "https://www.cmegroup.com/CmeWS/mvc/Quotes/Future/416/G"

    last_error = None

    for attempt in range(3):
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            data = r.json()

            quote = data["quotes"]["quote"][0]

            volume = quote.get("volume", "N/A")
            open_interest = quote.get("openInterest", "N/A")
            change_oi = quote.get("changeOpenInterest", "0")

            return {
                "volume": volume,
                "oi": open_interest,
                "change_oi": change_oi,
                "ok": True,
                "error": None,
            }

        except Exception as e:
            last_error = e
            if attempt < 2:
                time.sleep(3)

    # 三次都失败，返回优雅降级结果
    return {
        "volume": "—",
        "oi": "—",
        "change_oi": "0",
        "ok": False,
        "error": str(last_error) if last_error else "Unknown error",
    }

# ========== 工具函数：GLD ↔ XAU 换算 ==========

def gld_to_xau(price: float) -> float:
    """把 GLD 价格粗略换算成 XAUUSD（仅做区间参考）"""
    return float(price) * GLD_TO_XAU_FACTOR

# ========== 期权 MaxPain / Skew / 反转带 ==========

def get_maxpain_skew_summary():
    """
    使用 yfinance 获取 GLD 期权链，计算：
    - 最近到期合约的 MaxPain 行权价
    - 反转带（上下相邻两个行权价）
    - Skew（Put/Call OI & Volume）
    - 当前 GLD 价格 & 对应 XAUUSD 估算
    - MaxPain 偏离风险 & 反转带评估
    任何一步失败则优雅降级。
    """
    try:
        ticker = yf.Ticker("GLD")

        expiries = ticker.options
        if not expiries:
            raise ValueError("无可用到期日")

        # 取最近到期的那一组
        expiry = expiries[0]

        opt_chain = ticker.option_chain(expiry)
        calls = opt_chain.calls.copy()
        puts = opt_chain.puts.copy()

        if calls.empty or puts.empty:
            raise ValueError("期权链为空")

        # 当前 GLD 收盘价
        hist = ticker.history(period="2d")
        if hist.empty:
            raise ValueError("无法获取 GLD 行情")
        spot = float(hist["Close"].iloc[-1])

        # 基础清洗：确保 openInterest / volume 为数字
        for df in (calls, puts):
            if "openInterest" not in df.columns:
                df["openInterest"] = 0
            if "volume" not in df.columns:
                df["volume"] = 0
            df["openInterest"] = df["openInterest"].fillna(0).astype(float)
            df["volume"] = df["volume"].fillna(0).astype(float)
            df["strike"] = df["strike"].astype(float)

        # 计算 MaxPain
        strikes = sorted(set(calls["strike"]).union(set(puts["strike"])))
        call_oi = dict(zip(calls["strike"], calls["openInterest"]))
        put_oi = dict(zip(puts["strike"], puts["openInterest"]))

        best_strike = None
        min_pain = None

        for S in strikes:
            total_pain = 0.0

            # Call：S > K 时，卖方支付 (S-K)*OI
            for K, oi in call_oi.items():
                if S > K and oi > 0:
                    total_pain += (S - K) * oi

            # Put：S < K 时，卖方支付 (K-S)*OI
            for K, oi in put_oi.items():
                if S < K and oi > 0:
                    total_pain += (K - S) * oi

            if min_pain is None or total_pain < min_pain:
                min_pain = total_pain
                best_strike = S

        if best_strike is None:
            raise ValueError("MaxPain 计算失败")

        max_pain = float(best_strike)

        # 反转带：MaxPain 上下相邻两个行权价
        idx = strikes.index(best_strike)
        lower_idx = max(idx - 1, 0)
        upper_idx = min(idx + 1, len(strikes) - 1)
        lower_strike = float(strikes[lower_idx])
        upper_strike = float(strikes[upper_idx])
        reversion_zone = (lower_strike, upper_strike)

        # Skew：用 Put/Call 总 OI & Volume 简化刻画仓位偏向
        call_oi_total = calls["openInterest"].sum()
        put_oi_total = puts["openInterest"].sum()
        call_vol_total = calls["volume"].sum()
        put_vol_total = puts["volume"].sum()

        oi_ratio = put_oi_total / call_oi_total if call_oi_total > 0 else None
        vol_ratio = put_vol_total / call_vol_total if call_vol_total > 0 else None

        if oi_ratio is None or vol_ratio is None:
            skew_comment = "期权仓位数据不足，暂不评估 Skew。"
        else:
            skew_score = (oi_ratio + vol_ratio) / 2.0
            if skew_score > 1.2:
                skew_comment = (
                    f"Skew 偏空：Put/Call OI≈{oi_ratio:.2f}，"
                    f"Vol≈{vol_ratio:.2f}，防跌/看空对冲仓较多。"
                )
            elif skew_score < 0.8:
                skew_comment = (
                    f"Skew 偏多：Put/Call OI≈{oi_ratio:.2f}，"
                    f"Vol≈{vol_ratio:.2f}，整体偏看涨/压上方。"
                )
            else:
                skew_comment = (
                    f"Skew 中性：Put/Call OI≈{oi_ratio:.2f}，"
                    f"Vol≈{vol_ratio:.2f}，多空仓位较均衡。"
                )

        # MaxPain 偏离
        deviation_pct = (spot - max_pain) / max_pain * 100.0

        if abs(deviation_pct) < 0.5:
            deviation_comment = (
                f"GLD 价格贴近 MaxPain（偏离约 {deviation_pct:.2f}%），"
                "更偏向围绕中枢震荡；追单前要结合 CPR / OB 位置。"
            )
        elif abs(deviation_pct) < 1.5:
            deviation_comment = (
                f"GLD 相对 MaxPain 有一定偏离（约 {deviation_pct:.2f}%），"
                "存在回补/回归 MaxPain 的可能，注意反向波动风险。"
            )
        else:
            deviation_comment = (
                f"GLD 明显偏离 MaxPain（约 {deviation_pct:.2f}%），"
                "大资金博弈激烈，补价/反向拉扯概率较高，谨慎追单。"
            )

        # 反转带评估
        if lower_strike <= spot <= upper_strike:
            reversion_comment = (
                "GLD 当前位于反转带内部 → 当日更容易在该区间内震荡/洗盘，"
                "适合区间高抛低吸，谨慎突破单。"
            )
        elif spot > upper_strike:
            reversion_comment = (
                "GLD 当前在反转带上方 → 上方压力带附近容易出现冲高回落，"
                "注意在上沿附近寻找做空/减仓机会。"
            )
        else:
            reversion_comment = (
                "GLD 当前在反转带下方 → 下方支撑附近容易出现止跌反弹，"
                "注意在下沿附近寻找低吸/止损位置。"
            )

        # GLD → XAU 换算
        xau_mp = gld_to_xau(max_pain)
        xau_zone_low = gld_to_xau(lower_strike)
        xau_zone_high = gld_to_xau(upper_strike)
        xau_spot = gld_to_xau(spot)

        return {
            "underlying": "GLD 期权",
            "expiry": expiry,
            "max_pain_gld": max_pain,
            "max_pain_xau": xau_mp,
            "reversion_zone_gld": (lower_strike, upper_strike),
            "reversion_zone_xau": (xau_zone_low, xau_zone_high),
            "spot_gld": spot,
            "spot_xau": xau_spot,
            "deviation_pct": deviation_pct,
            "deviation_comment": deviation_comment,
            "reversion_comment": reversion_comment,
            "skew_comment": skew_comment,
        }

    except Exception as e:
        return {
            "underlying": "GLD 期权",
            "expiry": "数据获取失败",
            "max_pain_gld": None,
            "max_pain_xau": None,
            "reversion_zone_gld": None,
            "reversion_zone_xau": None,
            "spot_gld": None,
            "spot_xau": None,
            "deviation_pct": None,
            "deviation_comment": f"期权数据获取失败，暂不使用 MaxPain 偏离（{type(e).__name__}）。",
            "reversion_comment": "期权数据获取失败，暂不评估反转带位置。",
            "skew_comment": f"期权数据获取失败，暂不评估 Skew（{type(e).__name__}）。",
        }
