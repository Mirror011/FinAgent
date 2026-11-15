"""财报问答评测集

设计原则
--------
1. 标准答案由「基础数据 + 公式」计算得出，而非手写结果，避免计算错误
2. 基础数据均标注财报页码，可逐条回原文核对
3. 题型覆盖由易到难的七类分析，并包含「应当拒答」的题目
   ——只测答对率的评测会奖励模型编造，必须同时考察拒答能力

金额单位：百万美元
运行 python eval/eval_set.py 可导出 eval_set.json 并打印题目概览
"""
import json
import os

# ============================================================
# 基础数据（来自 10-K 原文，括号内为页码，供核对）
# ============================================================

TSLA = {
    "revenue":        {2025: 94827, 2024: 97690, 2023: 96773},   # Total revenues (p48)
    "auto_rev":       {2025: 69526, 2024: 77070, 2023: 82419},   # Total automotive revenues (p48)
    "auto_sales":     {2025: 65821, 2024: 72480, 2023: 78509},   # Automotive sales (p48)
    "reg_credits":    {2025: 1993,  2024: 2763,  2023: 1790},    # Automotive regulatory credits (p48)
    "auto_leasing":   {2025: 1712,  2024: 1827,  2023: 2120},    # Automotive leasing (p48)
    "services":       {2025: 12530, 2024: 10534, 2023: 8319},    # Services and other (p48)
    "energy_rev":     {2025: 12771, 2024: 10086, 2023: 6035},    # Energy generation and storage (p48)
    "cost_rev":       {2025: 77733, 2024: 80240, 2023: 79113},   # Total cost of revenues (p50)
    "gross_profit":   {2025: 17094, 2024: 17450, 2023: 17660},   # Total gross profit (p50)
    "auto_gp":        {2025: 12361, 2024: 14197, 2023: 16030},   # Gross profit total automotive (p50)
    "energy_gp":      {2025: 3802,  2024: 2640,  2023: 1141},    # Gross profit energy (p50)
    "op_income":      {2025: 4355,  2024: 7076,  2023: 8891},    # Income from operations (p64)
    "net_income_att": {2025: 3794,  2024: 7091,  2023: 14997},   # Net income attributable to common stockholders
    "net_income":     {2025: 3855,  2024: 7153,  2023: 14974},   # Net income（含少数股东）
    "rd":             {2025: 6411,  2024: 4540,  2023: 3969},    # Research and development (p64)
    "total_assets":   {2025: 137806, 2024: 122070},              # Total assets (p63)
    "rev_us_2025": 47627, "rev_china_2025": 20962, "rev_other_2025": 26238,  # 地区收入 (p116)
}

AAPL = {
    "revenue":      {2025: 416161, 2024: 391035, 2023: 383285},  # Total net sales (p32)
    "gross_margin": {2025: 195201, 2024: 180683, 2023: 169148},  # Gross margin 金额 (p32)
    "op_income":    {2025: 133050, 2024: 123216, 2023: 114301},  # Operating income (p32)
    "net_income":   {2025: 112010, 2024: 93736,  2023: 96995},   # Net income (p32)
    "services_2025": 109158,                                      # Services net sales (p25)
}


def pct_change(new, old):
    return (new - old) / old * 100


def share(part, whole):
    return part / whole * 100


def cagr(end, start, years):
    return ((end / start) ** (1 / years) - 1) * 100


# ============================================================
# 题目构造
# ============================================================

def G(kind, *values):
    """一个必须命中的数值点；kind: money / pct / ratio；多个 values 表示任一即可"""
    return {"kind": kind, "values": list(values)}


QUESTIONS = []


def add(qid, category, question, gold=None, keywords=None, need=0,
        abstain=False, note=""):
    QUESTIONS.append({
        "id": qid, "category": category, "question": question,
        "gold": gold or [], "keywords": keywords or [], "need": need,
        "abstain": abstain, "note": note,
    })


T, A = TSLA, AAPL

