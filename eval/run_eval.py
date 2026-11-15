"""评测运行器：三个系统消融对比 + 规则评分 + 报告

系统
----
naive  纯向量 RAG：固定切块 + 向量检索 top-5，无重排、无结构化数据库
rag    增强 RAG：rag_pipeline.ask（重排 + 章节加权 + 结构化取数注入）
agent  Agent：规划 + 工具调用 + MCP + Skill（每题独立会话，屏蔽长期记忆干扰）

评分（规则评分，可复现）
----
数值题  答案中须命中全部标准数值点：金额相对误差 ≤0.3%（自动识别"亿/十亿"换算），
        百分比绝对误差 ≤0.1 个百分点，倍数绝对误差 ≤0.05；若同时有关键词要求也须满足
文本题  关键要点覆盖数 ≥ 要求数
拒答题  答案须包含拒答表述

用法
----
python eval/run_eval.py                       # 跑全部三个系统
python eval/run_eval.py --systems agent       # 只跑某个系统
python eval/run_eval.py --limit 5             # 每个系统只跑前 5 题（冒烟测试）
python eval/run_eval.py --fresh               # 清空已有结果重跑（默认断点续跑）
python eval/run_eval.py --report              # 只根据已有结果生成报告
"""
import os
import re
import sys
import json
import time
import uuid
import asyncio
import argparse
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from dotenv import load_dotenv
load_dotenv()

from eval_set import QUESTIONS

RESULT_DIR = os.path.join(ROOT, "eval", "results")
EVAL_USER = "eval_user"            # 评测专用用户，避免污染真实长期记忆
ORDER = ["单值查询", "跨期计算", "比率计算", "构成归因", "跨公司对比", "文本理解", "拒答"]

ABSTAIN_MARKERS = ["无法", "未找到", "找不到", "查不到", "没有提供", "未提供", "未披露",
                   "没有披露", "不包含", "未包含", "未提及", "没有提及", "没有相关",
                   "无相关", "并未", "不披露", "不存在", "不涉及", "没有该", "没有这",
                   "仅包含", "没有数据", "没有 20", "没有20", "不含", "超出", "未单独",
                   "没有单独", "未按", "不在", "无法计算", "没有比亚迪", "没有印度"]

# 仅当"给出了估算值"时才算编造；"不会给出估算"这类拒绝声明不应误判
FABRICATE_PATTERNS = [
    r"估算[^。；\n]{0,12}?[约为是]\s*[\d]",     # 估算约 1000 亿
    r"预计[^。；\n]{0,12}?[约为是]\s*[\d]",
    r"大致[落在约]{1,2}[^。；\n]{0,10}\d",
    r"粗略[估计算][^。；\n]{0,12}\d",
]


# ============================================================
# 评分
# ============================================================

def extract_numbers(text):
    """提取文本中的数值。
    注意：\\w 在 Unicode 下包含中文，若用 (?<![\\w.]) 会漏掉"是94,827百万"这类紧跟中文的数字，
    故显式限定为 ASCII 字母数字与小数点。"""
    nums = []
    for m in re.finditer(r"(?<![A-Za-z0-9.])-?\d[\d,]*(?:\.\d+)?", text):
        try:
            nums.append(float(m.group().replace(",", "")))
        except ValueError:
            pass
    return nums


def hit(point, nums):
    kind, values = point["kind"], point["values"]
    for v in values:
        for x in nums:
            if kind == "money":
                for scale in (1, 100, 1000):          # 百万 / 亿 / 十亿
                    if abs(x * scale - v) <= abs(v) * 0.003:
                        return True
            elif kind == "pct":
                if abs(abs(x) - abs(v)) <= 0.1:       # 答案常写"下降2.93%"不带负号
                    return True
            elif kind == "ratio":
                if abs(x - v) <= 0.05:
                    return True
    return False


def keyword_cover(groups, text):
    low = text.lower()
    return sum(1 for g in groups if any(k.lower() in low for k in g))


def is_abstain(text):
    return any(m in text for m in ABSTAIN_MARKERS)


def is_fabricated(text):
    """是否真的给出了估算数字。排除"不会给出估算值"这类否定表述"""
    for p in FABRICATE_PATTERNS:
        for m in re.finditer(p, text):
            around = text[max(0, m.start() - 12):m.start()]
            if any(neg in around for neg in ("不会", "不能", "不应", "不作", "不给", "无法", "没有")):
                continue
            return True
    return False


def score(q, answer):
    """返回 (是否正确, 说明)"""
    if q["abstain"]:
        if not is_abstain(answer):
            return False, "未拒答（可能编造）"
        if is_fabricated(answer):
            return False, "声明无数据但仍给出估算"
        return True, "已拒答"

    notes, ok = [], True
    if q["gold"]:
        nums = extract_numbers(answer)
        miss = [p for p in q["gold"] if not hit(p, nums)]
        if miss:
            ok = False
            notes.append("缺失数值 " + ", ".join(f"{p['values'][0]:.2f}" for p in miss))
    if q["keywords"]:
        c = keyword_cover(q["keywords"], answer)
        if c < q["need"]:
            ok = False
        notes.append(f"要点覆盖 {c}/{len(q['keywords'])}（需 {q['need']}）")
    return ok, "；".join(notes) or "命中"


def has_citation(text):
    return bool(re.search(r"第\s*\d+\s*页|p\.?\s*\d+|page\s*\d+", text, re.I))


# ============================================================
# 三个被测系统
# ============================================================

NAIVE_PROMPT = """你是一个财报分析助手。请根据下面提供的财报内容回答问题，并标注来源页码。
如果问题需要计算，请基于内容中的数字计算并写出过程。
如果内容里找不到答案，就直接说"根据提供的财报内容，无法回答此问题"。

财报内容:
{context}

问题:{question}

回答:"""


