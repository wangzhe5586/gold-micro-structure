import math
import yfinance as yf


# ========= LBMA 定盘价（使用昨天版本的工作代码） =========
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


# ========= 配置 =========
# 这里请换成你自己仓库里的 BOT_TOKEN / CHAT_ID
BOT_TOKEN = "8053639726:AAE_Kjpin_UGi6rrHDeDRvT9WrYVKUtR3UY"
CHAT_ID = "6193487818"

CN_TZ = timezone(timedelta(hours=8))

# GLD → XAU 换算系数（经验值，大约 1 股 GLD ≈ 0.093 盎司黄金）
GLD_TO_XAU_FACTOR = 10.75  # 仅用于区间参考，不作为精确报价
# GLD → XAU 近似换算：**结构参考（不是实盘价）**
GLD_TO_XAU = 10.75


# ========= 发送 Telegram 消息 =========
def send_telegram_message(text: str):
def send_telegram(text: str):
url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, data={"chat_id": CHAT_ID, "text": text})
        resp.raise_for_status()  # 处理 HTTP 错误
        data = resp.json()
        if not data.get("ok", False):
            # Telegram API 返回错误，也当作错误抛出去
            print("Telegram API 返回错误：", data)
            raise RuntimeError(f"Telegram send error: {data}")
        else:
            print("Telegram 发送成功。")
    requests.post(url, data={"chat_id": CHAT_ID, "text": text})

    except Exception as e:
        print("发送 Telegram 失败：", e)
        raise

# ========= CME 成交量 / 持仓量（带重试 + 优雅降级） =========
def fetch_cme_oi():
# ========= CME OI（含重试） =========
def fetch_cme():
url = "https://www.cmegroup.com/CmeWS/mvc/Quotes/Future/416/G"
for _ in range(3):
try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            r = requests.get(url, timeout=10)
data = r.json()
            quote = data["quotes"]["quote"][0]
            volume = quote.get("volume", "N/A")
            open_interest = quote.get("openInterest", "N/A")
            change_oi = quote.get("changeOpenInterest", "0")
            q = data["quotes"]["quote"][0]
return {
                "volume": volume,
                "oi": open_interest,
                "change_oi": change_oi,
"ok": True,
                "error": None,
                "volume": q.get("volume", "—"),
                "oi": q.get("openInterest", "—"),
                "ch": q.get("changeOpenInterest", "0"),
}
        except Exception as e:
            if _ < 2:
                time.sleep(3)
            # 确保这里捕获异常并打印
            print("CME 数据获取失败，错误信息：", e)
        except:
            time.sleep(2)

    return {
        "volume": "—",
        "oi": "—",
        "change_oi": "0",
        "ok": False,
        "error": "请求超时或发生错误",
    }
    return {"ok": False}

# ========= GLD → XAU 换算 =========
def gld_to_xau(price: float) -> float:
    return float(price) * GLD_TO_XAU_FACTOR

# ========= 获取短期（≤10 天）GLD 期权链 =========
def get_short_term_option_chain(ticker):
def get_short_term_option_chain(ticker: yf.Ticker):
today = datetime.now().date()

expiries = ticker.options
if not expiries:
return None, None
@@ -85,8 +127,9 @@ def parse(d):
d = parse(e)
if d and 0 < (d - today).days <= 10:
near.append((e, (d - today).days))

    
if not near:
        # 没有短期期权 → 跳过此模块
return None, None

near_sorted = sorted(near, key=lambda x: x[1])
@@ -125,19 +168,28 @@ def parse(d):

return best_expiry, best_data


# ========= 计算短期 MaxPain / Skew =========
def calc_short_term_maxpain():
ticker = yf.Ticker("GLD")

    # GLD 最新收盘价
hist = ticker.history(period="5d")
if hist.empty:
return {"ok": False, "msg": "GLD 行情获取失败"}

spot = float(hist["Close"].iloc[-1])

expiry, data = get_short_term_option_chain(ticker)
if expiry is None:
        return {"ok": False, "msg": "未来 10 天无短期期权 → MaxPain/Skew 自动跳过"}
        return {
            "ok": False,
            "msg": "未来 10 天无短期期权 → MaxPain/Skew 自动跳过",
        }

calls, puts = data

    # 只看接近现价 ±15% 的行权价，避免远月/垃圾档干扰
lo = spot * 0.85
hi = spot * 1.15
calls = calls[(calls["strike"] >= lo) & (calls["strike"] <= hi)]
@@ -146,7 +198,10 @@ def calc_short_term_maxpain():
if calls.empty and puts.empty:
return {"ok": False, "msg": "短期期权无有效行权价"}

    strikes = sorted(list(set(calls["strike"]).union(set(puts["strike"]))))
    strikes = sorted(
        list(set(calls["strike"].tolist()) | set(puts["strike"].tolist()))
    )

call_oi = dict(zip(calls["strike"], calls["openInterest"]))
put_oi = dict(zip(puts["strike"], puts["openInterest"]))

@@ -167,15 +222,16 @@ def calc_short_term_maxpain():
best_strike = S

