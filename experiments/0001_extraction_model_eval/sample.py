"""Draw the evaluation sample from production (read-only) and freeze it to data/.

Sample = Reddit posts created in --month that the production pipeline already
extracted with the current model (processed on/after --processed-since), plus
Hacker News threads processed in the same period. Reddit posts are stratified
round-robin across subreddits so no community dominates. For every sampled
thread the stored production pains are saved too, for drift comparison.
"""
from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from datetime import date

from common import DATA, SAMPLE_PATH, connect
from prod_code import SYSTEM_PROMPT, build_prompt, pwc_from_sample

EVENT_COLS = ["id", "source", "source_table", "source_row_id", "event_type", "event_time",
              "surface", "author", "parent_event_id", "title", "text", "engagement_score", "raw_meta"]


def _event(row: tuple) -> dict:
    d = dict(zip(EVENT_COLS, row))
    d["event_time"] = d["event_time"].isoformat() if d["event_time"] else None
    d["raw_meta"] = d["raw_meta"] or {}
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", default="2026-06", help="Reddit post creation month YYYY-MM")
    ap.add_argument("--processed-since", default="2026-07-01",
                    help="only threads extracted on/after this date (current model, settings changed 2026-06-24)")
    ap.add_argument("--reddit", type=int, default=280)
    ap.add_argument("--hn", type=int, default=20)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    y, m = map(int, args.month.split("-"))
    start = date(y, m, 1)
    end = date(y + (m == 12), m % 12 + 1, 1)
    rng = random.Random(args.seed)
    DATA.mkdir(parents=True, exist_ok=True)

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT se.id, se.surface
            FROM processed_posts pp
            JOIN posts p ON p.id = pp.post_id
            JOIN signal_events se
              ON se.source = 'reddit' AND se.source_table = 'posts'
             AND se.source_row_id = p.id::text AND se.parent_event_id IS NULL
            WHERE pp.processed_at >= %s
              AND p.created_utc >= %s AND p.created_utc < %s
            ORDER BY md5(p.id::text || %s)
            LIMIT 6000
            """,
            (args.processed_since, start, end, str(args.seed)),
        )
        pool = cur.fetchall()
        by_sub: dict[str, list[int]] = defaultdict(list)
        for event_id, surface in pool:
            by_sub[surface or "?"].append(event_id)
        subs = list(by_sub)
        rng.shuffle(subs)
        reddit_ids: list[int] = []
        while len(reddit_ids) < args.reddit and any(by_sub.values()):
            for s in subs:
                if by_sub[s] and len(reddit_ids) < args.reddit:
                    reddit_ids.append(by_sub[s].pop())

        cur.execute(
            """
            SELECT se.id
            FROM processed_signal_events pse
            JOIN signal_events se ON se.id = pse.signal_event_id
            WHERE pse.source = 'hackernews' AND pse.processed_at >= %s
              AND se.parent_event_id IS NULL
            ORDER BY md5(se.id::text || %s)
            LIMIT %s
            """,
            (args.processed_since, str(args.seed), args.hn),
        )
        hn_ids = [r[0] for r in cur.fetchall()]

        parent_ids = reddit_ids + hn_ids
        cols = ", ".join(EVENT_COLS)
        cur.execute(f"SELECT {cols} FROM signal_events WHERE id = ANY(%s)", (parent_ids,))
        parents = {r[0]: _event(r) for r in cur.fetchall()}
        cur.execute(f"SELECT {cols} FROM signal_events WHERE parent_event_id = ANY(%s) ORDER BY parent_event_id, id",
                    (parent_ids,))
        children: dict[int, list[dict]] = defaultdict(list)
        for r in cur.fetchall():
            children[r[8]].append(_event(r))

        rows = []
        for pid in parent_ids:
            parent = parents[pid]
            rows.append({
                "key": f"{parent['source']}:{pid}",
                "source": parent["source"],
                "surface": parent["surface"],
                "parent": parent,
                "children": children.get(pid, []),
            })

        # stored production output for the same threads
        reddit_post_ids = [int(parents[i]["source_row_id"]) for i in reddit_ids]
        cur.execute(
            """SELECT post_id, pain_statement, raw_quote, mentioned_tools, is_workaround
               FROM pain_objects WHERE prompt_version = 'v1' AND post_id = ANY(%s)""",
            (reddit_post_ids,),
        )
        prod_reddit: dict[int, list[dict]] = defaultdict(list)
        for post_id, stmt, quote, tools, wk in cur.fetchall():
            prod_reddit[post_id].append({"pain_statement": stmt, "raw_quote": quote,
                                         "mentioned_tools": list(tools or []), "is_workaround": bool(wk)})
        hn_event_ids = hn_ids + [c["id"] for h in hn_ids for c in children.get(h, [])]
        cur.execute(
            """SELECT signal_event_id, pain_statement, raw_quote, mentioned_tools, is_workaround
               FROM pain_objects WHERE prompt_version = 'v1' AND signal_event_id = ANY(%s)""",
            (hn_event_ids,),
        )
        child_to_parent = {c["id"]: h for h in hn_ids for c in children.get(h, [])}
        prod_hn: dict[int, list[dict]] = defaultdict(list)
        for se_id, stmt, quote, tools, wk in cur.fetchall():
            prod_hn[child_to_parent.get(se_id, se_id)].append(
                {"pain_statement": stmt, "raw_quote": quote,
                 "mentioned_tools": list(tools or []), "is_workaround": bool(wk)})

    with SAMPLE_PATH.open("w", encoding="utf-8") as fh:
        for row in rows:
            pwc = pwc_from_sample(row)
            row["user_prompt"] = build_prompt(pwc)
            pid = row["parent"]["id"]
            row["production_pains"] = (prod_reddit.get(int(row["parent"]["source_row_id"]), [])
                                       if row["source"] == "reddit" else prod_hn.get(pid, []))
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    n_sub = len({r["surface"] for r in rows if r["source"] == "reddit"})
    chars = [len(SYSTEM_PROMPT) + len(r["user_prompt"]) for r in rows]
    with_pains = sum(1 for r in rows if r["production_pains"])
    print(f"pool={len(pool)} reddit={len(reddit_ids)} ({n_sub} subreddits) hn={len(hn_ids)}")
    print(f"prompt chars: mean={sum(chars)/len(chars):.0f} max={max(chars)}  "
          f"threads with stored production pains: {with_pains}/{len(rows)}")
    print(f"wrote {SAMPLE_PATH}")


if __name__ == "__main__":
    main()
