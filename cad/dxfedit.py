"""EDIT_EXISTING_DRAWING / DRAWING_ANALYSIS — 기존 DXF → Semantic Index → (LLM) Typed Operation → 검증 → save-as.

원본은 절대 덮어쓰지 않는다. 보호 레이어(standards protected, 기본 A-*) 엔티티는 수정·삭제 불가.
LLM에는 DXF 원문이 아니라 요약 인덱스(레이어·블록·존·장비·텍스트)만 들어간다 → 대형 도면도 context 폭발 없음.
"""
import fnmatch
import math
import re

import ezdxf
from ezdxf import bbox as ezb
from shapely.geometry import Point, Polygon

from . import dxfkit
from .ict import SYM, TYPE_ALIAS
from .util import num

OPS = ("add_equipment", "move", "delete", "rotate", "set_attr", "add_text", "connect")


def protected(layer, std=dxfkit.STD):
    return any(fnmatch.fnmatchcase(layer.upper(), p.upper()) for p in std.get("protected", []))


def _attribs(ins):
    try:
        return {a.dxf.tag: a.dxf.text for a in ins.attribs}
    except Exception:
        return {}


def _type_of(ins):
    a = _attribs(ins)
    if a.get("TYPE") in SYM:
        return a["TYPE"]
    n = ins.dxf.name.upper()
    if n.startswith("ICT_") and n[4:].lower() in SYM:
        return n[4:].lower()
    for k in SYM:
        if k.upper() in n:
            return k
    for k, v in TYPE_ALIAS.items():
        if k.upper() in n:
            return v
    return None


# ── 인덱스 ────────────────────────────────────────────────────────────────
def index(doc, max_items=400):
    msp = doc.modelspace()
    layers = {}
    for e in msp:
        l = e.dxf.get("layer", "0")
        layers[l] = layers.get(l, 0) + 1
    texts, inserts, polys = [], [], []
    for e in msp:
        t = e.dxftype()
        if t in ("TEXT", "MTEXT"):
            s = e.plain_text() if t == "MTEXT" else e.dxf.text
            p = e.dxf.insert
            if s and s.strip():
                texts.append({"h": e.dxf.handle, "text": s.strip()[:80], "pos": [round(p[0], 1), round(p[1], 1)], "layer": e.dxf.layer})
        elif t == "INSERT":
            p = e.dxf.insert
            a = _attribs(e)
            inserts.append({"h": e.dxf.handle, "block": e.dxf.name, "layer": e.dxf.layer, "pos": [round(p[0], 1), round(p[1], 1)],
                            "rot": round(e.dxf.get("rotation", 0), 1), "id": a.get("ID") or a.get("TAG") or "", "type": _type_of(e),
                            "scale": round(e.dxf.get("xscale", 1), 3), "attribs": {k: v for k, v in a.items() if k not in ("TYPE",)}})
        elif t == "LWPOLYLINE" and e.closed:
            pts = [(x, y) for x, y in e.get_points("xy")]
            if len(pts) >= 3:
                polys.append((e, pts))
        elif t == "POLYLINE" and e.is_closed:
            pts = [(v.dxf.location[0], v.dxf.location[1]) for v in e.vertices]
            if len(pts) >= 3:
                polys.append((e, pts))
    ext = ezb.extents(msp, fast=True)
    (x0, y0), (x1, y1) = ((ext.extmin.x, ext.extmin.y), (ext.extmax.x, ext.extmax.y)) if ext.has_data else ((0, 0), (0, 0))
    big = max(x1 - x0, y1 - y0, 1)
    zones = []
    for e, pts in polys:
        try:
            g = Polygon(pts)
        except Exception:
            continue
        if not g.is_valid or g.area < (big * 0.01) ** 2:
            continue
        inside = [t for t in texts if g.contains(Point(t["pos"]))]
        # 가장 작은 둘러싼 폴리곤의 텍스트만 라벨로 (중첩 존 처리)
        bx0, by0, bx1, by1 = g.bounds
        zones.append({"h": e.dxf.handle, "layer": e.dxf.layer, "bbox": [round(bx0, 1), round(by0, 1), round(bx1, 1), round(by1, 1)],
                      "area_m2": round(g.area / 1e6, 2), "_g": g, "_texts": inside})
    zones.sort(key=lambda z: z["_g"].area)
    for z in zones:
        own = [t for t in z["_texts"] if not any(o is not z and o["_g"].area < z["_g"].area and o["_g"].contains(Point(t["pos"])) for o in zones)]
        own.sort(key=lambda t: -1 * (t["layer"].upper().startswith("A-")))
        z["label"] = " ".join(t["text"] for t in own[:2]) or ""
    for z in zones:
        z.pop("_texts")
    equipment = [i for i in inserts if i["type"] or i["id"]]
    return {"extents": [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)], "units": doc.units,
            "layers": [{"name": k, "count": v, "protected": protected(k)} for k, v in sorted(layers.items(), key=lambda kv: -kv[1])],
            "blocks": _count([i["block"] for i in inserts]), "zones": zones, "equipment": equipment, "texts": texts[:max_items],
            "n_inserts": len(inserts), "n_texts": len(texts), "n_entities": sum(layers.values())}


