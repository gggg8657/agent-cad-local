"""기기 상세 템플릿 — 원통 한 개 대신 엔지니어가 알아보는 내부 구조를 만든다 (형상 기술자 목록).

모든 기술자에 mat(재질)을 붙인다 → 3D 색, 단면 해칭 패턴이 재질로 정해진다.
  steel(강재) concrete(콘크리트) graphite(흑연) fuel(핵연료) control(제어봉) water(물) sodium(소듐) tubes(전열관 다발) glass(차폐창) salt(용융염)
lattice:true 인 기술자는 하나로 합치지 않고 묶음(Compound)으로 둔다 (수백 개 집합체 불리언 회피).
원형(kb) 의 "style" 로 노심 격자·관통관 등을 정한다: {"lattice":"square|hex","pitch":215,"crdm":true,…}
"""
import math

MAT_RGB = {"steel": [168, 172, 178], "concrete": [150, 146, 138], "graphite": [70, 70, 75], "fuel": [235, 120, 160], "control": [60, 60, 60],
           "water": [90, 150, 220], "sodium": [200, 200, 150], "tubes": [110, 140, 180], "glass": [150, 210, 235], "salt": [235, 120, 170],
           "motor": [60, 70, 95], "insulation": [230, 220, 170]}
MAT_ALPHA = {"water": 0.22, "glass": 0.45, "sodium": 0.25}


def cyl(x, y, z, d, h, mat="steel", **k):
    return {"p": "cyl", "c": [x, y, z], "d": d, "h": h, "mat": mat, **k}


def tube(x, y, z, d, idd, h, mat="steel", **k):
    return {"p": "tube", "c": [x, y, z], "d": d, "id": idd, "h": h, "mat": mat, **k}


def head(x, y, z, d, h, up, mat="steel", **k):
    return {"p": "head", "c": [x, y, z], "d": d, "h": h, "up": up, "mat": mat, **k}


def seg(a, b, d, mat="steel", **k):
    return {"p": "seg", "a": a, "b": b, "d": d, "mat": mat, **k}


def lattice_positions(R, pitch, kind):
    """반경 R 원 안에 들어가는 집합체 중심 (사각 또는 육각 격자)."""
    pts = []
    n = int(R / pitch) + 2
    if kind == "hex":
        for i in range(-n, n + 1):
            for j in range(-n, n + 1):
                x = pitch * (i + j / 2)
                y = pitch * j * math.sqrt(3) / 2
                if math.hypot(x, y) + pitch / 2 <= R:
                    pts.append((x, y))
    else:
        for i in range(-n, n + 1):
            for j in range(-n, n + 1):
                x, y = (i + 0.5 * ((n * 2 + 1) % 2 == 0)) * pitch, j * pitch
                if math.hypot(abs(x) + pitch / 2, abs(y) + pitch / 2) <= R * 1.04:
                    pts.append((x, y))
    return pts


