from pathlib import Path

content = """
#  ================================
#   GOLD MICRO STRUCTURE REPORT
#   FINAL VERSION  — FIXED LBMA + TG + SHORT MAXPAIN
#  ================================

import requests
import time
from datetime import datetime, timedelta, timezone
import math
import yfinance as yf

# ===== 基本配置 =====
BOT_TOKEN = "8053639726:AAE_Kjpin_UGi6rrHDeDRvT9IwVYKUtR3UY"
CHAT_ID = "6193487818"

# 北京时间 = UTC+8
CN_TZ = timezone(timedelta(hours=8))

# GLD → XAU 换算系数（经验值，大约 1 股 GLD ≈ 0.093 盎司黄金）
GLD_TO_XAU_FACTOR = 10.75  # 仅用于区间参考，不作为精准报价


# ======= 发送 TG =======
def send_telegram_message(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, data={"chat_id": CHAT_ID, "text": text})
        resp.raise_for_status()
    except Exception as e:
        print(f"[TG 发送失败] {e}")
        print(f"返回内容: {resp.text if 'resp' in locals() else '无'}")
        raise


# ==================================================================
#  LBMA FIXING（采用昨天成功的版本）
# ==================================================================

def _fetch_latest_lbma_fix(url: str):
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    valid = [row for row in data if row.get("v") and row["v"][0]]
    if not valid:
        raise ValueError("LBMA 无有效定盘价记录")

    latest = max(valid, key=lambda x: x["d"])
    return latest["d"], float(latest["v"][0])


def get_lbma_fixing_summary():
    try:
        am_date, am_usd = _fetch_latest_lbma_fix("https://prices.lbma.org.uk/json/gold_am.json")
        pm_date, pm_usd = _fetch_latest_lbma_fix("https://prices.lbma.org.uk/json/gold_pm.json")
    except Exception as e:
        return {
            "am_fix": "None USD",
            "pm_fix": "None USD",
            "bias_comment": f"LBMA 数据获取失败（{e}）",
            "bias_score": 0
        }

    diff = pm_usd - am_usd

    if diff > 2:
        comment = f"PM({pm_usd:.2f}) > AM({am_usd:.2f}) ⟹ 多头占优，回踩支撑后偏多。"
        bias = 1
    elif diff < -2:
        comment = f"PM({pm_usd:.2f}) < AM({am_usd:.2f}) ⟹ 空头占优，反弹压力附近看空。"
        bias = -1
    else:
        comment = f"PM≈AM（{diff:.2f} 美元差）⟹ 中性震荡。"
        bias = 0

    return {
        "am_fix": f"{am_usd:.2f} USD  ({am_date})",
        "pm_fix": f"{pm_usd:.2f} USD  ({pm_date})",
        "bias_comment": comment,
        "bias_score": bias
    }


# ==================================================================
# GLD 短期期权 MaxPain（未来 1–5 天）
# ==================================================================

def get_shortterm_maxpain():
    expiry = (datetime.now(CN_TZ) + timedelta(days=3)).strftime("%Y-%m-%d")

    url = f"https://query2.finance.yahoo.com/v7/finance/options/GLD?date="
    try:
        opt = requests.get(url, timeout=10).json()
        chain = opt["optionChain"]["result"][0]
        options = chain["options"][0]
    except:
        return None

    # 计算 MaxPain
    strikes = {}
    for c in options["calls"]:
        strikes[c["strike"]] = strikes.get(c["strike"], 0) + c["openInterest"]

    for p in options["puts"]:
        strikes[p["strike"]] = strikes.get(p["strike"], 0) + p["openInterest"]

    maxpain = min(strikes, key=strikes.get)
    short_xau = maxpain * GLD_TO_XAU_FACTOR

    return {
        "expiry": expiry,
        "gld_mp": maxpain,
        "xau_mp": short_xau,
        "zone_low": (maxpain - 2) * GLD_TO_XAU_FACTOR,
        "zone_high": (maxpain + 2) * GLD_TO_XAU_FACTOR
    }


# ==================================================================
# 波动率（精简版）
# ==================================================================

def get_volatility_proxy():
    try:
        df = yf.download("GLD", period="1mo", interval="1d", progress=False)
        df["ret"] = df["Close"].pct_change()
        hv = df["ret"].std() * (252 ** 0.5)
        return hv * 100
    except:
        return None


# ==================================================================
#   主报告函数
# ==================================================================

def build_report():
    now = datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M")

    lines = []
    lines.append(f"📊 黄金微观结构报告（短期版·适合未来 1–5 天）")
    lines.append(f"时间（北京）：{now}\n")

    # ---- MaxPain ----
    mp = get_shortterm_maxpain()
    if mp:
        lines.append("【GLD 短期期权 MaxPain / Skew】")
        lines.append(f"• 到期日：{mp['expiry']}")
        lines.append(f"• 短期 MaxPain：GLD {mp['gld_mp']} ≈ XAU {mp['xau_mp']:.0f}")
        lines.append(f"• 结构区间：XAU {mp['zone_low']:.0f} - {mp['zone_high']:.0f}")
        lines.append("")
    else:
        lines.append("【GLD MaxPain】获取失败\n")

    # ---- LBMA ----
    lb = get_lbma_fixing_summary()
    lines.append("【LBMA 定盘价（精简）】")
    lines.append(f"• AM Fix: {lb['am_fix']}")
    lines.append(f"• PM Fix: {lb['pm_fix']}")
    lines.append(f"• 结论: {lb['bias_comment']}\n")

    # ---- 波动率 ----
    hv = get_volatility_proxy()
    lines.append("【波动率 Proxy（精简）】")
    if hv:
        lines.append(f"• 20 日年化波动率: {hv:.2f}%")
        lines.append("• 结论: 高波动 → 日内趋势与突破概率↑\n")
    else:
        lines.append("• 数据获取失败\n")

    # ---- 自动策略 ----
    lines.append("【短期方向（1–5 天）】")
    if mp:
        if mp["gld_mp"] < mp["gld_mp"] * 1.01:
            lines.append("→ GLD 明显高于 MaxPain（>1%），短期偏回落。\n")
        else:
            lines.append("→ 结构贴近 MaxPain，短期震荡。\n")

    return "\n".join(lines)


# ==================================================================
# 运行入口
# ==================================================================

if __name__ == "__main__":
    text = build_report()
    send_telegram_message(text)
    print("已发送 TG 报告：\n", text)

"""

path = Path("/mnt/data/gold_micro_report_final.py")
path.write_text(content, encoding="utf-8")
path
