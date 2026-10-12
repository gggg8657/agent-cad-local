#!/usr/bin/env python3
"""LLM 없이 전 경로 검증 — 가짜 LLM 응답으로 5개 작업(3D·2D·ICT·DXF 수정·DXF 분석) + 수리 루프 + IR 재빌드 + 리비전.
venv/bin/python selftest.py"""
import json
import os
import re
import shutil
import sys
import tempfile

WS = tempfile.mkdtemp(prefix="cadtest-")
os.environ["WORKSPACE"] = WS
os.environ["KB_OVERRIDES"] = os.path.join(WS, "kb_overrides")  # 지식 DB 사용자 수정도 임시 폴더에
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from cad import llm, pipeline, util, part2d, ict, dxfedit  # noqa: E402

QUEUE = []
SEEN = []


def fake_chat(system, messages, model=None, on_token=None, json_mode=False, images=None):
    SEEN.append((system[:40], messages))
    out = QUEUE.pop(0)
    out = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
    on_token and on_token(out)
    return out


llm.chat = fake_chat
events = []
emit = events.append
ok = lambda c, m: (print("  ✔", m) if c else (_ for _ in ()).throw(AssertionError(m)))

try:
    # 0) 유틸
    print("[유틸]")
    ok(util.extract_json('<think>x</think>```json\n{"a":[1,2,],}\n```') == {"a": [1, 2]}, "LLM 출력에서 JSON 추출 (think·펜스·끝쉼표)")
    r, names = util.resolve_params({"size": ["W", "W/2", 6], "name": "W"}, {"W": 80})
    ok(r["size"] == [80, 40, 6] and r["name"] == "W", "파라미터 수식 평가 (텍스트 키는 보존)")
    try:
        util.safe_eval("__import__('os')", {})
        ok(False, "안전 평가")
    except ValueError:
        ok(True, "수식 평가기가 함수 호출·속성 접근 차단")
    ok(pipeline.route("모터 브래킷 만들어줘", False)[0] == "part3d", "라우터: 브래킷 → part3d")
    ok(pipeline.route("판재 DXF 만들어줘 200x100", False)[0] == "part2d", "라우터: 판재 → part2d")
    ok(pipeline.route("지하철역 CCTV 배치도 초안", False)[0] == "ict", "라우터: CCTV 배치도 → ict")
    ok(pipeline.route("이 도면에 AP 4개 추가", True)[0] == "dxf_edit", "라우터: 첨부 + 추가 → dxf_edit")
    ok(pipeline.route("이 도면 분석해줘", True)[0] == "dxf_analyze", "라우터: 첨부 + 분석 → dxf_analyze")

    # 1) 2D — 첫 시도 구멍이 외곽 밖 → 수리 루프 → 통과
    print("[2D 판재 + 수리 루프]")
    bad = {"kind": "part2d", "name": "plate", "material": "SS400", "thickness": 3, "outline": {"type": "rect", "w": 200, "h": 100, "r": 10},
           "holes": [{"d": 6.6, "at": [[120, 0]]}]}
    good = dict(bad, holes=[{"thread": "M6", "pattern": "corners", "inset": 12}], cutouts=[{"type": "rect", "w": 40, "h": 20, "r": 3, "at": [0, 0]}],
                expect={"bbox": [200, 100]})
    QUEUE[:] = [bad, good]
    p = pipeline.run("200x100 판재 M6 4개", "part2d", emit=emit)
    r = p["revisions"][-1]
    ok(r["attempts"] == 2 and r["status"] == "pass", f"구멍 이탈(P2D-002) → 수리 1회 → pass ({r['status']})")
    ok("[오류]" in SEEN[-1][1][-1]["content"] and "P2D-002" in SEEN[-1][1][-1]["content"], "수리 프롬프트에 규칙 ID·현재 IR 포함")
    ok(r["metrics"]["holes"] == 4 and abs(r["metrics"]["mass_g"] - (200 * 100 - (100 - 4 * 10 * 10 + 3.14159 * 100) - 4 * 3.14159 * 3.3 ** 2 - (800 - (4 - 3.14159) * 9)) * 3 * 7.85 / 1000) < 15,
       f"질량 계산 {r['metrics']['mass_g']} g")
    d = os.path.join(pipeline.pdir(p["id"]), "R00")
    ok(all(os.path.exists(os.path.join(d, f)) for f in ("plate.dxf", "plate_cut.dxf", "drawing.svg", "report.md", "ir.json", "attempt1.err.txt")),
       "가공도·cut DXF·SVG·보고서·IR·수리 이력 저장")
    # 다듬기 → R01, IR diff
    QUEUE[:] = [dict(good, thickness=5)]
    p = pipeline.run("두께 5로", pid=p["id"], emit=emit)
    ok(len(p["revisions"]) == 2 and "[현재 IR]" in SEEN[-1][1][0]["content"], "다듬기 요청은 [현재 IR] 포함 → R01")
    ok(any("thickness" in x for x in p["revisions"][-1]["ir_diff"]), "IR diff 기록: " + p["revisions"][-1]["ir_diff"][0])
    # 수동 재빌드
    p = pipeline.rebuild(p["id"], dict(good, thickness=8), emit)
    ok(p["revisions"][-1]["rev"] == "R02" and p["revisions"][-1]["source"] == "manual", "IR 직접 수정 → LLM 없이 R02")

    # 2) 3D — 쉘 뒤 필렛을 엔진이 앞으로 재정렬
    print("[3D 부품 (OpenCascade 서브프로세스)]")
    case = {"kind": "part3d", "name": "case", "material": "ABS", "params": {"W": 100, "D": 70, "H": 30, "T": 2},
            "base": {"type": "box", "size": ["W", "D", "H"]},
            "features": [{"op": "shell", "face": "top", "t": "T"},
                         {"op": "hole", "face": "bottom", "thread": "M2.5", "tap": True, "pattern": "grid", "nx": 2, "ny": 2, "dx": 58, "dy": 49},
                         {"op": "cut", "shape": "box", "size": [6, 12, 6], "at": ["W/2", 0, 15]},
                         {"op": "fillet", "edges": "vertical", "r": 4}],
            "expect": {"bbox": [100, 70, 30]}}
    QUEUE[:] = [case]
    p = pipeline.run("라즈베리파이 케이스", "part3d", emit=emit)
    r = p["revisions"][-1]
    ok(r["status"] == "pass", f"케이스 빌드 {r['status']} {r.get('error') or ''}")
    ok(any("쉘 앞으로" in n for n in r["notes"]), "쉘 뒤 필렛 → 쉘 앞으로 재정렬 (설계의도 R4 유지)")
    ok(r["metrics"]["solids"] == 1 and r["metrics"]["bbox_mm"] == [100, 70, 30], f"단일 솔리드, 외형 {r['metrics']['bbox_mm']}")
    d = os.path.join(pipeline.pdir(p["id"]), "R00")
    ok(all(os.path.exists(os.path.join(d, f)) for f in ("case.step", "case.stl", "case.glb", "case_drawing.dxf")), "STEP·STL·GLB·3면도 DXF")
    lb = {"kind": "part3d", "name": "lb", "material": "AL6061",
          "base": {"type": "extrude", "plane": "XZ", "points": [[0, 0], [60, 0], [60, 5], [5, 5], [5, 50], [0, 50]], "h": 50},
          "features": [{"op": "hole", "face": "left", "d": 22, "at": [[0, 80]]}]}
    lb_fixed = dict(lb, features=[{"op": "hole", "face": "left", "d": 22, "at": [[0, 27.5]]},
                                  {"op": "hole", "face": "left", "thread": "M3", "pattern": "grid", "nx": 2, "ny": 2, "dx": 31, "dy": 31, "center": [0, 27.5]}])
    QUEUE[:] = [lb, lb_fixed]
    p = pipeline.run("L 브래킷", "part3d", emit=emit)
    r = p["revisions"][-1]
    ok(r["attempts"] == 2 and r["status"] == "pass", f"면 밖 구멍(P3D-004) → 수리 → pass, 질량 {r['metrics'].get('mass_g')} g")
    # tube 내경을 params 이름으로 (id 는 ICT 에선 텍스트 키지만 part3d 에선 숫자 필드)
    ring = {"kind": "part3d", "name": "ring", "material": "SUS304", "params": {"OD": 150, "ID": 120, "H": 5},
            "base": {"type": "tube", "d": "OD", "id": "ID", "h": "H"},
            "features": [{"op": "hole", "face": "top", "d": 8, "pattern": "polar", "n": 3, "pcd": 135}], "expect": {"bbox": [150, 150, 5]}}
    QUEUE[:] = [ring]
    p = pipeline.run("외경 150 내경 120 두께 5 링, 구멍 3개", "part3d", emit=emit)
    r = p["revisions"][-1]
    ok(r["attempts"] == 1 and r["status"] == "pass" and r["metrics"]["bbox_mm"] == [150, 150, 5], f"tube id='ID'(params 이름) → 1회에 pass ({r['status']} {r.get('error') or ''})")
    ok(util.resolve_params({"id": "CAM-01"}, {"CAM": 1})[0]["id"] == "CAM-01", "ICT 의 id 는 여전히 텍스트로 보존")
    # 같은 IR 을 되풀이하면 힌트 1회 후 수리 중단 (헛도는 수리 루프 방지)
    bad_tube = dict(ring, base={"type": "tube", "d": "OD", "id": "IDX", "h": "H"})
    QUEUE[:] = [bad_tube, bad_tube, bad_tube, bad_tube]
    p = pipeline.run("링", "part3d", emit=emit)
    r = p["revisions"][-1]
    ok(r["status"] == "failed" and r["attempts"] == 3 and "IDX" in r["error"], f"같은 IR 반복 → 3번째에 중단, 오류에 원인 값 표시 ({r['attempts']}회)")
    ok("[주의]" in SEEN[-1][1][0]["content"], "반복 IR 이면 수리 프롬프트에 '바꾸지 않았다' 힌트")
    # expect.bbox: 사용자가 말하지 않은 축은 LLM 추정 → 경고만 (수리 안 함), 말한 축은 오류 → 수리
    blk = {"kind": "part3d", "name": "blk", "material": "AL6061", "base": {"type": "box", "size": [60, 40, 20]},
           "features": [{"op": "boss", "face": "top", "shape": "circle", "d": 20, "at": [0, 0], "height": 10}], "expect": {"bbox": [60, 40, 20]}}
    QUEUE[:] = [blk]
    p = pipeline.run("보스 달린 블록", "part3d", emit=emit)
    r = p["revisions"][-1]
    ok(r["attempts"] == 1 and r["status"] == "warnings" and any(c["id"] == "P3D-005" and c["severity"] == "warning" for c in r["checks"]),
       f"치수 없는 요청의 LLM 추정 외형 불일치 → 경고, 수리 없음 ({r['status']})")
    QUEUE[:] = [blk, dict(blk, base={"type": "box", "size": [60, 40, 20]}, features=blk["features"][:0] + [dict(blk["features"][0], height=10)],
                          expect={"bbox": [60, 40, 30]})]
    p = pipeline.run("60x40x30 블록, 위에 Ø20 보스 높이 10", "part3d", emit=emit)
    r = p["revisions"][-1]
    ok(r["attempts"] == 2 and r["status"] == "pass", f"사용자가 말한 외형(30)과 다르면 오류 → 수리 → pass ({r['status']})")
    # 그림 첨부 3D: 구조 판독(비율) → 설계 → 결과 렌더 시각 비교 → 일치 낮으면 1회 시각 수리, 나은 쪽 채택
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(buf, "PNG")
    up = pipeline.save_upload("ref.png", buf.getvalue())
    plate = {"kind": "part3d", "name": "plate", "material": "AL6061", "base": {"type": "box", "size": [60, 40, 20]}, "features": []}
    QUEUE[:] = [{"views": [{"panel": "왼쪽", "view": "등각", "shows": "판"}], "shape": "얇은 사각 판", "proportions": {"W": 1.0, "D": 0.5, "H": 0.05}, "thin": True},
                plate, {"match": 30, "differences": [{"what": "두께가 너무 두꺼움", "severity": "high", "fix": "Z 를 X 의 0.05 로"}]},
                dict(plate, base={"type": "box", "size": [100, 50, 5]}), {"match": 85, "differences": []}]
    p = pipeline.run("첨부 그림대로 3D 부품", "part3d", upload=up, emit=emit)
    r = p["revisions"][-1]
    d = os.path.join(pipeline.pdir(p["id"]), "R00")
    ok(any("비율 X:Y:Z = 1 : 0.5 : 0.05" in m[1][0]["content"] for m in SEEN[-4:]),
       "그림 판독 비율이 설계 프롬프트에 들어감")
    ok(r.get("visual_repair") == "채택" and r["visual"]["match"] == 85 and r["metrics"]["bbox_mm"] == [100, 50, 5]
       and all(os.path.exists(os.path.join(d, f)) for f in ("part_render.png", "visual_compare.json", "attempt_visual.json")),
       f"시각 비교 일치 30 → 시각 수리 → 85 채택, 결과 렌더 저장 ({r.get('visual_repair')})")

    # 정의 안 된 치수 이름 → 0 으로 조용히 바꾸지 않고 오류 → 수리
    slot = {"kind": "part3d", "name": "slot", "material": "AL6061", "params": {"W": 60}, "base": {"type": "box", "size": ["W", 40, 20]},
            "features": [{"op": "pocket", "face": "top", "shape": "slot", "l": 30, "w": "slot_w", "at": [0, 0], "depth": 5}]}
    QUEUE[:] = [slot, dict(slot, params={"W": 60, "slot_w": 8})]
    p = pipeline.run("장공 블록", "part3d", emit=emit)
    r = p["revisions"][-1]
    ok(r["attempts"] == 2 and r["status"] == "pass" and "치수 이름 'slot_w' 이 정의되지 않음" in SEEN[-1][1][0]["content"],
       f"정의 안 된 이름 slot_w → 오류 문구로 수리 → pass ({r['status']})")
    # 필렛: 대상 모서리 없음/커널 실패 → 줄여 재시도 → 건너뛰고 경고 (빌드는 성공)
    QUEUE[:] = [{"kind": "part3d", "name": "disc", "base": {"type": "cylinder", "d": 80, "h": 20},
                 "features": [{"op": "hole", "face": "top", "d": 10, "at": [[0, 0]]}, {"op": "fillet", "edges": "vertical", "r": 2}]}]
    p = pipeline.run("원판", "part3d", emit=emit)
    r = p["revisions"][-1]
    ok(r["attempts"] == 1 and r["status"] == "warnings" and any(c["id"] == "P3D-008" for c in r["checks"]),
       f"원통 vertical 필렛 실패 → 건너뜀 경고, 빌드 성공 ({r['status']})")
    # 워커 보호: 삼각 프리즘 + 단면 밖까지 나간 포켓 + 필렛 = OCC segfault → fork 자식만 죽고 워커는 결과를 낸다
    from cad import part3d
    crash = {"kind": "part3d", "name": "crash", "base": {"type": "extrude", "plane": "XY", "points": [[0, 0], [150, 0], [0, 320]], "h": 20},
             "features": [{"op": "pocket", "face": "top", "shape": "rect", "w": 130, "h": 300, "at": [10, 10], "depth": 21},
                          {"op": "fillet", "edges": "vertical", "r": 3}]}
    res = part3d.build(part3d.normalize(crash)[0], os.path.join(WS, "crash"), util.resource("rules", "part.json"))
    ok(not res.get("error") and any(c["id"] == "P3D-008" for c in res["checks"]), "커널 segfault(필렛) → 워커 생존, 필렛만 건너뜀")
    # 새 형상 어휘: 꼭짓점 라운드 + 내부 루프, 속 빈 단면(wall), 경사 절단
    import math
    for ir, want_bbox, want_vol, lab in (
            ({"kind": "part3d", "name": "plate", "params": {"L": 120, "D": 60, "T": 8},
              "base": {"type": "extrude", "plane": "XY", "points": [["-L/2", "-D/2", 10], ["L/2", "-D/2", 10], ["L/2", "D/2", 10], ["-L/2", "D/2", 10]], "h": "T",
                       "holes": [[[-20, -10, 3], [20, -10, 3], [20, 10, 3], [-20, 10, 3]], {"type": "circle", "at": [40, 0], "d": 10}]}},
             [120, 60, 8], (120 * 60 - (4 - math.pi) * 100 - (800 - (4 - math.pi) * 9) - math.pi * 25) * 8, "꼭짓점 R10 + 내부 루프(R3 창·원)"),
            ({"kind": "part3d", "name": "rack", "base": {"type": "extrude", "plane": "XY", "points": [[0, 0], [100, 0], [100, 60], [0, 60]], "wall": 5, "h": 10}},
             [100, 60, 10], (100 * 60 - 90 * 50) * 10, "wall 5 속 빈 단면 (사각 프레임)"),
            ({"kind": "part3d", "name": "sliced", "base": {"type": "cylinder", "d": 40, "h": 80},
              "features": [{"op": "cut", "shape": "plane", "point": [0, 0, 80 - 20 * math.tan(math.radians(30))], "axis": "y", "angle": 30}]},
             [40, 40, 80], math.pi * 400 * (80 - 20 * math.tan(math.radians(30))), "경사 절단 (axis y, 30°)")):
        QUEUE[:] = [ir]
        p = pipeline.run(lab, "part3d", emit=emit)
        r = p["revisions"][-1]
        ok(r["status"] == "pass" and r["metrics"]["bbox_mm"] == want_bbox and abs(r["metrics"]["volume_mm3"] - want_vol) < 0.5,
           f"{lab}: 외형 {r['metrics'].get('bbox_mm')}, 부피 {r['metrics'].get('volume_mm3')} ≈ {want_vol:.1f}")

    # 3) ICT — 엔진 자동 산정(분기실·스위치·NVR) + 규칙
    print("[ICT 배치도·개황도]")
    ir = {"kind": "ict", "name": "OO역 ICT", "floors": [
        {"id": "B1", "name": "지하1층", "spaces": [{"id": "B1_COMM", "type": "comm_room"}]},
        {"id": "B2", "name": "지하2층", "spaces": [{"id": "B2_PLATFORM", "type": "platform", "name": "승강장"}]}],
        "equipment": [{"type": "cctv", "space": "B2_PLATFORM", "count": 12}, {"type": "ap", "space": "B2_PLATFORM", "count": 6},
                      {"type": "ephone", "space": "B2_PLATFORM", "count": 4}, {"type": "core_sw", "space": "B1_COMM", "count": 2}]}
    QUEUE[:] = [ir]
    p = pipeline.run("지하철역 ICT", "ict", emit=emit)
    r = p["revisions"][-1]
    m = util.load_json(os.path.join(pipeline.pdir(p["id"]), "R00", "model.json"))
    ok(not [c for c in r["checks"] if c["severity"] == "error"], f"규칙 error 0 ({r['status']}, 경고 {len(r['checks'])})")
    ok(any("통신분기실" in a for a in r["assumptions"]), "160 m 승강장 → 분기실 자동 추가 (가정으로 보고)")
    ok(max(c["length_m"] for c in m["connections"] if c["medium"] == "CAT6") <= 90, "모든 UTP ≤ 90 m")
    ok(all(any(c["from"] == e["id"] for c in m["connections"]) for e in m["equipment"] if e["type"] in ("cctv_fixed", "ap", "ephone")), "말단 전부 결선")
    ok(len([e for e in m["equipment"] if e["type"] == "nvr"]) == 1, "CCTV 12 → NVR 1 자동")
    ups = {e["id"] for e in m["equipment"]}
    ok(len(ups) == len(m["equipment"]), "장비 ID 유일")
    files = r["files"]
    ok(sum(f.endswith(".dxf") for f in files) == 3 and sum(f.endswith(".csv") for f in files) == 3 and any(f.endswith(".glb") for f in files),
       "배치도·개황도·랙도 DXF + 일람표 CSV 3종 + 3D 목업 GLB")
    # 규칙 위반 강제: 접속 스위치 1대에 말단 30대 → ICT-003 error
    bad = dict(ir, equipment=ir["equipment"] + [{"type": "access_sw", "space": "B1_COMM", "count": 1}])
    n, _ = ict.normalize(bad)
    mm = ict.expand(n)
    ids = {c["id"] for c in ict.validate(mm)}
    ok({"ICT-003", "ICT-005"} <= ids, f"스위치 1대 수동 지정 → 포트 초과·UTP 초과 검출 {sorted(ids)}")

    # 4) DXF 수정 — 존 기반 배치, 보호 레이어, 없는 대상 수리
    print("[기존 DXF 수정]")
    sys.path.insert(0, os.path.join(ROOT, "sample"))
    import make_sample
    src = make_sample.main(os.path.join(WS, "station_B2.dxf"))
    with open(src, "rb") as f:
        uid = pipeline.save_upload("station_B2.dxf", f.read())
    ops1 = {"ops": [{"op": "add_equipment", "type": "ap", "count": 4, "zone": "승강장"}, {"op": "delete", "target": "CAM-99"}]}
    ops2 = {"ops": [{"op": "add_equipment", "type": "ap", "count": 4, "zone": "승강장"}, {"op": "delete", "target": "CAM-06"},
                    {"op": "move", "target": "CAM-03", "by": [0, -4000]}], "summary": "AP 4 추가"}
    QUEUE[:] = [ops1, ops2]
    p = pipeline.run("승강장에 AP 4개 추가, CAM-06 삭제, CAM-03 4m 아래로", "dxf_edit", upload=uid, emit=emit)
    r = p["revisions"][-1]
    ok(r["attempts"] == 2 and r["status"] == "pass", f"없는 대상 CAM-99 → 수리 → pass ({r['status']})")
    ok(r["diff"]["added"].__len__() == 4 and len(r["diff"]["removed"]) == 1 and len(r["diff"]["moved"]) == 1, "diff: +4 −1 ↦1")
    ok("station_B2_rev_A.dxf" in r["files"] and os.path.exists(os.path.join(pipeline.pdir(p["id"]), "input", "station_B2.dxf")), "save-as rev_A, 원본 보존")
    ok("승강장" in json.dumps(SEEN[-1][1][0]["content"], ensure_ascii=False) and "LWPOLYLINE" not in SEEN[-1][1][0]["content"],
       "LLM에는 DXF 원문이 아닌 요약 인덱스만")
    doc = dxfedit.read(src)
    ix = dxfedit.index(doc)
    _, errs = dxfedit.apply_ops(doc, ix, [{"op": "delete", "target": ix["zones"][0]["h"]}])
    ok(errs and "보호 레이어" in errs[0], "보호 레이어(A-*) 삭제 거부")
    QUEUE[:] = [{"ops": [{"op": "set_attr", "target": "CAM-01", "tag": "TAG", "value": "CAM-101"}]}]
    p = pipeline.run("CAM-01 을 CAM-101 로", pid=p["id"], emit=emit)
    r = p["revisions"][-1]
    ok("station_B2_rev_B.dxf" in r["files"] and r["diff"]["changed"], "누적 수정 → rev_B, 속성 변경 diff")

    # 5) DXF 분석
    print("[DXF 분석]")
    QUEUE[:] = ["승강장 도면으로 보입니다."]
    p = pipeline.run("이 도면 분석해줘", "dxf_analyze", upload=pipeline.save_upload("s.dxf", open(src, "rb").read()), emit=emit)
    r = p["revisions"][-1]
    ok("통신기기실" in r["answer"] and r["metrics"]["zones"] >= 6, f"존 {r['metrics']['zones']}개 인식, 답변 생성")

    # 6) MSR 계통 조립 + 이미지 역설계 + GT 채점
    print("[MSR 계통 조립 · 이미지 역설계 · GT]")
    from cad import assembly
    full = {"kind": "assembly", "name": "MSR", "design": {"power_mwth": 20, "salt": "FLiBe"},
            "components": [{"type": t, "count": n} for t, n in {"core": 1, "reflector": 1, "reactor_vessel": 1, "riser": 1, "primary_pump": 2,
                                                                 "heat_exchanger": 2, "he_pump": 1, "freeze_plug": 1, "dump_tank": 4, "shield_cell": 1}.items()]}
    n, e = assembly.normalize(full)
    m = assembly.build_model(n)
    ok(not e and m["calc"]["drain"]["capacity_m3"] >= m["calc"]["salt"]["inventory_m3"], f"사이징: 염 {m['calc']['salt']['inventory_m3']} m³ ≤ 덤프 {m['calc']['drain']['capacity_m3']} m³")
    gt = pipeline.load_gt("msr_ureca_like")
    ok(assembly.gt_score(m, gt)["total"] == 1.0, "정답 구성 → GT 100점 (채점기 정상)")
    less = dict(full, components=[c for c in full["components"] if c["type"] != "dump_tank"])
    n2, _ = assembly.normalize(less)
    m2 = assembly.build_model(n2)
    ok(any(c["id"] == "MSR-001" for c in assembly.validate(m2)) and assembly.gt_score(m2, gt)["total"] < 0.9, "덤프탱크 누락 → MSR-001 + GT 감점")
    small = dict(full, components=[dict(c, count=1) if c["type"] == "dump_tank" else c for c in full["components"]], design={"power_mwth": 20, "drain_margin": 0.5})
    n3, _ = assembly.normalize(small)
    ok(any(c["id"] == "MSR-001" for c in assembly.validate(assembly.build_model(n3))), "배수 여유 0.5 → 덤프 용량 부족(MSR-001)")
    read = {"subject": "MSR", "instances": [{"type": "primary_pump", "view": 0, "bbox": [0.1, 0.1, 0.2, 0.2]}]}
    view = {"views": [{"view": "단면", "counts": {"primary_pump": 2, "heat_exchanger": 2, "dump_tank": 4}}]}
    cnt = {"counts": {"primary_pump": 2, "heat_exchanger": 2, "dump_tank": 4}, "reason": "대칭"}
    vis = {"match": 88, "same": ["상하 배치"], "differences": []}
    ref_png = os.path.join(pipeline.GT_DIR, "msr_ureca_like.png")  # 사용자 제공 그림 — 공개 저장소에는 없음
    if not os.path.exists(ref_png):
        print("  (건너뜀) gt/msr_ureca_like.png 없음 — 이미지 역설계 검사 생략")
    else:
        QUEUE[:] = [read, cnt, cnt, view, full, vis]
        up = pipeline.save_upload("ref.png", open(ref_png, "rb").read())
        p = pipeline.run("이 그림처럼 역설계", "auto", upload=up, emit=emit)
        r = p["revisions"][-1]
        ok(p["task"] == "assembly" and p.get("gt") == "msr_ureca_like", "이미지 첨부 → 역설계 작업, GT 원본 이미지 자동 매칭")
        ok(r["status"] == "pass" and r["gt"]["total"] == 1.0 and r["visual"]["match"] == 88, f"VLM 판독 → IR → 빌드 → 시각 비교 → GT {r['gt']['total']}")
        ok("[이미지 판독]" in SEEN[-2][1][0]["content"] and "primary_pump 2" in SEEN[-2][1][0]["content"], "설계 LLM 프롬프트에 판독 투표 포함")
        d = os.path.join(pipeline.pdir(p["id"]), "R00")
        ok(all(any(f.endswith(x) for f in os.listdir(d)) for x in (".step", "_cutaway.glb", "_GA.dxf", "_flow.dxf", "ga_render.png", "gt_score.json", "vlm_read.json")),
           "STEP·절개 GLB·GA·계통도·렌더 PNG·GT·판독 저장")

    # 7) 지식 DB 원형 → 범용 설비 엔진 (원자로·시설 7종)
    print("[지식 DB · 원자로/설비 7종]")
    from cad import kb, plant
    for q, want in (("SMART 같은 일체형 가압경수로", "pwr_integral"), ("소듐냉각고속로 풀형", "sfr_pool"), ("개방수조형 연구용 원자로", "research_pool"),
                    ("핫셀 3칸", "hot_cell"), ("고온가스로 수소생산", "htgr_prismatic"), ("APR1400 루프", "pwr_loop"), ("사용후핵연료 저장조", "spent_fuel_pool")):
        ok(kb.search(q, 1) == [want] and pipeline.route(q, False)[0] == "assembly", f"'{q}' → 지식 DB {want}")
    ok(pipeline.route("B2 승강장 CCTV 12대 배치도", False)[0] == "ict", "ICT 장비 요청은 지식 DB 보다 ICT 우선")
    for aid in ("pwr_integral", "sfr_pool", "research_pool", "hot_cell"):
        QUEUE[:] = [{"kind": "plant", "archetype": aid}]
        p = pipeline.run(f"{aid} 개념도", "assembly", emit=emit)
        r = p["revisions"][-1]
        d = os.path.join(pipeline.pdir(p["id"]), "R00")
        ok(r["status"] == "pass" and any(f.endswith("_GA.dxf") for f in os.listdir(d)) and any(f.endswith("_cutaway.glb") for f in os.listdir(d)),
           f"{aid}: 사이징·배치·3D·GA·계통도 ({r['metrics'].get('components')}기, 높이 {r['metrics'].get('height_m')} m) {r.get('error') or ''}")
    # 연구원이 원칙·검증을 고치면 즉시 반영
    kb.save_override("sfr_pool", {"principles": kb.get("sfr_pool")["principles"] + ["[사내] 펌프 수 = IHX 수"],
                                  "checks": [{"id": "SFR-K1", "expr": "n_pump == n_ihx", "msg": "사내: 펌프 수 = IHX 수", "severity": "error"}]}, "테스트", "시험")
    QUEUE[:] = [{"kind": "plant", "archetype": "sfr_pool"}, {"kind": "plant", "archetype": "sfr_pool", "components": {"pump": {"count": 4}}}]
    p = pipeline.run("SFR", "assembly", emit=emit)
    r = p["revisions"][-1]
    ok("[사내] 펌프 수 = IHX 수" in SEEN[-2][1][0]["content"], "편집한 원칙이 LLM 프롬프트에 들어감")
    ok(r["attempts"] == 2 and r["status"] == "pass", "편집한 검증 규칙 위반(SFR-K1) → 수리 → 펌프 4대로 통과 (기기 수량 ↔ 설계 변수 일관)")
    ok(kb.get("sfr_pool")["history"][-1]["author"] == "테스트", "수정 이력 기록")
    kb.reset_override("sfr_pool")
    ok(not kb.get("sfr_pool").get("edited"), "원본 복원")
    # 이미지 선택 입력: 명령에 작업 단서가 있으면 명령 우선
    ok(pipeline.route("이 도면대로 3D 브래킷", False, has_img=True, img=b"x")[0] == "part3d", "이미지 + '3D 브래킷' 명령 → 3D 부품 (명령 우선)")

    # 8) UI 는 로컬 자산만 (폐쇄망)
    print("[폐쇄망]")
    ui = util.read(os.path.join(ROOT, "ui.html"))
    ok(not re.search(r'<(script|link)[^>]+(src|href)="https?://', ui), "ui.html 외부 CDN 참조 없음")
    ok(all(os.path.exists(os.path.join(ROOT, "static", f)) for f in ("three.min.js", "GLTFLoader.js", "OrbitControls.js", "fonts/NanumGothic.ttf")), "three.js·한글 글꼴 동봉")
    ok(len(pipeline.list_projects()) >= 6, "프로젝트 목록")
    print("selftest OK")
finally:
    shutil.rmtree(WS, ignore_errors=True)
