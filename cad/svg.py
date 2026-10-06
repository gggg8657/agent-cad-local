"""DXF → SVG 미리보기 (자체 렌더러, matplotlib 불필요). 미리보기와 DXF가 같은 데이터에서 나오도록 모든 SVG는 DXF를 그린다."""
import html
import math

from ezdxf import bbox as ezbbox
from ezdxf.colors import aci2rgb

DASH = {"DASHED": "7 4", "HIDDEN": "4 3", "CENTER": "12 3 3 3", "DASHDOT": "10 3 2 3", "PHANTOM": "12 3 3 3 3 3", "DOT": "1 3"}


def _rgb(c):
    if c in (7, 0, 256, 257, None):
        return "#1f1f1f"
    r, g, b = aci2rgb(int(c))
    if 0.299 * r + 0.587 * g + 0.114 * b > 160:  # 노랑·하늘색 등 밝은 색은 흰 배경에서 안 보이므로 진하게
        r, g, b = int(r * 0.62), int(g * 0.62), int(b * 0.62)
    return f"#{r:02x}{g:02x}{b:02x}"


class Ctx:
    def __init__(self, doc, highlight=()):
        self.doc = doc
        self.hl = set(highlight)
        self.layers = {l.dxf.name: l for l in doc.layers}
        self.out = []
        self.pats = set()

    def style(self, e, layer=None):
        layer = layer or e.dxf.get("layer", "0")
        lay = self.layers.get(layer)
        c = e.dxf.get("color", 256)
        if c in (256, None) and lay is not None:
            c = lay.color
        lt = e.dxf.get("linetype", "BYLAYER")
        if lt.upper() in ("BYLAYER", "BYBLOCK") and lay is not None:
            lt = lay.dxf.linetype
        return _rgb(c), DASH.get(str(lt).upper())


def _p(v):
    return f"{v[0]:.2f},{-v[1]:.2f}"


