"""LAYOUT_2D / SYSTEM_OVERVIEW — ICT 요구 IR → 설계 모델(공간·장비·랙·결선·경로) → 규칙 검증 · 일람표.

LLM은 '무엇이 어디에 몇 대'만 말한다. 좌표·ID·결선·케이블 길이는 전부 여기서 결정론적으로 만든다.
엔진이 스스로 채운 것은 model["assumptions"] 에 남겨 보고서에 그대로 노출한다 (Human-in-the-loop).
"""
import copy
import math

from .util import num, resource

LIB = resource("library", "ict.json")
SYM = LIB["symbols"]
STYPES = LIB["space_types"]
TYPE_ALIAS = {"cctv": "cctv_fixed", "camera": "cctv_fixed", "fixed_cctv": "cctv_fixed", "ptz": "cctv_ptz", "wifi": "ap", "wifi_ap": "ap",
              "wap": "ap", "emergency_phone": "ephone", "비상통화장치": "ephone", "phone": "ephone", "display": "pids", "speaker_pa": "speaker",
              "switch": "access_sw", "l2": "access_sw", "access_switch": "access_sw", "edge_sw": "access_sw", "core": "core_sw", "l3": "core_sw",
              "core_switch": "core_sw", "server": "vms_server", "vms": "vms_server", "nvr_server": "nvr", "patch": "patch_panel"}
SPACE_ALIAS = {"communication_room": "comm_room", "통신실": "comm_room", "tps": "idf", "통신분기실": "idf", "승강장": "platform", "대합실": "concourse",
               "역무실": "office", "전기실": "elec_room", "기계실": "machine_room", "hall": "concourse", "lobby": "concourse", "factory": "factory_floor"}
RACK_U = LIB["rack"]["units"]
RW, RD = LIB["rack"]["footprint"]


def _role(t):
    return SYM[t]["role"]


# ── 1. 정규화 ─────────────────────────────────────────────────────────────
def normalize(ir):
    errs = []
    ir = copy.deepcopy(ir or {})
    ir["kind"] = "ict"
    ir.setdefault("name", "ICT 배치 초안")
    floors = ir.get("floors") or []
    if not floors:
        errs.append("floors 가 비어 있음 (최소 1개 층과 공간 필요)")
    seen = set()
    for fi, f in enumerate(floors):
        f["id"] = str(f.get("id") or f"F{fi + 1}")
        f.setdefault("name", f["id"])
        f["height"] = num(f.get("height"), 5000)
        sp = []
        for si, s in enumerate(f.get("spaces") or []):
            s = dict(s)
            t = str(s.get("type") or "room").lower()
            t = SPACE_ALIAS.get(t, t)
            if t not in STYPES:
                t = "room"
            s["type"] = t
            s["id"] = str(s.get("id") or f"{f['id']}_{t.upper()}{si + 1}")
            if s["id"] in seen:
                errs.append(f"공간 id 중복: {s['id']}")
            seen.add(s["id"])
            s.setdefault("name", STYPES[t]["label"])
            if s.get("rect"):
                s["rect"] = [num(v, 0) for v in s["rect"]][:4]
            if s.get("size"):
                s["size"] = [num(v, 0) for v in s["size"]][:2]
            sp.append(s)
        f["spaces"] = sp
    ir["floors"] = floors
    eq = []
    space_ids = {s["id"] for f in floors for s in f["spaces"]}
    for i, e in enumerate(ir.get("equipment") or []):
        e = dict(e)
        t = str(e.get("type") or "").lower()
        t = TYPE_ALIAS.get(t, t)
        if t not in SYM:
            errs.append(f"equipment[{i}]: type {e.get('type')!r} 는 라이브러리에 없음 ({', '.join(SYM)})")
            continue
        e["type"] = t
        e["count"] = max(1, int(num(e.get("count"), 1)))
        if e.get("space") not in space_ids:
            errs.append(f"equipment[{i}] ({t}): space {e.get('space')!r} 가 floors.spaces 에 없음 (가능: {', '.join(sorted(space_ids))})")
            continue
        eq.append(e)
    ir["equipment"] = eq
    o = ir.setdefault("options", {})
    o.setdefault("dual_homing", True)
    o.setdefault("auto_access_switch", True)
    o.setdefault("auto_core", True)
    o.setdefault("auto_passive", True)
    return ir, errs


