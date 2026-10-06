"""범용 원자력 설비 엔진 — 지식 DB 원형(kb/archetypes) + 설계 IR → 사이징 → 배치 → 형상 → 배관·계통 → 검증.

설계 IR (LLM 출력):
  {"kind":"plant","archetype":"sfr_pool","name":"…","design":{"power_mwth":600,"n_ihx":6},
   "components":{"pump":{"count":3}},          # 원형 기기의 수량 덮어쓰기 (count 0 이면 제거)
   "add":[{…원형과 같은 문법의 기기…}], "connections_add":[…], "principles_applied":["…"], "notes":["…"]}
  원형이 없으면 "archetype":"custom" + components 를 배치 문법으로 전부 쓴다.

출력 model 은 MSR 조립 모델과 같은 형식 (worker_asm·GA·계통도 공용).
"""
import copy
import math
import re

from . import detail, kb
from .util import num, safe_eval

PALETTE = [1, 5, 30, 3, 6, 140, 40, 200]


class PlantError(Exception):
    pass


def _pair(A, B, pos):
    """A·B 인스턴스 짝짓기: 같은 수면 1:1, 한쪽이 1개면 부채꼴, 둘 다 여러 개면 많은 쪽 각각을 가장 가까운 상대에."""
    if len(A) == len(B):
        return list(zip(A, B))
    if len(A) == 1 or len(B) == 1:
        return [(a, b) for a in A for b in B]
    near = lambda p, Q: min(Q, key=lambda q: math.hypot(pos(p)[0] - pos(q)[0], pos(p)[1] - pos(q)[1]))
    return [(a, near(a, B)) for a in A] if len(A) > len(B) else [(near(b, A), b) for b in B]


# ── 정규화 ────────────────────────────────────────────────────────────────
def normalize(ir):
    errs = []
    ir = copy.deepcopy(ir or {})
    ir["kind"] = "plant"
    aid = ir.get("archetype") or "custom"
    allk = kb.load_all()
    if aid != "custom" and aid not in allk:
        errs.append(f"archetype {aid!r} 가 지식 DB 에 없음 (가능: {', '.join(allk)} 또는 custom)")
        return ir, errs, None
    arch = copy.deepcopy(allk[aid]) if aid != "custom" else {"id": "custom", "name": ir.get("name", "사용자 정의 설비"), "design": {}, "components": [],
                                                              "connections": [], "loops": [], "principles": [], "checks": [], "coolant": ir.get("coolant", "none")}
    ir.setdefault("name", arch["name"])
    d = ir.get("design") or {}
    for k, v in list(d.items()):
        x = num(v)
        if x is None:
            errs.append(f"design.{k} 값이 숫자가 아님: {v!r}")
            continue
        d[k] = x
        rng = (arch.get("design", {}).get(k) or {}).get("range")
        if rng and not (rng[0] <= x <= rng[1]):
            ir.setdefault("warnings", []).append(f"design.{k}={x:g} 가 지식 DB 대표 범위 {rng} 밖 — 의도한 값인지 확인")
    ir["design"] = d
    comps = ir.get("components") or {}
    if isinstance(comps, list):  # LLM 이 목록으로 쓴 경우 → id/type 기준 수량 덮어쓰기
        comps = {c.get("id") or c.get("type"): c for c in comps if isinstance(c, dict)}
    ir["components"] = comps
    known = {c["id"] for c in arch.get("components", [])}
    for cid in comps:
        if cid not in known and arch.get("builder") != "msr":
            errs.append(f"components.{cid}: 원형 {aid} 에 없는 기기 (원형 기기: {', '.join(sorted(known)) or '-'}; 새 기기는 add 로)")
    lib = kb.components()
    for i, c in enumerate(ir.get("add") or []):
        if not c.get("id") or not c.get("type") or not c.get("place"):
            errs.append(f"add[{i}]: id·type·place 필요")
        elif c["type"] not in lib:
            errs.append(f"add[{i}]: type {c['type']!r} 없음 (가능: {', '.join(lib)})")
    if aid == "custom" and not ir.get("add"):
        errs.append("custom 은 add 에 기기를 배치 문법으로 써야 함")
    return ir, errs, arch