def _count(xs):
    d = {}
    for x in xs:
        d[x] = d.get(x, 0) + 1
    return [{"name": k, "count": v} for k, v in sorted(d.items(), key=lambda kv: -kv[1])]


def summary_for_llm(ix, max_text=120, max_eq=200):
    """LLM 프롬프트용 압축 요약 (원문 대신)."""
    z = [{"zone": i, "label": q["label"], "bbox": q["bbox"], "area_m2": q["area_m2"]} for i, q in enumerate(ix["zones"]) if q["label"]][:60]
    eq = [{"id": e["id"], "type": e["type"], "block": e["block"], "pos": e["pos"], "layer": e["layer"]} for e in ix["equipment"]][:max_eq]
    tx = [{"text": t["text"], "pos": t["pos"]} for t in ix["texts"] if len(t["text"]) <= 30][:max_text]
    return {"extents": ix["extents"], "layers": [l["name"] + (" (보호)" if l["protected"] else "") for l in ix["layers"][:40]],
            "blocks": ix["blocks"][:30], "zones": z, "equipment": eq, "texts": tx}


def describe(ix):
    lines = [f"- 엔티티 {ix['n_entities']:,}개, 블록 참조 {ix['n_inserts']}개, 텍스트 {ix['n_texts']}개",
             f"- 범위: X {ix['extents'][0]:,.0f} ~ {ix['extents'][2]:,.0f}, Y {ix['extents'][1]:,.0f} ~ {ix['extents'][3]:,.0f} (단위 코드 {ix['units']})",
             f"- 레이어 {len(ix['layers'])}개: " + ", ".join(f"{l['name']}({l['count']}{', 보호' if l['protected'] else ''})" for l in ix["layers"][:15]),
             f"- 블록: " + (", ".join(f"{b['name']}×{b['count']}" for b in ix["blocks"][:12]) or "없음")]
    zl = [z for z in ix["zones"] if z["label"]]
    lines.append(f"- 라벨 있는 존(닫힌 폴리라인) {len(zl)}개: " + ", ".join(f"{z['label']}({z['area_m2']:g} m²)" for z in zl[:15]))
    ty = {}
    for e in ix["equipment"]:
        ty[e["type"] or e["block"]] = ty.get(e["type"] or e["block"], 0) + 1
    lines.append("- 인식된 장비: " + (", ".join(f"{k} {v}" for k, v in ty.items()) or "없음"))
    return "\n".join(lines)


# ── 연산 ──────────────────────────────────────────────────────────────────
class OpError(Exception):
    pass


def _find_zone(ix, q):
    if isinstance(q, int) and 0 <= q < len(ix["zones"]):
        return ix["zones"][q]
    q = str(q).strip()
    if q.isdigit() and int(q) < len(ix["zones"]):
        return ix["zones"][int(q)]
    cands = [z for z in ix["zones"] if z["label"] and (q in z["label"] or z["label"] in q)]
    if not cands:
        raise OpError(f"존 {q!r} 를 찾지 못함 (가능: {', '.join(sorted({z['label'] for z in ix['zones'] if z['label']})[:20])})")
    return max(cands, key=lambda z: z["_g"].area)


def _find(doc, ix, target):
    t = str(target)
    for e in ix["equipment"]:
        if e["id"] == t:
            return doc.entitydb[e["h"]]
    ent = doc.entitydb.get(t)
    if ent is not None and ent.dxf.owner == doc.modelspace().layout_key:
        return ent
    raise OpError(f"대상 {t!r} 없음 (장비 ID 또는 핸들)")


