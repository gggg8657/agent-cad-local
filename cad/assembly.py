"""ASSEMBLY_3D (계통 조립) — MSR 개념설계: 설계 입력 IR → 공학 사이징 → 배치 → 계통 연결 → 규칙 → GT 채점.

LLM 은 '어떤 기기가 몇 개, 설계 조건(열출력·염·ΔT…)'만 정한다. 크기·좌표·배관은 여기서 결정론적으로 계산하고,
사이징 근거와 가정은 전부 model["calc"]·model["assumptions"] 에 남긴다. 3D 솔리드·단면도는 worker_asm(서브프로세스, OCC).

형상 기술자 (worker_asm 이 build123d 로 만든다, 단위 mm, Z 위):
  {"p":"cyl","c":[x,y,z0],"d":D,"h":H}          z0 부터 위로
  {"p":"tube","c":[x,y,z0],"d":D,"id":d,"h":H}
  {"p":"head","c":[x,y,z],"d":D,"h":h,"up":bool}  타원형 경판 (반타원체)
  {"p":"box","min":[..],"max":[..]}
  {"p":"seg","a":[x,y,z],"b":[x,y,z],"d":D}      두 점 사이 원통 (배관·노즐)
  {"p":"bottle","zb":..,"zs":..,"zt":..,"R":..,"Rn":..,"t":..}  병 모양 용기 (외형 회전체 − 내부)
"""
import copy
import math

from .util import num, resource

LIB = resource("library", "msr.json")
COMP = LIB["components"]
D0 = LIB["defaults"]
ALIAS = {"vessel": "reactor_vessel", "reactor": "reactor_vessel", "pump": "primary_pump", "fuel_pump": "primary_pump", "hx": "heat_exchanger",
         "phx": "heat_exchanger", "helium_pump": "he_pump", "off_gas": "he_pump", "drain_tank": "dump_tank", "tank": "dump_tank",
         "freeze_valve": "freeze_plug", "shield": "shield_cell", "containment": "shield_cell", "chimney": "riser", "upper_plenum": "riser"}


def _t(x):
    x = str(x or "").lower()
    return ALIAS.get(x, x)


# ── 1. 정규화 ─────────────────────────────────────────────────────────────
def normalize(ir):
    errs = []
    ir = copy.deepcopy(ir or {})
    ir["kind"] = "assembly"
    ir.setdefault("domain", "msr")
    ir.setdefault("name", "MSR 개념설계")
    d = dict(ir.get("design") or {})
    for k in ("power_mwth", "core_dT", "power_density_mw_m3", "salt_fraction", "hx_U", "hx_dTlm", "pipe_velocity", "drain_margin"):
        if k in d:
            d[k] = num(d[k])
            if d[k] is None or d[k] <= 0:
                errs.append(f"design.{k} 는 양수")
    if d.get("salt") and d["salt"] not in LIB["salts"]:
        errs.append(f"design.salt {d['salt']!r} 미지원 ({', '.join(LIB['salts'])})")
    ir["design"] = d
    comps = []
    for i, c in enumerate(ir.get("components") or []):
        c = dict(c)
        c["type"] = _t(c.get("type"))
        if c["type"] not in COMP or c["type"] == "pipe":
            errs.append(f"components[{i}]: type {c.get('type')!r} 미지원 ({', '.join(k for k in COMP if k != 'pipe')})")
            continue
        c["count"] = max(1, int(num(c.get("count"), 1)))
        for k in ("D", "H"):
            if k in c:
                c[k] = num(c[k])
        comps.append(c)
    have = {c["type"] for c in comps}
    for must in ("core", "reactor_vessel"):
        if must not in have:
            errs.append(f"components 에 {must} 가 없음 (필수)")
    ir["components"] = comps
    ir.setdefault("arrangement", {})
    return ir, errs


def _count(ir, t):
    return sum(c["count"] for c in ir["components"] if c["type"] == t)


def _over(ir, t):
    return next((c for c in ir["components"] if c["type"] == t), {})


