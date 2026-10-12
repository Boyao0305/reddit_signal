"""Score every model run against a reference run and write the results tables.

Comparisons are per thread. A failed call (API error, empty choices, truncated
or unparsable JSON) counts as "no pains" for that thread — that is what
production does: the thread is marked processed with zero pains. Failure rates
are reported separately.

Downstream signal (what clustering actually consumes): each pain_statement is
embedded with the production embedder (all-MiniLM-L6-v2, normalized) and
assigned to the nearest analysis-run-16 centroid if cosine >= 0.70, exactly as
the daily job does. The set of (thread, cluster) pairs a model produces is
compared with the reference's set (precision / recall / F1).
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
from collections import defaultdict

import numpy as np

from common import DATA, OUTPUTS, SAMPLE_PATH, connect, read_jsonl

REF_DEFAULT = "anthropic/claude-sonnet-5.5"
CONTINUITY_REF = "google/gemini-2.5-flash-lite"
PRODUCTION_LABEL = "production (stored)"
ASSIGN_THRESHOLD = 0.70   # scripts/b_ops_1_daily.sh: assign_to_centroid --threshold 0.70
MATCH_THRESHOLD = 0.80
CLUSTER_RUN = 16          # run 17 is degenerate (4,120 pains); run 16 is the last healthy run
POSTS_PER_MONTH = 5000 * 30
BACKLOG_POSTS = 1_390_000


def load_runs(sample: list[dict]) -> dict[str, dict[str, dict]]:
    runs: dict[str, dict[str, dict]] = {}
    for path in sorted(OUTPUTS.glob("*.jsonl")):
        rows = read_jsonl(path)
        if not rows:
            continue
        label = rows[0]["model"] + (f" [{rows[0]['tag']}]" if rows[0].get("tag") else "")
        runs[label] = {r["key"]: r for r in rows}
    runs[PRODUCTION_LABEL] = {
        s["key"]: {"key": s["key"], "status": "ok", "parse_status": "ok", "pains": s["production_pains"]}
        for s in sample
    }
    return runs


def pains_of(row: dict | None) -> list[dict]:
    if not row or row.get("status") != "ok" or row.get("parse_status") != "ok":
        return []
    return row.get("pains") or []


class Embedder:
    def __init__(self, texts: set[str]):
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-MiniLM-L6-v2")
        ordered = sorted(texts)
        vecs = model.encode(ordered, batch_size=128, normalize_embeddings=True, show_progress_bar=False)
        self.vec = {t: v for t, v in zip(ordered, vecs)}

    def __call__(self, text: str) -> np.ndarray:
        return self.vec[text]


def load_centroids() -> tuple[np.ndarray, list[int]]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, centroid::text FROM pain_clusters WHERE run_id = %s AND centroid IS NOT NULL",
                    (CLUSTER_RUN,))
        rows = cur.fetchall()
    ids = [r[0] for r in rows]
    mat = np.array([np.fromstring(r[1].strip("[]"), sep=",", dtype=np.float32) for r in rows])
    mat /= np.linalg.norm(mat, axis=1, keepdims=True)
    return mat, ids


def greedy_match(a: np.ndarray, b: np.ndarray) -> list[tuple[int, int, float]]:
    """One-to-one greedy matching on cosine similarity (rows a, cols b)."""
    if len(a) == 0 or len(b) == 0:
        return []
    sim = a @ b.T
    pairs = []
    used_a, used_b = set(), set()
    for flat in np.argsort(-sim, axis=None):
        i, j = divmod(int(flat), sim.shape[1])
        if i in used_a or j in used_b:
            continue
        pairs.append((i, j, float(sim[i, j])))
        used_a.add(i)
        used_b.add(j)
        if len(used_a) == len(a) or len(used_b) == len(b):
            break
    return pairs


def prf(tp: int, n_pred: int, n_true: int) -> tuple[float, float, float]:
    p = tp / n_pred if n_pred else 0.0
    r = tp / n_true if n_true else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


def tools_jaccard(a: list[str], b: list[str]) -> float:
    sa = {t.strip().lower() for t in a if t}
    sb = {t.strip().lower() for t in b if t}
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


def compare(model_run: dict[str, dict], ref_run: dict[str, dict], keys: list[str],
            emb: Embedder, assign, nearest, n_boot: int = 300) -> dict:
    tp_post = n_pred_post = n_true_post = 0
    abs_count_err = []
    n_ref_pains = n_mod_pains = matched = 0
    best_cos = []
    wk_agree, tool_j = [], []
    ref_pairs, mod_pairs = set(), set()
    ref_near, mod_near = set(), set()
    ref_topics: dict[int, int] = defaultdict(int)
    mod_topics: dict[int, int] = defaultdict(int)
    matched07 = 0
    per_post = []  # (true_pos_post, pred_pos_post, near_tp, near_pred, near_true) for bootstrap
    for k in keys:
        rp, mp = pains_of(ref_run.get(k)), pains_of(model_run.get(k))
        tp_post += bool(rp) and bool(mp)
        n_pred_post += bool(mp)
        n_true_post += bool(rp)
        abs_count_err.append(abs(len(rp) - len(mp)))
        n_ref_pains += len(rp)
        n_mod_pains += len(mp)
        ra = np.array([emb(p["pain_statement"]) for p in rp]) if rp else np.zeros((0, 384))
        ma = np.array([emb(p["pain_statement"]) for p in mp]) if mp else np.zeros((0, 384))
        if len(ra):
            best = (ra @ ma.T).max(axis=1) if len(ma) else np.zeros(len(ra))
            best_cos.extend(best.tolist())
        for i, j, s in greedy_match(ra, ma):
            if s >= 0.70:
                matched07 += 1
            if s >= MATCH_THRESHOLD:
                matched += 1
                wk_agree.append(rp[i]["is_workaround"] == mp[j]["is_workaround"])
                tool_j.append(tools_jaccard(rp[i]["mentioned_tools"], mp[j]["mentioned_tools"]))
        for v in ra:
            c = assign(v)
            if c is not None:
                ref_pairs.add((k, c))
        for v in ma:
            c = assign(v)
            if c is not None:
                mod_pairs.add((k, c))
        rn = {nearest(v) for v in ra}
        mn = {nearest(v) for v in ma}
        for c in (nearest(v) for v in ra):
            ref_topics[c] += 1
        for c in (nearest(v) for v in ma):
            mod_topics[c] += 1
        ref_near |= {(k, c) for c in rn}
        mod_near |= {(k, c) for c in mn}
        per_post.append((bool(rp), bool(mp), len(rn & mn), len(mn), len(rn)))
    post_p, post_r, post_f = prf(tp_post, n_pred_post, n_true_post)
    _, _, near_f = prf(len(ref_near & mod_near), len(mod_near), len(ref_near))
    topics = sorted(set(ref_topics) | set(mod_topics))
    rv = np.array([ref_topics[t] for t in topics], dtype=float)
    mv = np.array([mod_topics[t] for t in topics], dtype=float)
    topic_cos = float(rv @ mv / (np.linalg.norm(rv) * np.linalg.norm(mv))) if rv.any() and mv.any() else 0.0
    rng = np.random.default_rng(0)
    arr = np.array(per_post, dtype=float)
    boot_post, boot_near = [], []
    for _ in range(n_boot):
        b = arr[rng.integers(0, len(arr), len(arr))]
        tp = float(np.sum(b[:, 0] * b[:, 1]))
        boot_post.append(prf(tp, b[:, 1].sum(), b[:, 0].sum())[2])
        boot_near.append(prf(b[:, 2].sum(), b[:, 3].sum(), b[:, 4].sum())[2])
    ci = lambda xs: (float(np.percentile(xs, 2.5)), float(np.percentile(xs, 97.5)))
    _, _, cl_f = prf(len(ref_pairs & mod_pairs), len(mod_pairs), len(ref_pairs))
    cl_p, cl_r, _ = prf(len(ref_pairs & mod_pairs), len(mod_pairs), len(ref_pairs))
    return {
        "post_precision": post_p, "post_recall": post_r, "post_f1": post_f,
        "count_mae": statistics.mean(abs_count_err),
        "pains": n_mod_pains, "ref_pains": n_ref_pains,
        "stmt_recall@0.8": matched / n_ref_pains if n_ref_pains else 0.0,
        "stmt_recall@0.7": matched07 / n_ref_pains if n_ref_pains else 0.0,
        "stmt_precision@0.7": matched07 / n_mod_pains if n_mod_pains else 0.0,
        "post_f1_ci": ci(boot_post),
        "nearest_cluster_f1": near_f, "nearest_cluster_f1_ci": ci(boot_near),
        "topic_cosine": topic_cos,
        "stmt_precision@0.8": matched / n_mod_pains if n_mod_pains else 0.0,
        "mean_best_cos": statistics.mean(best_cos) if best_cos else 0.0,
        "workaround_agree": statistics.mean(wk_agree) if wk_agree else None,
        "tools_jaccard": statistics.mean(tool_j) if tool_j else None,
        "cluster_precision": cl_p, "cluster_recall": cl_r, "cluster_f1": cl_f,
        "cluster_pairs": len(mod_pairs), "ref_cluster_pairs": len(ref_pairs),
    }


def run_stats(run: dict[str, dict], keys: list[str]) -> dict:
    rows = [run.get(k) for k in keys]
    rows = [r for r in rows if r]
    n = len(rows)
    ok = [r for r in rows if r.get("status") == "ok"]
    costs = [float(r.get("cost_usd") or 0.0) for r in rows]
    per_post = statistics.mean(costs) if costs else 0.0

    def mean_of(field):
        vals = [r.get(field) for r in rows if r.get(field) is not None]
        return statistics.mean(vals) if vals else None

    lat = [r["latency_s"] for r in ok if r.get("latency_s") is not None]
    return {
        "n": n,
        "valid_rate": sum(1 for r in ok if r.get("parse_status") == "ok") / n if n else 0.0,
        "truncated_rate": sum(1 for r in ok if r.get("finish_reason") == "length") / n if n else 0.0,
        "empty_choices": sum(1 for r in rows if r.get("status") == "empty_choices"),
        "api_errors": sum(1 for r in rows if r.get("status") in ("api_error", "transport_error")),
        "unparsable": sum(1 for r in ok if r.get("parse_status") in ("json_error", "not_array")),
        "prompt_tokens": mean_of("prompt_tokens"), "completion_tokens": mean_of("completion_tokens"),
        "reasoning_tokens": mean_of("reasoning_tokens"),
        "cost_per_post": per_post,
        "cost_month": per_post * POSTS_PER_MONTH, "cost_backlog": per_post * BACKLOG_POSTS,
        "latency_p50": statistics.median(lat) if lat else None,
        "providers": sorted({str(r.get("provider")) for r in ok}),
        "max_tokens": rows[0].get("max_tokens") if rows else None,
        "reasoning_effort": rows[0].get("reasoning_effort") if rows else None,
    }


def fmt(v, pct=False, money=False, digits=2):
    if v is None:
        return "–"
    if money:
        return f"${v:,.2f}" if v >= 1 else f"${v:.4f}"
    if pct:
        return f"{100 * v:.0f}%"
    return f"{v:.{digits}f}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default=REF_DEFAULT)
    ap.add_argument("--handcheck", type=int, default=50)
    ap.add_argument("--handcheck-models", nargs="*", default=None,
                    help="candidate labels for the hand-check sheet (default: top 2 by nearest-cluster F1)")
    args = ap.parse_args()

    sample = [json.loads(l) for l in SAMPLE_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    runs = load_runs(sample)
    if args.ref not in runs:
        raise SystemExit(f"reference run {args.ref!r} not found; have {sorted(runs)}")
    texts = {p["pain_statement"] for run in runs.values() for row in run.values() for p in pains_of(row)}
    emb = Embedder(texts)
    cmat, cids = load_centroids()

    cache: dict[bytes, int | None] = {}

    def assign(v: np.ndarray):
        key = v.tobytes()
        if key not in cache:
            sims = cmat @ v
            i = int(np.argmax(sims))
            cache[key] = cids[i] if sims[i] >= ASSIGN_THRESHOLD else None
        return cache[key]

    near_cache: dict[bytes, int] = {}

    def nearest(v: np.ndarray) -> int:
        key = v.tobytes()
        if key not in near_cache:
            near_cache[key] = cids[int(np.argmax(cmat @ v))]
        return near_cache[key]

    all_keys = [s["key"] for s in sample]
    results = {"reference": args.ref, "continuity_reference": CONTINUITY_REF, "models": {}}
    for label, run in runs.items():
        keys = [k for k in all_keys if k in run]
        complete = len(keys) >= len(all_keys) * 0.9
        if not (complete or label == args.ref or label.endswith("[b]")):
            continue  # unfinished pilot / aborted run
        entry = {"stats": run_stats(run, keys) if label != PRODUCTION_LABEL else {"n": len(keys)}}
        if label != args.ref:
            entry["vs_ref"] = compare(run, runs[args.ref], keys, emb, assign, nearest)
        if label != CONTINUITY_REF and CONTINUITY_REF in runs:
            entry["vs_flashlite"] = compare(run, runs[CONTINUITY_REF], keys, emb, assign, nearest)
        results["models"][label] = entry

    # assignment rate of reference pains (context for the downstream metric)
    ref_pains = [p for row in runs[args.ref].values() for p in pains_of(row)]
    results["ref_assigned_rate"] = (sum(1 for p in ref_pains if assign(emb(p["pain_statement"])) is not None)
                                    / len(ref_pains)) if ref_pains else 0.0

    DATA.mkdir(exist_ok=True)
    (DATA / "results.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")

    lines = [
        f"Reference: `{args.ref}` · threads: {len(all_keys)} · reference pains assigned to a run-16 "
        f"cluster at 0.70: {fmt(results['ref_assigned_rate'], pct=True)}",
        "",
        "| Model | valid | trunc. | pains | post F1 [95% CI] | stmt R/P @0.7 | nearest-cluster F1 [95% CI] | "
        "topic cos | cluster F1 @0.70 | nearest-cluster F1 vs flash-lite | workaround agree | tools J | "
        "$/post | $/month (5k/day) | $ backlog (1.39M) | p50 s |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    def sort_key(item):
        label, e = item
        return -(e.get("vs_ref") or {}).get("nearest_cluster_f1", 2.0)

    def with_ci(v, name):
        if name not in v:
            return "–"
        lo, hi = v[name + "_ci"]
        return f"{v[name]:.2f} [{lo:.2f}–{hi:.2f}]"

    for label, e in sorted(results["models"].items(), key=sort_key):
        s, v, c = e["stats"], e.get("vs_ref") or {}, e.get("vs_flashlite") or {}
        is_prod = label == PRODUCTION_LABEL
        lines.append("| " + " | ".join([
            f"`{label}`" + (" (ref)" if label == args.ref else ""),
            "–" if is_prod else fmt(s.get("valid_rate"), pct=True),
            "–" if is_prod else fmt(s.get("truncated_rate"), pct=True),
            str(v.get("pains", sum(len(pains_of(r)) for r in runs[label].values()))),
            with_ci(v, "post_f1"),
            (f"{fmt(v['stmt_recall@0.7'], pct=True)} / {fmt(v['stmt_precision@0.7'], pct=True)}"
             if "stmt_recall@0.7" in v else "–"),
            with_ci(v, "nearest_cluster_f1"), fmt(v.get("topic_cosine")), fmt(v.get("cluster_f1")),
            "(self)" if label == CONTINUITY_REF else fmt(c.get("nearest_cluster_f1")),
            fmt(v.get("workaround_agree"), pct=True), fmt(v.get("tools_jaccard")),
            "–" if is_prod else fmt(s.get("cost_per_post"), money=True),
            "–" if is_prod else fmt(s.get("cost_month"), money=True),
            "–" if is_prod else fmt(s.get("cost_backlog"), money=True),
            "–" if is_prod else fmt(s.get("latency_p50"), digits=1),
        ]) + " |")
    table = "\n".join(lines)
    (DATA / "results.md").write_text(table + "\n", encoding="utf-8")
    print(table)

    # hand-check sheet: reference vs the two best candidates by cluster F1
    candidates = args.handcheck_models or [
        l for l, e in sorted(results["models"].items(), key=sort_key)
        if l not in (args.ref, PRODUCTION_LABEL) and not l.endswith("[b]")][:2]
    rng = random.Random(7)
    interesting = [s for s in sample if any(pains_of(runs[l].get(s["key"])) for l in [args.ref, *candidates])]
    picked = rng.sample(interesting, min(args.handcheck, len(interesting)))
    out = [f"# Hand-check — reference `{args.ref}` vs {', '.join(f'`{c}`' for c in candidates)}", ""]
    for s in picked:
        out.append(f"## {s['key']} · {s['surface']}")
        out.append("```")
        out.append(s["user_prompt"][:600])
        out.append("```")
        for l in [args.ref, *candidates, PRODUCTION_LABEL]:
            ps = pains_of(runs[l].get(s["key"]))
            out.append(f"**{l}** ({len(ps)})")
            for p in ps:
                wk = " [workaround]" if p["is_workaround"] else ""
                tools = f" tools={p['mentioned_tools']}" if p["mentioned_tools"] else ""
                out.append(f"- {p['pain_statement']}{wk}{tools}")
            out.append("")
    (DATA / "handcheck.md").write_text("\n".join(out), encoding="utf-8")
    print(f"\nwrote {DATA / 'results.md'}, results.json, handcheck.md (candidates: {candidates})")


if __name__ == "__main__":
    main()
