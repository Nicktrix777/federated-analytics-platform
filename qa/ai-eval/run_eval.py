#!/usr/bin/env python3
"""
AI response evaluation harness for the federated analytics platform.

Fires a battery of natural-language scenarios (qa/ai-eval/scenarios.json) at the
live stack through Core API's /api/query (full plan -> Trino execution path) and
grades each response on:

  deterministic checks   plan returned? executed without error? non-empty rows?
                         right source/table referenced? UNNEST used where the
                         question needs to traverse a nested array?
  LLM-as-judge (opt-in)  gpt-4o-mini scores 0-5 whether the SQL+result actually
                         answers the question (independent of the gpt-4o planner).

Outputs qa/ai-eval/results/{results.json, scorecard.md}. Stdlib only — no pip install.

Usage:
  python3 qa/ai-eval/run_eval.py                 # deterministic only
  python3 qa/ai-eval/run_eval.py --judge         # + LLM judge (reads OPENAI_API_KEY from .env)
  python3 qa/ai-eval/run_eval.py --grep es-      # only scenarios whose id matches
"""
import argparse, concurrent.futures as cf, json, os, statistics, sys, time, urllib.request, urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
RESULTS_DIR = HERE / "results"


def load_env(path: Path) -> dict:
    env = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def http_json(url, payload, headers, timeout):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers}, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode())
            return {"status": r.status, "body": body, "ms": int((time.time() - t0) * 1000)}
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
        except Exception:
            body = {"error": str(e)}
        return {"status": e.code, "body": body, "ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        return {"status": 0, "body": {"error": str(e)}, "ms": int((time.time() - t0) * 1000)}


def extract(resp_body: dict):
    """Pull the fields we grade from a /api/query response, tolerating shape drift."""
    plan = resp_body.get("plan") or {}
    sql = plan.get("sql") or resp_body.get("sql") or ""
    # A clarification can arrive under a few possible keys.
    clar = resp_body.get("clarification") or plan.get("clarification")
    if not clar:
        for k, v in resp_body.items():
            if "clarif" in k.lower() and v:
                clar = v
                break
    return {
        "sql": sql,
        "confidence": plan.get("confidence"),
        "columns": resp_body.get("columns") or [],
        "rows": resp_body.get("rows") or [],
        "row_count": resp_body.get("row_count", len(resp_body.get("rows") or [])),
        "error": resp_body.get("error"),
        "clarification": clar,
        "exec_ms": resp_body.get("execution_time_ms"),
    }


def grade(sc: dict, http: dict) -> dict:
    ex = extract(http["body"])
    sql_l = ex["sql"].lower()
    checks, fails = {}, []

    http_ok = http["status"] == 200
    checks["http_ok"] = http_ok
    if not http_ok:
        fails.append(f"http {http['status']}")

    clarified = bool(ex["clarification"])
    # Graceful-handling scenarios: a clarification IS the correct outcome.
    if sc.get("allow_clarification") and clarified:
        return {"passed": http_ok, "checks": {**checks, "clarified_gracefully": True},
                "fails": fails, "extract": ex, "clarified": True}

    checks["plan_returned"] = bool(ex["sql"])
    if not ex["sql"] and not clarified:
        fails.append("no SQL in plan")

    executed = http_ok and not ex["error"] and not clarified
    checks["executed_ok"] = executed
    if ex["error"]:
        fails.append(f"exec error: {str(ex['error'])[:120]}")

    if sc.get("expect_rows") and not sc.get("allow_zero_rows"):
        ok = (ex["row_count"] or 0) > 0
        checks["nonzero_rows"] = ok
        if not ok:
            fails.append("zero rows")

    for sub in sc.get("expect_sql_contains", []):
        key = f"sql_contains[{sub}]"
        ok = sub.lower() in sql_l
        checks[key] = ok
        if not ok:
            fails.append(f"SQL missing '{sub}'")

    if sc.get("nested"):
        ok = "unnest" in sql_l
        checks["uses_unnest"] = ok
        if not ok:
            fails.append("nested question but no UNNEST")

    passed = all(v for v in checks.values())
    return {"passed": passed, "checks": checks, "fails": fails, "extract": ex, "clarified": clarified}


def llm_judge(sc: dict, ex: dict, key: str, model: str) -> dict:
    cols = ex["columns"]
    sample = []
    for row in (ex["rows"] or [])[:5]:
        if isinstance(row, list) and cols:
            sample.append({c: (str(v)[:80] if v is not None else None) for c, v in zip(cols, row)})
        else:
            sample.append(row)
    prompt = (
        "You are grading a natural-language analytics assistant that translates questions into "
        "Trino SQL over federated sources (PostgreSQL, MongoDB, Elasticsearch with heavily nested "
        "ARRAY(ROW) fields).\n\n"
        f"USER QUESTION:\n{sc['question']}\n\n"
        f"GENERATED SQL:\n{ex['sql'] or '(none)'}\n\n"
        f"RESULT COLUMNS: {cols}\n"
        f"SAMPLE ROWS (up to 5): {json.dumps(sample, default=str)[:1500]}\n"
        f"ROW COUNT: {ex['row_count']}\n\n"
        "Judge whether the SQL and its result correctly and completely answer the question: right "
        "tables/sources, correct handling of nesting/joins/aggregation, and a plausible result. "
        'Respond ONLY as JSON: {"score": 0-5, "verdict": "pass"|"fail", "reason": "<one sentence>"}.'
    )
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "max_tokens": 300,
    }
    # Retry on 429 (rate limit) with exponential backoff so judge coverage
    # doesn't collapse under load.
    r = None
    for attempt in range(5):
        r = http_json("https://api.openai.com/v1/chat/completions", payload,
                      {"Authorization": f"Bearer {key}"}, timeout=60)
        if r["status"] != 429:
            break
        time.sleep(2 * (attempt + 1))
    if r["status"] != 200:
        return {"score": None, "verdict": "skipped", "reason": f"judge http {r['status']}: {str(r['body'])[:120]}"}
    try:
        content = r["body"]["choices"][0]["message"]["content"]
        j = json.loads(content)
        return {"score": j.get("score"), "verdict": j.get("verdict"), "reason": j.get("reason")}
    except Exception as e:
        return {"score": None, "verdict": "skipped", "reason": f"judge parse error: {e}"}