# ── 2. 사이징 ─────────────────────────────────────────────────────────────
def size(ir):
    d = {**D0, **{k: v for k, v in ir["design"].items() if v is not None}}
    salt = LIB["salts"][d["salt"]]
    calc, A = {}, []
    P = d["power_mwth"]
    V = P / d["power_density_mw_m3"]
    core = _over(ir, "core")
    if core.get("D") and core.get("H"):
        Dc, Hc = core["D"], core["H"]
        A.append(f"노심 치수 지정값 사용 D{Dc:.0f}×H{Hc:.0f} mm")
    else:
        Dm = (4 * V / (math.pi * d["core_hd"])) ** (1 / 3)
        Dc, Hc = Dm * 1000, Dm * d["core_hd"] * 1000
    Vc = math.pi * (Dc / 2000) ** 2 * (Hc / 1000)
    calc["core"] = {"V_m3": round(Vc, 3), "D_mm": round(Dc), "H_mm": round(Hc), "q_MW_m3": round(P / Vc, 2),
                    "식": "V = P / q''' , D = (4V/(π·H/D))^(1/3)"}
    inv = Vc * d["salt_fraction"] * (1 + d["loop_inventory_factor"])
    calc["salt"] = {"name": salt["label"], "inventory_m3": round(inv, 3), "mass_t": round(inv * salt["rho"] / 1000, 2),
                    "식": f"노심 염 = V×{d['salt_fraction']}, 루프 계수 {d['loop_inventory_factor']}"}
    m = P * 1e6 / (salt["cp"] * d["core_dT"])
    Q = m / salt["rho"]
    npump = _count(ir, "primary_pump") or 0
    nhx = _count(ir, "heat_exchanger") or 0
    qp = Q / max(npump, 1)
    dp = math.sqrt(4 * qp / (math.pi * d["pipe_velocity"])) * 1000
    calc["flow"] = {"m_kg_s": round(m, 1), "Q_m3_s": round(Q, 4), "per_pump_m3_s": round(qp, 4), "pipe_d_mm": round(dp),
                    "식": f"ṁ = P/(cp·ΔT), ΔT={d['core_dT']} K, 배관 유속 {d['pipe_velocity']} m/s"}
    Ahx = P * 1e6 / (d["hx_U"] * d["hx_dTlm"])
    Lhx = Hc * d["hx_length_ratio"]
    hx = _over(ir, "heat_exchanger")
    if nhx:
        Ah = Ahx / nhx
        dt = d["hx_tube_od"] / 1000
        nt = math.ceil(Ah / (math.pi * dt * Lhx / 1000))
        pitch = dt * d["hx_pitch_ratio"]
        Dhx = math.sqrt(4 * nt * 0.866 * pitch ** 2 / 0.7 / math.pi) * 1000 + 100
        if hx.get("D"):
            Dhx = hx["D"]
        if hx.get("H"):
            Lhx = hx["H"]
        calc["hx"] = {"A_total_m2": round(Ahx, 1), "A_each_m2": round(Ah, 1), "tubes_each": nt, "shell_D_mm": round(Dhx), "L_mm": round(Lhx),
                      "식": f"A = P/(U·ΔTlm), U={d['hx_U']} W/m²K, ΔTlm={d['hx_dTlm']} K, 관 Ø{d['hx_tube_od']}"}
    else:
        Dhx = 0
    ndt = _count(ir, "dump_tank")
    if ndt:
        Vt = inv * d["drain_margin"] / ndt
        Dt = (4 * Vt / (math.pi * d["tank_hd"])) ** (1 / 3) * 1000
        Ht = Dt * d["tank_hd"]
        dtk = _over(ir, "dump_tank")
        if dtk.get("D") and dtk.get("H"):
            Dt, Ht = dtk["D"], dtk["H"]
            A.append("덤프탱크 치수 지정값 사용")
        Vt = math.pi * (Dt / 2000) ** 2 * (Ht / 1000)
        calc["drain"] = {"tank_V_m3": round(Vt, 3), "tanks": ndt, "capacity_m3": round(Vt * ndt, 3), "margin": d["drain_margin"],
                         "D_mm": round(Dt), "H_mm": round(Ht), "식": "Σ탱크 ≥ 여유 × 전체 염 인벤토리"}
    else:
        Dt = Ht = 0
    cas = max(3 * dp, 300)
    calc["pump"] = {"casing_D_mm": round(cas), "motor_D_mm": round(cas * 1.3), "식": "케이싱 D = max(3·배관 d, 300)"}
    return d, calc, A, {"Dc": Dc, "Hc": Hc, "dp": dp, "Dhx": Dhx, "Lhx": Lhx, "Dt": Dt, "Ht": Ht, "cas": cas, "inv": inv}


