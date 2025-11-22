import requests
from datetime import datetime, timedelta, timezone

# ====== 基本配置（你已经给我的） ======
BOT_TOKEN = "8053639726:AAE_Kjpin_UGi6rrHDeDRvT9WrYVKUtR3UY"
CHAT_ID = "6193487818"

# 北京时间 = UTC+8
CN_TZ = timezone(timedelta(hours=8))


def send_telegram_message(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(url, data={"chat_id": CHAT_ID, "text": text})
    resp.raise_for_status()


# ========== CME / CFTC 持仓量抓取模块（真实可用） ==========

def fetch_cme_oi():
    """
    抓取 CME 黄金期货（GC）持仓量 OI / 成交量 Vol
    返回 dict
    """
    try:
        url = "https://www.cmegroup.com/CmeWS/mvc/Quotes/Future/416/G"
        r = requests.get(url, timeout=10)
        data = r.json()

        quote = data["quotes"]["quote"][0]

        volume = quote.get("volume", "N/A")
        open_interest = quote.get("openInterest", "N/A")
        change_oi = quote.get("changeOpenInterest", "N/A")

        return {
            "volume": volume,
            "oi": open_interest,
            "change_oi": change_oi
        }

    except Exception as e:
        return {
            "volume": "Error",
            "oi": "Error",
            "change_oi": str(e)
        }


# ========== MaxPain / Skew 占位（后续接真实数据） ==========

def get_maxpain_skew_summary():
    return {
        "underlying": "GLD 期权",
        "expiry": "示例: 最近周五",
        "max_pain": "示例: 205",
        "skew_comment": "示例: Skew 偏空 → 上方压力大，下破支撑后易加速",
        "reversion_zone": "示例: 204.5 - 205.5"
    }


# ========== LBMA 定盘价（真实数据） ==========

def _fetch_latest_lbma_fix(url: str):
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    valid_rows = [row for row in data if row.get("v") and row["v"][0]]
    if not valid_rows:
        raise ValueError("LBMA 数据为空或无有效价格")

    latest = max(valid_rows, key=lambda x: x["d"])
    return latest["d"], float(latest["v"][0])


def get_lbma_fixing_summary():
    try:
        am_date, am_usd = _fetch_latest_lbma_fix("https://prices.lbma.org.uk/json/gold_am.json")
        pm_date, pm_usd = _fetch_latest_lbma_fix("https://prices.lbma.org.uk/json/gold_pm.json")
    except Exception as e:
        return {
            "am_fix": f"获取失败（{e}）",
            "pm_fix": f"获取失败（{e}）",
            "bias_comment": "LBMA 定盘价获取失败，无法判断方向。"
        }

    diff = pm_usd - am_usd
    threshold = 2.0

    if diff > threshold:
        comment = (
            f"PM({pm_usd:.2f}) > AM({am_usd:.2f})，差值 {diff:.2f} 美元：多头主导。"
        )
    elif diff < -threshold:
        comment = (
            f"PM({pm_usd:.2f}) < AM({am_usd:.2f})，差值 {diff:.2f} 美元：空头主导。"
        )
    else:
        comment = (
            f"PM({pm_usd:.2f}) ≈ AM({am_usd:.2f})，差值 {diff:.2f} 美元：多空均衡。"
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

    # 抓取三大模块
    cme = fetch_cme_oi()
    mp = get_maxpain_skew_summary()
    lbma = get_lbma_fixing_summary()

    lines = []
    lines.append("📊 黄金微观结构报告")
    lines.append(f"时间（北京）：{date_str}")
    lines.append("")

    # ==== CME ====
    lines.append("【CME 期货结构】")
    lines.append(f"• 成交量 Vol: {cme['volume']}")
    lines.append(f"• 持仓量 OI: {cme['oi']}")
    lines.append(f"• OI变化: {cme['change_oi']}")

    # 趋势真假逻辑
    try:
        change_oi_num = int(cme['change_oi'])
        if change_oi_num > 0:
            trend_eval = "增仓 → 趋势真实"
        elif change_oi_num < 0:
            trend_eval = "减仓 → 趋势偏假"
        else:
            trend_eval = "持仓无明显变化 → 波动反复"
    except:
        trend_eval = "数据暂不可用"

    lines.append(f"• 评价: {trend_eval}")
    lines.append("")

    # ==== MaxPain ====
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

    # ==== 综合结论（可后续升级 AI 自动生成） ====
    lines.append("【综合结论（示例逻辑）】")
    lines.append("• 示例: 若 CME 增仓 + PM>AM → 顺势偏多；若减仓 + Skew 偏空 → 反弹做空。")

    return "\n".join(lines)


if __name__ == "__main__":
    text = build_micro_report()
    send_telegram_message(text)