def control_positions(pts, pitch, kind, every=3):
    """제어봉 집합체 위치: 격자 안에서 고르게 (대칭)."""
    out = []
    for x, y in pts:
        if kind == "hex":
            i = round((x - y / math.sqrt(3)) / pitch)
            j = round(2 * y / (math.sqrt(3) * pitch))
            if (i - j) % every == 0 and (i + 2 * j) % every == 0:
                out.append((x, y))
        else:
            i, j = round(x / pitch), round(y / pitch)
            if i % 2 == 0 and j % 2 == 0 and (i // 2 + j // 2) % 2 == 0:
                out.append((x, y))
    return out


def core(x, y, z, S, st, ctx):
    D, H = S["D"], S["H"]
    kind = st.get("lattice", "square")
    pitch = st.get("pitch") or max(D / 12, 80)
    R = D / 2 - 30
    pts = lattice_positions(R, pitch, kind)
    if len(pts) > 600:  # 너무 촘촘하면 피치를 키워 표현 (도면용)
        pitch *= math.sqrt(len(pts) / 600)
        pts = lattice_positions(R, pitch, kind)
    ctrl = set(control_positions(pts, pitch, kind, st.get("ctrl_every", 3)))
    ctx["lattice"] = {"pts": [(x + a, y + b) for a, b in pts], "ctrl": [(x + a, y + b) for a, b in ctrl], "pitch": pitch, "kind": kind}
    fuel_mat = st.get("fuel_mat", "fuel")
    out = [tube(x, y, z, D, D - 60, H, "steel", part="노심 배플/쉬라우드")]
    w = pitch * 0.94
    for a, b in pts:
        m = "control" if (a, b) in ctrl else fuel_mat
        if kind == "hex":
            out.append({"p": "hex", "c": [x + a, y + b, z], "af": w, "h": H, "mat": m, "lattice": True})
        else:
            out.append({"p": "box", "min": [x + a - w / 2, y + b - w / 2, z], "max": [x + a + w / 2, y + b + w / 2, z + H], "mat": m, "lattice": True})
    out.append(cyl(x, y, z - 200, D + 100, 200, "steel", part="하부 노심판"))
    out.append(cyl(x, y, z + H, D, 120, "steel", part="상부 노심판"))
    return out


def vessel(x, y, z, S, st, ctx):
    """원자로 용기: 몸체(벽 회전체) + 플랜지 + 지지 스커트 + 노즐 + 상부 관통관(CRDM)."""
    D, H, t = S["D"], S["H"], S.get("t") or 50
    out = [{"p": "shell", "c": [x, y, z], "d": D, "h": H, "t": t, "mat": "steel"}]
    hh = D * 0.22
    if st.get("flange", True):
        fz = z + H - hh - 250
        out.append(tube(x, y, fz, D + 2 * max(t, 120), D - 2, 350, "steel", part="용기 플랜지"))
    if st.get("skirt", True):
        out.append(tube(x, y, z + hh * 0.3 - 1800, D * 0.8, D * 0.8 - 2 * max(t * 0.6, 40), 1800, "steel", part="지지 스커트"))
    for nz in st.get("nozzles", []):  # [{"z": abs, "d":.., "angles":[0,180]}] — plant 가 수식 평가해 넣음
        for a in nz.get("angles", [0, 180]):
            ux, uy = math.cos(math.radians(a)), math.sin(math.radians(a))
            r0 = D / 2 - t
            out.append(seg([x + ux * r0, y + uy * r0, nz["z"]], [x + ux * (D / 2 + nz.get("len", 900)), y + uy * (D / 2 + nz.get("len", 900)), nz["z"]],
                           nz["d"], "steel", part="노즐"))
    lat = ctx.get("lattice")
    if st.get("crdm") and lat:
        top = z + H
        r_lim = D / 2 * 0.62
        for a, b in lat["ctrl"]:
            if math.hypot(a - x, b - y) < r_lim:
                zz = top - hh * (1 - (math.hypot(a - x, b - y) / (D / 2)) ** 2) ** 0.5
                out.append(cyl(a, b, zz - 100, max(100, lat["pitch"] * 0.45), top - zz + st.get("crdm_len", 2800), "steel", part="제어봉 구동장치(CRDM)"))
    return out


def steam_generator(x, y, z, S, st, ctx):
    D, H = S["D"], S["H"]
    if st.get("sg_type") == "u_tube":  # 루프형: 아래 관다발부 + 원뿔 전이 + 위 증기드럼(넓음) + 반구 채널헤드
        Dl, Du = D * 0.78, D
        zt = z + H * 0.62
        out = [{"p": "rev", "c": [x, y, z], "mat": "steel",
                "prof": [(0, 0), (Dl / 2, 0), (Dl / 2, H * 0.62 - z * 0), (Du / 2, H * 0.70), (Du / 2, H - Du * 0.25), (Du * 0.35, H - Du * 0.08), (0, H)]},
               tube(x, y, z + D * 0.4, Dl * 0.82, Dl * 0.5, H * 0.5, "tubes", part="U자 전열관 다발"),
               seg([x, y, z + 300], [x - Dl / 2 - 800, y, z + 300], D * 0.22, "steel", part="1차 입구 노즐"),
               seg([x, y, z + H - Du * 0.3], [x, y, z + H + 900], D * 0.22, "steel", part="주증기 노즐")]
        return out
    hh = D * 0.25
    out = [cyl(x, y, z + hh, D, max(H - 2 * hh, 1)), head(x, y, z + H - hh, D, hh, True), head(x, y, z + hh, D, hh, False),
           tube(x, y, z + hh + H * 0.08, D * 0.86, D * 0.32, H * 0.62, "tubes", part="나선형 전열관 다발")]
    return out


def pump(x, y, z, S, st, ctx):
    D, H = S["D"], S["H"]
    mz, mD = S.get("motor_z", z + H + 300), S.get("motor_D", D * 1.2)
    top = z + H
    out = [cyl(x, y, z + H * 0.25, D, H * 0.5, "steel", part="펌프 케이싱"), head(x, y, z + H * 0.75, D, H * 0.25, True), head(x, y, z + H * 0.25, D, H * 0.25, False)]
    if mz > top:
        out.append(cyl(x, y, top, D * 0.22, mz - top, "steel", out=True, part="축"))
    base = max(mz, top)
    out += [cyl(x, y, base, mD * 1.15, mD * 0.12, "steel", out=True, part="모터 플랜지"),
            cyl(x, y, base + mD * 0.12, mD, mD * 1.3, "motor", out=True, part="모터"),
            head(x, y, base + mD * 1.42, mD, mD * 0.18, True, "motor", out=True)]
    return out


def pressurizer(x, y, z, S, st, ctx):
    D, H = S["D"], S["H"]
    hh = D * 0.3
    out = [cyl(x, y, z + hh, D, max(H - 2 * hh, 1)), head(x, y, z + H - hh, D, hh, True), head(x, y, z + hh, D, hh, False)]
    n = 7
    for k in range(n):  # 전열기 다발 (아래 경판 관통)
        a = 2 * math.pi * k / n
        r = D * 0.22 if k else 0
        out.append(cyl(x + r * math.cos(a), y + r * math.sin(a), z, max(40, D * 0.03), H * 0.35, "insulation", part="전열기"))
    out.append(cyl(x, y, z + H - 50, max(80, D * 0.08), 450, "steel", part="스프레이 노즐"))
    return out


def capsule(x, y, z, S, st, ctx, mat="steel"):
    D, H = S["D"], S["H"]
    hh = D * 0.25
    return [cyl(x, y, z + hh, D, max(H - 2 * hh, 1), mat), head(x, y, z + H - hh, D, hh, True, mat), head(x, y, z + hh, D, hh, False, mat)]


def ihx(x, y, z, S, st, ctx):
    D, H = S["D"], S["H"]
    out = capsule(x, y, z, S, st, ctx)
    out.append(tube(x, y, z + H * 0.15, D * 0.85, D * 0.35, H * 0.6, "tubes", part="전열관 다발"))
    out.append(cyl(x, y, z + H * 0.1, D * 0.28, H * 0.9 + st.get("ihx_riser", 2500), "steel", part="중간 소듐 하강관"))
    return out


def containment(x, y, z, S, st, ctx):
    D, H, t = S["D"], S["H"], S.get("t") or 1200
    out = [{"p": "dome", "c": [x, y, z], "d": D, "h": H, "t": t, "mat": "concrete"},
           cyl(x, y, z - 2500, D + 3000, 2500, "concrete", part="기초 매트")]
    if st.get("operating_floor"):
        fz = st["operating_floor"]
        out.append(tube(x, y, fz, D - 2 * t, st.get("floor_hole", D * 0.35), 800, "concrete", part="운전층"))
    out.append(tube(x, y, z + H - 1500, D - 2 * t, D - 2 * t - 1200, 600, "steel", part="폴라 크레인 레일"))
    return out


def pool(x, y, z, S, st, ctx):
    H, t = S["H"], S.get("t") or 400
    wl = S.get("water_level", H - 800)
    if S.get("W"):
        W, L = S["W"], S["L"]
        return [{"p": "pool_box", "min": [x - W / 2, y - L / 2, z], "max": [x + W / 2, y + L / 2, z + H], "t": t, "mat": "concrete"},
                {"p": "pool_box", "min": [x - W / 2 + t, y - L / 2 + t, z + t], "max": [x + W / 2 - t, y + L / 2 - t, z + H], "t": 12, "mat": "steel", "part": "라이너"},
                {"p": "box", "min": [x - W / 2 + t + 12, y - L / 2 + t + 12, z + t + 12], "max": [x + W / 2 - t - 12, y + L / 2 - t - 12, z + wl], "mat": "water",
                 "part": "냉각수", "no_cut": False}]
    D = S["D"]
    return [{"p": "pool_cyl", "c": [x, y, z], "d": D, "h": H, "t": t, "mat": "concrete"},
            {"p": "pool_cyl", "c": [x, y, z + t], "d": D - 2 * t, "h": H - t, "t": 12, "mat": "steel", "part": "라이너"},
            cyl(x, y, z + t + 12, D - 2 * t - 24, wl - t - 12, "water", part="냉각수")]


def roof_deck(x, y, z, S, st, ctx):
    D, H = S["D"], S["H"]
    return [cyl(x, y, z, D, H, "steel", part="상부 덮개"), cyl(x, y, z + H, D * 0.62, 350, "steel", part="대회전 플러그"),
            cyl(x, y + D * 0.08, z + H + 350, D * 0.3, 300, "steel", part="소회전 플러그")]


def reflector(x, y, z, S, st, ctx):
    D, H, t = S["D"], S["H"], S.get("t") or S["D"] * 0.1
    return [tube(x, y, z, D, max(D - 2 * t, 1), H, st.get("refl_mat", "graphite"))]


def cell(x, y, z, S, st, ctx):
    W, L, H, t = S["W"], S["L"], S["H"], S.get("t") or 1000
    return [{"p": "pool_box", "min": [x - W / 2, y - L / 2, z], "max": [x + W / 2, y + L / 2, z + H], "t": t, "mat": "concrete"},
            {"p": "box", "min": [x - W / 2 + t, y - L / 2 + t, z + H - t], "max": [x + W / 2 - t, y + L / 2 - t, z + H], "mat": "concrete", "part": "천장 슬래브"},
            {"p": "box", "min": [x - (W - 2 * t) * 0.3, y - (L - 2 * t) * 0.15, z + t], "max": [x + (W - 2 * t) * 0.3, y + (L - 2 * t) * 0.25, z + t + 900],
             "mat": "steel", "part": "작업대"}]


DETAIL = {"core": core, "rpv": vessel, "guard_vessel": vessel, "steam_generator": steam_generator, "pump": pump, "circulator": pump,
          "pressurizer": pressurizer, "ihx": ihx, "heat_exchanger": capsule, "tank": capsule, "containment": containment, "pool": pool,
          "roof_deck": roof_deck, "reflector": reflector, "cell": cell}