def run_one(sc, base, token, judge_key, judge_model, timeout):
    # Retry transient infra errors (502/503/504 or connection drop) so a blip in
    # the AI engine doesn't get miscounted as an AI-quality failure.
    http = None
    for attempt in range(4):
        http = http_json(f"{base}/api/query", {"question": sc["question"], "mode": "ai"},
                         {"Authorization": f"Bearer {token}"}, timeout)
        if http["status"] not in (0, 502, 503, 504):
            break
        time.sleep(3 * (attempt + 1))
    g = grade(sc, http)
    rec = {
        "id": sc["id"], "source": sc["source"], "category": sc["category"],
        "question": sc["question"], "note": sc.get("note"),
        "passed": g["passed"], "clarified": g["clarified"], "fails": g["fails"],
        "checks": g["checks"], "sql": g["extract"]["sql"],
        "confidence": g["extract"]["confidence"], "row_count": g["extract"]["row_count"],
        "columns": g["extract"]["columns"], "error": g["extract"]["error"],
        "latency_ms": http["ms"], "http_status": http["status"],
    }
    if judge_key:
        rec["judge"] = llm_judge(sc, g["extract"], judge_key, judge_model)
    status = "PASS" if g["passed"] else ("CLARIFY" if g["clarified"] else "FAIL")
    jn = f" judge={rec['judge']['score']}" if judge_key and rec.get("judge") else ""
    print(f"  [{status:7}] {sc['id']:<34} {http['ms']:>6}ms rows={rec['row_count']}{jn}"
          + ("" if g["passed"] else f"  ← {', '.join(g['fails'])[:90]}"))
    return rec


def pct(n, d):
    return f"{(100.0 * n / d):.0f}%" if d else "n/a"