# ── 2. 공간 배치 ──────────────────────────────────────────────────────────
def layout_spaces(model):
    for f in model["floors"]:
        big = [s for s in f["spaces"] if not s.get("rect") and STYPES[s["type"]].get("big")]
        small = [s for s in f["spaces"] if not s.get("rect") and not STYPES[s["type"]].get("big")]
        y = 0
        for s in big:
            w, h = s.get("size") or STYPES[s["type"]]["size"]
            s["rect"] = [0, y, w, h]
            y += h + 4000
        top = max([s["rect"][1] + s["rect"][3] for s in f["spaces"] if s.get("rect")] or [0])
        idfs = [s for s in small if s["type"] == "idf"]
        main_big = max((s for s in f["spaces"] if s.get("rect") and STYPES[s["type"]].get("big")), key=lambda s: s["rect"][2], default=None)
        if idfs and main_big:  # 분기실은 대공간을 따라 균등 분산 (UTP 거리 최소화)
            ax, a0, b0, L, Wd = _axis(main_big["rect"])
            for i, s in enumerate(idfs):
                w, h = s.get("size") or STYPES["idf"]["size"]
                c = _pt(ax, a0 + L * (i + 0.5) / len(idfs), b0 + Wd + 1500 + (h if ax == "x" else w) / 2)
                s["rect"] = [c[0] - w / 2, c[1] - h / 2, w, h]
            small = [s for s in small if s["type"] != "idf"]
            top = max(top, max(s["rect"][1] + s["rect"][3] for s in idfs))
        x = 0
        for s in small:
            w, h = s.get("size") or STYPES[s["type"]]["size"]
            s["rect"] = [x, top + 2000, w, h]
            x += w + 1500
        if not f.get("riser"):
            f["riser"] = [-3000, top + 2000 if small else top / 2]
            model["assumptions"].append(f"{f['id']}: 수직 간선(TPS) 위치를 도면 좌측 ({f['riser'][0]:.0f}, {f['riser'][1]:.0f}) 로 가정")


def space_of(model, sid):
    for f in model["floors"]:
        for s in f["spaces"]:
            if s["id"] == sid:
                return f, s
    return None, None


def is_comm(s):
    return bool(STYPES[s["type"]].get("comm"))