class Systems:
    def __init__(self):
        self._vs = self._llm = None
        self._agent_ready = False

    def _base(self):
        import rag_pipeline
        if self._vs is None:
            self._vs = rag_pipeline.get_vectorstore()
            self._llm = rag_pipeline.get_llm()
        return rag_pipeline

    async def naive(self, q):
        self._base()
        docs = self._vs.similarity_search(q, k=5)
        ctx = "\n\n".join(f"[第{d.metadata.get('page', '?')}页]\n{d.page_content}" for d in docs)
        resp = await self._llm.ainvoke(NAIVE_PROMPT.format(context=ctx, question=q))
        return resp.content, 0

    async def rag(self, q):
        rp = self._base()
        return await asyncio.to_thread(rp.ask, self._vs, self._llm, q), 0

    async def agent(self, q):
        import agent
        import memory
        if not self._agent_ready:
            await agent.warmup()
            self._agent_ready = True
        memory.clear(EVAL_USER)                     # 每题清空，保证题目之间互不影响
        answer, trace = await agent.arun_agent(
            q, user_id=EVAL_USER, thread_id=str(uuid.uuid4()), verbose=False)
        return answer, len(trace)


# ============================================================
# 运行与报告
# ============================================================

def result_path(name):
    return os.path.join(RESULT_DIR, f"{name}.jsonl")


def load_results(name):
    path = result_path(name)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return {r["id"]: r for r in map(json.loads, f) if r}


async def run_system(name, systems, limit=None):
    done = load_results(name)
    qs = QUESTIONS[:limit] if limit else QUESTIONS
    todo = [q for q in qs if q["id"] not in done]
    print(f"\n===== {name}：共 {len(qs)} 题，已完成 {len(qs) - len(todo)}，待跑 {len(todo)} =====")

    fn = getattr(systems, name)
    with open(result_path(name), "a", encoding="utf-8") as f:
        for i, q in enumerate(todo, 1):
            t0 = time.time()
            try:
                answer, calls = await fn(q["question"])
            except Exception as e:
                answer, calls = f"[运行出错] {e}", 0
            latency = time.time() - t0
            ok, detail = score(q, answer)
            rec = {"id": q["id"], "category": q["category"], "question": q["question"],
                   "correct": ok, "detail": detail, "latency": round(latency, 2),
                   "tool_calls": calls, "citation": has_citation(answer), "answer": answer}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            print(f"  [{i}/{len(todo)}] {q['id']} {'✓' if ok else '✗'}  {latency:5.1f}s  {detail}")


def summarize(name):
    res = load_results(name)
    if not res:
        return None
    by_cat = defaultdict(list)
    for r in res.values():
        by_cat[r["category"]].append(r)
    answered = [r for r in res.values() if r["category"] != "拒答"]
    return {
        "n": len(res),
        "acc": sum(r["correct"] for r in res.values()) / len(res),
        "cat": {c: (sum(r["correct"] for r in v), len(v)) for c, v in by_cat.items()},
        "latency": sum(r["latency"] for r in res.values()) / len(res),
        "tools": sum(r["tool_calls"] for r in res.values()) / len(res),
        "cite": (sum(r["citation"] for r in answered) / len(answered)) if answered else 0,
    }


def report(names):
    sums = {n: summarize(n) for n in names}
    sums = {n: s for n, s in sums.items() if s}
    if not sums:
        print("暂无结果")
        return

    cols = list(sums)
    lines = ["# 评测报告", "",
             f"评测集：{len(QUESTIONS)} 题，七类。评分为规则评分（见 run_eval.py 文件头说明）。", "",
             "## 分类准确率", "",
             "| 类别 | " + " | ".join(cols) + " |",
             "|---|" + "---|" * len(cols)]
    for c in ORDER:
        row = [c]
        for n in cols:
            k, tot = sums[n]["cat"].get(c, (0, 0))
            row.append(f"{k}/{tot}（{k / tot * 100:.0f}%）" if tot else "-")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("| **总体** | " + " | ".join(f"**{sums[n]['acc'] * 100:.1f}%**" for n in cols) + " |")

    lines += ["", "## 效率与可追溯性", "",
              "| 指标 | " + " | ".join(cols) + " |",
              "|---|" + "---|" * len(cols),
              "| 平均耗时（秒） | " + " | ".join(f"{sums[n]['latency']:.1f}" for n in cols) + " |",
              "| 平均工具调用次数 | " + " | ".join(f"{sums[n]['tools']:.1f}" for n in cols) + " |",
              "| 引用出处比例 | " + " | ".join(f"{sums[n]['cite'] * 100:.0f}%" for n in cols) + " |"]

    lines += ["", "## 错题明细", ""]
    for n in cols:
        wrong = [r for r in load_results(n).values() if not r["correct"]]
        lines.append(f"### {n}（错 {len(wrong)} 题）")
        for r in wrong:
            lines.append(f"- **{r['id']}** {r['question']} —— {r['detail']}")
        lines.append("")

    text = "\n".join(lines)
    with open(os.path.join(RESULT_DIR, "report.md"), "w", encoding="utf-8") as f:
        f.write(text)
    print("\n" + text)
    print(f"\n报告已保存：{os.path.join(RESULT_DIR, 'report.md')}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--systems", default="naive,rag,agent")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    os.makedirs(RESULT_DIR, exist_ok=True)
    names = [s.strip() for s in args.systems.split(",") if s.strip()]

    if not args.report:
        if args.fresh:
            for n in names:
                if os.path.exists(result_path(n)):
                    os.remove(result_path(n))
        systems = Systems()
        for n in names:
            await run_system(n, systems, args.limit)
    report(names)


if __name__ == "__main__":
    asyncio.run(main())
