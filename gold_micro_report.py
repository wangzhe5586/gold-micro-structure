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
            "ok": True/False
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
                "ok": True
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
        "ok": False
    }


# ========== 期权 MaxPain / Skew 模块（目前为结构占位，后面可接真实 GLD 期权数据） ==========

def get_maxpain_skew_summary():
    """
    使用 yfinance 获取 GLD 期权链，计算：
    - 最近到期合约的 MaxPain 行权价
    - 简单仓位 Skew（Put/Call OI & Volume）
    任何一步失败则优雅降级，返回“暂无数据”的提示。
    """
    try:
        # 1. 获取 GLD 期权链
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

        # 获取当前 GLD 现价（收盘价 / 最近价格）
        hist = ticker.history(period="1d")
        if hist.empty:
            raise ValueError("无法获取 GLD 行情")
        spot = float(hist["Close"].iloc[-1])

        # 2. 基础清洗：确保 openInterest / volume 为数字
        for df in (calls, puts):
            if "openInterest" not in df.columns:
                df["openInterest"] = 0
            if "volume" not in df.columns:
                df["volume"] = 0
            df["openInterest"] = df["openInterest"].fillna(0).astype(float)
            df["volume"] = df["volume"].fillna(0).astype(float)
            df["strike"] = df["strike"].astype(float)

        # 3. 计算 MaxPain（经典 OI 版本）
        #    - 遍历所有可能的结算价 S（用所有 strike 作为候选）
        #    - 对每个 S，计算 Call & Put 的总支付额，取最小值对应的 S 作为 MaxPain
        strikes = sorted(set(calls["strike"]).union(set(puts["strike"])))
        call_oi = dict(zip(calls["strike"], calls["openInterest"]))
        put_oi = dict(zip(puts["strike"], puts["openInterest"]))

        best_strike = None
        min_pain = None

        for S in strikes:
            total_pain = 0.0

            # Call 部分：S > K 时，卖方需要支付 (S-K) * OI
            for K, oi in call_oi.items():
                if S > K and oi > 0:
                    total_pain += (S - K) * oi

            # Put 部分：S < K 时，卖方需要支付 (K-S) * OI
            for K, oi in put_oi.items():
                if S < K and oi > 0:
                    total_pain += (K - S) * oi

            if min_pain is None or total_pain < min_pain:
                min_pain = total_pain
                best_strike = S

        if best_strike is None:
            raise ValueError("MaxPain 计算失败")

        max_pain = float(best_strike)

        # 4. 反转带（reversion zone）：取 MaxPain 上下相邻两个行权价
        idx = strikes.index(best_strike)
        lower_idx = max(idx - 1, 0)
        upper_idx = min(idx + 1, len(strikes) - 1)
        lower_strike = float(strikes[lower_idx])
        upper_strike = float(strikes[upper_idx])

        reversion_zone = f"{lower_strike:.1f} - {upper_strike:.1f}"

        # 5. Skew：用 Put/Call 总 OI & Volume 简化刻画仓位偏向
        call_oi_total = calls["openInterest"].sum()
        put_oi_total = puts["openInterest"].sum()
        call_vol_total = calls["volume"].sum()
        put_vol_total = puts["volume"].sum()

        oi_ratio = put_oi_total / call_oi_total if call_oi_total > 0 else None
        vol_ratio = put_vol_total / call_vol_total if call_vol_total > 0 else None

        if oi_ratio is None or vol_ratio is None:
            skew_comment = "期权仓位数据不足，暂不评估 Skew。"
        else:
            # 简单把 OI 比 + 成交量比做个综合
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

        # 6. 额外信息：MaxPain 相对现价的偏离
        diff_pct = (max_pain - spot) / spot * 100.0
        direction = "上方" if max_pain > spot else "下方"
        skew_comment += f" 当前 MaxPain≈{max_pain:.1f} ({direction}{abs(diff_pct):.2f}%)。"

        return {
            "underlying": "GLD 期权",
            "expiry": expiry,                     # 例如 '2025-11-22'
            "max_pain": f"{max_pain:.1f}",
            "reversion_zone": reversion_zone,
            "skew_comment": skew_comment,
        }

    except Exception as e:
        # 任何错误都优雅降级，避免 TG 里看到一大串英文报错
        return {
            "underlying": "GLD 期权",
            "expiry": "数据获取失败",
            "max_pain": "暂无",
            "reversion_zone": "暂无",
            "skew_comment": f"期权数据获取失败，暂不使用 MaxPain/Skew（{type(e).__name__}）。",
        }



# ========== LBMA 定盘价（真实数据） ==========

def _fetch_latest_lbma_fix(url: str):
    """
    从 LBMA 官方 JSON 接口获取最新一条（USD 不为 0 的）定盘价记录
    """
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    valid_rows = [row for row in data if row.get("v") and row["v"][0]]
    if not valid_rows:
        raise ValueError("LBMA 数据为空或没有有效价格")

    latest = max(valid_rows, key=lambda x: x["d"])
    date_str = latest["d"]
    usd_price = float(latest["v"][0])
    return date_str, usd_price