# ── 3. 장비 확장 (자동 보충 포함) ─────────────────────────────────────────
def expand(ir):
    model = copy.deepcopy(ir)
    model["assumptions"] = []
    layout_spaces(model)
    floors = model["floors"]
    reqs = model["equipment"]
    opt = model["options"]
    A = model["assumptions"]
    comm_rooms = [s for f in floors for s in f["spaces"] if is_comm(s)]
    main = opt.get("main_room")
    main_room = next((s for s in comm_rooms if s["id"] == main), None) or next((s for s in comm_rooms if s["type"] == "comm_room"), None) \
        or (comm_rooms[0] if comm_rooms else None)
    if main_room is None:
        f0 = floors[0]
        main_room = {"id": f"{f0['id']}_COMM", "name": "통신실", "type": "comm_room"}
        w, h = STYPES["comm_room"]["size"]
        x = max([s["rect"][0] + s["rect"][2] for s in f0["spaces"] if s["rect"][1] > 0] or [0]) + 1500
        top = max(s["rect"][1] + s["rect"][3] for s in f0["spaces"] if s["type"] != "comm_room" and STYPES[s["type"]].get("big")) \
            if any(STYPES[s["type"]].get("big") for s in f0["spaces"]) else 0
        main_room["rect"] = [x if x > 1500 else 0, top + 2000, w, h]
        f0["spaces"].append(main_room)
        A.append(f"통신실이 없어 {f0['id']} 층에 통신실({main_room['id']})을 추가")
    model["main_room"] = main_room["id"]
    have = {t: sum(r["count"] for r in reqs if r["type"] == t) for t in SYM}
    # 코어
    if not have["core_sw"] and opt["auto_core"]:
        n = 2 if opt["dual_homing"] else 1
        reqs.append({"type": "core_sw", "space": main_room["id"], "count": n, "auto": True})
        A.append(f"코어 스위치 {n}대를 {main_room['name']}({main_room['id']})에 배치 (이중화={opt['dual_homing']})")
    # NVR
    cams = have["cctv_fixed"] + have["cctv_ptz"]
    if cams and not have["nvr"] and opt["auto_passive"]:
        n = math.ceil(cams / SYM["nvr"]["channels"])
        reqs.append({"type": "nvr", "space": main_room["id"], "count": n, "auto": True})
        A.append(f"CCTV {cams}대 → NVR {n}대(64ch) 추가")
    # 인스턴스 생성
    eqs, counters = [], {}
    for r in reqs:
        f, s = space_of(model, r["space"])
        sym = SYM[r["type"]]
        ids = list(r.get("ids") or [])
        for k in range(r["count"]):
            key = (sym["prefix"], f["id"])
            counters[key] = counters.get(key, 0) + 1
            eid = ids[k] if k < len(ids) else f"{sym['prefix']}-{f['id']}-{counters[key]:02d}"
            e = {"id": eid, "type": r["type"], "floor": f["id"], "space": s["id"], "auto": bool(r.get("auto"))}
            pos = (r.get("positions") or [None] * (k + 1))[k] if k < len(r.get("positions") or []) else None
            if pos:
                e["pos"] = [num(pos[0], 0), num(pos[1], 0)]
                e["fixed"] = True
            eqs.append(e)
    model["equipment"] = eqs
    place_endpoints(model)
    if not have["access_sw"] and opt["auto_access_switch"]:
        auto_access(model, counters)
    # 패시브 랙 장비 (ODF·패치패널·UPS)
    if opt["auto_passive"]:
        rooms = sorted({e["space"] for e in eqs if _role(e["type"]) in ("access", "core", "head")})
        extra = []
        for sid in rooms:
            f, s = space_of(model, sid)
            nsw = sum(1 for e in eqs if e["space"] == sid and e["type"] == "access_sw")
            for t, n in (("odf", 1), ("patch_panel", nsw), ("ups", 1)):
                for k in range(n):
                    key = (SYM[t]["prefix"], f["id"])
                    counters[key] = counters.get(key, 0) + 1
                    extra.append({"id": f"{SYM[t]['prefix']}-{f['id']}-{counters[key]:02d}", "type": t, "floor": f["id"], "space": sid, "auto": True})
        if extra:
            A.append(f"랙 실장 보조장비 자동 추가: ODF·패치패널(접속 스위치당 1)·UPS — {len(extra)}대")
        eqs.extend(extra)
    place_racks(model)
    connect(model)
    route(model)
    return model


# ── 4. 배치 ───────────────────────────────────────────────────────────────
def _instances(model, t, room, n, counters, auto=True):
    f, _ = space_of(model, room["id"])
    out = []
    for _ in range(n):
        key = (SYM[t]["prefix"], f["id"])
        counters[key] = counters.get(key, 0) + 1
        out.append({"id": f"{SYM[t]['prefix']}-{f['id']}-{counters[key]:02d}", "type": t, "floor": f["id"], "space": room["id"], "auto": auto})
    model["equipment"].extend(out)
    return out


def _mdist(p, rect):
    x, y, w, h = rect
    return abs(p[0] - (x + w / 2)) + abs(p[1] - (y + h / 2))


