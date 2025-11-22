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


# ========== 四件套占位函数（后面逐个接数据源） ==========

def get_cme_summary():
    """
    TODO: 用 CME / CFTC 的脚本替换这里。
    这里先返回一个示例结构，方便你先打通 TG 流程。
    """
    return {
        "symbol": "GC",
        "volume": "示例: 250k",
        "oi": "示例: 480k (+12k 增仓)",
        "comment": "示例: 增仓下跌 → 空头真实力量偏强"
    }


def get_maxpain_skew_summary():
    """
    TODO: 用 yfinance + open_interest 仓库计算 GLD 的 MaxPain 和 Skew.
    """
    return {
        "underlying": "GLD 期权",
        "expiry": "示例: 最近周五",
        "max_pain": "示例: 205",
        "skew_comment": "示例: Skew 偏空 → 上方压力大，下破支撑后易加速",
        "reversion_zone": "示例: 204.5 - 205.5"
    }


def get_lbma_fixing_summary():
    """
    TODO: 用 lbma 仓库或 Alpha Vantage 拉昨日 AM/PM Fix.
    """
    return {
        "am_fix": "示例: 2405.3",
        "pm_fix": "示例: 2412.8",
        "bias_comment": "示例: PM > AM，多头主导，回踩后仍偏多处理"
    }


def build_micro_report():
    now = datetime.now(CN_TZ)
    date_str = now.strftime("%Y-%m-%d %H:%M")

    cme = get_cme_summary()
    mp = get_maxpain_skew_summary()
    lbma = get_lbma_fixing_summary()

    lines = []
    lines.append(f"📊 黄金微观结构报告")
    lines.append(f"时间（北京）：{date_str}")
    lines.append("")
    lines.append("【CME 期货结构】")
    lines.append(f"• 品种: {cme['symbol']}")
    lines.append(f"• 成交量: {cme['volume']}")
    lines.append(f"• 持仓量(OI): {cme['oi']}")
    lines.append(f"• 评估: {cme['comment']}")
    lines.append("")
    lines.append("【期权 MaxPain / Skew】")
    lines.append(f"• 标的: {mp['underlying']}")
    lines.append(f"• 到期日: {mp['expiry']}")
    lines.append(f"• MaxPain: {mp['max_pain']}")
    lines.append(f"• 反转带: {mp['reversion_zone']}")
    lines.append(f"• 评估: {mp['skew_comment']}")
    lines.append("")
    lines.append("【LBMA 定盘价】")
    lines.append(f"• AM Fix: {lbma['am_fix']}")
    lines.append(f"• PM Fix: {lbma['pm_fix']}")
    lines.append(f"• 评估: {lbma['bias_comment']}")
    lines.append("")
    lines.append("【综合结论（示例逻辑，后续可细化）】")
    lines.append("• 示例: CME 增仓下跌 + Skew 偏空 + PM>AM：")
    lines.append("  → 日内偏空主导，反弹到关键阻力/OB 附近优先做空；")
    lines.append("  → 美盘若放量下破 CPR，下行趋势概率高。")

    return "\n".join(lines)


if __name__ == "__main__":
    text = build_micro_report()
    send_telegram_message(text)