def get_lbma_fixing_summary():
    """
    真实版 LBMA AM/PM 定盘价：
    - 从官方 JSON 拿最新一日 AM / PM
    - 计算 PM - AM 差值
    - 给出方向文字结论
    """
    try:
        am_date, am_usd = _fetch_latest_lbma_fix("https://prices.lbma.org.uk/json/gold_am.json")
        pm_date, pm_usd = _fetch_latest_lbma_fix("https://prices.lbma.org.uk/json/gold_pm.json")
    except Exception as e:
        return {
            "am_fix": f"获取失败（{e}）",
            "pm_fix": f"获取失败（{e}）",
            "bias_comment": "LBMA 定盘价获取失败，暂时无法根据 Fixing 判断多空基准。"
        }

    diff = pm_usd - am_usd
    threshold = 2.0  # 美元差值阈值

    if diff > threshold:
        comment = (
            f"PM({pm_usd:.2f}) > AM({am_usd:.2f})，差值约 {diff:.2f} 美元："
            "整体偏多头主导，回踩支撑后偏多看待。"
        )
    elif diff < -threshold:
        comment = (
            f"PM({pm_usd:.2f}) < AM({am_usd:.2f})，差值约 {diff:.2f} 美元："
            "整体偏空头主导，反弹到压力/OB 附近偏空处理。"
        )
    else:
        comment = (
            f"PM({pm_usd:.2f}) ≈ AM({am_usd:.2f})，差值约 {diff:.2f} 美元："
            "多空力量均衡，日内更容易震荡或区间博弈。"
        )

    return {
        "am_fix": f"{am_usd:.2f} USD（{am_date}）",
        "pm_fix": f"{pm_usd:.2f} USD（{pm_date}）",
        "bias_comment": comment
    }


# ========== 构建最终报告 ==========

def build_micro_report():
    now = datetime.now(CN_TZ)
    date_str = now.strftime("%Y-%m-%d %H:%M")

    cme = fetch_cme_oi()
    mp = get_maxpain_skew_summary()
    lbma = get_lbma_fixing_summary()

    lines = []
    lines.append("📊 黄金微观结构报告")
    lines.append(f"时间（北京）：{date_str}")
    lines.append("")

    # ==== CME ====
    lines.append("【CME 期货结构】")
    if not cme["ok"]:
        lines.append("• 成交量 Vol: 暂无（CME 接口未响应）")
        lines.append("• 持仓量 OI: 暂无")
        lines.append("• OI变化: 暂无")
        lines.append("• 评价: 今日暂无法连接 CME，忽略此维度，不影响 LBMA / 期权 / TV 信号。")
        lines.append("")
    else:
        lines.append(f"• 成交量 Vol: {cme['volume']}")
        lines.append(f"• 持仓量 OI: {cme['oi']}")
        lines.append(f"• OI变化: {cme['change_oi']}")
        try:
            change_oi_num = int(cme["change_oi"])
            if change_oi_num > 0:
                trend_eval = "增仓 → 趋势真实（若上涨=真涨、若下跌=真跌）"
            elif change_oi_num < 0:
                trend_eval = "减仓 → 趋势偏假（上涨易回落 / 下跌易反弹）"
            else:
                trend_eval = "持仓无明显变化 → 方向可能反复"
        except Exception:
            trend_eval = "数据解析异常"
        lines.append(f"• 评价: {trend_eval}")
        lines.append("")

    # ==== MaxPain / Skew ====
    lines.append("【期权 MaxPain / Skew】")
    lines.append(f"• 标的: {mp['underlying']}")
    lines.append(f"• 到期日: {mp['expiry']}")
    lines.append(f"• MaxPain: {mp['max_pain']}")
    lines.append(f"• 反转带: {mp['reversion_zone']}")
    lines.append(f"• 评估: {mp['skew_comment']}")
    lines.append("")

    # ==== LBMA ====
    lines.append("【LBMA 定盘价】")
    lines.append(f"• AM Fix: {lbma['am_fix']}")
    lines.append(f"• PM Fix: {lbma['pm_fix']}")
    lines.append(f"• 评估: {lbma['bias_comment']}")
    lines.append("")

    # ==== 综合结论（后续可以再智能化） ====
    lines.append("【综合结论（示例逻辑，后续可细化）】")
    lines.append("• 示例: 若 CME 增仓 + PM>AM → 顺势偏多；若减仓 + Skew 偏空 → 反弹做空；")
    lines.append("→ 美盘若放量下破 CPR，下行趋势概率高。")

    return "\n".join(lines)


if __name__ == "__main__":
    text = build_micro_report()
    send_telegram_message(text)