# ---------- 一、单值查询 ----------
C = "单值查询"
add("F01", C, "特斯拉2025年的总营收是多少？", [G("money", T["revenue"][2025])])
add("F02", C, "苹果2025财年的总营收是多少？", [G("money", A["revenue"][2025])])
add("F03", C, "特斯拉2024年能源发电与储能业务收入是多少？", [G("money", T["energy_rev"][2024])])
add("F04", C, "特斯拉2025年的营业利润是多少？", [G("money", T["op_income"][2025])])
add("F05", C, "苹果2025财年的净利润是多少？", [G("money", A["net_income"][2025])])
add("F06", C, "特斯拉2025年的研发费用是多少？", [G("money", T["rd"][2025])])
add("F07", C, "特斯拉2025年末的总资产是多少？", [G("money", T["total_assets"][2025])])
add("F08", C, "特斯拉2025年在中国地区的收入是多少？", [G("money", T["rev_china_2025"])])
add("F09", C, "苹果2025财年服务业务的营收是多少？", [G("money", A["services_2025"])])
add("F10", C, "特斯拉2023年的汽车监管积分收入是多少？", [G("money", T["reg_credits"][2023])])

# ---------- 二、跨期计算 ----------
C = "跨期计算"
add("P01", C, "特斯拉2025年总营收同比变化了多少？",
    [G("pct", pct_change(T["revenue"][2025], T["revenue"][2024]))])
add("P02", C, "特斯拉能源业务收入2023到2025年的年均复合增长率是多少？",
    [G("pct", cagr(T["energy_rev"][2025], T["energy_rev"][2023], 2))])
add("P03", C, "苹果2025财年营收同比增长了多少？",
    [G("pct", pct_change(A["revenue"][2025], A["revenue"][2024]))])
add("P04", C, "苹果2025财年净利润同比增长了多少？",
    [G("pct", pct_change(A["net_income"][2025], A["net_income"][2024]))])
add("P05", C, "特斯拉服务及其他业务收入2025年同比增长多少？",
    [G("pct", pct_change(T["services"][2025], T["services"][2024]))])
add("P06", C, "特斯拉能源业务收入2024年和2025年的同比增速分别是多少？",
    [G("pct", pct_change(T["energy_rev"][2024], T["energy_rev"][2023])),
     G("pct", pct_change(T["energy_rev"][2025], T["energy_rev"][2024]))])

# ---------- 三、比率计算 ----------
C = "比率计算"
add("R01", C, "特斯拉2025年的毛利率是多少？",
    [G("pct", share(T["gross_profit"][2025], T["revenue"][2025]))])
add("R02", C, "特斯拉2025年的营业利润率是多少？",
    [G("pct", share(T["op_income"][2025], T["revenue"][2025]))])
add("R03", C, "苹果2025财年的毛利率是多少？",
    [G("pct", share(A["gross_margin"][2025], A["revenue"][2025]))],
    note="陷阱：苹果的 Gross margin 是金额而非比率")
add("R04", C, "特斯拉能源业务收入占2025年总营收的比例是多少？",
    [G("pct", share(T["energy_rev"][2025], T["revenue"][2025]))])
add("R05", C, "特斯拉2025年汽车业务的毛利率是多少？",
    [G("pct", share(T["auto_gp"][2025], T["auto_rev"][2025]))])
add("R06", C, "剔除监管积分后，特斯拉2025年汽车业务毛利率是多少？",
    [G("pct", share(T["auto_gp"][2025] - T["reg_credits"][2025],
                    T["auto_rev"][2025] - T["reg_credits"][2025]))],
    note="需要领域知识：监管积分几乎无成本")
add("R07", C, "特斯拉2025年研发费用占营收的比例是多少？",
    [G("pct", share(T["rd"][2025], T["revenue"][2025]))])

# ---------- 四、构成归因 ----------
C = "构成归因"
_drop = T["auto_rev"][2025] - T["auto_rev"][2024]
add("B01", C, "特斯拉汽车业务收入2025年下滑，汽车销售这一子项贡献了总降幅的多少？",
    [G("pct", share(T["auto_sales"][2025] - T["auto_sales"][2024], _drop))])
add("B02", C, "特斯拉汽车业务各子项中，2025年同比降幅最大的是哪一项，降了多少？",
    [G("pct", pct_change(T["reg_credits"][2025], T["reg_credits"][2024]))],
    keywords=[["监管积分", "regulatory credit"]], need=1)