def write_reports(records, meta):
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "results.json").write_text(json.dumps({"meta": meta, "results": records}, indent=2, default=str))

    total = len(records)
    passed = sum(1 for r in records if r["passed"])
    by_source, by_cat = {}, {}
    for r in records:
        by_source.setdefault(r["source"], []).append(r)
        by_cat.setdefault(r["category"], []).append(r)
    judged = [r for r in records if r.get("judge") and r["judge"].get("score") is not None]
    avg_judge = statistics.mean([r["judge"]["score"] for r in judged]) if judged else None
    lat = [r["latency_ms"] for r in records]

    L = []
    L.append(f"# AI Response Scorecard\n")
    L.append(f"- Stack: `{meta['base']}`  |  Judge: `{meta['judge_model'] if meta['judge'] else 'disabled'}`  |  Run: {meta['ts']}")
    L.append(f"- **Deterministic pass: {passed}/{total} ({pct(passed,total)})**")
    if avg_judge is not None:
        L.append(f"- **LLM-judge mean score: {avg_judge:.2f}/5** over {len(judged)} scenarios")
    L.append(f"- Latency: median {int(statistics.median(lat))}ms, max {max(lat)}ms\n")

    L.append("## By source\n\n| Source | Pass | Rate |\n|---|---|---|")
    for s, rs in sorted(by_source.items()):
        L.append(f"| {s} | {sum(1 for r in rs if r['passed'])}/{len(rs)} | {pct(sum(1 for r in rs if r['passed']), len(rs))} |")
    L.append("\n## By category\n\n| Category | Pass | Rate |\n|---|---|---|")
    for c, rs in sorted(by_cat.items()):
        L.append(f"| {c} | {sum(1 for r in rs if r['passed'])}/{len(rs)} | {pct(sum(1 for r in rs if r['passed']), len(rs))} |")

    fails = [r for r in records if not r["passed"] and not r["clarified"]]
    if fails:
        L.append("\n## Failures\n")
        for r in fails:
            L.append(f"### ❌ {r['id']}  ({r['source']} / {r['category']})")
            L.append(f"- Q: {r['question']}")
            L.append(f"- Why: {', '.join(r['fails'])}")
            if r.get("judge"):
                L.append(f"- Judge: {r['judge'].get('score')}/5 — {r['judge'].get('reason')}")
            if r["sql"]:
                L.append(f"- SQL: `{r['sql'][:300]}`")
            L.append("")

    low = [r for r in judged if r["passed"] and r["judge"]["score"] is not None and r["judge"]["score"] <= 3]
    if low:
        L.append("\n## Passed checks but judge flagged (score ≤ 3)\n")
        for r in low:
            L.append(f"- **{r['id']}** ({r['judge']['score']}/5): {r['judge'].get('reason')}")

    (RESULTS_DIR / "scorecard.md").write_text("\n".join(L) + "\n")
    print(f"\nWrote {RESULTS_DIR/'results.json'} and {RESULTS_DIR/'scorecard.md'}")
    print(f"DETERMINISTIC PASS: {passed}/{total} ({pct(passed,total)})"
          + (f"  |  JUDGE MEAN: {avg_judge:.2f}/5" if avg_judge is not None else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("BASE", "http://localhost:8081"))
    ap.add_argument("--token", default=os.environ.get("TOKEN", "poc-demo-token-2024"))
    ap.add_argument("--judge", action="store_true", help="enable LLM-as-judge (needs OPENAI_API_KEY)")
    ap.add_argument("--judge-model", default="gpt-4o-mini")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=200)
    ap.add_argument("--grep", default="")
    args = ap.parse_args()

    scenarios = json.loads((HERE / "scenarios.json").read_text())
    if args.grep:
        scenarios = [s for s in scenarios if args.grep in s["id"]]

    judge_key = None
    if args.judge:
        env = load_env(REPO / ".env")
        judge_key = os.environ.get("OPENAI_API_KEY") or env.get("OPENAI_API_KEY") or env.get("LLM_API_KEY")
        if not judge_key:
            print("WARNING: --judge set but no OPENAI_API_KEY found; running deterministic only.")

    meta = {"base": args.base, "judge": bool(judge_key), "judge_model": args.judge_model,
            "count": len(scenarios), "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    print(f"Running {len(scenarios)} scenarios against {args.base} "
          f"(judge={'on' if judge_key else 'off'}, concurrency={args.concurrency})\n")

    records = []
    with cf.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futs = [pool.submit(run_one, sc, args.base, args.token, judge_key, args.judge_model, args.timeout)
                for sc in scenarios]
        for f in cf.as_completed(futs):
            records.append(f.result())

    order = {s["id"]: i for i, s in enumerate(scenarios)}
    records.sort(key=lambda r: order.get(r["id"], 999))
    write_reports(records, meta)


if __name__ == "__main__":
    main()
