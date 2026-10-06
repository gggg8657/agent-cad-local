"""DXF → PNG (Pillow 만 사용, 시스템 프로그램 불필요 — 폐쇄망). VLM 시각 비교·썸네일용.
한글 글꼴: static/fonts/NanumGothic.ttf (OFL) 동봉, 없으면 기본 글꼴(한글 생략)."""
import os

from ezdxf import bbox as ezb
from ezdxf.colors import aci2rgb
from ezdxf.path import make_path
from PIL import Image, ImageDraw, ImageFont

from .util import ROOT

FONT = os.path.join(ROOT, "static", "fonts", "NanumGothic.ttf")
_fonts = {}


def _font(px):
    px = max(6, min(int(px), 200))
    if px not in _fonts:
        try:
            _fonts[px] = ImageFont.truetype(FONT, px)
        except OSError:
            _fonts[px] = ImageFont.load_default()
    return _fonts[px]


def render(doc, path, width=1600, skip_layers=("ICT-TITLE",)):
    msp = doc.modelspace()
    layers = {l.dxf.name: l for l in doc.layers}
    ents = [e for e in msp if e.dxf.get("layer", "0") not in skip_layers]
    ext = ezb.extents(ents, fast=True)
    if not ext.has_data:
        Image.new("RGB", (width, width), "white").save(path)
        return path
    x0, y0, x1, y1 = ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y
    m = 0.03 * max(x1 - x0, y1 - y0)
    x0, y0, x1, y1 = x0 - m, y0 - m, x1 + m, y1 + m
    k = width / (x1 - x0)
    H = int((y1 - y0) * k) + 1
    im = Image.new("RGB", (width, H), "white")
    dr = ImageDraw.Draw(im)
    P = lambda x, y: ((x - x0) * k, (y1 - y) * k)

    def col(e, lay):
        c = e.dxf.get("color", 256)
        L = layers.get(lay)
        if c in (256, 0, None) and L is not None:
            c = L.color
        if c in (7, 256, 0, None):
            return (30, 30, 30)
        r, g, b = aci2rgb(abs(int(c)))
        if 0.299 * r + 0.587 * g + 0.114 * b > 160:
            r, g, b = int(r * 0.62), int(g * 0.62), int(b * 0.62)
        return (r, g, b)

    def draw(e, lay=None, depth=0):
        t = e.dxftype()
        lay = lay or e.dxf.get("layer", "0")
        if t in ("INSERT", "DIMENSION") and depth < 6:
            try:
                for v in e.virtual_entities():
                    draw(v, lay if v.dxf.get("layer", "0") == "0" else None, depth + 1)
            except Exception:
                pass
            if t == "INSERT":
                for a in e.attribs:
                    if not a.is_invisible:
                        draw(a, None, depth + 1)
            return
        c = col(e, lay)
        if t in ("TEXT", "ATTRIB", "MTEXT"):
            s = e.plain_text() if t == "MTEXT" else e.dxf.text
            h = (e.dxf.char_height if t == "MTEXT" else e.dxf.height) * k
            if not s or h < 5:
                return
            try:
                p = e.dxf.insert if t != "TEXT" else e.get_placement()[1]
            except Exception:
                p = e.dxf.insert
            ha = e.dxf.get("halign", 0) if t != "MTEXT" else 0
            anchor = {0: "l", 1: "m", 2: "r", 4: "m"}.get(ha, "l") + {0: "s", 1: "d", 2: "m", 3: "a"}.get(e.dxf.get("valign", 0) if t != "MTEXT" else 2, "s")
            dr.text(P(p[0], p[1]), s.split("\n")[0], fill=c, font=_font(h * 1.15), anchor=anchor)
            return
        if t in ("SOLID", "TRACE"):
            pts = [e.dxf.get(f"vtx{i}") for i in (0, 1, 3, 2)]
            dr.polygon([P(q[0], q[1]) for q in pts if q is not None], fill=c)
            return
        try:
            pts = [P(v.x, v.y) for v in make_path(e).flattening(0.5 / k)]
        except Exception:
            return
        if len(pts) >= 2:
            dr.line(pts, fill=c, width=2 if lay.startswith(("M-PART", "MSR")) else 1)
    for e in ents:
        L = layers.get(e.dxf.get("layer", "0"))
        if L is not None and L.is_off():
            continue
        draw(e)
    im.save(path)
    return path