# ── 기하 도우미 ───────────────────────────────────────────────────────────
def _bbox(shapes, inner=False):
    xs, ys, zs = [], [], []
    for s in shapes:
        if inner and s.get("out"):
            continue
        p = s["p"]
        if p in ("cyl", "tube", "shell", "dome", "pool_cyl", "capsule", "slab"):
            x, y, z = s["c"]
            r = s["d"] / 2
            ax = s.get("axis", "z")
            if ax == "z":
                xs += [x - r, x + r]
                ys += [y - r, y + r]
                zs += [z, z + s["h"] + (s["d"] / 2 if p == "dome" else 0)]
            elif ax == "x":
                xs += [x, x + s["h"]]
                ys += [y - r, y + r]
                zs += [z - r, z + r]
            else:
                xs += [x - r, x + r]
                ys += [y, y + s["h"]]
                zs += [z - r, z + r]
        elif p == "hex":
            x, y, z = s["c"]
            r = s["af"] / math.sqrt(3)
            xs += [x - r, x + r]
            ys += [y - r, y + r]
            zs += [z, z + s["h"]]
        elif p == "rev":
            x, y, z = s["c"]
            r = max(q[0] for q in s["prof"])
            xs += [x - r, x + r]
            ys += [y - r, y + r]
            zs += [z + min(q[1] for q in s["prof"]), z + max(q[1] for q in s["prof"])]
        elif p in ("box", "pool_box"):
            xs += [s["min"][0], s["max"][0]]
            ys += [s["min"][1], s["max"][1]]
            zs += [s["min"][2], s["max"][2]]
        elif p == "seg":
            r = s["d"] / 2
            for q in (s["a"], s["b"]):
                xs += [q[0] - r, q[0] + r]
                ys += [q[1] - r, q[1] + r]
                zs += [q[2] - r, q[2] + r]
    if not xs:
        return [0, 0, 0, 0, 0, 0]
    return [round(v, 1) for v in (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))]


def _shapes(shape, x, y, z, S):
    """형상 템플릿 → 형상 기술자 목록. S: 평가된 size (D,H,W,L,t,motor_z,motor_D,axis)."""
    D, H = S.get("D", 0), S.get("H", 0)
    if shape in ("cyl", "slab"):
        return [{"p": "cyl", "c": [x, y, z], "d": D, "h": H, "axis": S.get("axis", "z")}]
    if shape == "tube":
        t = S.get("t") or D * 0.1
        return [{"p": "tube", "c": [x, y, z], "d": D, "id": max(D - 2 * t, 1), "h": H}]
    if shape == "capsule":
        hh = D * 0.25
        return [{"p": "cyl", "c": [x, y, z + hh], "d": D, "h": max(H - 2 * hh, 1)},
                {"p": "head", "c": [x, y, z + H - hh], "d": D, "h": hh, "up": True}, {"p": "head", "c": [x, y, z + hh], "d": D, "h": hh, "up": False}]
    if shape == "vessel":
        return [{"p": "shell", "c": [x, y, z], "d": D, "h": H, "t": S.get("t") or 50}]
    if shape == "dome":
        return [{"p": "dome", "c": [x, y, z], "d": D, "h": H, "t": S.get("t") or 1000}]
    if shape == "pool":
        if S.get("W"):
            W, L, t = S["W"], S["L"], S.get("t") or 300
            return [{"p": "pool_box", "min": [x - W / 2, y - L / 2, z], "max": [x + W / 2, y + L / 2, z + H], "t": t}]
        return [{"p": "pool_cyl", "c": [x, y, z], "d": D, "h": H, "t": S.get("t") or 300}]
    if shape == "room":
        W, L, t = S["W"], S["L"], S.get("t") or 300
        return [{"p": "pool_box", "min": [x - W / 2, y - L / 2, z], "max": [x + W / 2, y + L / 2, z + H], "t": t}]
    if shape == "box":
        W, L = S.get("W") or D, S.get("L") or D
        return [{"p": "box", "min": [x - W / 2, y - L / 2, z], "max": [x + W / 2, y + L / 2, z + H]}]
    if shape == "pump":
        mz = S.get("motor_z", z + H + 300)
        mD = S.get("motor_D", D * 1.2)
        top = z + H
        out = [{"p": "cyl", "c": [x, y, z], "d": D, "h": H}]
        if mz > top:
            out.append({"p": "cyl", "c": [x, y, top], "d": D * 0.25, "h": mz - top, "out": True})
        out.append({"p": "cyl", "c": [x, y, max(mz, top)], "d": mD, "h": mD * 1.4, "out": True})
        return out
    raise PlantError(f"형상 템플릿 {shape!r} 미지원")