def auto_access(model, counters):
    """층별로 말단을 가장 가까운 통신실/분기실에 묶고 포트·PoE 기준 대수 산정. 통신실이 없으면 UTP 거리 기준으로 분기실 생성."""
    A = model["assumptions"]
    margin = model["options"].get("port_margin", 0.2)
    reach = resource("rules", "ict.json")["cable"].get("idf_reach_mm", 55000)
    per = math.floor(SYM["access_sw"]["ports"] * (1 - margin))
    for f in model["floors"]:
        ends = [e for e in model["equipment"] if e["floor"] == f["id"] and _role(e["type"]) == "endpoint" and e.get("pos")]
        if not ends:
            continue
        rooms = [s for s in f["spaces"] if is_comm(s)]
        added = 0
        w, h = STYPES["idf"]["size"]
        for big in [s for s in f["spaces"] if STYPES[s["type"]].get("big") and any(e["space"] == s["id"] for e in ends)] or \
                ([] if rooms else [max(f["spaces"], key=lambda s: s["rect"][2] * s["rect"][3])]):
            ax, a0, b0, L, Wd = _axis(big["rect"])
            k = max(1, math.ceil(L / (2 * reach)))
            for i in range(k):
                a = a0 + L * (i + 0.5) / k
                seg = _pt(ax, a, b0 + Wd / 2)
                if rooms and min(_mdist(seg, r["rect"]) for r in rooms) <= reach:
                    continue  # 기존 통신실이 이 구간을 커버
                added += 1
                c = _pt(ax, a, b0 + Wd + 1500 + (h if ax == "x" else w) / 2)
                room = {"id": f"{f['id']}_IDF{added}", "name": f"통신분기실{added}", "type": "idf", "rect": [c[0] - w / 2, c[1] - h / 2, w, h]}
                f["spaces"].append(room)
                rooms.append(room)
        if added:
            A.append(f"{f['id']}: 기존 통신실에서 UTP 90 m 안에 들지 않는 구간이 있어 통신분기실 {added}개 추가 (분기실당 커버 반경 {reach / 1000:.0f} m 가정)")
        groups = {r["id"]: [] for r in rooms}
        for e in ends:
            groups[min(rooms, key=lambda r: _mdist(e["pos"], r["rect"]))["id"]].append(e)
        for r in rooms:
            es = groups[r["id"]]
            if not es:
                continue
            poe = sum(SYM[e["type"]].get("poe_w", 0) for e in es)
            n = max(math.ceil(len(es) / per), math.ceil(poe / (SYM["access_sw"]["poe_budget_w"] * (1 - margin))))
            for sw in _instances(model, "access_sw", r, n, counters):
                pass
            for e in es:
                e["_room"] = r["id"]
            A.append(f"{f['id']}: 말단 {len(es)}포트·PoE {poe:.0f} W → 접속 스위치 {n}대 ({SYM['access_sw']['ports']}포트, 여유 {margin:.0%}) 를 {r['name']}({r['id']})에")


def _axis(rect):
    x, y, w, h = rect
    return ("x", x, y, w, h) if w >= h else ("y", y, x, h, w)  # (긴축, 긴축 시작, 짧은축 시작, 길이, 폭)


def _pt(ax, a, b):
    return [a, b] if ax == "x" else [b, a]


def place_endpoints(model):
    _place(model, endpoints=True)


def place_racks(model):
    model["racks"] = []
    _place(model, endpoints=False)


def _place(model, endpoints):
    by_space = {}
    for e in model["equipment"]:
        by_space.setdefault(e["space"], []).append(e)
    for sid, items in by_space.items():
        f, s = space_of(model, sid)
        ax, a0, b0, L, Wd = _axis(s["rect"])
        if not endpoints:
            _racks(model, f, s, items)
            continue
        groups = {}
        for e in items:
            if not e.get("fixed"):
                groups.setdefault(e["type"], []).append(e)
        for t, es in groups.items():
            n = len(es)
            role = _role(t)
            if role in ("access", "core", "head", "rack"):
                continue  # 랙에서 처리
            if t in ("cctv_fixed",):
                rows = [0.22, 0.78] if n > 1 else [0.5]
                per = math.ceil(n / len(rows))
                for i, e in enumerate(es):
                    row, k = i % len(rows), i // len(rows)
                    a = a0 + L * (k + 0.5 + (0.5 if row else 0)) / (per + 0.5)
                    e["pos"] = _pt(ax, a, b0 + Wd * rows[row])
                    fwd = 0 if (k + row) % 2 == 0 else 180
                    e["rot"] = fwd if ax == "x" else fwd + 90
            elif t == "ap" or t == "speaker":
                nx = max(1, round(math.sqrt(n * L / Wd)))
                nx = min(nx, n)
                ny = math.ceil(n / nx)
                for i, e in enumerate(es):
                    ix, iy = i % nx, i // nx
                    cnt_row = min(nx, n - iy * nx)
                    a = a0 + L * (ix + 0.5) / cnt_row
                    b = b0 + Wd * (iy + 0.5) / ny
                    if t == "speaker":
                        a += L / cnt_row * 0.25
                    e["pos"] = _pt(ax, a, b)
            else:
                off = {"ephone": 0.9, "pids": 0.64, "cctv_ptz": 0.36, "access_control": 0.08}.get(t, 0.5)
                for i, e in enumerate(es):
                    a = a0 + L * (i + 0.5) / n
                    e["pos"] = _pt(ax, a, b0 + Wd * off)


