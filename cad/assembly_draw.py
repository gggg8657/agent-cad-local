"""조립 모델 → 계통도(flow diagram) DXF + 부품표 CSV. OCC 불필요 (메인 프로세스)."""
import re

from ezdxf import bbox as ezb

from . import dxfkit
from .assembly import COMP

LOOP = {"fuel": ("MSR-FUEL", 1, "연료염 1차 루프"), "drain": ("MSR-DRAINL", 30, "비상 배수"), "offgas": ("MSR-OFFGAS", 3, "오프가스(He)"),
        "secondary": ("MSR-SEC", 5, "2차 계통")}
ROWS = [["he_pump"], ["primary_pump"], ["heat_exchanger"], ["riser"], ["core"], ["freeze_plug"], ["dump_tank"]]


def flow_dxf(model, path, rev="R00"):
    doc = dxfkit.new_doc()
    msp = doc.modelspace()
    for k, (lay, col, _) in LOOP.items():
        doc.layers.add(lay, color=col, linetype="DASHED" if k == "drain" else "CONTINUOUS")
    C = {c["id"]: c for c in model["components"] if c["type"] not in ("pipe", "shield_cell", "reflector")}
    bw, bh, dx, dy = 40, 14, 62, 30
    pos = {}
    y = 0
    for row in ROWS:
        ids = [cid for cid, c in C.items() if c["type"] in row]
        if not ids:
            continue
        ids.sort(key=(lambda i: i) if row == ["dump_tank"] else (lambda i: C[i]["bbox"][0] + C[i]["bbox"][1] * 0.01))
        for k, cid in enumerate(ids):
            x = (k - (len(ids) - 1) / 2) * dx
            pos[cid] = (x, y)
        y -= dy
    vessel = next((c for c in C.values() if c["type"] == "reactor_vessel"), None)
    # 용기 경계 (점선 박스): 펌프~노심
    inner = [pos[i] for i in pos if C[i]["type"] in ("primary_pump", "heat_exchanger", "riser", "core")]
    if vessel and inner:
        x0 = min(p[0] for p in inner) - bw / 2 - 14
        x1 = max(p[0] for p in inner) + bw / 2 + 14
        y1 = max(p[1] for p in inner) + bh / 2 + 8
        y0 = min(p[1] for p in inner) - bh / 2 - 8
        msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True, dxfattribs={"layer": "MSR-VESSEL", "linetype": "DASHED"})
        dxfkit.text(msp, "원자로 용기 (일체형 1차 계통)", (x0 + 2, y1 - 2), 2.6, "ICT-TEXT", "TOP_LEFT")
        pos[vessel["id"]] = ((x0 + x1) / 2, y1)
    for cid, (x, yy) in pos.items():
        c = C[cid]
        if c["type"] == "reactor_vessel":
            continue
        dxfkit.rect(msp, x - bw / 2, yy - bh / 2, bw, bh, COMP[c["type"]]["layer"])
        dxfkit.text(msp, c["label"], (x, yy + 2), 3, "ICT-TEXT")
        dxfkit.text(msp, cid, (x, yy - 3.5), 1.9, "ICT-TEXT")
    # 2차 계통 (외부)
    hx = [i for i in pos if C[i]["type"] == "heat_exchanger"]
    if hx:
        sx = max(p[0] for p in pos.values()) + bw + 40
        sy = pos[hx[0]][1]
        dxfkit.rect(msp, sx - bw / 2, sy - bh / 2, bw, bh, "MSR-SEC")
        dxfkit.text(msp, "2차 계통", (sx, sy + 2), 3, "ICT-TEXT")
        dxfkit.text(msp, "(중간 열교환·발전)", (sx, sy - 3.5), 1.9, "ICT-TEXT")
        pos["SECONDARY"] = (sx, sy)
    lane = {}
    for f in model["flows"]:
        a, b = pos.get(f["from"]), pos.get(f["to"])
        if not a or not b:
            continue
        lay = LOOP[f["loop"]][0]
        ta, tb = (C.get(f["from"]) or {}).get("type"), (C.get(f["to"]) or {}).get("type")
        if f["to"] == "SECONDARY":  # 열교환기 위쪽 차선으로 빠져 2차 계통 박스로 (다른 박스 관통 방지)
            k = lane.setdefault("sec", 0)
            lane["sec"] += 1
            yy = a[1] + bh / 2 + 3 + k * 2.5
            msp.add_lwpolyline([(a[0] + bw / 4, a[1] + bh / 2), (a[0] + bw / 4, yy), (b[0] - bw / 2 - 6 - k * 2, yy),
                                (b[0] - bw / 2 - 6 - k * 2, b[1]), (b[0] - bw / 2, b[1])], dxfattribs={"layer": lay})
        elif ta == "heat_exchanger" and tb == "core":  # 하강관: 바깥쪽으로 돌아 노심 옆구리로
            side = 1 if a[0] >= 0 else -1
            xo = a[0] + side * (bw / 2 + 6)
            msp.add_lwpolyline([(a[0] + side * bw / 2, a[1] - 3), (xo, a[1] - 3), (xo, b[1]), (b[0] + side * bw / 2, b[1])], dxfattribs={"layer": lay})
            _arrow(msp, (b[0] + side * bw / 2, b[1]), -side, lay)
        elif ta == "riser" and tb == "primary_pump":  # 상승관 → 중앙 위로 → 펌프 안쪽 옆구리 (열교환기 박스 회피)
            side = 1 if b[0] >= a[0] else -1
            msp.add_lwpolyline([(a[0], a[1] + bh / 2), (a[0], b[1]), (b[0] - side * bw / 2, b[1])], dxfattribs={"layer": lay})
            _arrow(msp, (b[0] - side * bw / 2, b[1]), side, lay)
        elif ta == "he_pump":
            msp.add_lwpolyline([(a[0], a[1] - bh / 2), (b[0], b[1])], dxfattribs={"layer": lay})
        else:
            down = a[1] > b[1]
            p0 = (a[0], a[1] - bh / 2) if down else (a[0], a[1] + bh / 2)
            p1 = (b[0], b[1] + bh / 2) if down else (b[0], b[1] - bh / 2)
            ym = (p0[1] + p1[1]) / 2
            msp.add_lwpolyline([p0, (p0[0], ym), (p1[0], ym), p1], dxfattribs={"layer": lay})
            _arrow(msp, p1, 0, lay, up=not down)
    # 범례 + 사이징 요약
    lx = max(p[0] for p in pos.values()) + bw + 30
    ly = 10
    dxfkit.text(msp, "범례", (lx, ly), 3.5, "ICT-TEXT", "TOP_LEFT")
    for i, (k, (lay, _, lab)) in enumerate(LOOP.items()):
        yy = ly - 8 - i * 6
        msp.add_line((lx, yy), (lx + 10, yy), dxfattribs={"layer": lay})
        dxfkit.text(msp, lab, (lx + 13, yy), 2.6, "ICT-TEXT", "MIDDLE_LEFT")
    c = model["calc"]
    lines = [f"P = {model['design']['power_mwth']:g} MWth, {c['salt']['name']}", f"ṁ = {c['flow']['m_kg_s']} kg/s (ΔT {model['design']['core_dT']:g} K)",
             f"연료염 {c['salt']['inventory_m3']} m³"]
    if "drain" in c:
        lines.append(f"덤프 용량 {c['drain']['capacity_m3']} m³")
    for i, t in enumerate(lines):
        dxfkit.text(msp, t, (lx, ly - 40 - i * 5), 2.4, "ICT-TEXT", "TOP_LEFT")
    e = ezb.extents(msp)
    dxfkit.frame(msp, ((e.extmin.x, e.extmin.y), (e.extmax.x, e.extmax.y)), model["name"], "MSR 계통도 (연료염·배수·오프가스·2차)", scale=1, paper="A3", rev=rev)
    doc.saveas(path)
    return doc