if best_strike is None:
        return {"ok": False, "msg": "MaxPain 计算失败"}
        return {"ok": False, "msg": "MaxPain 无法计算"}

idx = strikes.index(best_strike)
low = strikes[max(0, idx - 1)]
high = strikes[min(len(strikes) - 1, idx + 1)]

    call_oi_total = calls["openInterest"].sum()
    put_oi_total = puts["openInterest"].sum()
    skew = put_oi_total / call_oi_total if call_oi_total > 0 else None
    # Skew：总 Put OI / 总 Call OI
    call_oi_t = calls["openInterest"].sum()
    put_oi_t = puts["openInterest"].sum()
    skew = put_oi_t / call_oi_t if call_oi_t > 0 else None

return {
"ok": True,
@@ -189,14 +245,43 @@ def calc_short_term_maxpain():
"dev": (spot - best_strike) / best_strike * 100,
}


# ========= 波动率 Proxy（20 日 HV） =========
def get_hist_volatility(symbol: str, window: int = 20):
    """
    简单历史波动率：收盘价收益率标准差 * sqrt(252)
    返回：年化波动率（百分比），失败返回 None
    """
    try:
        ticker = yf.Ticker(symbol)
        # 取 3 倍窗口长度的数据，保证样本数量
        hist = ticker.history(period=f"{window * 3}d")
        if hist.empty:
            return None

        ret = hist["Close"].pct_change().dropna()
        if ret.empty:
            return None

        daily_vol = ret.std()
        hv = daily_vol * math.sqrt(252) * 100
        return float(hv)
    except:
        return None


# ========= 生成报告 ==========
def build_report():
now = datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M")
    cme = fetch_cme_oi()

    cme = fetch_cme()
op = calc_short_term_maxpain()

    lbma_am = get_lbma_fix("AM")
    lbma_pm = get_lbma_fix("PM")
    # LBMA 定盘价（使用昨天版本的工作代码）
    lbma_summary = get_lbma_fixing_summary()

    # 波动率 Proxy（精简）
    hv20 = get_hist_volatility("GLD", window=20)

lines = []
lines.append("📊 黄金微观结构报告（短期版·适合未来 1–5 天）")
@@ -230,36 +315,37 @@ def build_report():

# ==== LBMA 定盘价（精简）====
lines.append("【LBMA 定盘价（精简）】")
    lines.append(f"• AM Fix: {lbma_am} USD")
    lines.append(f"• PM Fix: {lbma_pm} USD")

    if lbma_am is not None and lbma_pm is not None:
        if lbma_pm > lbma_am:
            lbma_comment = "PM > AM → 偏多（多头主导）"
        elif lbma_pm < lbma_am:
            lbma_comment = "PM < AM → 偏空（空头主导）"
        else:
            lbma_comment = "PM ≈ AM → 中性"
    else:
        lbma_comment = "本次 LBMA 获取失败，仅作背景参考。"

    lines.append(f"• 结论: {lbma_comment}")
    lines.append(f"• AM Fix: {lbma_summary['am_fix']}")
    lines.append(f"• PM Fix: {lbma_summary['pm_fix']}")
    lines.append(f"• 结论: {lbma_summary['bias_comment']}")
lines.append("")

# ==== 波动率 Proxy（精简）====
lines.append("【波动率 Proxy（精简）】")
    hv20 = 23.91  # 使用预设值
    lines.append(f"• 20 日年化波动率: {hv20:.2f}%")
    if hv20 >= 22:
        hv_comment = "高波动 → 容易出现趋势突破单（日内波动大）。"
    elif hv20 >= 17:
        hv_comment = "中等波动 → 趋势/震荡并存，需要结合 CPR / OB 结构。"
    if hv20 is None:
        lines.append("• 20 日年化波动率: 数据获取失败")
        hv_comment = "波动率暂不可用，行情节奏以图表结构为主。"
else:
        hv_comment = "低波动 → 偏震荡，突破概率低。"

        lines.append(f"• 20 日年化波动率: {hv20:.2f}%")
        if hv20 >= 22:
            hv_comment = "高波动 → 容易出现趋势突破单（日内波动大）。"
        elif hv20 >= 17:
            hv_comment = "中等波动 → 趋势/震荡并存，需要结合 CPR / OB 结构。"
        else:
            hv_comment = "低波动 → 偏震荡，突破概率低。"
lines.append(f"• 结论: {hv_comment}")
lines.append("")

    # ==== CME ====
    lines.append("【CME 黄金期货（GC）】")
    if not cme["ok"]:
        lines.append("• CME 数据获取失败 → 以 CFTC 周度为背景参考。")
    else:
        lines.append(f"• 成交量 Vol：{cme['volume']}")
        lines.append(f"• 持仓量 OI：{cme['oi']}")
        lines.append(f"• OI 变化：{cme['ch']}")
    lines.append("")

# ==== 综合短期方向（1–5 天）====
lines.append("【短期方向（1–5 天）】")
if op["ok"]:
@@ -279,4 +365,4 @@ def build_report():

if __name__ == "__main__":
text = build_report()
    send_telegram_message(text)
    send_telegram(text)
