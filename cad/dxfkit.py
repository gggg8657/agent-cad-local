"""ezdxf 공통 도구 — 표준 레이어/글꼴, 라이브러리 심볼 블록, 도곽·표제란·범례. 모든 2D 산출물이 이 모듈을 거친다."""
import datetime
import math

import ezdxf
from ezdxf.enums import TextEntityAlignment

from .util import resource

STD = resource("standards", "ict_v1.json")
LIB = resource("library", "ict.json")
STYLE = "KR"


def new_doc(standard=STD):
    doc = ezdxf.new("R2018", setup=True)  # setup → DASHED/HIDDEN/CENTER 선종류·치수 스타일
    doc.units = ezdxf.units.MM
    doc.header["$INSUNITS"] = 4
    doc.header["$MEASUREMENT"] = 1
    doc.styles.add(STYLE, font=standard.get("font", "malgun.ttf"))
    for name, d in standard["layers"].items():
        if name not in doc.layers:
            doc.layers.add(name, color=d["color"], linetype=d["lt"] if d["lt"] in doc.linetypes else "CONTINUOUS")
    return doc


def ensure_layer(doc, name, color=7):
    if name not in doc.layers:
        d = STD["layers"].get(name)
        doc.layers.add(name, color=d["color"] if d else color, linetype=(d or {}).get("lt", "CONTINUOUS") if d and d["lt"] in doc.linetypes else "CONTINUOUS")


def text(msp, s, pos, h, layer="ICT-TEXT", align="MIDDLE_CENTER", rot=0):
    t = msp.add_text(str(s), height=h, rotation=rot, dxfattribs={"layer": layer, "style": STYLE})
    t.set_placement(pos, align=TextEntityAlignment[align])
    return t


# ── 라이브러리 심볼 → 블록 ────────────────────────────────────────────────
def block_name(stype):
    return "ICT_" + stype.upper()


def ensure_symbol(doc, stype):
    """폭 1 기준 블록. INSERT 때 xscale=yscale=심볼 크기(mm)."""
    name = block_name(stype)
    if name in doc.blocks:
        return name
    sym = LIB["symbols"][stype]
    blk = doc.blocks.new(name)
    for g in sym["glyph"]:
        k = g[0]
        if k == "rect":
            _, x, y, w, h = g
            blk.add_lwpolyline([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], close=True)
        elif k == "poly":
            blk.add_lwpolyline(g[1], close=g[2])
        elif k == "circle":
            blk.add_circle((g[1], g[2]), g[3])
        elif k == "arc":
            blk.add_arc((g[1], g[2]), g[3], g[4], g[5])
        elif k == "line":
            blk.add_line((g[1], g[2]), (g[3], g[4]))
        elif k == "text":
            t = blk.add_text(g[1], height=g[4], dxfattribs={"style": STYLE})
            t.set_placement((g[2], g[3]), align=TextEntityAlignment.MIDDLE_CENTER)
    blk.add_attdef("ID", (0, -0.62), dxfattribs={"height": 0.26, "style": STYLE, "halign": 1, "valign": 3})
    blk.block.dxf.description = sym["label"]
    for a in blk.query("ATTDEF"):
        a.set_placement((0, -0.62), align=TextEntityAlignment.TOP_CENTER)
    blk.add_attdef("TYPE", (0, 0), dxfattribs={"height": 0.1, "style": STYLE, "flags": 1})  # 숨김 속성: 장비 종류
    return name


def add_symbol(msp, stype, pos, size, eid, rot=0, layer=None):
    doc = msp.doc
    name = ensure_symbol(doc, stype)
    layer = layer or LIB["symbols"][stype]["layer"]
    ensure_layer(doc, layer)
    ins = msp.add_blockref(name, pos, dxfattribs={"layer": layer, "xscale": size, "yscale": size, "rotation": 0})
    ins.add_auto_attribs({"ID": eid, "TYPE": stype})
    if rot:  # 심볼 몸체만 회전 (ID 글자는 바로 세움)
        ins.dxf.rotation = rot
        for a in ins.attribs:
            if a.dxf.tag == "ID":
                a.dxf.rotation = 0
                a.set_placement((pos[0], pos[1] - 0.62 * size), align=TextEntityAlignment.TOP_CENTER)
    return ins


# ── 도곽·표제란·범례 ───────────────────────────────────────────────────────
PAPER = {"A3": (420, 297), "A2": (594, 420), "A1": (841, 594)}
SCALES = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000]