def _racks(model, f, s, items):
    sid = s["id"]
    rackitems = [e for e in items if _role(e["type"]) in ("access", "core", "head", "rack")]
    if not rackitems:
        return
    order = {"odf": 0, "patch_panel": 1, "core_sw": 2, "access_sw": 3, "nvr": 4, "vms_server": 5, "ups": 9}
    rackitems.sort(key=lambda e: (order.get(e["type"], 6), e["id"]))
    cap = RACK_U
    racks, cur, used = [], [], 0
    for e in rackitems:
        u = SYM[e["type"]].get("rack_u", 1)
        if used + u > math.floor(cap * 0.8) and cur:
            racks.append(cur)
            cur, used = [], 0
        cur.append(e)
        used += u
    racks.append(cur)
    x, y, w, h = s["rect"]
    horiz = w >= h
    for ri, rs in enumerate(racks):
        rid = f"RACK-{f['id']}-{len([r for r in model['racks'] if r['floor'] == f['id']]) + 1:02d}"
        if horiz:
            rx, ry = x + 1000 + RW * (ri + 0.5), y + h - 600 - RD / 2
        else:
            rx, ry = x + w - 600 - RD / 2, y + 1000 + RW * (ri + 0.5)
        ustart = 1
        slots = []
        for e in reversed(rs):  # UPS 아래(1U부터), ODF 위
            u = SYM[e["type"]].get("rack_u", 1)
            slots.append({"id": e["id"], "type": e["type"], "u": ustart, "size": u})
            e["rack"], e["u"], e["pos"] = rid, ustart, [rx, ry]
            ustart += u + (1 if e["type"] in ("ups", "nvr", "vms_server") else 0)
        model["racks"].append({"id": rid, "floor": f["id"], "space": sid, "pos": [rx, ry], "rot": 0 if horiz else 90, "slots": slots,
                               "used_u": sum(sl["size"] for sl in slots), "top_u": ustart - 1})


# ── 5. 결선 ───────────────────────────────────────────────────────────────
def _floor_index(model, fid):
    return next(i for i, f in enumerate(model["floors"]) if f["id"] == fid)


def _elev(model, fid):
    z = 0
    for f in model["floors"]:
        if f["id"] == fid:
            return z
        z -= f["height"]
    return z


def connect(model):
    E = {e["id"]: e for e in model["equipment"]}
    conns = []
    cores = [e for e in model["equipment"] if e["type"] == "core_sw"]
    access = [e for e in model["equipment"] if e["type"] == "access_sw"]
    used = {a["id"]: 0 for a in access}
    cap = {a["id"]: SYM["access_sw"]["ports"] for a in access}
    soft = math.floor(SYM["access_sw"]["ports"] * (1 - model["options"].get("port_margin", 0.2)))
    poe = {a["id"]: 0.0 for a in access}
    poe_soft = SYM["access_sw"]["poe_budget_w"] * (1 - model["options"].get("port_margin", 0.2))
    dual = model["options"]["dual_homing"]

    def medium(a, b):
        if a["space"] == b["space"]:
            return "MMF"
        return "SMF"

    def est(a, b):  # 대략 거리 (층간 + 평면 맨해튼)
        dz = abs(_elev(model, a["floor"]) - _elev(model, b["floor"]))
        if a["floor"] == b["floor"]:
            return abs(a["pos"][0] - b["pos"][0]) + abs(a["pos"][1] - b["pos"][1])
        fa = model["floors"][_floor_index(model, a["floor"])]
        fb = model["floors"][_floor_index(model, b["floor"])]
        return (abs(a["pos"][0] - fa["riser"][0]) + abs(a["pos"][1] - fa["riser"][1]) + dz
                + abs(b["pos"][0] - fb["riser"][0]) + abs(b["pos"][1] - fb["riser"][1]))
    for e in model["equipment"]:
        if _role(e["type"]) != "endpoint":
            continue
        w = SYM[e["type"]].get("poe_w", 0)
        cands = sorted(access, key=lambda a: (used[a["id"]] >= cap[a["id"]], a["floor"] != e["floor"], a["space"] != e.get("_room", a["space"]),
                                              used[a["id"]] >= soft or poe[a["id"]] + w > poe_soft, est(e, a)))
        if not cands:
            continue
        a = cands[0]
        used[a["id"]] += 1
        poe[a["id"]] += w
        e["switch"], e["port"] = a["id"], used[a["id"]]
        conns.append({"from": e["id"], "to": a["id"], "medium": SYM[e["type"]].get("medium", "CAT6"), "kind": "access"})
    for a in access:
        for c in (cores[:2] if dual else cores[:1]):
            conns.append({"from": a["id"], "to": c["id"], "medium": medium(a, c), "kind": "uplink"})
    if len(cores) >= 2:
        conns.append({"from": cores[0]["id"], "to": cores[1]["id"], "medium": "MMF", "kind": "isl"})
    for h in [e for e in model["equipment"] if _role(e["type"]) == "head"]:
        for c in (cores[:2] if dual else cores[:1]):
            conns.append({"from": h["id"], "to": c["id"], "medium": "CAT6" if h["space"] == c["space"] else "SMF", "kind": "uplink"})
    # LLM/사용자가 명시한 연결 추가
    for c in model.get("connections") or []:
        if c.get("from") in E and c.get("to") in E:
            conns.append({"from": c["from"], "to": c["to"], "medium": c.get("medium", "CAT6"), "kind": "manual"})
    for i, c in enumerate(conns):
        c["id"] = f"C-{i + 1:03d}"
    model["connections"] = conns


