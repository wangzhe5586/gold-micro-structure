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


# ========== 波动率 Proxy（不付费 IV，基于历史波动率） ==========
def get_vol_proxy():
    """
    用 GLD 过去 20 个交易日的历史波动率，做一个“简化版波动率指标”：
    - hv_20: 20 日年化波动率（%）
    - level: 低波动 / 中等波动 / 高波动
    """
    try:
        ticker = yf.Ticker("GLD")
        hist = ticker.history(period="60d")
        if hist.empty or len(hist) < 22:
            raise ValueError("历史数据不足")

        # 计算对数收益率
        hist["ret"] = (hist["Close"] / hist["Close"].shift(1)).apply(lambda x: math.log(x))
        rets = hist["ret"].dropna().tail(20)
        if rets.empty:
            raise ValueError("无法计算波动率")

        # 年化波动率
        hv_20 = float(rets.std() * math.sqrt(252) * 100)

        if hv_20 < 15:
            level = "低波动"
            comment = (
                f"20 日年化波动率约 {hv_20:.1f}%，处于低波动环境，"
                "价格更容易在关键区间内反复来回，突破需要更大成交配合。"
            )
        elif hv_20 < 25:
            level = "中等波动"
            comment = (
                f"20 日年化波动率约 {hv_20:.1f}%，处于中等水平，"
                "趋势与震荡机会并存，需要结合 CPR / OB 结构判断。"
            )
        else:
            level = "高波动"
            comment = (
                f"20 日年化波动率约 {hv_20:.1f}%，处于高波动阶段，"
                "假突破和剧烈拉扯都更频繁，仓位和止损需要更保守。"
            )

        return {
            "hv_20": hv_20,
            "level": level,
            "comment": comment,
        }
    except Exception as e:
        return {
            "hv_20": None,
            "level": "数据获取失败",
            "comment": f"波动率数据获取失败（{type(e).__name__}），暂不根据 HV 调整仓位。",
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
            "bias_comment": "LBMA 定盘价获取失败，暂时无法根据 Fixing 判断多空基准。",
            "bias_score": 0.0,
        }

    diff = pm_usd - am_usd
    threshold = 2.0  # 美元差值阈值

    if diff > threshold:
        comment = (
            f"PM({pm_usd:.2f}) > AM({am_usd:.2f})，差值约 {diff:.2f} 美元："
            "整体偏多头主导，回踩支撑后偏多看待。"
        )
        bias_score = 1.0
    elif diff < -threshold:
        comment = (
            f"PM({pm_usd:.2f}) < AM({am_usd:.2f})，差值约 {diff:.2f} 美元："
            "整体偏空头主导，反弹到压力/OB 附近偏空处理。"
        )
        bias_score = -1.0
    else:
        comment = (
            f"PM({pm_usd:.2f}) ≈ AM({am_usd:.2f})，差值约 {diff:.2f} 美元："
            "多空力量均衡，日内更容易震荡或区间博弈。"
        )
        bias_score = 0.0

    return {
        "am_fix": f"{am_usd:.2f} USD（{am_date}）",
        "pm_fix": f"{pm_usd:.2f} USD（{pm_date}）",
        "bias_comment": comment,
        "bias_score": bias_score,
    }