def pick_scale(w, h, paper="A1", fill=0.82):
    pw, ph = PAPER[paper]
    for s in SCALES:
        if w / s <= pw * fill and h / s <= ph * fill:
            return s
    return SCALES[-1]


def frame(msp, bbox, title, sub="", scale=1, paper="A1", rev="R00", kind="ICT", extra=()):
    """bbox(모델 공간 내용 범위) 를 감싸는 도곽 + 우하단 표제란. scale = 1:N. 반환: 도곽 범위."""
    (x0, y0), (x1, y1) = bbox
    pw, ph = PAPER[paper]
    W, H = max(pw * scale, (x1 - x0) * 1.12), max(ph * scale, (y1 - y0) * 1.12 + 60 * scale)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2 - 22 * scale
    fx0, fy0, fx1, fy1 = cx - W / 2, cy - H / 2, cx + W / 2, cy + H / 2
    L = "ICT-TITLE"
    msp.add_lwpolyline([(fx0, fy0), (fx1, fy0), (fx1, fy1), (fx0, fy1)], close=True, dxfattribs={"layer": L})
    m = 10 * scale
    msp.add_lwpolyline([(fx0 + m, fy0 + m), (fx1 - m, fy0 + m), (fx1 - m, fy1 - m), (fx0 + m, fy1 - m)], close=True,
                       dxfattribs={"layer": L, "const_width": 0})
    # 표제란 180 x 56 (paper mm)
    tw, th = 180 * scale, 56 * scale
    tx1, ty0 = fx1 - m, fy0 + m
    tx0, ty1 = tx1 - tw, ty0 + th
    msp.add_lwpolyline([(tx0, ty0), (tx1, ty0), (tx1, ty1), (tx0, ty1)], close=True, dxfattribs={"layer": L})
    rows = [("도면명", title), ("내용", sub or "-"), ("축척", f"1:{scale}" if scale != 1 else "1:1"),
            ("일자 / REV", f"{datetime.date.today()}  /  {rev}"), ("작성", "agent-cad-local (AI 초안)")] + list(extra)
    rh = th / (len(rows) + 1)
    for i, (k, v) in enumerate(rows):
        y = ty1 - (i + 1) * rh
        msp.add_line((tx0, y), (tx1, y), dxfattribs={"layer": L})
        text(msp, k, (tx0 + 4 * scale, y + rh / 2), 3.2 * scale, L, "MIDDLE_LEFT")
        text(msp, v, (tx0 + 38 * scale, y + rh / 2), 3.6 * scale if i else 4.6 * scale, L, "MIDDLE_LEFT")
    msp.add_line((tx0 + 34 * scale, ty0 + rh), (tx0 + 34 * scale, ty1), dxfattribs={"layer": L})
    text(msp, "DRAFT — NOT FOR CONSTRUCTION  (검토·승인 전 시공 불가)", ((tx0 + tx1) / 2, ty0 + rh / 2), 3.6 * scale, "ICT-ANNOTATION")
    return (fx0, fy0), (fx1, fy1)


def legend(msp, origin, items, scale, title="범례"):
    """items: [(stype or ('line', layer, label)), ...]  origin: 좌상단."""
    x, y = origin
    h = 7 * scale
    text(msp, title, (x, y), 4.5 * scale, "ICT-TEXT", "TOP_LEFT")
    y -= 10 * scale
    for it in items:
        if isinstance(it, tuple) and it[0] == "line":
            _, layer, label = it
            msp.add_line((x, y), (x + 10 * scale, y), dxfattribs={"layer": layer})
        else:
            add_symbol(msp, it, (x + 5 * scale, y), 6 * scale, "", layer=LIB["symbols"][it]["layer"])
            label = LIB["symbols"][it]["label"]
        text(msp, label, (x + 14 * scale, y), 3.2 * scale, "ICT-TEXT", "MIDDLE_LEFT")
        y -= h
    return y


def rect(msp, x, y, w, h, layer, **kw):
    return msp.add_lwpolyline([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], close=True, dxfattribs={"layer": layer, **kw})


def wedge(msp, c, r, a0, a1, layer):
    """CCTV 화각 부채꼴."""
    cx, cy = c
    p0 = (cx + r * math.cos(math.radians(a0)), cy + r * math.sin(math.radians(a0)))
    p1 = (cx + r * math.cos(math.radians(a1)), cy + r * math.sin(math.radians(a1)))
    msp.add_line(c, p0, dxfattribs={"layer": layer})
    msp.add_line(c, p1, dxfattribs={"layer": layer})
    msp.add_arc(c, r, a0, a1, dxfattribs={"layer": layer})