# ── 6. 경로·길이 ──────────────────────────────────────────────────────────
def trays(model):
    """말단 장비가 있는 대공간에만 트레이 (빈 계단·통로에는 만들지 않음)."""
    out = []
    has_ep = {e["space"] for e in model["equipment"] if _role(e["type"]) == "endpoint"}
    for f in model["floors"]:
        for s in f["spaces"]:
            if not STYPES[s["type"]].get("big") or s["id"] not in has_ep:
                continue
            x, y, w, h = s["rect"]
            if w >= h:
                out.append({"floor": f["id"], "space": s["id"], "line": [[x + 500, y + h - 800], [x + w - 500, y + h - 800]], "ax": "x"})
            else:
                out.append({"floor": f["id"], "space": s["id"], "line": [[x + w - 800, y + 500], [x + w - 800, y + h - 500]], "ax": "y"})
    return out


def _plen(pts):
    return sum(abs(pts[i + 1][0] - pts[i][0]) + abs(pts[i + 1][1] - pts[i][1]) for i in range(len(pts) - 1))


def _manhattan(a, b):
    return [list(a), [b[0], a[1]], list(b)]


def _to_exit(model, e, target, T):
    """장비 위치 → 공간 출구까지 (트레이가 있으면 수직 낙하 → 트레이 따라 target 쪽 끝)."""
    p = e["pos"]
    t = next((t for t in T if t["space"] == e["space"]), None)
    if not t:
        return [list(p)]
    (x0, y0), (x1, y1) = t["line"]
    if t["ax"] == "x":
        drop = [p[0], y0]
        end = [min(max(target[0], x0), x1), y0]  # 목표의 트레이 투영점
    else:
        drop = [x0, p[1]]
        end = [x0, min(max(target[1], y0), y1)]
    return [list(p), drop, end] if end != drop else [list(p), drop]


def route(model):
    rules = resource("rules", "ict.json")["cable"]
    E = {e["id"]: e for e in model["equipment"]}
    F = {f["id"]: f for f in model["floors"]}
    T = trays(model)
    model["trays"] = T
    for c in model["connections"]:
        a, b = E[c["from"]], E[c["to"]]
        if a["floor"] == b["floor"]:
            if a["space"] == b["space"]:
                path = {a["floor"]: _manhattan(a["pos"], b["pos"])}
            else:
                pa = _to_exit(model, a, b["pos"], T)
                path = {a["floor"]: pa + _manhattan(pa[-1], b["pos"])[1:]}
            vert = 0
        else:
            ra, rb = F[a["floor"]]["riser"], F[b["floor"]]["riser"]
            pa = _to_exit(model, a, ra, T)
            pb = _to_exit(model, b, rb, T)
            path = {a["floor"]: pa + _manhattan(pa[-1], ra)[1:], b["floor"]: list(reversed(pb + _manhattan(pb[-1], rb)[1:]))}
            vert = abs(_elev(model, a["floor"]) - _elev(model, b["floor"]))
        plan = sum(_plen(p) for p in path.values())
        c["path"] = path
        c["length_m"] = round((plan + vert) / 1000 * (1 + rules["route_margin"]) + 2 * rules["slack_end_m"], 1)