def _radial(shapes, cx, cy):
    r = 0
    for s in shapes:
        if s.get("out"):
            continue
        if s["p"] in ("box", "pool_box"):
            for xx in (s["min"][0], s["max"][0]):
                for yy in (s["min"][1], s["max"][1]):
                    r = max(r, math.hypot(xx - cx, yy - cy))
        elif s["p"] == "hex":
            r = max(r, math.hypot(s["c"][0] - cx, s["c"][1] - cy) + s["af"] / math.sqrt(3))
        elif s["p"] == "rev":
            r = max(r, math.hypot(s["c"][0] - cx, s["c"][1] - cy) + max(q[0] for q in s["prof"]))
        elif "c" in s:
            r = max(r, math.hypot(s["c"][0] - cx, s["c"][1] - cy) + s["d"] / 2)
    return r


# ── 모델 생성 ─────────────────────────────────────────────────────────────
def build_model(ir, arch):
    lib = kb.components()
    cool = kb.coolants().get(arch.get("coolant") or "none", kb.coolants()["none"])
    N = {"pi": math.pi}
    for k, d in (arch.get("design") or {}).items():
        N[k] = d.get("v") if isinstance(d, dict) else d
    N.update(ir.get("design") or {})
    # 기기 수량 덮어쓰기가 '변수 하나'로 정해진 수량이면 그 설계 변수도 같이 바꾼다 (규칙·사이징과 일관)
    for spec in arch.get("components", []):
        o = (ir.get("components") or {}).get(spec["id"]) or {}
        cnt = str(spec.get("count", ""))
        if "count" in o and re.fullmatch(r"[a-z_][a-z0-9_]*", cnt) and cnt in N:
            N[cnt] = num(o["count"], N[cnt])
    N["rho"], N["cp"] = cool["rho"], cool["cp"]
    calc = {"coolant": {"name": cool["label"], "rho": cool["rho"], "cp": cool["cp"]}}
    if N.get("power_mwth") and N.get("power_density_mw_m3"):
        hd = N.get("core_hd", 1.0)
        N["core_V"] = N["power_mwth"] / N["power_density_mw_m3"]
        Dm = (4 * N["core_V"] / (math.pi * hd)) ** (1 / 3)
        N["core_D"], N["core_H"] = Dm * 1000, Dm * hd * 1000
        calc["core"] = {"V_m3": round(N["core_V"], 3), "D_mm": round(N["core_D"]), "H_mm": round(N["core_H"]), "식": "V = P/q''', D = (4V/(π·H/D))^(1/3)"}
    if N.get("power_mwth") and N.get("core_dT") and cool["cp"] > 1:
        N["m_dot"] = N["power_mwth"] * 1e6 / (cool["cp"] * N["core_dT"])
        N["Q_vol"] = N["m_dot"] / cool["rho"]
        N["pipe_d"] = math.sqrt(4 * N["Q_vol"] / (math.pi * N.get("pipe_v", 3))) * 1000
        calc["flow"] = {"m_kg_s": round(N["m_dot"], 1), "Q_m3_s": round(N["Q_vol"], 3), "pipe_d_mm": round(N["pipe_d"]),
                        "식": f"ṁ = P/(cp·ΔT), ΔT={N['core_dT']:g} K, 유속 {N.get('pipe_v', 3):g} m/s"}
    sz = {}
    for k, ex in (arch.get("sizing") or {}).items():
        try:
            N[k] = safe_eval(ex, N)
            sz[k] = round(N[k], 1)
        except Exception as e:
            raise PlantError(f"sizing.{k} = {ex!r} 계산 실패: {e}")
    if sz:
        calc["sizing"] = sz
    model = {"kind": "assembly", "domain": "plant", "archetype": arch["id"], "name": ir.get("name") or arch["name"], "design": {k: N[k] for k in (arch.get("design") or {})} | (ir.get("design") or {}),
             "calc": calc, "assumptions": list(ir.get("warnings") or []), "components": [], "flows": [], "notes": list(ir.get("notes") or []),
             "contains": {}, "principles": arch.get("principles", [])}
    over = ir.get("components") or {}
    specs = []
    for c in arch.get("components", []):
        c = copy.deepcopy(c)
        o = over.get(c["id"]) or {}
        if "count" in o:
            c["count"] = str(o["count"])
        if isinstance(o.get("size"), dict):
            c.setdefault("size", {}).update(o["size"])
        specs.append(c)
    specs += [copy.deepcopy(c) for c in ir.get("add") or []]
    inst_of = {}
    ctx = {}  # 기기 간 공유 (노심 격자 → 용기 CRDM 위치)
    for spec in specs:
        cid = spec["id"]
        try:
            n = int(round(safe_eval(spec.get("count", "1"), N)))
        except Exception as e:
            raise PlantError(f"{cid}.count 계산 실패: {e}")
        if n <= 0:
            continue
        T = lib.get(spec["type"]) or lib["generic"]
        S = {}
        for k, ex in (spec.get("size") or {}).items():
            if k == "axis":
                S[k] = ex
                continue
            try:
                S[k] = safe_eval(ex, N)
            except Exception as e:
                raise PlantError(f"{cid}.size.{k} = {ex!r} 계산 실패: {e}")
        pl = spec.get("place") or {"at": {"z": "0"}}
        kind, P = next(iter(pl.items()))
        ev = lambda ex, default=0.0: safe_eval(ex, N) if ex not in (None, "") else default
        pos = []
        try:
            if kind == "at":
                xy = P.get("xy") or [0, 0]
                pos = [(ev(xy[0]), ev(xy[1]), ev(P.get("z", "0")), 0)] * n
            elif kind == "ring":
                r, z0, a0 = ev(P["r"]), ev(P.get("z", "0")), ev(P.get("a0"), 0)
                cx, cy = ev(P.get("cx"), 0), ev(P.get("cy"), 0)
                pos = [(cx + r * math.cos(math.radians(a0 + 360 * k / n)), cy + r * math.sin(math.radians(a0 + 360 * k / n)), z0, a0 + 360 * k / n) for k in range(n)]
            elif kind == "grid":
                nx, ny, pitch, z0 = int(ev(P["nx"])), int(ev(P["ny"])), ev(P["pitch"]), ev(P.get("z", "0"))
                cx, cy = ev(P.get("cx"), 0), ev(P.get("cy"), 0)
                pos = [(cx + (k % nx - (nx - 1) / 2) * pitch, cy + (k // nx - (ny - 1) / 2) * pitch, z0, 0) for k in range(min(n, nx * ny))]
            elif kind == "line":
                pitch, z0 = ev(P["pitch"]), ev(P.get("z", "0"))
                cx, cy = ev(P.get("cx"), 0), ev(P.get("cy"), 0)
                per = int(ev(P.get("per"), 1)) or 1
                off = ev(P.get("offsets"), 0)
                m = math.ceil(n / per)
                for k in range(n):
                    i, j = k // per, k % per
                    o = (j - (per - 1) / 2) * 2 * off if per > 1 else 0
                    t = (i - (m - 1) / 2) * pitch + o
                    pos.append((cx + t, cy, z0, 0) if P.get("dir", "x") == "x" else (cx, cy + t, z0, 0))
            elif kind == "beside":
                of = P["of"]
                d = P.get("dir", "+x")
                gap = ev(P.get("gap"), 1000)
                r0 = N[f"{of}_R"]
                rr = (S.get("D") or S.get("W") or 0) / 2
                sgn = 1 if d[0] == "+" else -1
                ox, oy = N[f"{of}_x"], N[f"{of}_y"]
                dist = r0 + gap + rr
                pos = [((ox + sgn * dist) if d[1] == "x" else ox, (oy + sgn * dist) if d[1] == "y" else oy, ev(P.get("z", "0")), 0)] * n
            elif kind == "contain":
                ids = P["ids"]
                bbs = [i for x in ids for i in inst_of.get(x, [])]
                if not bbs:
                    raise PlantError(f"{cid}: contain 대상 {ids} 가 아직 배치되지 않음 (목록 순서 확인)")
                bb = [min(b["bbox_in"][0] for b in bbs), min(b["bbox_in"][1] for b in bbs), min(b["bbox_in"][2] for b in bbs),
                      max(b["bbox_in"][3] for b in bbs), max(b["bbox_in"][4] for b in bbs), max(b["bbox_in"][5] for b in bbs)]
                pad, pt, pb = ev(P.get("pad"), 300), ev(P.get("pad_top"), ev(P.get("pad"), 300)), ev(P.get("pad_bot"), ev(P.get("pad"), 300))
                cx, cy = (bb[0] + bb[3]) / 2, (bb[1] + bb[4]) / 2
                if T["shape"] in ("room", "pool") and (spec.get("size") or {}).get("W") is None and T["shape"] == "room":
                    S["W"], S["L"] = bb[3] - bb[0] + 2 * pad, bb[4] - bb[1] + 2 * pad
                else:
                    R = max(_radial(b["shapes"], cx, cy) for b in bbs)
                    S["D"] = 2 * (R + pad) + 2 * (S.get("t") or 0)
                S["H"] = bb[5] - bb[2] + pt + pb
                pos = [(cx, cy, bb[2] - pb, 0)]
                model["contains"][cid] = ids
            else:
                raise PlantError(f"{cid}: 배치 {kind!r} 미지원 (at|ring|grid|line|beside|contain)")
        except KeyError as e:
            raise PlantError(f"{cid}: 배치 수식이 아직 정의되지 않은 변수 {e} 를 참조 (기기 순서 확인)")
        except PlantError:
            raise
        except Exception as e:
            raise PlantError(f"{cid}: 배치 계산 실패 {type(e).__name__}: {e}")
        st = dict(arch.get("style") or {})
        for k2, v2 in (spec.get("detail") or {}).items():  # 기기별 상세 옵션 (수식은 평가)
            if k2 == "nozzles":
                st[k2] = [{**nz, "z": safe_eval(nz["z"], N), "d": safe_eval(nz["d"], N)} for nz in v2]
            elif isinstance(v2, str) and not v2.isalpha():
                try:
                    st[k2] = safe_eval(v2, N)
                except Exception:
                    st[k2] = v2
            else:
                st[k2] = v2
        insts = []
        for k, (x, y, z, a) in enumerate(pos):
            fn = detail.DETAIL.get(spec["type"]) if (ir.get("detail", True) and arch.get("detail", True)) else None
            try:
                shapes = fn(x, y, z, S, st, ctx) if fn and (S.get("D") or S.get("W")) and S.get("H") else _shapes(T["shape"], x, y, z, S)
            except Exception as e:
                raise PlantError(f"{cid}: 상세 형상 실패 {type(e).__name__}: {e}")
            label = spec.get("label") or T["label"]
            c = {"id": f"{cid.upper()}-{k + 1:02d}" if n > 1 else cid.upper(), "key": cid, "type": spec["type"], "label": label + (f" {k + 1}" if n > 1 else ""),
                 "shapes": shapes, "bbox": _bbox(shapes), "bbox_in": _bbox(shapes, inner=True), "rgb": T["rgb"], "alpha": T.get("alpha", 1.0),
                 "material": spec.get("material", ""), "role": T["role"], "layer": T["layer"], "color": T["color"], "angle": a, "xy": [x, y]}
            insts.append(c)
            model["components"].append(c)
        inst_of[cid] = insts
        b = insts[0]["bbox"]
        bi = [min(i["bbox_in"][k] for i in insts) for k in range(3)] + [max(i["bbox_in"][k] for i in insts) for k in range(3, 6)]
        N[f"{cid}_top"], N[f"{cid}_bot"] = bi[5], bi[2]
        N[f"{cid}_x"], N[f"{cid}_y"] = insts[0]["xy"]
        N[f"{cid}_D"] = S.get("D") or S.get("W") or (b[3] - b[0])
        N[f"{cid}_H"] = S.get("H") or (b[5] - b[2])
        N[f"{cid}_W"], N[f"{cid}_L"] = S.get("W") or N[f"{cid}_D"], S.get("L") or N[f"{cid}_D"]
        N[f"{cid}_R"] = N[f"{cid}_D"] / 2 if len(insts) == 1 else _radial([s for i in insts for s in i["shapes"]], 0, 0)
    model["_N"] = N
    model["_inst"] = {k: [c["id"] for c in v] for k, v in inst_of.items()}
    # 배관
    outR = max([_radial(c["shapes"], 0, 0) for c in model["components"] if c["role"] == "container"] or [0]) + 1000
    pn = 0
    for con in (arch.get("connections") or []) + (ir.get("connections_add") or []):
        A = inst_of.get(con["from"], [])
        B = inst_of.get(con["to"], []) if con["to"] != "outside" else []
        if not A or (con["to"] != "outside" and not B):
            continue
        try:
            z = safe_eval(con.get("z", "0"), N)
            d = safe_eval(con.get("d", "200"), N)
        except Exception as e:
            raise PlantError(f"connection {con['from']}→{con['to']} 수식 실패: {e}")
        pairs = _pair(A, B, lambda c: c["xy"]) if B else []
        if con["to"] == "outside":
            pairs = [(a, None) for a in A]
        for a, b in pairs:
            ax, ay = a["xy"]
            ra = (a["bbox_in"][3] - a["bbox_in"][0]) / 2
            if b is None:
                if con.get("vertical_exit"):
                    segs = [([ax, ay, a["bbox_in"][5]], [ax, ay, z])]
                else:
                    ang = math.atan2(ay, ax) if (ax or ay) else 0
                    segs = [([ax + math.cos(ang) * ra, ay + math.sin(ang) * ra, z], [math.cos(ang) * outR, math.sin(ang) * outR, z])]
            else:
                bx, by = b["xy"]
                rb = (b["bbox_in"][3] - b["bbox_in"][0]) / 2
                L = math.hypot(bx - ax, by - ay) or 1
                ux, uy = (bx - ax) / L, (by - ay) / L
                if L < 1:  # 같은 축 (상하)
                    segs = [([ax, ay, z], [bx, by, b["bbox_in"][2]])]
                else:
                    sa = [ax + ux * min(ra, L / 2), ay + uy * min(ra, L / 2), z]
                    sb = [bx - ux * min(rb, L / 2), by - uy * min(rb, L / 2), z]
                    segs = [(sa, sb)]
            for p0, p1 in segs:
                pn += 1
                sh = [{"p": "seg", "a": p0, "b": p1, "d": d}]
                model["components"].append({"id": f"PIPE-{pn:02d}", "key": "pipe", "type": "pipe", "label": con.get("label") or "배관", "loop": con.get("loop", ""),
                                            "shapes": sh, "bbox": _bbox(sh), "bbox_in": _bbox(sh), "rgb": [150, 150, 150], "alpha": 1.0, "material": "",
                                            "role": "pipe", "layer": "NUC-PIPE", "color": 9, "xy": [p0[0], p0[1]]})
    # 계통 흐름
    loops = arch.get("loops") or []
    model["loops"] = [{"key": l["key"], "name": l["name"], "color": PALETTE[i % len(PALETTE)]} for i, l in enumerate(loops)]
    for l in loops:
        path = l["path"]
        for a_id, b_id in zip(path, path[1:]):
            A = model["_inst"].get(a_id) or ([a_id] if not re.fullmatch(r"[a-z_0-9]+", a_id) else [])
            B = model["_inst"].get(b_id) or ([b_id] if not re.fullmatch(r"[a-z_0-9]+", b_id) else [])
            if not A or not B:
                continue
            byid = {c["id"]: c for c in model["components"]}
            pairs = _pair(A, B, lambda i: byid[i]["xy"] if i in byid else (0, 0))
            for a, b in pairs:
                model["flows"].append({"from": a, "to": b, "loop": l["key"]})
    eq = [c for c in model["components"] if c["role"] not in ("pipe",)]
    model["extent"] = [min(c["bbox"][k] for c in eq) for k in range(3)] + [max(c["bbox"][k] for c in eq) for k in range(3, 6)]
    model["ga_notes"] = ga_notes(model, N, arch)
    model["allow_overlap"] = arch.get("allow_overlap", [])
    return model


def ga_notes(model, N, arch):
    c = model["calc"]
    L = []
    if N.get("power_mwth"):
        L.append(f"열출력 {N['power_mwth']:g} MWth · 냉각재 {c['coolant']['name']}" + (f" · ΔT {N['core_dT']:g} K" if N.get("core_dT") else ""))
    if "core" in c:
        L.append(f"노심 Ø{c['core']['D_mm']} × H{c['core']['H_mm']} mm, 출력밀도 {N.get('power_density_mw_m3', 0):g} MW/m³")
    if "flow" in c:
        L.append(f"유량 {c['flow']['m_kg_s']} kg/s, 대표 배관 Ø{c['flow']['pipe_d_mm']} mm")
    cnt = {}
    for x in model["components"]:
        if x["role"] != "pipe":
            cnt[x["label"].rsplit(" ", 1)[0] if re.search(r" \d+$", x["label"]) else x["label"]] = cnt.get(x["label"].rsplit(" ", 1)[0] if re.search(r" \d+$", x["label"]) else x["label"], 0) + 1
    L.append("구성: " + ", ".join(f"{k} {v}" for k, v in cnt.items()))
    L.append(f"원형: {arch['name']} (지식 DB) — 개념 초안, 대표값 기반, 실제 설계값 아님")
    return L


# ── 검증 ──────────────────────────────────────────────────────────────────
def _c(b):
    return [(b[0] + b[3]) / 2, (b[1] + b[4]) / 2, (b[2] + b[5]) / 2]


def _inside(a, b, tol=5.0):
    return all(a[k] >= b[k] - tol for k in range(3)) and all(a[k] <= b[k] + tol for k in range(3, 6))


def eval_relation(r, types, extent, N=None):
    """관계 하나 평가 → (ok, detail). types: {type: [component...]} (배관 제외)."""
    A = types.get(r["a"], [])
    B = types.get(r.get("b"), []) if r.get("b") else []
    rel = r["rel"]
    if not A or (r.get("b") and not B):
        return False, "대상 없음"
    if rel in ("above", "below"):
        za = [_c(a["bbox"])[2] for a in A]
        zb = [_c(b["bbox"])[2] for b in B]
        ok = min(za) > max(zb) if rel == "above" else max(za) < min(zb)
        return ok, f"z {min(za):.0f}~{max(za):.0f} vs {min(zb):.0f}~{max(zb):.0f}"
    if rel == "coaxial":
        a, b = _c(A[0]["bbox"]), _c(B[0]["bbox"])
        w = max(B[0]["bbox"][3] - B[0]["bbox"][0], 1)
        dd = math.hypot(a[0] - b[0], a[1] - b[1])
        return dd < 0.1 * w, f"축 거리 {dd:.0f} mm"
    if rel == "symmetric":
        cs = [_c(a["bbox"]) for a in A]
        m = [sum(c[0] for c in cs) / len(cs), sum(c[1] for c in cs) / len(cs)]
        ref = _c(B[0]["bbox"]) if B else [0, 0]
        ext = max(extent[3] - extent[0], 1)
        dd = math.hypot(m[0] - ref[0], m[1] - ref[1])
        return len(A) >= 2 and dd < 0.05 * ext, f"{len(A)}개, 무게중심 편차 {dd:.0f} mm"
    if rel == "same_azimuth":
        ang = lambda c: math.degrees(math.atan2(_c(c["bbox"])[1], _c(c["bbox"])[0]))
        ok = all(any(abs((ang(a) - ang(b) + 180) % 360 - 180) < 10 for b in B) for a in A)
        return ok, "방위각 일치" if ok else "방위각 불일치"
    if rel == "inside":
        ok = all(_inside(a.get("bbox_in", a["bbox"]), B[0]["bbox"]) for a in A)
        return ok, "포함" if ok else "일부가 밖"
    if rel == "beside":
        a, b = A[0]["bbox"], B[0]["bbox"]
        sep = a[0] > b[3] or a[3] < b[0] or a[1] > b[4] or a[4] < b[1]
        zov = a[2] < b[5] and b[2] < a[5]
        return sep and zov, "옆" if sep and zov else "겹치거나 높이가 다름"
    if rel == "grid":
        n = [int(safe_eval(str(v), N or {})) for v in r["n"]]
        xs = sorted({round(_c(a["bbox"])[0]) for a in A})
        ys = sorted({round(_c(a["bbox"])[1]) for a in A})
        return [len(xs), len(ys)] in (n, n[::-1]), f"{len(xs)}×{len(ys)}"
    return False, f"관계 {rel!r} 미지원"


def validate(model, arch):
    out = []
    types = {}
    for c in model["components"]:
        if c["role"] != "pipe":
            types.setdefault(c["type"], []).append(c)
    N = model["_N"]
    for i, ck in enumerate(arch.get("checks") or []):
        if "expr" in ck:
            try:
                ok = bool(safe_eval(ck["expr"], N))
            except Exception as e:
                ok, ck = False, {**ck, "msg": f"검증식 평가 실패 {ck['expr']!r}: {e}", "severity": "warning"}
            if not ok:
                out.append({"id": ck.get("id", f"{arch['id'].upper()}-{i + 1:03d}"), "name": "설계 원칙", "severity": ck.get("severity", "warning"),
                            "msg": ck["msg"], "repairable": ck.get("severity") == "error"})
        else:
            ok, why = eval_relation(ck, types, model["extent"], N)
            if not ok:
                out.append({"id": ck.get("id", f"REL-{i + 1:03d}"), "name": "배치 원칙", "severity": ck.get("severity", "error"),
                            "msg": f"{ck['rel']}({ck['a']}{', ' + ck['b'] if ck.get('b') else ''}) 불만족 — {why}", "repairable": True})
    return out


def strip(model):
    model.pop("_N", None)
    model.pop("_inst", None)
    for c in model["components"]:
        c.pop("bbox_in", None)
    return model