def _sym_size(ix):
    sc = [e["scale"] for e in ix["equipment"] if e["block"].startswith("ICT_") and e["scale"] > 0]
    if sc:
        return sorted(sc)[len(sc) // 2]
    x0, y0, x1, y1 = ix["extents"]
    return max(x1 - x0, y1 - y0, 1) / 160


def _next_ids(ix, prefix, n, extra=()):
    nums = []
    for e in list(ix["equipment"]) + [{"id": i} for i in extra]:
        m = re.fullmatch(rf"{re.escape(prefix)}-(?:.*-)?(\d+)", e["id"] or "")
        if m:
            nums.append(int(m.group(1)))
    k = max(nums or [0])
    return [f"{prefix}-{k + i + 1:02d}" for i in range(n)]


def _distribute(z, n, t):
    x0, y0, x1, y1 = z["bbox"]
    g = z["_g"]
    w, h = x1 - x0, y1 - y0
    horiz = w >= h
    L, W = (w, h) if horiz else (h, w)
    if t == "ap" and n > 2:
        nx = max(1, min(n, round(math.sqrt(n * L / W))))
        ny = math.ceil(n / nx)
        pts = [((i % nx + 0.5) / nx, (i // nx + 0.5) / ny) for i in range(n)]
    else:
        pts = [((i + 0.5) / n, 0.5) for i in range(n)]
    out = []
    for a, b in pts:
        p = (x0 + a * w, y0 + b * h) if horiz else (x0 + b * w, y0 + a * h)
        if not g.contains(Point(p)):  # L자 등 비볼록 존: 내부 최근접점
            q = g.representative_point() if g.area else Point(p)
            from shapely.ops import nearest_points
            p = nearest_points(g.buffer(-min(w, h) * 0.05), Point(p))[0].coords[0] if g.buffer(-min(w, h) * 0.05).area else q.coords[0]
        out.append(p)
    return out


def _delta(msp, p, rev, size):
    """리비전 델타(삼각형+기호) — 변경 위치 표시."""
    x, y = p[0] + size * 0.9, p[1] + size * 0.9
    r = size * 0.45
    msp.add_lwpolyline([(x - r, y - r * 0.6), (x + r, y - r * 0.6), (x, y + r * 0.9)], close=True, dxfattribs={"layer": "ICT-ANNOTATION"})
    dxfkit.text(msp, rev, (x, y - r * 0.05), r * 0.8, "ICT-ANNOTATION")


def apply_ops(doc, ix, ops, rev="A"):
    """ops 를 doc 에 적용. 반환 (log[], errors[]). 실패한 op 는 건너뛰고 errors 에 남긴다 (나머지는 적용)."""
    msp = doc.modelspace()
    for name in dxfkit.STD["layers"]:
        dxfkit.ensure_layer(doc, name)
    if dxfkit.STYLE not in doc.styles:
        doc.styles.add(dxfkit.STYLE, font=dxfkit.STD.get("font", "malgun.ttf"))
    log, errs = [], []
    size = _sym_size(ix)
    new_ids = []
    for i, op in enumerate(ops):
        kind = str(op.get("op", "")).lower()
        try:
            if kind not in OPS:
                raise OpError(f"op {kind!r} 미지원 ({'|'.join(OPS)})")
            if kind == "add_equipment":
                t = str(op.get("type", "")).lower()
                t = TYPE_ALIAS.get(t, t)
                if t not in SYM:
                    raise OpError(f"장비 종류 {op.get('type')!r} 없음 ({', '.join(SYM)})")
                n = max(1, int(num(op.get("count"), 1)))
                if op.get("position") or op.get("positions"):
                    pos = op.get("positions") or [op["position"]]
                    pos = [(num(p[0], 0), num(p[1], 0)) for p in pos][:n]
                    n = len(pos)
                    zone = None
                else:
                    zone = _find_zone(ix, op.get("zone"))
                    pos = _distribute(zone, n, t)
                ids = list(op.get("ids") or [])
                ids += _next_ids(ix, op.get("prefix") or SYM[t]["prefix"], n - len(ids), new_ids)
                for p, eid in zip(pos, ids):
                    if any(e["id"] == eid for e in ix["equipment"]) or eid in new_ids:
                        raise OpError(f"ID 중복 {eid}")
                    ins = dxfkit.add_symbol(msp, t, p, size, eid, rot=num(op.get("rotation"), 0))
                    _delta(msp, p, rev, size)
                    new_ids.append(eid)
                    log.append({"op": kind, "id": eid, "type": t, "pos": [round(p[0], 1), round(p[1], 1)], "handle": ins.dxf.handle,
                                "zone": zone["label"] if zone else None})
            elif kind in ("move", "delete", "rotate", "set_attr"):
                ent = _find(doc, ix, op.get("target"))
                if protected(ent.dxf.layer):
                    raise OpError(f"{op.get('target')} 는 보호 레이어 {ent.dxf.layer} 에 있음 — 수정 불가")
                if kind == "delete":
                    p = ent.dxf.get("insert", (0, 0, 0))
                    msp.delete_entity(ent)
                    _delta(msp, p, rev, size)
                    log.append({"op": kind, "target": op.get("target")})
                elif kind == "move":
                    p0 = ent.dxf.insert
                    if op.get("to"):
                        to = (num(op["to"][0], 0), num(op["to"][1], 0))
                    elif op.get("zone"):
                        to = _find_zone(ix, op["zone"])["_g"].representative_point().coords[0]
                    else:
                        by = op.get("by") or [0, 0]
                        to = (p0[0] + num(by[0], 0), p0[1] + num(by[1], 0))
                    dx, dy = to[0] - p0[0], to[1] - p0[1]
                    ent.translate(dx, dy, 0)
                    _delta(msp, to, rev, size)
                    log.append({"op": kind, "target": op.get("target"), "from": [round(p0[0], 1), round(p0[1], 1)], "to": [round(to[0], 1), round(to[1], 1)]})
                elif kind == "rotate":
                    ent.dxf.rotation = num(op.get("angle"), 0)
                    log.append({"op": kind, "target": op.get("target"), "angle": ent.dxf.rotation})
                else:
                    tag = str(op.get("tag") or "ID").upper()
                    hit = [a for a in ent.attribs if a.dxf.tag == tag]
                    if not hit:
                        ent.add_attrib(tag, str(op.get("value", "")), ent.dxf.insert)
                    else:
                        hit[0].dxf.text = str(op.get("value", ""))
                    log.append({"op": kind, "target": op.get("target"), "tag": tag, "value": op.get("value")})
            elif kind == "add_text":
                if op.get("position"):
                    p = (num(op["position"][0], 0), num(op["position"][1], 0))
                else:
                    p = _find_zone(ix, op.get("zone"))["_g"].representative_point().coords[0]
                h = num(op.get("height"), size * 0.4)
                t = dxfkit.text(msp, str(op.get("text", "")), p, h, "ICT-TEXT")
                log.append({"op": kind, "text": op.get("text"), "pos": [round(p[0], 1), round(p[1], 1)], "handle": t.dxf.handle})
            elif kind == "connect":
                a = _find(doc, ix, op.get("from")) if op.get("from") not in new_ids else None
                b = _find(doc, ix, op.get("to")) if op.get("to") not in new_ids else None
                if a is None or b is None:
                    raise OpError("connect 는 기존 장비끼리만 (새 장비는 다음 리비전에서)")
                pa, pb = a.dxf.insert, b.dxf.insert
                med = str(op.get("medium") or "CAT6").upper()
                lay = "ICT-FIBER" if "F" in med or "SMF" in med or "MMF" in med else "ICT-UTP"
                msp.add_lwpolyline([(pa[0], pa[1]), (pb[0], pa[1]), (pb[0], pb[1])], dxfattribs={"layer": lay})
                log.append({"op": kind, "from": op.get("from"), "to": op.get("to"), "medium": med})
        except OpError as e:
            errs.append(f"ops[{i}]({kind}): {e}")
        except Exception as e:
            errs.append(f"ops[{i}]({kind}): {type(e).__name__}: {e}")
    return log, errs


def diff(before, after):
    """인덱스 비교 → 추가/삭제/이동/속성변경."""
    b = {e["h"]: e for e in before["equipment"]}
    a = {e["h"]: e for e in after["equipment"]}
    out = {"added": [a[h] for h in a if h not in b], "removed": [b[h] for h in b if h not in a], "moved": [], "changed": []}
    for h in a:
        if h in b:
            if a[h]["pos"] != b[h]["pos"]:
                out["moved"].append({"id": a[h]["id"] or h, "from": b[h]["pos"], "to": a[h]["pos"]})
            if a[h]["attribs"] != b[h]["attribs"] or a[h]["rot"] != b[h]["rot"]:
                out["changed"].append({"id": a[h]["id"] or h, "before": b[h]["attribs"], "after": a[h]["attribs"]})
    out["entities"] = [before["n_entities"], after["n_entities"]]
    return out


def read(path):
    try:
        return ezdxf.readfile(path)
    except ezdxf.DXFStructureError:
        from ezdxf import recover
        doc, auditor = recover.readfile(path)
        return doc


def zones_as_spaces(ix):
    """ICT 배치에 기존 도면을 쓸 때: 라벨 있는 존 → (label, rect)."""
    return [{"zone": i, "label": z["label"], "rect": [z["bbox"][0], z["bbox"][1], z["bbox"][2] - z["bbox"][0], z["bbox"][3] - z["bbox"][1]]}
            for i, z in enumerate(ix["zones"]) if z["label"]]
