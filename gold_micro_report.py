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


# ...（你原来上面的 BOT_TOKEN、CHAT_ID、CN_TZ 等保持不变）


def _fetch_latest_lbma_fix(url: str):
    """
    从 LBMA 官方 JSON 接口获取最新一条（USD 不为 0 的）定盘价记录
    示例接口：
        AM: https://prices.lbma.org.uk/json/gold_am.json
        PM: https://prices.lbma.org.uk/json/gold_pm.json
    返回: (date_str, price_usd)
    """
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()  # data 是一个列表，每个元素是 {"d": "YYYY-MM-DD", "v": [usd, gbp, eur], ...}

    # 过滤掉没有价格的数据（v[0] == 0），避免早年数据干扰
    valid_rows = [row for row in data if row.get("v") and row["v"][0]]
    if not valid_rows:
        raise ValueError("LBMA 数据为空或没有有效价格")

    # 按日期排序，取最新一条
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
        # 报错时给出提示，但不中断整个日报
        return {
            "am_fix": f"获取失败（{e}）",
            "pm_fix": f"获取失败（{e}）",
            "bias_comment": "LBMA 定盘价获取失败，暂时无法根据 Fixing 判断多空基准。"
        }

    # 正常情况下 AM/PM 日期应该相同，这里做个保护
    date_str = pm_date if pm_date == am_date else f"{am_date} / {pm_date}"

    diff = pm_usd - am_usd

    # 你可以之后调整这个阈值，现在先给一个稳健的版本
    threshold = 2.0  # 美元差值阈值，大于 2 认为方向比较明确

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