def _emit(ctx, e, layer=None, depth=0):
    t = e.dxftype()
    if t in ("INSERT", "DIMENSION") and depth < 6:
        lay = e.dxf.layer if t == "INSERT" else None
        eid = ""
        if t == "INSERT":
            ids = [a.dxf.text for a in e.attribs if a.dxf.tag == "ID"]
            eid = ids[0] if ids else ""
        cls = ' class="hl"' if e.dxf.handle in ctx.hl else ""
        ctx.out.append(f'<g data-h="{e.dxf.handle}" data-id="{html.escape(eid)}"{cls}>')
        try:
            for v in e.virtual_entities():
                vl = v.dxf.get("layer", "0")
                _emit(ctx, v, lay if vl == "0" and lay else None, depth + 1)
        except Exception:
            pass
        if t == "INSERT":
            for a in e.attribs:
                if not a.is_invisible:
                    _emit(ctx, a, None, depth + 1)
        ctx.out.append("</g>")
        return
    col, dash = ctx.style(e, layer)
    st = f'stroke="{col}"' + (f' stroke-dasharray="{dash}"' if dash else "")
    hl = ' class="hl"' if e.dxf.get("handle") in ctx.hl else ""
    if t == "LINE":
        s, d = e.dxf.start, e.dxf.end
        ctx.out.append(f'<path{hl} {st} d="M{_p(s)}L{_p(d)}"/>')
    elif t in ("LWPOLYLINE", "POLYLINE", "SPLINE", "ELLIPSE"):
        try:
            if t == "LWPOLYLINE" and not any(b for *_, b in e.get_points("xyb")):
                pts = [(x, y) for x, y in e.get_points("xy")]
                closed = e.closed
            else:
                pts = [(v.x, v.y) for v in e.flattening(0.5)] if t != "LWPOLYLINE" else None
                closed = False
                if pts is None:
                    from ezdxf.path import make_path
                    pts = [(v.x, v.y) for v in make_path(e).flattening(0.5)]
        except Exception:
            return
        if len(pts) < 2:
            return
        d = "M" + "L".join(_p(p) for p in pts) + ("Z" if closed else "")
        ctx.out.append(f'<path{hl} {st} d="{d}"/>')
    elif t == "CIRCLE":
        c = e.dxf.center
        ctx.out.append(f'<circle{hl} {st} cx="{c[0]:.2f}" cy="{-c[1]:.2f}" r="{e.dxf.radius:.2f}"/>')
    elif t == "ARC":
        c, r = e.dxf.center, e.dxf.radius
        a0, a1 = math.radians(e.dxf.start_angle), math.radians(e.dxf.end_angle)
        if a1 <= a0:
            a1 += 2 * math.pi
        p0 = (c[0] + r * math.cos(a0), c[1] + r * math.sin(a0))
        p1 = (c[0] + r * math.cos(a1), c[1] + r * math.sin(a1))
        large = 1 if a1 - a0 > math.pi else 0
        ctx.out.append(f'<path{hl} {st} d="M{_p(p0)}A{r:.2f},{r:.2f} 0 {large} 0 {_p(p1)}"/>')
    elif t in ("TEXT", "ATTRIB"):
        txt = e.dxf.text
        if not txt:
            return
        try:
            align, p, _ = e.get_placement() if t == "TEXT" else (None, e.dxf.align_point if e.dxf.halign or e.dxf.valign else e.dxf.insert, None)
        except Exception:
            p = e.dxf.insert
        ha = {0: "start", 1: "middle", 2: "end", 4: "middle"}.get(e.dxf.get("halign", 0), "start")
        dy = {0: 0, 1: -0.2, 2: 0.36, 3: 0.86}.get(e.dxf.get("valign", 0), 0)  # dominant-baseline 은 뷰어마다 달라 dy(em) 사용
        h = e.dxf.height
        rot = e.dxf.get("rotation", 0)
        tr = f' transform="rotate({-rot:.1f} {p[0]:.2f} {-p[1]:.2f})"' if rot else ""
        ctx.out.append(f'<text{hl} x="{p[0]:.2f}" y="{-p[1]:.2f}" font-size="{h:.2f}" fill="{col}" text-anchor="{ha}" '
                       f'dy="{dy}em"{tr}>{html.escape(txt)}</text>')
    elif t == "MTEXT":
        p = e.dxf.insert
        h = e.dxf.char_height
        ap = e.dxf.get("attachment_point", 1)
        ha = ["start", "middle", "end"][(ap - 1) % 3]
        lines = e.plain_text().split("\n")
        dy0 = {0: 0.8, 1: 0.5 - 0.6 * (len(lines) - 1), 2: 0.0 - 1.2 * (len(lines) - 1)}[(ap - 1) // 3]
        for i, ln in enumerate(lines):
            ctx.out.append(f'<text x="{p[0]:.2f}" y="{-p[1] + (dy0 + i * 1.2) * h:.2f}" font-size="{h:.2f}" fill="{col}" '
                           f'text-anchor="{ha}" dy="0.36em">{html.escape(ln)}</text>')
    elif t in ("SOLID", "TRACE", "3DFACE"):
        pts = [e.dxf.get(f"vtx{i}") for i in range(4)]
        pts = [p for p in pts if p is not None]
        if len(pts) == 4:
            pts = [pts[0], pts[1], pts[3], pts[2]]
        ctx.out.append(f'<path fill="{col}" stroke="none" d="M{"L".join(_p(p) for p in pts)}Z"/>')
    elif t == "HATCH":
        try:
            from ezdxf.path import from_hatch
            solid = bool(e.dxf.get("solid_fill", 0))
            pat = (e.dxf.get("pattern_name") or "SOLID").upper()
            d = ""
            for path in from_hatch(e):
                pts = [(v.x, v.y) for v in path.flattening(0.5)]
                if len(pts) > 2:
                    d += f'M{"L".join(_p(p) for p in pts)}Z'
            if d:
                if solid or pat == "SOLID":
                    ctx.out.append(f'<path fill="{col}" fill-opacity="0.55" fill-rule="evenodd" stroke="none" d="{d}"/>')
                else:
                    key = (pat, col)
                    ctx.pats.add(key)
                    ctx.out.append(f'<path fill="url(#p{abs(hash(key)) % 10 ** 8})" fill-rule="evenodd" stroke="none" d="{d}"/>')
        except Exception:
            pass


def render(doc, highlight=(), max_entities=60000, bg="#ffffff"):
    msp = doc.modelspace()
    ents = list(msp)[:max_entities]
    ctx = Ctx(doc, highlight)
    for e in ents:
        try:
            if ctx.layers.get(e.dxf.get("layer", "0")) is not None and ctx.layers[e.dxf.layer].is_off():
                continue
            _emit(ctx, e)
        except Exception:
            continue
    ext = ezbbox.extents(ents, fast=True)
    if not ext.has_data:
        return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"></svg>'
    (x0, y0, _), (x1, y1, _) = ext.extmin, ext.extmax
    w, h = max(x1 - x0, 1), max(y1 - y0, 1)
    m = 0.02 * max(w, h)
    vb = f"{x0 - m:.1f} {-y1 - m:.1f} {w + 2 * m:.1f} {h + 2 * m:.1f}"
    sw = max(w, h) / 1800  # 선 굵기·점선 간격은 사용자 좌표 단위 (rsvg·브라우저 동일)
    body = "".join(ctx.out)
    for pat in set(DASH.values()):
        body = body.replace(f'stroke-dasharray="{pat}"', 'stroke-dasharray="' + " ".join(f"{float(x) * sw * 2.5:.2f}" for x in pat.split()) + '"')
    sp = sw * 14  # 해칭 패턴 간격 (사용자 좌표)
    defs = []
    for pat, col in ctx.pats:
        pid = f"p{abs(hash((pat, col))) % 10 ** 8}"
        if pat.startswith("AR-CONC"):
            body_p = f'<rect width="{sp}" height="{sp}" fill="{col}" fill-opacity="0.12"/><circle cx="{sp * .3:.2f}" cy="{sp * .3:.2f}" r="{sw * 1.6:.2f}" fill="{col}"/><circle cx="{sp * .75:.2f}" cy="{sp * .7:.2f}" r="{sw:.2f}" fill="{col}"/>'
        elif pat in ("ANSI37", "ANSI32"):
            body_p = f'<rect width="{sp}" height="{sp}" fill="{col}" fill-opacity="0.18"/><path d="M0,{sp}L{sp},0M0,0L{sp},{sp}" stroke="{col}" stroke-width="{sw * .8:.3f}"/>'
        else:
            body_p = f'<rect width="{sp}" height="{sp}" fill="{col}" fill-opacity="0.10"/><path d="M0,{sp}L{sp},0M{-sp / 2:.2f},{sp / 2:.2f}L{sp / 2:.2f},{-sp / 2:.2f}M{sp / 2:.2f},{sp * 1.5:.2f}L{sp * 1.5:.2f},{sp / 2:.2f}" stroke="{col}" stroke-width="{sw * .8:.3f}"/>'
        defs.append(f'<pattern id="{pid}" width="{sp:.2f}" height="{sp:.2f}" patternUnits="userSpaceOnUse">{body_p}</pattern>')
    head = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" data-w="{w:.0f}" data-h="{h:.0f}" data-sw="{sw:.3f}" '
            f'font-family="Malgun Gothic,NanumGothic,Noto Sans CJK KR,sans-serif">'
            f'<style>path:not([fill]),circle:not([fill]){{fill:none}}path,circle{{stroke-width:{sw:.3f}}}'
            f'.hl path,.hl circle,path.hl,circle.hl{{stroke:#ff2d55!important;stroke-width:{sw * 3:.3f}}}.hl text,text.hl{{fill:#ff2d55!important}}</style>'
            f'<defs>{"".join(defs)}</defs>'
            f'<rect x="{x0 - m:.1f}" y="{-y1 - m:.1f}" width="{w + 2 * m:.1f}" height="{h + 2 * m:.1f}" fill="{bg}"/>')
    return head + body + "</svg>"