# ── 7. 규칙 ───────────────────────────────────────────────────────────────
def validate(model, rules=None):
    rules = rules or resource("rules", "ict.json")
    R = {r["id"]: r for r in rules["rules"] if r.get("enabled", True)}
    out = []

    def add(rid, msg, sev=None, ref=None):
        if rid in R:
            out.append({"id": rid, "severity": sev or R[rid]["severity"], "msg": msg, "ref": ref, "name": R[rid]["name"],
                        "repairable": bool(R[rid].get("repairable"))})
    E = {e["id"]: e for e in model["equipment"]}
    adj = {}
    for c in model["connections"]:
        adj.setdefault(c["from"], set()).add(c["to"])
        adj.setdefault(c["to"], set()).add(c["from"])
    cores = [e["id"] for e in model["equipment"] if e["type"] == "core_sw"]
    # 001 도달성
    reach = set(cores)
    stack = list(cores)
    while stack:
        n = stack.pop()
        for m in adj.get(n, ()):
            if m not in reach:
                reach.add(m)
                stack.append(m)
    for e in model["equipment"]:
        if _role(e["type"]) in ("endpoint", "access", "head") and e["id"] not in reach:
            add("ICT-001", f"{e['id']} 에서 코어 네트워크까지 경로 없음" + ("" if cores else " (코어 스위치 없음)"), ref=e["id"])
    # 002 이중화
    if "ICT-002" in R:
        if len(cores) < R["ICT-002"].get("min_core_switches", 2):
            add("ICT-002", f"코어 스위치 {len(cores)}대 (이중화 기준 {R['ICT-002'].get('min_core_switches', 2)}대)")
        for e in model["equipment"]:
            if e["type"] == "access_sw":
                up = [m for m in adj.get(e["id"], ()) if E[m]["type"] == "core_sw"]
                if len(cores) >= 2 and len(up) < 2:
                    add("ICT-002", f"{e['id']} 가 코어 {len(up)}대에만 귀속 (단일 장애점)", ref=e["id"])
    # 003 포트 / 009 PoE / 010 업링크
    for e in model["equipment"]:
        if _role(e["type"]) not in ("access", "core"):
            continue
        sym = SYM[e["type"]]
        nb = [E[m] for m in adj.get(e["id"], ())]
        down = [n for n in nb if _role(n["type"]) == "endpoint"]
        up = [n for n in nb if _role(n["type"]) in ("core", "head") or (e["type"] == "core_sw" and _role(n["type"]) == "access")]
        if e["type"] == "access_sw":
            m = R.get("ICT-003", {}).get("margin", 0.2)
            if len(down) > sym["ports"]:
                add("ICT-003", f"{e['id']} 포트 초과: {len(down)}/{sym['ports']}", "error", e["id"])
            elif len(down) > sym["ports"] * (1 - m):
                add("ICT-003", f"{e['id']} 포트 여유 부족: {len(down)}/{sym['ports']} (여유 기준 {m:.0%})", ref=e["id"])
            poe = sum(SYM[n["type"]].get("poe_w", 0) for n in down)
            e["poe_w"] = round(poe, 1)
            pm = R.get("ICT-009", {}).get("margin", 0.2)
            if poe > sym["poe_budget_w"] * (1 - pm):
                add("ICT-009", f"{e['id']} PoE {poe:.0f} W > 예산 {sym['poe_budget_w']} W × {1 - pm:.0%}", ref=e["id"])
            nup = len([n for n in nb if n["type"] == "core_sw"])
            if nup > sym["uplinks"]:
                add("ICT-010", f"{e['id']} 업링크 {nup} > {sym['uplinks']}", ref=e["id"])
        else:
            n_ports = len([n for n in nb])
            if n_ports > sym["ports"]:
                add("ICT-010", f"{e['id']} 포트 {n_ports} > {sym['ports']}", ref=e["id"])
    # 004 중복
    ids = [e["id"] for e in model["equipment"]]
    for d in sorted({i for i in ids if ids.count(i) > 1}):
        add("ICT-004", f"장비 ID 중복: {d}", ref=d)
    # 005 UTP 길이
    mx = R.get("ICT-005", {}).get("max_m", 90)
    for c in model["connections"]:
        if c["medium"].startswith("CAT") and c["length_m"] > mx:
            add("ICT-005", f"{c['from']}→{c['to']} UTP {c['length_m']} m > {mx} m (현장 분기실 또는 광 미디어컨버터 필요)", ref=c["from"])
    # 006 랙
    rm = R.get("ICT-006", {}).get("margin", 0.2)
    for r in model["racks"]:
        if r["top_u"] > RACK_U:
            add("ICT-006", f"{r['id']} {r['top_u']}U 사용 > {RACK_U}U", "error", r["id"])
        elif r["top_u"] > RACK_U * (1 - rm):
            add("ICT-006", f"{r['id']} {r['top_u']}U 사용 (여유 기준 {RACK_U * (1 - rm):.0f}U)", ref=r["id"])
    # 007 공간 내부
    for e in model["equipment"]:
        f, s = space_of(model, e["space"])
        if not e.get("pos") or not s:
            continue
        x, y, w, h = s["rect"]
        if not (x - 1 <= e["pos"][0] <= x + w + 1 and y - 1 <= e["pos"][1] <= y + h + 1):
            add("ICT-007", f"{e['id']} 좌표 ({e['pos'][0]:.0f},{e['pos'][1]:.0f}) 가 {s['name']}({s['id']}) 밖", ref=e["id"])
    # 008 AP 커버리지 (격자 샘플)
    for f in model["floors"]:
        for s in f["spaces"]:
            aps = [e for e in model["equipment"] if e["space"] == s["id"] and e["type"] == "ap"]
            if not aps:
                continue
            x, y, w, h = s["rect"]
            rr = SYM["ap"]["coverage_r"]
            n = tot = 0
            for i in range(40):
                for j in range(12):
                    px, py = x + w * (i + 0.5) / 40, y + h * (j + 0.5) / 12
                    tot += 1
                    n += any((px - a["pos"][0]) ** 2 + (py - a["pos"][1]) ** 2 <= rr * rr for a in aps)
            ratio = n / tot
            s["ap_coverage"] = round(ratio, 3)
            mr = R.get("ICT-008", {}).get("min_ratio", 0.9)
            if ratio < mr:
                need = math.ceil(len(aps) * mr / max(ratio, 0.01))
                add("ICT-008", f"{s['name']}({s['id']}) AP 커버리지 {ratio:.0%} < {mr:.0%} (반경 {rr / 1000:g} m 가정, 약 {need}대 필요)", ref=s["id"])
    # 011 NVR
    cams = sum(1 for e in model["equipment"] if e["type"] in ("cctv_fixed", "cctv_ptz"))
    ch = sum(SYM["nvr"]["channels"] for e in model["equipment"] if e["type"] == "nvr")
    if cams and cams > ch:
        add("ICT-011", f"CCTV {cams}대 > NVR 채널 {ch}")
    return out


