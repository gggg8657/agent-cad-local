#!/usr/bin/env python3
"""GT 벤치마크 — 이미지 역설계(이미지만) vs 텍스트 설계를 GT 별로 N회 돌려 점수 평균. 실제 LLM/VLM 사용.
  venv/bin/python bench_gt.py                 # gt/*.json 전부, 3회
  venv/bin/python bench_gt.py msr_ureca_like 5
결과: 표준출력 표 + WORKSPACE/bench/<시각>.json (프로젝트들은 WORKSPACE/projects 에 남음)"""
import datetime
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cad import pipeline  # noqa: E402

IMG_Q = "첨부한 그림과 같은 용융염원자로를 역설계해줘. 내부는 원리로 추정."
TXT_Q = "용융염원자로(MSR) 개념설계해줘."


def one(gid, mode):
    g = pipeline.load_gt(gid)
    up = pipeline.save_upload("bench.png", open(os.path.join(pipeline.GT_DIR, g["image"]), "rb").read()) if mode == "image" else None
    t = time.time()
    p = pipeline.run(IMG_Q if mode == "image" else TXT_Q, "assembly", upload=up, gt=gid)
    r = p["revisions"][-1]
    d = json.load(open(os.path.join(pipeline.pdir(p["id"]), r["rev"], "gt_score.json")))
    return {"gt": gid, "mode": mode, "project": p["id"], "status": r["status"], "total": r["gt"]["total"], "sec": round(time.time() - t, 1),
            "diff": d["components"]["diff"], "visual": (r.get("visual") or {}).get("match")}


def main():
    ids = [sys.argv[1]] if len(sys.argv) > 1 and not sys.argv[1].isdigit() else [g["id"] for g in pipeline.list_gt()]
    n = int(sys.argv[-1]) if sys.argv[-1].isdigit() else 3
    rows = []
    for gid in ids:
        for mode in ("image", "text"):
            for _ in range(n):
                r = one(gid, mode)
                rows.append(r)
                print(f"{gid:20s} {mode:5s} {r['total']:.3f} {r['status']:8s} {r['sec']:5.1f}s diff={r['diff']}", flush=True)
    print("\n| GT | 방식 | 평균 | 최소 | 최대 | n |\n|---|---|---|---|---|---|")
    for gid in ids:
        for mode in ("image", "text"):
            v = [r["total"] for r in rows if r["gt"] == gid and r["mode"] == mode]
            print(f"| {gid} | {mode} | {statistics.mean(v):.3f} | {min(v):.3f} | {max(v):.3f} | {len(v)} |")
    out = os.path.join(pipeline.WS, "bench", datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(rows, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("저장:", out)


if __name__ == "__main__":
    main()
