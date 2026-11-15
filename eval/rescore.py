"""对已保存的答案重新评分（评分规则修改后使用，无需重新调用模型）"""
import os, sys, json
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "eval"))

from eval_set import QUESTIONS
import importlib.util
spec = importlib.util.spec_from_file_location("re_mod", os.path.join(ROOT, "eval", "run_eval.py"))
mod = importlib.util.module_from_spec(spec)
sys.argv = ["x", "--report"]
spec.loader.exec_module(mod)

Q = {q["id"]: q for q in QUESTIONS}
for name in ("naive", "rag", "agent"):
    path = os.path.join(ROOT, "eval", "results", f"{name}.jsonl")
    if not os.path.exists(path):
        continue
    recs = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    changed = 0
    for r in recs:
        ok, detail = mod.score(Q[r["id"]], r["answer"])
        if ok != r["correct"]:
            changed += 1
            print(f"  {name} {r['id']}: {r['correct']} → {ok}  ({detail})")
        r["correct"], r["detail"] = ok, detail
    with open(path, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{name}: 重新评分 {len(recs)} 题，{changed} 题判定改变")

mod.report(["naive", "rag", "agent"])