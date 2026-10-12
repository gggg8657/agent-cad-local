"""STL → PNG (numpy z-buffer 직교 투영 + 평면 음영 + 윤곽선, Pillow). 외부 렌더러·GPU 없이 — 폐쇄망.
3D 부품 시각 자기검증(참조 그림 vs 결과)의 입력."""
import struct

import numpy as np
from PIL import Image, ImageDraw

from .raster import _font

VIEWS = {"등각": (-45.0, 35.264), "정면": (-90.0, 0.0), "평면": (-90.0, 90.0)}  # (방위각, 고도): 정면 = -Y 에서 봄, 평면 = 위에서


def read_stl(path):
    """삼각형 (N,3,3). 바이너리·ASCII 모두."""
    data = open(path, "rb").read()
    if len(data) >= 84:
        n = struct.unpack("<I", data[80:84])[0]
        if 84 + n * 50 == len(data):
            rec = np.frombuffer(data[84:], dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]), count=n)
            return rec["v"].astype(float)
    vals = [float(x) for line in data.decode(errors="ignore").splitlines() if line.strip().startswith("vertex") for x in line.split()[1:4]]
    return np.array(vals, float).reshape(-1, 3, 3)


def _basis(az, el):
    a, e = np.radians(az), np.radians(el)
    cam = np.array([np.cos(e) * np.cos(a), np.cos(e) * np.sin(a), np.sin(e)])
    up = np.array([0, 0, 1.0]) if abs(el) < 89 else np.array([0, 1.0, 0])
    r = np.cross(up, cam)
    r /= np.linalg.norm(r)
    return r, np.cross(cam, r), cam


def render(tris, view="등각", size=420, color=(150, 170, 200)):
    T = tris - (tris.reshape(-1, 3).min(0) + tris.reshape(-1, 3).max(0)) / 2
    r, u, cam = _basis(*VIEWS[view])
    P = np.stack([T @ r, T @ u, T @ cam], -1)  # (N,3,3)
    ext = max(np.ptp(P[..., 0]), np.ptp(P[..., 1]), 1e-9)
    s = size * 0.86 / ext
    xy = P[..., :2] * s + size / 2
    xy[..., 1] = size - xy[..., 1]
    z = P[..., 2]
    N = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    N /= np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-12)
    light = cam * 0.6 + u * 0.5 + r * 0.25
    shade = 0.35 + 0.65 * np.abs(N @ (light / np.linalg.norm(light)))
    zb = np.full((size, size), -np.inf)
    fid = np.full((size, size), -1)
    img = np.full((size, size, 3), 255.0)
    base = np.array(color, float)
    for i in range(len(T)):
        p, zz = xy[i], z[i]
        x0, y0 = np.maximum(np.floor(p.min(0)).astype(int), 0)
        x1, y1 = np.minimum(np.ceil(p.max(0)).astype(int), size - 1)
        if x1 < x0 or y1 < y0:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        (ax, ay), (bx, by), (cx, cy) = p
        d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(d) < 1e-12:
            continue
        w0 = ((by - cy) * (gx - cx) + (cx - bx) * (gy - cy)) / d
        w1 = ((cy - ay) * (gx - cx) + (ax - cx) * (gy - cy)) / d
        w2 = 1 - w0 - w1
        ins = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
        if not ins.any():
            continue
        zi = w0 * zz[0] + w1 * zz[1] + w2 * zz[2]
        sub = zb[y0:y1 + 1, x0:x1 + 1]
        upd = ins & (zi > sub)
        sub[upd] = zi[upd]
        img[y0:y1 + 1, x0:x1 + 1][upd] = base * shade[i]
        fid[y0:y1 + 1, x0:x1 + 1][upd] = i
    # 윤곽선: 바탕 경계 + 깊이 불연속 + 법선이 크게 꺾이는 곳
    mask = fid >= 0
    nrm = np.where(mask[..., None], N[np.maximum(fid, 0)], 0)
    zf = np.where(mask, zb, np.nan)
    edge = np.zeros_like(mask)
    for dy, dx in ((0, 1), (1, 0)):
        a, b = (slice(None, size - dy), slice(None, size - dx)), (slice(dy, None), slice(dx, None))
        e = mask[a] != mask[b]
        e |= mask[a] & mask[b] & (np.abs(np.nan_to_num(zf[a] - zf[b])) > 2.5 / s)
        e |= mask[a] & mask[b] & ((nrm[a] * nrm[b]).sum(-1) < 0.9)
        edge[a] |= e
    img[edge] = (30, 30, 40)
    return Image.fromarray(img.astype(np.uint8))


def panel(stl_path, path, views=("등각", "정면", "평면"), size=420):
    tris = read_stl(stl_path)
    out = Image.new("RGB", (size * len(views), size + 28), "white")
    dr = ImageDraw.Draw(out)
    for k, v in enumerate(views):
        out.paste(render(tris, v, size), (k * size, 28))
        dr.text((k * size + 8, 4), f"({k + 1}) {v}", fill=(0, 0, 0), font=_font(18))
    out.save(path)
    return path