def _arrow(msp, p, dirx, layer, up=False):
    """p 에 화살촉. dirx=+1 오른쪽으로 향함, -1 왼쪽, 0 이면 세로(up=True 위로)."""
    x, y = p
    s = 2.2
    if dirx:
        pts = [(x, y), (x - dirx * s * 1.6, y + s * 0.6), (x - dirx * s * 1.6, y - s * 0.6)]
    else:
        d = -1 if up else 1
        pts = [(x, y), (x - s * 0.6, y + d * s * 1.6), (x + s * 0.6, y + d * s * 1.6)]
    msp.add_solid(pts, dxfattribs={"layer": layer})


def bom_rows(model):
    rows = [["ID", "기기", "재질", "폭X(mm)", "폭Y(mm)", "높이(mm)", "중심 Z(mm)", "비고"]]
    for c in model["components"]:
        b = c["bbox"]
        rows.append([c["id"], c["label"], c["material"], round(b[3] - b[0]), round(b[4] - b[1]), round(b[5] - b[2]), round((b[2] + b[5]) / 2),
                     c.get("loop", "")])
    return rows


def flow_dxf_generic(model, path, rev="R00"):
    """지식 DB 원형 모델용 계통도 (공정 흐름도 관례): 루프마다 한 줄, 경로 순서대로 좌→우, 순환 복귀는 아래로.
    같은 기기 여러 대는 '펌프 ×2' 한 상자. 여러 루프가 공유하는 기기는 처음 나온 자리 그대로."""
    doc = dxfkit.new_doc()
    msp = doc.modelspace()
    loops = model.get("loops", [])
    for l in loops:
        doc.layers.add("FLOW-" + l["key"].upper(), color=l["color"])
    comps = [c for c in model["components"] if c["role"] != "pipe"]
    bykey = {}
    for c in comps:
        bykey.setdefault(c.get("key") or c["type"], []).append(c)
    id2key = {c["id"]: c.get("key") or c["type"] for c in comps}
    # 루프 경로 (기기 key 수준) 복원
    paths = {}
    for f in model["flows"]:
        a, b = id2key.get(f["from"], f["from"]), id2key.get(f["to"], f["to"])
        seq = paths.setdefault(f["loop"], [])
        if not seq:
            seq += [a, b]
        elif seq[-1] == a and (b not in seq or b == seq[0]):
            seq.append(b)
        elif b not in seq and a in seq:
            seq.insert(seq.index(a) + 1, b)
    bw, bh, dx, dy = 40, 14, 62, 42
    pos = {}
    row = 0
    for l in loops:
        seq = paths.get(l["key"], [])
        if not seq:
            continue
        new = [n for n in dict.fromkeys(seq) if n not in pos]
        if new:
            x0 = 0
            anchor = next((pos[n] for n in seq if n in pos), None)
            if anchor:
                x0 = anchor[0] + dx
            for i, n in enumerate(new):
                pos[n] = (x0 + i * dx, -row * dy)
            row += 1
    for n, (x, y) in pos.items():
        cs = bykey.get(n)
        lay = cs[0].get("layer", "0") if cs else "ICT-TEXT"
        dxfkit.rect(msp, x - bw / 2, y - bh / 2, bw, bh, lay)
        if cs:
            lab = re.sub(r" \d+$", "", cs[0]["label"]) + (f" ×{len(cs)}" if len(cs) > 1 else "")
            dxfkit.text(msp, lab, (x, y + 2), 3, "ICT-TEXT")
            dxfkit.text(msp, cs[0]["id"] if len(cs) == 1 else f"{cs[0]['id']} ~ {cs[-1]['id']}", (x, y - 3.5), 1.9, "ICT-TEXT")
        else:
            dxfkit.text(msp, n.replace("_", " "), (x, y + 1), 2.8, "ICT-TEXT")
            dxfkit.text(msp, "(외부 계통)", (x, y - 3.5), 1.9, "ICT-TEXT")
    for li, l in enumerate(loops):
        seq = paths.get(l["key"], [])
        lay = "FLOW-" + l["key"].upper()
        for k, (a, b) in enumerate(zip(seq, seq[1:])):
            if a not in pos or b not in pos:
                continue
            (xa, ya), (xb, yb) = pos[a], pos[b]
            if abs(ya - yb) < 1 and xb > xa:  # 같은 줄 앞으로
                pts = [(xa + bw / 2, ya), (xb - bw / 2, yb)]
                _arrow(msp, pts[-1], 1, lay)
            elif abs(ya - yb) < 1:  # 같은 줄 뒤로 (순환 복귀) → 아래로 돌아감
                yv = ya - bh / 2 - 6 - li * 2
                pts = [(xa, ya - bh / 2), (xa, yv), (xb, yv), (xb, yb - bh / 2)]
                _arrow(msp, pts[-1], 0, lay, up=True)
            else:
                xm = xb - bw / 2 - 6 if xb > xa else xb + bw / 2 + 6
                pts = [(xa + (bw / 2 if xb > xa else -bw / 2), ya), (xm, ya), (xm, yb), (xb + (-bw / 2 if xb > xa else bw / 2), yb)]
                _arrow(msp, pts[-1], 1 if xb > xa else -1, lay)
            msp.add_lwpolyline(pts, dxfattribs={"layer": lay})
    xs = [p[0] for p in pos.values()] or [0]
    lx = max(xs) + bw + 20
    dxfkit.text(msp, "계통 (루프)", (lx, 10), 3.5, "ICT-TEXT", "TOP_LEFT")
    for i, l in enumerate(loops):
        msp.add_line((lx, 2 - i * 6), (lx + 10, 2 - i * 6), dxfattribs={"layer": "FLOW-" + l["key"].upper()})
        dxfkit.text(msp, l["name"], (lx + 13, 2 - i * 6), 2.6, "ICT-TEXT", "MIDDLE_LEFT")
    for i, t in enumerate(model.get("ga_notes", [])[:5]):
        dxfkit.text(msp, t, (lx, -20 - i * 5), 2.2, "ICT-TEXT", "TOP_LEFT")
    e = ezb.extents(msp)
    dxfkit.frame(msp, ((e.extmin.x, e.extmin.y), (e.extmax.x, e.extmax.y)), model["name"], "계통도 (지식 DB 원형 루프)", scale=1, paper="A3", rev=rev)
    doc.saveas(path)
    return doc
