import requests
import time
from datetime import datetime, timedelta, timezone

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
    目前先用占位数据，结构已经搭好，后面可以接 GLD 期权链真实计算：
      - MaxPain = 使卖方总亏损最小的执行价
      - Skew = 看多/看空倾斜度（用看涨/看跌隐含波动率差计算）
    """
    return {
        "underlying": "GLD 期权",
        "expiry": "示例：最近周五到期",
        "max_pain": "示例：205",
        "reversion_zone": "示例：204.5 - 205.5",
        "skew_comment": "示例：Skew 偏空 → 上方压力大，下破支撑后易加速"
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
