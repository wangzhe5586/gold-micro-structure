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

# GLD -> XAUUSD 价格换算系数（经验值，长期比较稳定）
GLD_XAU_RATIO = 0.093  # 黄金价格 ≈ GLD / 0.093


def send_telegram_message(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(url, data={"chat_id": CHAT_ID, "text": text})
    resp.raise_for_status()


# ========== CME 成交量 / 持仓量（带重试 + CFTC 备选） ==========

def fetch_cftc_weekly_note():
    """
    CME 多次超时后的备选信息：给出 CFTC 周度报告链接，避免完全“瞎子摸象”。
    不去强行解析 txt，只给出说明文字和链接，保证脚本稳定。
    """
    try:
        url = "https://www.cftc.gov/dea/newcot/deafut.txt"
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        # 能访问就认为 CFTC 周报可用，给出手动查看链接
        note = (
            "CME 实时接口多次超时，已降级为 CFTC 周度持仓数据；"
            "建议手动查看周报： https://www.cftc.gov/dea/futures/deafut.htm"
        )
    except Exception:
        note = (
            "CME 实时接口多次超时，尝试访问 CFTC 周度报告也失败；"
            "本轮报告忽略持仓维度，仅参考 LBMA / 期权 / TV 结构。"
        )
    return {
        "volume": "—",
        "oi": "—",
        "change_oi": "0",
        "ok": False,
        "source": "CFTC",
        "note": note,
    }


def fetch_cme_oi():
    """
    抓取 CME 黄金期货（GC）持仓量 OI / 成交量 Vol
    逻辑：
      1）优先用 CME 实时接口，最多重试 3 次；
      2）若仍失败，则降级为 CFTC 周度报告（仅给出说明文字，不强行解析数字）。
    返回 dict:
        {
            "volume": ...,
            "oi": ...,
            "change_oi": ...,
            "ok": True/False,
            "source": "CME" / "CFTC",
            "note": "说明文字"
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
                "source": "CME",
                "note": "",
            }

        except Exception as e:
            last_error = e
            if attempt < 2:
                time.sleep(3)

    # 三次都失败，走 CFTC 备选逻辑
    return fetch_cftc_weekly_note()


# ========== 期权 MaxPain / Skew + 波动率 Proxy 模块 ==========

def get_maxpain_skew_summary():
    """
    使用 yfinance 获取 GLD 期权链，计算：
    - 最近到期合约的 MaxPain 行权价
    - 简单仓位 Skew（Put/Call OI & Volume）
    - 20 日历史波动率 HV + 波动等级（高 / 中等 / 低）
    - 当日 GLD 现价相对 MaxPain 的偏离幅度 -> 风险提示
    - GLD 相对反转带（Reversion Zone）的所在位置 -> 反转带说明
    任何一步失败则优雅降级，返回“暂无数据”的提示。
    """
    try:
        ticker = yf.Ticker("GLD")
        expiries = ticker.options
        if not expiries:
            raise ValueError("无可用到期日")

        # 最近到期
        expiry = expiries[0]
        opt_chain = ticker.option_chain(expiry)
        calls = opt_chain.calls.copy()
        puts = opt_chain.puts.copy()
        if calls.empty or puts.empty:
            raise ValueError("期权链为空")

        # 取 GLD 行情，用于：
        # - 现价 spot
        # - 20 日历史波动率 HV
        hist = ticker.history(period="2mo", interval="1d")
        if hist.empty:
            raise ValueError("无法获取 GLD 行情")
        spot = float(hist["Close"].iloc[-1])

        hv_20 = None
        if len(hist) >= 20:
            rets = hist["Close"].pct_change().dropna()
            last20 = rets[-20:]
            if len(last20) > 0:
                hv_20 = float(last20.std() * math.sqrt(252))

        if hv_20 is None:
            vol_level = "未知"
            vol_comment = "GLD 历史数据不足，暂无法评估波动环境。"
        else:
            if hv_20 > 0.25:
                vol_level = "高波动"
                vol_comment = (
                    f"20 日年化波动率约 {hv_20*100:.1f}%，属于高波动环境，"
                    "更容易出现大幅单边或假突破，注意控制仓位和止损。"
                )
            elif hv_20 < 0.15:
                vol_level = "低波动"
                vol_comment = (
                    f"20 日年化波动率约 {hv_20*100:.1f}%，偏低波动，"
                    "更容易走区间震荡，目标价不宜拉太远。"
                )
            else:
                vol_level = "中等波动"
                vol_comment = (
                    f"20 日年化波动率约 {hv_20*100:.1f}%，处于中等水平，"
                    "趋势与震荡机会并存，需要结合 CPR / OB 结构判断。"
                )

        # 基础清洗
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
            for K, oi in call_oi.items():
                if S > K and oi > 0:
                    total_pain += (S - K) * oi
            for K, oi in put_oi.items():
                if S < K and oi > 0:
                    total_pain += (K - S) * oi
            if min_pain is None or total_pain < min_pain:
                min_pain = total_pain
                best_strike = S

        if best_strike is None:
            raise ValueError("MaxPain 计算失败")

        max_pain = float(best_strike)

        # 反转带（MaxPain 上下相邻行权价）
        idx = strikes.index(best_strike)
        lower_idx = max(idx - 1, 0)
        upper_idx = min(idx + 1, len(strikes) - 1)
        lower_strike = float(strikes[lower_idx])
        upper_strike = float(strikes[upper_idx])
        reversion_zone = f"{lower_strike:.1f} - {upper_strike:.1f}"

        # Skew：Put/Call OI + Volume
        call_oi_total = calls["openInterest"].sum()
        put_oi_total = puts["openInterest"].sum()
        call_vol_total = calls["volume"].sum()
        put_vol_total = puts["volume"].sum()

        oi_ratio = put_oi_total / call_oi_total if call_oi_total > 0 else None
        vol_ratio = put_vol_total / call_vol_total if call_vol_total > 0 else None

        if oi_ratio is None or vol_ratio is None:
            skew_bias = "neutral"
            skew_score = 0.0
            skew_comment = "期权仓位数据不足，暂不评估 Skew。"
        else:
            skew_score = float((oi_ratio + vol_ratio) / 2.0)
            if skew_score > 1.2:
                skew_bias = "bear"
                skew_comment = (
                    f"Skew 偏空：Put/Call OI≈{oi_ratio:.2f}，"
                    f"Vol≈{vol_ratio:.2f}，防跌/看空对冲仓较多。"
                )
            elif skew_score < 0.8:
                skew_bias = "bull"
                skew_comment = (
                    f"Skew 偏多：Put/Call OI≈{oi_ratio:.2f}，"
                    f"Vol≈{vol_ratio:.2f}，整体偏看涨/压上方。"
                )
            else:
                skew_bias = "neutral"
                skew_comment = (
                    f"Skew 中性：Put/Call OI≈{oi_ratio:.2f}，"
                    f"Vol≈{vol_ratio:.2f}，多空仓位较均衡。"
                )

        # ========== MaxPain 偏离风险（核心增强） ==========
        deviation_pct = (spot - max_pain) / max_pain * 100.0  # GLD 相对 MaxPain 的偏离百分比
        if abs(deviation_pct) < 0.5:
            deviation_comment = (
                f"GLD 价格贴近 MaxPain（偏离约 {deviation_pct:.2f}%），"
                "更偏向围绕中枢震荡；追单前要结合 CPR / OB 位置。"
            )
        elif 0.5 <= deviation_pct < 1.5:
            if deviation_pct > 0:
                deviation_comment = (
                    f"GLD 略高于 MaxPain（偏离约 +{deviation_pct:.2f}%），"
                    "上方追多需谨慎，回踩中枢/反转带后再接多胜率更高。"
                )
            else:
                deviation_comment = (
                    f"GLD 略低于 MaxPain（偏离约 {deviation_pct:.2f}%），"
                    "下破空间有限，更倾向于回补中枢；盲目追空风险偏大。"
                )
        else:
            if deviation_pct > 0:
                deviation_comment = (
                    f"GLD 明显高于 MaxPain（偏离约 +{deviation_pct:.2f}%），"
                    "冲高回落/补跌风险上升，不宜高位追多。"
                )
            else:
                deviation_comment = (
                    f"GLD 明显低于 MaxPain（偏离约 {deviation_pct:.2f}%），"
                    "超跌反弹/补价差概率高，空单需谨慎。"
                )

        # ========== 反转带位置说明（Reversion Zone 解释） ==========
        try:
            lower_val, upper_val = [float(x.strip()) for x in reversion_zone.split("-")]
            if lower_val <= spot <= upper_val:
                reversion_comment = (
                    "GLD 当前位于反转带内部 → 当日更容易在该区间内震荡/洗盘，"
                    "适合区间高抛低吸，谨慎突破单。"
                )
            elif spot > upper_val:
                reversion_comment = (
                    "GLD 位于反转带上方 → 向下回补该区间的概率较高，"
                    "高位做空要优先参考 OB / CPR 共振位置。"
                )
            else:  # spot < lower_val
                reversion_comment = (
                    "GLD 位于反转带下方 → 向上反弹回补该区间的概率较高，"
                    "低位盲目追空风险较大。"
                )
        except Exception:
            reversion_comment = "反转带位置解析失败。"

        # 附加：MaxPain 相对 spot 的简单说明
        diff_pct_spot = (max_pain - spot) / spot * 100.0
        direction = "上方" if max_pain > spot else "下方"
        skew_comment += f" 当前 MaxPain≈{max_pain:.1f}（在现价{direction}{abs(diff_pct_spot):.2f}%）。"

        return {
            "underlying": "GLD 期权",
            "expiry": expiry,
            "max_pain": f"{max_pain:.1f}",
            "reversion_zone": reversion_zone,
            "skew_comment": skew_comment,
            "skew_bias": skew_bias,
            "skew_score": skew_score,
            "hv_20": hv_20,
            "vol_level": vol_level,
            "vol_comment": vol_comment,
            "spot_gld": spot,
            "deviation_pct": deviation_pct,
            "deviation_comment": deviation_comment,
            "reversion_comment": reversion_comment,
        }

    except Exception as e:
        return {
            "underlying": "GLD 期权",
            "expiry": "数据获取失败",
            "max_pain": "暂无",
            "reversion_zone": "暂无",
            "skew_comment": f"期权数据获取失败，暂不使用 MaxPain/Skew（{type(e).__name__}）。",
            "skew_bias": "neutral",
            "skew_score": 0.0,
            "hv_20": None,
            "vol_level": "未知",
            "vol_comment": "期权数据获取失败，暂不判断波动环境。",
            "spot_gld": None,
            "deviation_pct": None,
            "deviation_comment": "MaxPain 偏离风险暂不可用。",
            "reversion_comment": "反转带位置暂不可用。",
        }


# ========= GLD → 黄金 XAUUSD 自动换算工具 =========

def gld_to_xau(gld_price: float) -> float:
    """将 GLD 价格转换为黄金美元价格：黄金价格 ≈ GLD / 0.093"""
    return gld_price / GLD_XAU_RATIO


def convert_gld_zone_to_xau(zone_str: str) -> str:
    """将 '374.0 - 376.0' 形式的反转带转换为黄金价格区间"""
    try:
        lower, upper = zone_str.split("-")
        lower = float(lower.strip())
        upper = float(upper.strip())
        xau_lower = gld_to_xau(lower)
        xau_upper = gld_to_xau(upper)
        return f"{xau_lower:.0f} - {xau_upper:.0f}"
    except Exception:
        return "转换失败"


# ========== LBMA 定盘价（真实数据） ==========

def _fetch_latest_lbma_fix(url: str):
    """从 LBMA 官方 JSON 接口获取最新一条（USD 不为 0 的）定盘价记录"""
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
            "bias_comment": "LBMA 定盘价获取失败，暂时无法根据 Fixing 判断多空基准。",
            "am_val": None,
            "pm_val": None,
            "diff": None,
        }

    diff = pm_usd - am_usd
    threshold = 2.0

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
        "bias_comment": comment,
        "am_val": am_usd,
        "pm_val": pm_usd,
        "diff": diff,
    }


# ========== 多空结构星级评分（Skew + LBMA） ==========

def build_bias_rating(cme, mp, lbma):
    """
    综合 Skew + LBMA，给出 -2 ~ +2 的多空评分，并转成星级。
    CME 在这里不直接打分，只用于结论部分做“真假趋势”参考。
    """
    score = 0
    detail_parts = []

    # 期权 Skew
    skew_bias = mp.get("skew_bias", "neutral")
    if skew_bias == "bull":
        score += 1
        detail_parts.append("期权 Skew 偏多（仓位偏向看涨一侧）（+1）")
    elif skew_bias == "bear":
        score -= 1
        detail_parts.append("期权 Skew 偏空（仓位偏向防跌/看空）（-1）")
    else:
        detail_parts.append("期权 Skew 中性（0）")

    # LBMA PM-AM
    diff = lbma.get("diff")
    if diff is not None:
        if diff > 2:
            score += 1
            detail_parts.append("LBMA PM 明显高于 AM，偏多头（+1）")
        elif diff < -2:
            score -= 1
            detail_parts.append("LBMA PM 明显低于 AM，偏空头（-1）")
        else:
            detail_parts.append("LBMA PM≈AM，多空均衡（0）")
    else:
        detail_parts.append("LBMA 数据缺失（0）")

    # 限制范围
    score = max(-2, min(2, score))

    if score == 2:
        stars = "★★★★★ 强多头"
        direction_comment = "整体强多头结构，日内以逢低做多为主，空单仅作为短线反弹博弈。"
    elif score == 1:
        stars = "★★★★☆ 偏多"
        direction_comment = "整体偏多，优先考虑顺势多单，高位空单以短线为主。"
    elif score == 0:
        stars = "★★★☆☆ 中性震荡"
        direction_comment = "多空力量接近平衡，适合区间思路，高抛低吸为主，谨慎追单。"
    elif score == -1:
        stars = "★★☆☆☆ 偏空"
        direction_comment = "整体偏空，反弹到 CPR / OB 上沿更适合做空，多单以短线为主。"
    else:
        stars = "★☆☆☆☆ 强空头"
        direction_comment = "强空结构，反弹做空为主，谨慎接多，注意控制仓位。"

    return {
        "score": score,
        "stars": stars,
        "direction_comment": direction_comment,
        "detail": "；".join(detail_parts),
    }


def build_auto_conclusion(cme, mp, lbma, rating):
    """
    生成综合交易结论：结构评级 + 波动环境 + MaxPain 偏离风险 + CME 真假趋势 + 策略倾向
    """
    parts = []

    # 结构评级
    parts.append(f"• 结构评级: {rating['stars']} → {rating['direction_comment']}")

    # 波动环境
    vol_level = mp.get("vol_level", "未知")
    if vol_level == "高波动":
        parts.append("• 波动环境: 高波动 → 更适合突破/趋势单，止损要更果断。")
    elif vol_level == "低波动":
        parts.append("• 波动环境: 低波动 → 更适合区间博弈，止盈目标不宜太远。")
    elif vol_level == "中等波动":
        parts.append("• 波动环境: 中等波动 → 趋势与震荡机会并存，重点结合 CPR / OB 区域。")
    else:
        parts.append("• 波动环境: 数据不足，暂不评价。")

    # MaxPain 偏离风险（直接复用上面计算）
    dev_pct = mp.get("deviation_pct")
    dev_comment = mp.get("deviation_comment")
    if dev_pct is not None:
        parts.append(f"• MaxPain 偏离: 当前 GLD 相对 MaxPain 偏离约 {dev_pct:.2f}% → {dev_comment}")

    # CME 真假趋势 / 备选 CFTC 说明
    if cme["source"] == "CFTC":
        parts.append(f"• CME/CFTC: {cme['note']}")
    else:
        if not cme["ok"]:
            parts.append("• CME：实时数据缺失 → 以 LBMA + 期权结构为主，CME 暂时忽略。")
        else:
            try:
                change_oi = int(cme["change_oi"])
                if change_oi > 0:
                    parts.append("• CME：增仓 → 当前方向更容易延续，不宜重仓逆势。")
                elif change_oi < 0:
                    parts.append("• CME：减仓 → 当前走势更可能是假突破，适合等待反向确认。")
                else:
                    parts.append("• CME：持仓平稳 → 容易走震荡或假突破。")
            except Exception:
                parts.append("• CME：OI 变化解析失败 → 暂时忽略。")

    # 策略倾向
    s = rating["score"]
    if s >= 1:
        parts.append("→ 策略倾向：日内以 **顺势多单** 为主；4H/1H OB / CPR 上方短空为辅。")
    elif s <= -1:
        parts.append("→ 策略倾向：日内以 **反弹做空** 为主；关键支撑附近轻仓多单博反弹。")
    else:
        parts.append("→ 策略倾向：**区间震荡思路**，在 OB / CPR 区间两端高抛低吸，避免追高杀跌。")

    return "\n".join(parts)


# ========== 构建最终报告 ==========

def build_micro_report():
    now = datetime.now(CN_TZ)
    date_str = now.strftime("%Y-%m-%d %H:%M")

    cme = fetch_cme_oi()
    mp = get_maxpain_skew_summary()
    lbma = get_lbma_fixing_summary()
    rating = build_bias_rating(cme, mp, lbma)

    lines = []
    lines.append("📊 黄金微观结构报告")
    lines.append(f"时间（北京）：{date_str}")
    lines.append("")

    # ==== CME ====
    lines.append("【CME / CFTC 持仓结构】")
    if cme["source"] == "CME" and not cme["ok"]:
        lines.append("• 成交量 Vol: 暂无（CME 实时接口未响应）")
        lines.append("• 持仓量 OI: 暂无")
        lines.append("• OI变化: 暂无")
        lines.append("• 评价: 今日暂无法可靠获取 CME 数据，忽略此维度，不影响 LBMA / 期权 / TV 信号。")
        lines.append("")
    elif cme["source"] == "CFTC":
        lines.append("• 数据来源: CFTC 周度持仓报告（CME 实时接口多次超时）")
        lines.append(f"• 说明: {cme['note']}")
        lines.append("• 评价: 本报告中不使用具体持仓数字，仅把周度持仓作为背景参考。")
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

    # ==== MaxPain / Skew + 反转带 ====
    lines.append("【期权 MaxPain / Skew】")
    lines.append(f"• 标的: {mp['underlying']}")
    lines.append(f"• 到期日: {mp['expiry']}")
    if mp["max_pain"] == "暂无":
        lines.append("• MaxPain: 暂无")
        lines.append("• 反转带: 暂无")
    else:
        max_pain_val = float(mp["max_pain"])
        xau_mp = gld_to_xau(max_pain_val)
        xau_zone = convert_gld_zone_to_xau(mp["reversion_zone"])
        lines.append(f"• MaxPain(GLD): {mp['max_pain']}  ≈ XAU {xau_mp:.0f} 美元")
        lines.append(f"• 反转带(GLD): {mp['reversion_zone']}  ≈ XAU {xau_zone} 美元")
        lines.append(f"• 当前 GLD 价格: {mp['spot_gld']:.2f}，相对 MaxPain 偏离约 {mp['deviation_pct']:.2f}%")
    lines.append(f"• 偏离风险: {mp['deviation_comment']}")
    lines.append(f"• 反转带评估: {mp['reversion_comment']}")
    lines.append(f"• Skew评估: {mp['skew_comment']}")
    lines.append("")

    # ==== 波动率 Proxy ====
    lines.append("【波动率 Proxy】")
    if mp["hv_20"] is None:
        lines.append("• 20 日年化波动率: 暂无")
    else:
        lines.append(f"• 20 日年化波动率: {mp['hv_20']*100:.1f}%")
    lines.append(f"• 波动等级: {mp['vol_level']}")
    lines.append(f"• 评估: {mp['vol_comment']}")
    lines.append("")

    # ==== LBMA ====
    lines.append("【LBMA 定盘价】")
    lines.append(f"• AM Fix: {lbma['am_fix']}")
    lines.append(f"• PM Fix: {lbma['pm_fix']}")
    lines.append(f"• 评估: {lbma['bias_comment']}")
    lines.append("")

    # ==== 多空结构评分 ====
    lines.append("【多空结构评分】")
    lines.append(f"• 评级: {rating['stars']}")
    lines.append(f"• 方向结论: {rating['direction_comment']}")
    lines.append(f"• 说明: {rating['detail']}")
    lines.append("")

    # ==== 综合结论 ====
    lines.append("【综合结论】")
    lines.append(build_auto_conclusion(cme, mp, lbma, rating))

    return "\n".join(lines)


if __name__ == "__main__":
    text = build_micro_report()
    send_telegram_message(text)