add("B03", C, "特斯拉2025年总营收由哪几个业务板块构成？汽车业务占比多少？",
    [G("pct", share(T["auto_rev"][2025], T["revenue"][2025]))],
    keywords=[["汽车", "automotive"], ["能源", "energy"], ["服务", "services"]], need=3)
add("B04", C, "特斯拉2025年各地区收入中，中国地区占总营收的比例是多少？",
    [G("pct", share(T["rev_china_2025"], T["revenue"][2025]))])

# ---------- 五、跨公司对比 ----------
C = "跨公司对比"
add("X01", C, "苹果2025财年营收是特斯拉2025年营收的多少倍？",
    [G("ratio", A["revenue"][2025] / T["revenue"][2025])])
add("X02", C, "特斯拉和苹果2025年谁的毛利率更高？分别是多少？",
    [G("pct", share(T["gross_profit"][2025], T["revenue"][2025])),
     G("pct", share(A["gross_margin"][2025], A["revenue"][2025]))],
    keywords=[["苹果", "apple"]], need=1)
add("X03", C, "特斯拉和苹果2025年的营业利润率分别是多少？",
    [G("pct", share(T["op_income"][2025], T["revenue"][2025])),
     G("pct", share(A["op_income"][2025], A["revenue"][2025]))])
add("X04", C, "特斯拉和苹果2025年的净利率分别是多少？",
    [G("pct", share(T["net_income_att"][2025], T["revenue"][2025]),
              share(T["net_income"][2025], T["revenue"][2025])),
     G("pct", share(A["net_income"][2025], A["revenue"][2025]))],
    note="特斯拉净利润两种口径均判对")
add("X05", C, "2025年特斯拉和苹果的营收同比变化方向是否一致？各是多少？",
    [G("pct", pct_change(T["revenue"][2025], T["revenue"][2024])),
     G("pct", pct_change(A["revenue"][2025], A["revenue"][2024]))])
add("X06", C, "比较特斯拉和苹果的2025年数据时，在时间口径上需要注意什么？",
    keywords=[["9月", "九月", "september"], ["12月", "十二月", "december", "自然年"]], need=2,
    note="考察财年不对齐")

# ---------- 六、文本理解 ----------
C = "文本理解"
add("T01", C, "特斯拉在10-K中披露的主要风险有哪些？",
    keywords=[["供应链", "供应商", "零部件"], ["竞争"], ["马斯克", "musk", "关键人员"],
              ["关税", "贸易"], ["电池", "锂"]], need=3)
add("T02", C, "苹果在10-K中披露的主要风险有哪些？",
    keywords=[["供应链", "供应商"], ["竞争"], ["网络安全", "隐私", "数据"],
              ["反垄断", "监管", "法律"], ["汇率", "外汇"]], need=3)
add("T03", C, "特斯拉有哪几个报告分部？",
    keywords=[["汽车", "automotive"], ["能源", "energy"]], need=2)
add("T04", C, "根据管理层分析，特斯拉2025年汽车销售收入下降的原因是什么？",
    keywords=[["交付", "销量"], ["平均售价", "单价", "售价", "激励"]], need=2)
add("T05", C, "苹果的财年在每年什么时候结束？",
    keywords=[["9月", "九月", "september"]], need=1)

# ---------- 七、拒答 ----------
C = "拒答"
add("N01", C, "特斯拉2026年的总营收是多少？", abstain=True, note="未来数据，10-K 不包含")
add("N02", C, "比亚迪2025年的毛利率是多少？", abstain=True, note="库中无该公司")
add("N03", C, "特斯拉汽车业务单独的研发费用是多少？", abstain=True, note="研发费用不按分部披露")
add("N04", C, "苹果2025财年在印度市场的营收是多少？", abstain=True, note="地区仅披露大区，无印度单列")
add("N05", C, "特斯拉2020年的总营收是多少？", abstain=True, note="10-K 仅含近三年数据")


if __name__ == "__main__":
    from collections import Counter
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval_set.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(QUESTIONS, f, ensure_ascii=False, indent=2)
    print(f"共 {len(QUESTIONS)} 题，已导出 {out}")
    for cat, n in Counter(q["category"] for q in QUESTIONS).items():
        print(f"  {cat}：{n}")