# ========== 多空结构评分 ==========
def calc_structure_score(cme, mp, vol, lbma):
    """
    综合 CME / 期权 Skew / LBMA / 波动率，给一个 1–5 星的方向评分
    """
    score = 3.0  # 中性起点
    detail_parts = []

    # 1）LBMA 偏多/偏空
    lbma_score = lbma.get("bias_score", 0.0)
    score += lbma_score * 0.7
    if lbma_score > 0:
        detail_parts.append("LBMA PM 明显高于 AM（+偏多）")
    elif lbma_score < 0:
        detail_parts.append("LBMA PM 明显低于 AM（+偏空）")

    # 2）Skew 偏向
    skew_comment = mp.get("skew_comment", "")
    if "Skew 偏多" in skew_comment:
        score += 0.7
        detail_parts.append("期权 Skew 偏多（压上方、看涨仓较多）")
    elif "Skew 偏空" in skew_comment:
        score -= 0.7
        detail_parts.append("期权 Skew 偏空（防跌/看空对冲仓较多）")

    # 3）CME 持仓变化（如果有）
    if cme.get("ok"):
        try:
            change_oi = int(cme.get("change_oi", "0"))
            if change_oi > 0:
                score += 0.5
                detail_parts.append("CME 增仓 → 趋势更真实")
            elif change_oi < 0:
                score -= 0.5
                detail_parts.append("CME 减仓 → 假突破风险更高")
        except Exception:
            pass

    # 4）波动率环境：高波下适度降级评级
    if vol.get("hv_20") is not None and vol.get("hv_20") > 25:
        score = (score + 3.0) / 2.0  # 高波环境下向中性拉一部分
        detail_parts.append("高波动环境 → 方向确定性打折")

    # 限制在 1–5
    score = max(1.0, min(5.0, score))

    # 星级 & 文本
    rounded = int(round(score))
    stars = "★" * rounded + "☆" * (5 - rounded)

    if score >= 4.5:
        bias = "强多"
        direction_comment = "整体偏强多，顺势多单为主，空单只做短线博弈。"
    elif score >= 3.5:
        bias = "偏多"
        direction_comment = "整体偏多，优先考虑顺势多单，高位空单以短线为主。"
    elif score >= 2.5:
        bias = "中性"
        direction_comment = "整体中性，适合围绕关键支撑/阻力做区间高抛低吸。"
    else:
        bias = "偏空"
        direction_comment = "整体偏空，反弹到压力/OB 附近优先考虑逢高做空。"

    detail = "；".join(detail_parts) if detail_parts else "结构信号整体中性，无明显多空倾斜。"

    return {
        "score": score,
        "stars": stars,
        "bias": bias,
        "direction_comment": direction_comment,
        "detail": detail,
    }


# ========== 自动策略建议 ==========
def build_auto_strategy_lines(cme, mp, vol, lbma, rating):
    """
    把结构信号翻译成接近“今日交易计划”的语句
    输出一段【自动策略建议】文本，格式类似：
      ⭐ 今日结构: 偏多
      🎯 做单方向: 主多
      🟢 多单区: xxxx
      🔴 空单区: xxxx
      ⛔ 禁止追空: MaxPain 偏离低风险
      💡 提示: 波动率中等，CPR 较窄 → 易震荡/洗盘
    """
    lines = []

    score = rating["score"]
    stars = rating["stars"]
    bias = rating["bias"]

    # === 1）整体结构 & 做单方向 ===
    lines.append("【自动策略建议】")
    lines.append(f"⭐ 今日结构: {bias}（{stars}）")

    if bias in ("强多", "偏多"):
        lines.append("🎯 做单方向: 主多（回踩做多为主，高位空单仅短线博弈）。")
    elif bias == "偏空":
        lines.append("🎯 做单方向: 主空（反弹做空为主，支撑附近轻仓博反弹）。")
    else:
        lines.append("🎯 做单方向: 区间思路（关键位高抛低吸，避免追高杀跌）。")

    # === 2）多单区 / 空单区：基于 MaxPain 反转带（用 XAU 换算） ===
    max_pain_xau = mp.get("max_pain_xau")
    rev_zone_xau = mp.get("reversion_zone_xau")
    spot_xau = mp.get("spot_xau")

    if max_pain_xau is not None and rev_zone_xau is not None:
        low_xau, high_xau = rev_zone_xau
        mid_xau = (low_xau + high_xau) / 2

        # 多单区：反转带下半区附近
        long_zone = f"{low_xau:.0f} - {mid_xau:.0f}"
        # 空单区：反转带上半区及其上方
        short_zone = f"{mid_xau:.0f} - {high_xau:.0f}+"

        lines.append(f"🟢 多单区: {long_zone}（反转带下沿/CPR 下侧附近优先找多）。")
        lines.append(f"🔴 空单区: {short_zone}（反转带上沿/CPR 上侧附近优先找空）。")

        if spot_xau is not None:
            lines.append(
                f"   参考：GLD 换算 XAU 约 {spot_xau:.0f}，开盘后对照 TV 上的 XAUUSD 实盘价格。"
            )
    else:
        lines.append("🟢 多单区: 暂不生成固定区间，请结合 TV 上 4H/1H OB + CPR 下沿。")
        lines.append("🔴 空单区: 暂不生成固定区间，请结合 TV 上 4H/1H OB + CPR 上沿。")

    # === 3）禁止追空 / 禁止追多：基于 MaxPain 偏离方向 ===
    dev_pct = mp.get("deviation_pct")
    if dev_pct is not None:
        # dev_pct > 0 表示 GLD 高于 MaxPain（上方有补跌风险），反之则下方有补涨风险
        if abs(dev_pct) < 0.5:
            lines.append("⛔ 禁止追单: 价格贴近 MaxPain，中枢震荡概率高，追多追空都不划算。")
        elif dev_pct > 0:
            # 价格在 MaxPain 上方：追多风险更大
            lines.append(
                f"⛔ 禁止追多: GLD 高于 MaxPain 约 {dev_pct:.2f}% ，"
                "上方补跌/回踩概率增加，只在支撑附近低吸。"
            )
        else:
            # 价格在 MaxPain 下方：追空风险更大
            lines.append(
                f"⛔ 禁止追空: GLD 低于 MaxPain 约 {abs(dev_pct):.2f}% ，"
                "上方补涨/回归中枢概率增加，避免底部追空。"
            )
    else:
        lines.append("⛔ 禁止追单: MaxPain 数据不足，避免在中轴附近追高杀跌。")

    # === 4）提示：结合波动率 + CME 情况给一个执行层面提醒 ===
    hv = vol.get("hv_20")
    vol_level = vol.get("level")

    tip_parts = []

    if hv is not None and vol_level:
        if hv < 15:
            tip_parts.append(
                f"当前为 {vol_level}（HV≈{hv:.1f}%），"
                "突破需要更强成交量确认，优先区间思路。"
            )
        elif hv < 25:
            tip_parts.append(
                f"当前为 {vol_level}（HV≈{hv:.1f}%），"
                "趋势与震荡机会并存，关键看 CPR / OB 方向性突破。"
            )
        else:
            tip_parts.append(
                f"当前为 {vol_level}（HV≈{hv:.1f}%），"
                "假突破/急拉急杀更频繁，仓位和止损要更保守。"
            )

    # CME / CFTC 补一句真假趋势
    if not cme.get("ok"):
        tip_parts.append("CME 实时持仓暂缺，本报告只把 CFTC 周度持仓当作背景。")
    else:
        try:
            change_oi = int(cme.get("change_oi", "0"))
            if change_oi > 0:
                tip_parts.append("CME 增仓 → 当前方向更容易延续，不宜重仓逆势。")
            elif change_oi < 0:
                tip_parts.append("CME 减仓 → 假突破/扫损后反向的概率更高。")
            else:
                tip_parts.append("CME 持仓变化不大 → 更容易走震荡或拉锯。")
        except Exception:
            tip_parts.append("CME 持仓解析失败 → 以盘面结构为主，不强行解读 OI。")

    if tip_parts:
        lines.append("💡 提示: " + " ".join(tip_parts))

    return lines