# ── 3. 배치 ───────────────────────────────────────────────────────────────
def _bbox(shapes):
    if not shapes:
        return [0, 0, 0, 0, 0, 0]
    xs, ys, zs = [], [], []
    for s in shapes:
        p = s["p"]
        if p in ("cyl", "tube", "head"):
            x, y, z = s["c"]
            r = s["d"] / 2
            xs += [x - r, x + r]
            ys += [y - r, y + r]
            zs += [z, z + s["h"]] if p != "head" else ([z, z + s["h"]] if s.get("up", True) else [z - s["h"], z])
        elif p == "box":
            xs += [s["min"][0], s["max"][0]]
            ys += [s["min"][1], s["max"][1]]
            zs += [s["min"][2], s["max"][2]]
        elif p == "seg":
            r = s["d"] / 2
            for q in (s["a"], s["b"]):
                xs += [q[0] - r, q[0] + r]
                ys += [q[1] - r, q[1] + r]
                zs += [q[2] - r, q[2] + r]
        elif p == "bottle":
            xs += [-s["R"], s["R"]]
            ys += [-s["R"], s["R"]]
            zs += [s["zb"], s["zt"]]
    return [round(v, 1) for v in (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))]


def build_model(ir):
    d, calc, A, z = size(ir)
    g = d["gap"]
    model = {"kind": "assembly", "name": ir["name"], "domain": "msr", "design": d, "calc": calc, "assumptions": A, "components": [], "flows": [],
             "notes": list(ir.get("notes") or [])}
    comps = model["components"]

    def add(t, i, shapes, **kw):
        cid = f"{t.upper().replace('_', '-')}-{i + 1:02d}"
        c = {"id": cid, "type": t, "label": COMP[t]["label"] + (f" {i + 1}" if _count(ir, t) > 1 else ""), "shapes": shapes, "bbox": _bbox(shapes),
             "rgb": COMP[t]["rgb"], "alpha": COMP[t]["alpha"], "material": COMP[t]["material"], "role": COMP[t]["role"],
             "mat": {"core": "salt", "reflector": "graphite", "heat_exchanger": "tubes", "shield_cell": "concrete"}.get(t, "steel"), **kw}
        comps.append(c)
        return c
    Dc, Hc = z["Dc"], z["Hc"]
    core = add("core", 0, [{"p": "cyl", "c": [0, 0, 0], "d": Dc, "h": Hc}])
    Rout = Dc / 2
    if _count(ir, "reflector"):
        t = d["reflector_t"]
        add("reflector", 0, [{"p": "tube", "c": [0, 0, 0], "d": Dc + 2 * t, "id": Dc + 10, "h": Hc}])
        Rout += t
    nhx, npump = _count(ir, "heat_exchanger"), _count(ir, "primary_pump")
    Dr = max(z["dp"] * 1.6, 200)
    z_top_core = Hc + d["plenum"]
    riser = None
    if _count(ir, "riser"):
        riser = add("riser", 0, [], role_note="노심 출구 → 펌프 흡입")
    # 열교환기 링
    Rhx = max(z["Dhx"] / 2 + Dr / 2 + g, Rout - z["Dhx"] / 2) if nhx else 0
    angs = [180 + 360 * k / max(nhx, 1) for k in range(nhx)]  # 2기면 좌(180°)·우(0°)
    hxs = []
    for k, a in enumerate(angs):
        x, y = Rhx * math.cos(math.radians(a)), Rhx * math.sin(math.radians(a))
        z0 = z_top_core + g
        L = z["Lhx"]
        sh = [{"p": "cyl", "c": [x, y, z0], "d": z["Dhx"], "h": L}, {"p": "head", "c": [x, y, z0 + L], "d": z["Dhx"], "h": z["Dhx"] * 0.25, "up": True},
              {"p": "head", "c": [x, y, z0], "d": z["Dhx"], "h": z["Dhx"] * 0.25, "up": False}]
        hxs.append(add("heat_exchanger", k, sh, angle=a, z0=z0, L=L, xy=[x, y]))
    zc_top = (z_top_core + g + z["Lhx"] + z["Dhx"] * 0.25) if nhx else z_top_core + g
    # 펌프 (각 열교환기 위, 없으면 링 반경)
    cas = z["cas"]
    pumps = []
    pang = angs if npump == nhx and nhx else [180 + 360 * k / max(npump, 1) for k in range(npump)]
    Rp = Rhx if nhx else Rout * 0.6
    z_cas0 = zc_top + g
    z_cas1 = z_cas0 + cas * 1.2
    zs = z_cas1 + d["gas_plenum"]  # 어깨 (용기 상부 헤드 시작)
    for k, a in enumerate(pang):
        pumps.append((k, a, Rp * math.cos(math.radians(a)), Rp * math.sin(math.radians(a))))
    # 용기 (병 모양)
    Rv_need = max(Rout, Rhx + z["Dhx"] / 2 if nhx else 0, Rp + cas / 2 if npump else 0) + g
    Rn = max((Rp + cas * 0.25 + g) if npump else Rv_need * 0.6, Rv_need * 0.55)
    Rn = min(Rn, Rv_need)
    tw = d["vessel_wall"]
    zb = -d["plenum"] - tw
    zt = zs + (Rv_need - Rn) * 0.8 + 500
    vessel = add("reactor_vessel", 0, [{"p": "bottle", "zb": zb, "zs": zs, "zt": zt, "R": Rv_need + tw, "Rn": Rn + tw, "t": tw}],
                 R=Rv_need + tw, Rn=Rn + tw, zb=zb, zt=zt)
    for k, a, x, y in pumps:
        mD = cas * 1.3
        sh = [{"p": "cyl", "c": [x, y, z_cas0], "d": cas, "h": cas * 1.2},
              {"p": "cyl", "c": [x, y, z_cas1], "d": cas * 0.25, "h": zt - z_cas1 + 300},
              {"p": "cyl", "c": [x, y, zt + 300], "d": mD, "h": mD * 1.5}]
        add("primary_pump", k, sh, angle=a, xy=[x, y])
    if riser is not None:
        riser["shapes"] = [{"p": "cyl", "c": [0, 0, Hc], "d": Dr, "h": z_cas0 + cas * 0.6 - Hc}]
        riser["bbox"] = _bbox(riser["shapes"])
    if _count(ir, "he_pump"):
        hd = max(cas * 0.6, 250)
        add("he_pump", 0, [{"p": "cyl", "c": [0, 0, zs], "d": hd * 0.3, "h": zt - zs + 300}, {"p": "cyl", "c": [0, 0, zt + 300], "d": hd, "h": hd * 1.6}])
    # 배수 계통
    zfp1 = zb - 300
    fpL = 400
    dfp = max(z["dp"] * 0.5, 60)
    if _count(ir, "freeze_plug"):
        add("freeze_plug", 0, [{"p": "cyl", "c": [0, 0, zfp1 - fpL], "d": dfp, "h": fpL},
                               {"p": "tube", "c": [0, 0, zfp1 - fpL * 0.8], "d": dfp * 2.6, "id": dfp * 1.1, "h": fpL * 0.6}])
        add_pipe(model, "drain", [0, 0, zb], [0, 0, zfp1], dfp)
    ndt = _count(ir, "dump_tank")
    zh = zfp1 - fpL - 400
    if ndt:
        Dt, Ht = z["Dt"], z["Ht"]
        grid = (ir.get("arrangement") or {}).get("dump_grid") or ([2, 2] if ndt == 4 else [ndt, 1])
        nx, ny = int(grid[0]), int(grid[1]) if len(grid) > 1 else 1
        pitch = Dt + 2 * g
        ztt = zh - 400
        for k in range(ndt):
            ix, iy = k % nx, (k // nx) % max(ny, 1)
            x, y = (ix - (nx - 1) / 2) * pitch, (iy - (ny - 1) / 2) * pitch
            hh = Dt * 0.25
            add("dump_tank", k, [{"p": "cyl", "c": [x, y, ztt - Ht], "d": Dt, "h": Ht}, {"p": "head", "c": [x, y, ztt], "d": Dt, "h": hh, "up": True},
                                 {"p": "head", "c": [x, y, ztt - Ht], "d": Dt, "h": hh, "up": False}], xy=[x, y])
            add_pipe(model, "drain", [0, 0, zfp1 - fpL], [0, 0, zh], dfp * 0.8) if k == 0 else None
            add_pipe(model, "drain", [0, 0, zh], [x, 0, zh], dfp * 0.8)
            add_pipe(model, "drain", [x, 0, zh], [x, y, zh], dfp * 0.8)
            add_pipe(model, "drain", [x, y, zh], [x, y, ztt + hh], dfp * 0.8)
    # 2차 계통 노즐 (열교환기 → 용기 밖)
    for hx in hxs:
        x, y = hx["xy"]
        ux, uy = math.cos(math.radians(hx["angle"])), math.sin(math.radians(hx["angle"]))
        for zz in (hx["z0"] + hx["L"] * 0.85, hx["z0"] + hx["L"] * 0.15):
            add_pipe(model, "secondary", [x + ux * z["Dhx"] / 2, y + uy * z["Dhx"] / 2, zz],
                     [ux * (vessel["R"] + 700), uy * (vessel["R"] + 700), zz], max(z["dp"] * 0.8, 80), of=hx["id"])
    # 차폐 구조 (L자: 뒷벽 + 왼쪽 벽 + 바닥) — 앞·오른쪽은 열어 둠 (그림의 외관과 동일)
    if _count(ir, "shield_cell"):
        eq = [c["bbox"] for c in comps]
        x0, y0, z0 = min(b[0] for b in eq) - 800, min(b[1] for b in eq) - 800, min(b[2] for b in eq) - 300
        x1, y1, z1 = max(b[3] for b in eq) + 800, max(b[4] for b in eq) + 800, vessel["zb"] + (zs - vessel["zb"]) * 0.75
        t = d["shield_t"]
        add("shield_cell", 0, [{"p": "box", "min": [x0 - t, y1, z0 - t], "max": [x1, y1 + t, z1]},
                               {"p": "box", "min": [x0 - t, y0, z0 - t], "max": [x0, y1, z1]},
                               {"p": "box", "min": [x0 - t, y0, z0 - t], "max": [x1, y1, z0]}])
    # 계통 흐름 (논리)
    F = model["flows"]
    ids = lambda t: [c["id"] for c in comps if c["type"] == t]
    P_, H_, R_ = ids("primary_pump"), ids("heat_exchanger"), ids("riser")
    for r in R_:
        F.append({"from": core["id"], "to": r, "loop": "fuel"})
    for i, p in enumerate(P_):
        F.append({"from": R_[0] if R_ else core["id"], "to": p, "loop": "fuel"})
        if H_:
            F.append({"from": p, "to": H_[i % len(H_)], "loop": "fuel"})
    for h in H_:
        F.append({"from": h, "to": core["id"], "loop": "fuel"})
        F.append({"from": h, "to": "SECONDARY", "loop": "secondary"})
    for f in ids("freeze_plug"):
        F.append({"from": core["id"], "to": f, "loop": "drain"})
        for t in ids("dump_tank"):
            F.append({"from": f, "to": t, "loop": "drain"})
    for h in ids("he_pump"):
        F.append({"from": h, "to": vessel["id"], "loop": "offgas"})
    eqb = [c["bbox"] for c in comps if c["type"] not in ("shield_cell",) and COMP[c["type"]]["role"] != "pipe"]
    model["extent"] = [min(b[0] for b in eqb), min(b[1] for b in eqb), min(b[2] for b in eqb), max(b[3] for b in eqb), max(b[4] for b in eqb), max(b[5] for b in eqb)]
    return model


def add_pipe(model, loop, a, b, dmm, of=None):
    n = sum(1 for c in model["components"] if c["type"] == "pipe") + 1
    c = {"id": f"PIPE-{n:02d}", "type": "pipe", "label": {"drain": "배수관", "secondary": "2차 계통 노즐", "fuel": "연료염 배관"}.get(loop, "배관"),
         "loop": loop, "shapes": [{"p": "seg", "a": a, "b": b, "d": dmm}], "rgb": COMP["pipe"]["rgb"], "alpha": 1.0, "material": COMP["pipe"]["material"],
         "role": "pipe", "of": of}
    c["bbox"] = _bbox(c["shapes"])
    model["components"].append(c)
    return c


# ── 4. 규칙 (OCC 불필요한 것) ─────────────────────────────────────────────
def _inside(a, b, tol=1.0):
    return a[0] >= b[0] - tol and a[1] >= b[1] - tol and a[2] >= b[2] - tol and a[3] <= b[3] + tol and a[4] <= b[4] + tol and a[5] <= b[5] + tol


def validate(model, rules=None):
    rules = rules or resource("rules", "msr.json")
    R = {r["id"]: r for r in rules["rules"] if r.get("enabled", True)}
    out = []

    def add(rid, msg, sev=None):
        if rid in R:
            out.append({"id": rid, "name": R[rid]["name"], "severity": sev or R[rid]["severity"], "msg": msg, "repairable": bool(R[rid].get("repairable"))})
    C = model["components"]
    by = lambda t: [c for c in C if c["type"] == t]
    calc = model["calc"]
    if "drain" in calc:
        need = calc["salt"]["inventory_m3"] * R.get("MSR-001", {}).get("margin", 1.0)
        if calc["drain"]["capacity_m3"] < need - 1e-6:
            add("MSR-001", f"덤프탱크 총용량 {calc['drain']['capacity_m3']} m³ < 필요 {need:.3f} m³ (염 {calc['salt']['inventory_m3']} m³)")
    else:
        add("MSR-001", "덤프탱크 없음 — 비상 배수 불가", "error")
    core = by("core")[0]
    for f in by("freeze_plug"):
        v = by("reactor_vessel")[0]["bbox"]
        if f["bbox"][5] > v[2] + 1:
            add("MSR-002", f"{f['id']} 상단이 용기 바닥보다 높음 — 중력 배수 불가")
        for t in by("dump_tank"):
            if t["bbox"][5] > f["bbox"][2]:
                add("MSR-002", f"{t['id']} 상단({t['bbox'][5]:.0f})이 동결 플러그 하단({f['bbox'][2]:.0f})보다 높음 — 중력 배수 불가")
    if not by("freeze_plug") and by("dump_tank"):
        add("MSR-002", "동결 플러그가 없어 수동 배수 경계 없음", "warning")
    np_, nh = len(by("primary_pump")), len(by("heat_exchanger"))
    if np_ != nh:
        add("MSR-004", f"펌프 {np_}대 · 열교환기 {nh}기 — 루프 짝이 맞지 않음")
    if np_ < 1 or nh < 1:
        add("MSR-004", "펌프 또는 열교환기 없음 — 열 제거 경로 없음", "error")
    for v in by("reactor_vessel"):
        for t in ("core", "reflector", "heat_exchanger", "riser"):
            for c in by(t):
                if not _inside(c["bbox"], v["bbox"]):
                    add("MSR-005", f"{c['id']} 가 원자로 용기 밖으로 나감")
    if not by("he_pump"):
        add("MSR-006", "He 펌프(오프가스·기포 제거) 없음 — 핵분열 기체(Xe/Kr) 제거 계통 필요")
    if "hx" in calc:
        if calc["hx"]["A_each_m2"] * max(nh, 1) < calc["hx"]["A_total_m2"] - 1e-6:
            add("MSR-007", "열교환기 전열면적 부족")
    if calc["flow"]["pipe_d_mm"] and calc["flow"]["per_pump_m3_s"] / (math.pi * (calc["flow"]["pipe_d_mm"] / 2000) ** 2) > R.get("MSR-008", {}).get("max_v", 5):
        add("MSR-008", "배관 유속 과다")
    sh = by("shield_cell")
    if not sh:
        add("MSR-009", "차폐 구조 없음")
    # 간섭 (bbox 1차) — 정밀 판정은 worker_asm 의 솔리드 교차
    eq = [c for c in C if COMP[c["type"]]["role"] == "equipment"]
    for i in range(len(eq)):
        for j in range(i + 1, len(eq)):
            a, b = eq[i]["bbox"], eq[j]["bbox"]
            if all(a[k] < b[k + 3] - 1 and b[k] < a[k + 3] - 1 for k in range(3)) and {eq[i]["type"], eq[j]["type"]} not in ({"core", "reflector"}, {"riser", "core"}):
                model.setdefault("_overlap_pairs", []).append([eq[i]["id"], eq[j]["id"]])
    return out


# ── 5. GT 채점 ────────────────────────────────────────────────────────────
def _c(b):
    return [(b[0] + b[3]) / 2, (b[1] + b[4]) / 2, (b[2] + b[5]) / 2]


def gt_score(model, gt):
    C = [c for c in model["components"] if c["type"] != "pipe"]
    types = {}
    for c in C:
        types.setdefault(c["type"], []).append(c)
    rep = {"gt": gt["id"], "name": gt["name"], "items": []}
    # 1) 구성
    matched = sum(min(n, len(types.get(t, []))) for t, n in gt["components"].items())
    gt_n = sum(gt["components"].values())
    ag_n = sum(len(v) for t, v in types.items() if t not in gt.get("neutral", []))
    P = matched / ag_n if ag_n else 0
    Rc = matched / gt_n if gt_n else 0
    f1 = 2 * P * Rc / (P + Rc) if P + Rc else 0
    rep["components"] = {"f1": round(f1, 3), "precision": round(P, 3), "recall": round(Rc, 3),
                         "diff": {t: [n, len(types.get(t, []))] for t, n in gt["components"].items() if len(types.get(t, [])) != n} |
                         {t: [0, len(v)] for t, v in types.items() if t not in gt["components"] and t not in gt.get("neutral", [])}}
    # 2) 흐름 (종류 수준)
    T = {c["id"]: c["type"] for c in model["components"]}
    ag = {(T.get(f["from"], f["from"]), T.get(f["to"], f["to"]), f["loop"]) for f in model["flows"] if f["to"] != "SECONDARY"}
    gs = {tuple(f) for f in gt["flows"]}
    tp = len(ag & gs)
    fp_ = len(ag) - tp
    fn = len(gs) - tp
    ff1 = 2 * tp / (2 * tp + fp_ + fn) if tp else 0
    rep["flows"] = {"f1": round(ff1, 3), "missing": sorted(map(list, gs - ag)), "extra": sorted(map(list, ag - gs))}
    # 3) 관계
    ok_n = 0
    for r in gt["relations"]:
        A = types.get(r["a"], [])
        B = types.get(r.get("b"), []) if r.get("b") else []
        rel = r["rel"]
        ok, why = False, ""
        if not A or (r.get("b") and not B):
            why = "대상 없음"
        elif rel in ("above", "below"):
            za = [_c(a["bbox"])[2] for a in A]
            zb = [_c(b["bbox"])[2] for b in B]
            ok = min(za) > max(zb) if rel == "above" else max(za) < min(zb)
            why = f"z {min(za):.0f}~{max(za):.0f} vs {min(zb):.0f}~{max(zb):.0f}"
        elif rel == "coaxial":
            a, b = _c(A[0]["bbox"]), _c(B[0]["bbox"])
            w = max(B[0]["bbox"][3] - B[0]["bbox"][0], 1)
            ok = math.hypot(a[0] - b[0], a[1] - b[1]) < 0.1 * w
            why = f"축 거리 {math.hypot(a[0] - b[0], a[1] - b[1]):.0f} mm"
        elif rel == "symmetric":
            cs = [_c(a["bbox"]) for a in A]
            m = [sum(c[0] for c in cs) / len(cs), sum(c[1] for c in cs) / len(cs)]
            ext = max(model["extent"][3] - model["extent"][0], 1)
            ok = len(A) >= 2 and math.hypot(*m) < 0.05 * ext
            why = f"{len(A)}개, 무게중심 편차 {math.hypot(*m):.0f} mm"
        elif rel == "same_azimuth":
            ang = lambda c: math.degrees(math.atan2(_c(c["bbox"])[1], _c(c["bbox"])[0]))
            ok = all(any(abs((ang(a) - ang(b) + 180) % 360 - 180) < 10 for b in B) for a in A)
            why = "방위각 일치" if ok else "방위각 불일치"
        elif rel == "inside":
            ok = all(_inside(a["bbox"], B[0]["bbox"], 5) for a in A)
            why = "포함" if ok else "일부가 밖"
        elif rel == "grid":
            xs = sorted({round(_c(a["bbox"])[0]) for a in A})
            ys = sorted({round(_c(a["bbox"])[1]) for a in A})
            ok = [len(xs), len(ys)] == list(r["n"]) or [len(ys), len(xs)] == list(r["n"])
            why = f"{len(xs)}×{len(ys)}"
        ok_n += ok
        rep["items"].append({"kind": "relation", "rel": rel, "a": r["a"], "b": r.get("b"), "ok": bool(ok), "detail": why, "why": r.get("why", "")})
    rep["relations"] = {"score": round(ok_n / max(len(gt["relations"]), 1), 3), "ok": ok_n, "total": len(gt["relations"])}
    # 4) 비율
    core = types["core"][0]["bbox"] if types.get("core") else None
    pk = 0
    for p in gt["proportions"]:
        num_, _, den = p["what"].partition(" / ")
        val = None
        if core:
            Hc = core[5] - core[2]
            e = model["extent"]
            if num_ == "assembly_height":
                val = (e[5] - e[2]) / Hc
            elif num_ == "assembly_width":
                val = (e[3] - e[0]) / Hc
            else:
                t = num_.split(".")[0]
                if types.get(t):
                    bb = types[t][0]["bbox"]
                    val = (bb[5] - bb[2]) / Hc
        ok = val is not None and abs(val - p["value"]) / p["value"] <= p["tol"]
        pk += ok
        rep["items"].append({"kind": "proportion", "what": p["what"], "gt": p["value"], "got": round(val, 2) if val else None, "tol": p["tol"], "ok": bool(ok)})
    rep["proportions"] = {"score": round(pk / max(len(gt["proportions"]), 1), 3), "ok": pk, "total": len(gt["proportions"])}
    w = gt["weights"]
    rep["total"] = round(w["components"] * f1 + w["flows"] * ff1 + w["relations"] * rep["relations"]["score"] + w["proportions"] * rep["proportions"]["score"], 3)
    return rep


def gt_markdown(rep):
    L = [f"## GT 비교 — {rep['name']}", "", f"**종합 {rep['total'] * 100:.0f}점** (구성 F1 {rep['components']['f1']:.2f} · 흐름 F1 {rep['flows']['f1']:.2f} · "
         f"배치 관계 {rep['relations']['ok']}/{rep['relations']['total']} · 비율 {rep['proportions']['ok']}/{rep['proportions']['total']})", ""]
    if rep["components"]["diff"]:
        L.append("- 구성 차이 (GT, 설계): " + ", ".join(f"{t} {a}→{b}" for t, (a, b) in rep["components"]["diff"].items()))
    if rep["flows"]["missing"]:
        L.append("- 빠진 흐름: " + ", ".join("→".join(f[:2]) + f"({f[2]})" for f in rep["flows"]["missing"]))
    if rep["flows"]["extra"]:
        L.append("- GT에 없는 흐름: " + ", ".join("→".join(f[:2]) + f"({f[2]})" for f in rep["flows"]["extra"]))
    L += ["", "| 항목 | 기준 | 결과 | 판정 |", "|---|---|---|---|"]
    for it in rep["items"]:
        if it["kind"] == "relation":
            L.append(f"| {it['rel']} | {it['a']}{' / ' + it['b'] if it['b'] else ''} {('— ' + it['why']) if it['why'] else ''} | {it['detail']} | {'✔' if it['ok'] else '✘'} |")
        else:
            L.append(f"| 비율 | {it['what']} = {it['gt']} ±{it['tol'] * 100:.0f}% | {it['got']} | {'✔' if it['ok'] else '✘'} |")
    return "\n".join(L)