# ── 8. 일람표 ─────────────────────────────────────────────────────────────
def schedules(model):
    E = {e["id"]: e for e in model["equipment"]}
    eq = [["ID", "종류", "층", "공간", "X(mm)", "Y(mm)", "접속 스위치", "포트", "PoE(W)", "랙", "U", "자동추가"]]
    for e in model["equipment"]:
        _, s = space_of(model, e["space"])
        eq.append([e["id"], SYM[e["type"]]["label"], e["floor"], s["name"], f"{e['pos'][0]:.0f}" if e.get("pos") else "", f"{e['pos'][1]:.0f}" if e.get("pos") else "",
                   e.get("switch", ""), e.get("port", ""), SYM[e["type"]].get("poe_w", "") or e.get("poe_w", ""), e.get("rack", ""), e.get("u", ""),
                   "Y" if e.get("auto") else ""])
    cb = [["케이블 ID", "From", "To", "매체", "길이(m)", "구분", "경유 층"]]
    for c in model["connections"]:
        cb.append([c["id"], c["from"], c["to"], c["medium"], c["length_m"], {"access": "말단", "uplink": "업링크", "isl": "코어 간", "manual": "지정"}[c["kind"]],
                   "/".join(c["path"].keys())])
    bom = {}
    for e in model["equipment"]:
        bom[SYM[e["type"]]["label"]] = bom.get(SYM[e["type"]]["label"], 0) + 1
    rows = [["품목", "수량", "단위"]] + [[k, v, "EA"] for k, v in bom.items()] + [[LIB["rack"]["label"], len(model["racks"]), "EA"]]
    for m in sorted({c["medium"] for c in model["connections"]}):
        rows.append([f"케이블 {m}", round(sum(c["length_m"] for c in model["connections"] if c["medium"] == m), 1), "m"])
    return {"equipment": eq, "cables": cb, "bom": rows}