# ========== 构建最终报告 ==========
def build_micro_report():
    now = datetime.now(CN_TZ)
    date_str = now.strftime("%Y-%m-%d %H:%M")

    cme = fetch_cme_oi()
    mp = get_maxpain_skew_summary()
    vol = get_vol_proxy()
    lbma = get_lbma_fixing_summary()
    rating = calc_structure_score(cme, mp, vol, lbma)

    lines = []
    lines.append("📊 黄金微观结构报告")
    lines.append(f"时间（北京）：{date_str}")
    lines.append("")

    # ==== CME / CFTC 持仓结构 ====
    lines.append("【CME / CFTC 持仓结构】")
    if not cme.get("ok"):
        lines.append("• 数据来源: CFTC 周度持仓报告（CME 实时接口多次超时）")
        lines.append(
            "• 说明: CME 实时接口多次超时，已降级为 CFTC 周度持仓数据；建议手动查看周报： https://www.cftc.gov/dea/futures/deafut.htm"
        )
        lines.append("• 评价: 本报告中不使用具体持仓数字，仅把周度持仓作为背景参考。")
    else:
        lines.append(f"• 成交量 Vol: {cme['volume']}")
        lines.append(f"• 持仓量 OI: {cme['oi']}")
        lines.append(f"• OI 变化: {cme['change_oi']}")
        try:
            change_oi_num = int(cme["change_oi"])
            if change_oi_num > 0:
                trend_eval = "增仓 → 趋势真实（若上涨=真涨、若下跌=真跌）。"
            elif change_oi_num < 0:
                trend_eval = "减仓 → 趋势偏假（上涨易回落 / 下跌易反弹）。"
            else:
                trend_eval = "持仓无明显变化 → 方向可能反复。"
        except Exception:
            trend_eval = "数据解析异常，暂不根据 OI 变化判断趋势。"
        lines.append(f"• 评价: {trend_eval}")
    lines.append("")

    # ==== 期权 MaxPain / Skew ====
    lines.append("【期权 MaxPain / Skew】")
    lines.append(f"• 标的: {mp['underlying']}")
    lines.append(f"• 到期日: {mp['expiry']}")

    if mp["max_pain_gld"] is None:
        lines.append("• MaxPain: 暂无")
        lines.append("• 反转带: 暂无")
        lines.append(f"• 偏离风险: {mp['deviation_comment']}")
        lines.append(f"• 反转带评估: {mp['reversion_comment']}")
        lines.append(f"• Skew评估: {mp['skew_comment']}")
    else:
        max_pain_gld = mp["max_pain_gld"]
        xau_mp = mp["max_pain_xau"]
        low_gld, high_gld = mp["reversion_zone_gld"]
        low_xau, high_xau = mp["reversion_zone_xau"]
        spot_gld = mp["spot_gld"]
        spot_xau = mp["spot_xau"]

        lines.append(f"• MaxPain(GLD): {max_pain_gld:.1f}  ≈ XAU {xau_mp:.0f} 美元")
        lines.append(
            f"• 反转带(GLD): {low_gld:.1f} - {high_gld:.1f}  ≈ XAU {low_xau:.0f} - {high_xau:.0f} 美元"
        )
        lines.append(f"• 当前 GLD 价格: {spot_gld:.2f}")
        lines.append(
            f"  → 换算为黄金现货价格 ≈ {spot_xau:.0f} 美元（仅用于区间参考）"
        )
        lines.append(
            "  （提示：GLD 为美股收盘价，周一 22:30 开盘后会跳空对齐黄金 XAUUSD）"
        )
        lines.append(f"• 偏离风险: {mp['deviation_comment']}")
        lines.append(f"• 反转带评估: {mp['reversion_comment']}")
        lines.append(f"• Skew评估: {mp['skew_comment']}")
    lines.append("")

    # ==== 波动率 Proxy ====
    lines.append("【波动率 Proxy】")
    if vol["hv_20"] is None:
        lines.append("• 20 日年化波动率: 暂无")
    else:
        lines.append(f"• 20 日年化波动率: {vol['hv_20']:.1f}%")
    lines.append(f"• 波动等级: {vol['level']}")
    lines.append(f"• 评估: {vol['comment']}")
    lines.append("")

    # ==== LBMA ====
    lines.append("【LBMA 定盘价】")
    lines.append(f"• AM Fix: {lbma['am_fix']}")
    lines.append(f"• PM Fix: {lbma['pm_fix']}")
    lines.append(f"• 评估: {lbma['bias_comment']}")
    lines.append("")

    # ==== 多空结构评分 ====
    lines.append("【多空结构评分】")
    lines.append(f"• 评级: {rating['stars']} {rating['bias']}")
    lines.append(f"• 方向结论: {rating['direction_comment']}")
    lines.append(f"• 说明: {rating['detail']}")
    lines.append("")

    # ==== 综合结论 ====
    lines.append("【综合结论】")
    lines.append(
        f"• 结构评级: {rating['stars']} {rating['bias']} → {rating['direction_comment']}"
    )
    lines.append(f"• 波动环境: {vol['level']} → {vol['comment']}")
    if mp["max_pain_gld"] is not None and mp["deviation_pct"] is not None:
        lines.append(
            f"• MaxPain 偏离: 当前 GLD 相对 MaxPain 偏离约 {mp['deviation_pct']:.2f}% → {mp['deviation_comment']}"
        )
    lines.append(
        "• CME/CFTC: 若 CME 接口持续超时，则仅把 CFTC 周度持仓作为背景参考，不单独依赖持仓博方向。"
    )
    lines.append(
        "→ 策略倾向：日内优先结合 4H/1H OB + CPR 结构做顺势单；"
        "短线逆势单只在关键 OB / CPR / 反转带附近轻仓尝试。"
    )
    lines.append("")

    # ==== 自动策略建议 ====
    lines.extend(build_auto_strategy_lines(cme, mp, vol, lbma, rating))

    return "\n".join(lines)


if __name__ == "__main__":
    text = build_micro_report()
    send_telegram_message(text)
