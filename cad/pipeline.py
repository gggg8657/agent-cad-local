"""CAD Orchestrator — 라우팅 → LLM(IR/ops) → 정규화 → 결정론 빌드 → 검증 → 수리 루프(≤3) → 리비전 저장.

프로젝트 폴더 (WORKSPACE/projects/<pid>/):
  project.json            메타·대화·리비전 목록
  input/                  첨부 원본 (읽기 전용 취급)
  R00/ R01/ ...           리비전별 ir.json · 산출물 · report.md · *.svg 미리보기
"""
import copy
import csv
import datetime
import json
import os
import re
import secrets
import shutil
import time

from . import assembly, assembly_draw, dxfedit, dxfkit, ict, ict_draw, kb, llm, part2d, part3d, plant, svg
from .util import ROOT, dump_json, extract_json, load_json, read, resource, write

WS = os.environ.get("WORKSPACE") or os.path.join(ROOT, "_workspace")
PROJ = os.path.join(WS, "projects")
UPLOAD = os.path.join(WS, "uploads")
MAX_REPAIR = int(os.environ.get("MAX_REPAIR", "3"))
TASKS = ("part3d", "part2d", "ict", "dxf_edit", "dxf_analyze", "assembly")
TASK_LABEL = {"part3d": "3D 부품", "part2d": "2D 판재", "ict": "ICT 배치도·개황도", "dxf_edit": "기존 DXF 수정", "dxf_analyze": "DXF 분석",
              "assembly": "원자로·원자력 설비 (지식 DB)"}
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")
GT_DIR = os.path.join(ROOT, "gt")


def prompt(name):
    return read(os.path.join(ROOT, "prompts", name + ".md"))


# ── 라우팅 ────────────────────────────────────────────────────────────────
KW = [("dxf_analyze", r"(분석|요약|뭐가 있|무슨 도면|검토해|설명해)"),
      ("assembly", r"(원자로|SMR|MSR|용융염|노심|덤프탱크|동결 ?플러그|어셈블리|조립체|역설계)"),
      ("ict", r"(CCTV|AP\b|와이파이|스위치|랙|ICT|통신|개황도|계통도|구성도|토폴로지|배치도|NVR|승강장|대합실|역사|비상통화)"),
      ("part2d", r"(판재|플레이트|가스켓|패널|레이저|워터젯|절단|DXF로|2D|평판|철판)"),
      ("part3d", r"(브래킷|브라켓|케이스|하우징|플랜지|지그|블록|STEP|STL|3D|축|부싱|스페이서|홀더|마운트|박스)")]


def route(text, has_dxf, model=None, emit=None, has_img=False, img=None):
    if has_img:  # 이미지는 선택 입력 — 명령이 우선, 명령에 작업 단서가 없으면 VLM 이 그림을 보고 고른다
        t, why = route(text, False, model, emit) if re.search(r"[가-힣A-Za-z]{2,}", text or "") else (None, "")
        if t and why not in ("기본값",):
            return t, why + " (+참조 이미지)"
        try:
            j = extract_json(llm.chat("너는 도면 분류기다. JSON 만.", [{"role": "user", "content":
                "이 그림을 CAD 로 만들려면 어떤 작업인가? part3d(3D 부품) | part2d(판재·평면 가공 형상) | ict(건물·공간의 장비 배치도) | assembly(원자로·원자력 설비·플랜트 계통). "
                'JSON: {"task":"...","reason":"..."}' + (f"\n사용자 요청: {text}" if text else "")}], llm.VISION_MODEL or model, json_mode=True, images=[img]))
            if j.get("task") in ("part3d", "part2d", "ict", "assembly"):
                return j["task"], "VLM 이미지 분류: " + str(j.get("reason", ""))[:60]
        except Exception as e:
            emit and emit({"log": f"VLM 분류 실패: {e}"})
        return "assembly", "참조 이미지 (기본)"
    if has_dxf:
        if re.search(KW[0][1], text) and not re.search(r"(추가|이동|삭제|옮겨|넣어|바꿔|변경|지워)", text):
            return "dxf_analyze", "첨부 도면 + 분석 요청"
        if re.search(r"(배치도|개황도|계통도|초안 작성|배치해)", text) and re.search(KW[1][1], text):
            return "ict", "첨부 도면 위 ICT 배치"
        return "dxf_edit", "첨부 도면 + 수정 요청"
    hits = [t for t, p in KW[1:] if re.search(p, text, flags=re.I)]
    if "ict" in hits and re.search(r"(CCTV|AP\b|와이파이|스위치|NVR|비상통화|ICT)", text, flags=re.I):
        return "ict", "ICT 장비 키워드"
    kbh = kb.search(text, 1)
    if kbh:
        return "assembly", f"지식 DB 원형 '{kbh[0]}'"
    if hits and hits[0] == "assembly":
        return "assembly", "키워드"
    if len(hits) == 1:
        return hits[0], "키워드"
    try:
        j = extract_json(llm.chat(prompt("router"), [{"role": "user", "content": text}], model, json_mode=True))
        if j.get("task") in TASKS and not (j["task"].startswith("dxf") and not has_dxf):
            return j["task"], "LLM: " + str(j.get("reason", ""))[:80]
    except Exception as e:
        emit and emit({"log": f"라우터 LLM 실패 → 기본값: {e}"})
    return (hits[0] if hits else "part3d"), "기본값"


# ── 프로젝트 저장소 ──────────────────────────────────────────────────────
def pdir(pid):
    if not re.fullmatch(r"[0-9]{8}-[0-9a-f]{6}", pid or ""):
        raise ValueError("잘못된 프로젝트 id")
    return os.path.join(PROJ, pid)


def load(pid):
    return load_json(os.path.join(pdir(pid), "project.json"))


def save(p):
    dump_json(os.path.join(pdir(p["id"]), "project.json"), p)


def new_project(task, title, owner=None):
    pid = datetime.date.today().strftime("%Y%m%d") + "-" + secrets.token_hex(3)
    os.makedirs(os.path.join(PROJ, pid, "input"), exist_ok=True)
    p = {"id": pid, "task": task, "title": title[:60], "created": datetime.datetime.now().isoformat(timespec="seconds"),
         "messages": [], "revisions": [], "input": None, "owner": owner}
    save(p)
    return p


def list_projects(limit=80, owner=None, show_all=False):
    """숨김 제외. owner(브라우저 id)를 주면 그 사람이 만든 것만 (show_all 이면 모두)."""
    if not os.path.isdir(PROJ):
        return []
    out = []
    for pid in sorted(os.listdir(PROJ), reverse=True):
        if len(out) >= limit:
            break
        try:
            p = load(pid)
            if p.get("hidden") or (owner and not show_all and p.get("owner") != owner):
                continue
            last = p["revisions"][-1] if p["revisions"] else {}
            out.append({"id": pid, "task": p["task"], "title": p["title"], "created": p["created"], "revs": len(p["revisions"]),
                        "status": last.get("status"), "rev": last.get("rev")})
        except Exception:
            continue
    return out


def set_hidden(pid, hidden=True):
    p = load(pid)
    p["hidden"] = bool(hidden)
    save(p)
    return {"id": pid, "hidden": p["hidden"]}


def save_upload(name, data):
    os.makedirs(UPLOAD, exist_ok=True)
    if not name.lower().endswith((".dxf",) + IMG_EXT):
        raise ValueError("DXF 또는 이미지(PNG/JPG)만 (DWG는 ODA File Converter로 DXF 변환 후)")
    if len(data) > 200 << 20:
        raise ValueError("200 MB 초과")
    uid = secrets.token_hex(6)
    safe = re.sub(r"[^\w.\-가-힣]", "_", os.path.basename(name))[:80]
    path = os.path.join(UPLOAD, f"{uid}__{safe}")
    with open(path, "wb") as f:
        f.write(data)
    return uid


def upload_path(uid):
    if not re.fullmatch(r"[0-9a-f]{12}", uid or ""):
        raise ValueError("잘못된 업로드 id")
    for n in os.listdir(UPLOAD):
        if n.startswith(uid + "__"):
            return os.path.join(UPLOAD, n)
    raise FileNotFoundError(uid)


# ── LLM 호출 + 수리 ──────────────────────────────────────────────────────
def ask_json(system, messages, model, emit, label):
    t0 = time.time()
    emit({"stage": "llm", "msg": f"{label} — LLM 작성 중"})
    out = llm.chat(system, messages, model, on_token=lambda t: emit({"token": t}), json_mode=True)
    emit({"log": f"LLM {time.time() - t0:.1f}s, {len(out)}자"})
    return extract_json(out), out


def _conv(p, user_text, extra=""):
    """다듬기: 직전 IR 을 [현재 IR] 로 붙여 전체 IR 을 다시 받는다."""
    last = p["revisions"][-1] if p["revisions"] else None
    if last and last.get("ir") and p["task"] in ("part3d", "part2d", "ict", "assembly"):
        cur = load_json(os.path.join(pdir(p["id"]), last["rev"], "ir.json"))
        cur.pop("_resolved", None)
        return [{"role": "user", "content": f"[현재 IR]\n{json.dumps(cur, ensure_ascii=False)}\n\n[요청]\n{user_text}{extra}"}]
    return [{"role": "user", "content": user_text + extra}]


def next_rev(p):
    return f"R{len(p['revisions']):02d}"


# ── 작업별 빌더 ──────────────────────────────────────────────────────────
def build_part(task, ir, outdir, rev, emit):
    rules = resource("rules", "part.json")
    if task == "part2d":
        n, errs = part2d.normalize(ir)
        if errs:
            return n, {"error": "; ".join(errs)}
        checks, metrics = part2d.validate(n, rules)
        emit({"stage": "build", "msg": "ezdxf 가공도 생성"})
        files, doc = part2d.build(n, outdir, rev)
        previews = {"drawing": _svg(doc, outdir, "drawing.svg")}
        return n, {"files": files, "checks": checks, "metrics": metrics, "previews": previews}
    n, errs = part3d.normalize(ir)
    if errs:
        return n, {"error": "; ".join(errs)}
    emit({"stage": "build", "msg": "OpenCascade 솔리드 빌드 (서브프로세스)"})
    r = part3d.build(n, outdir, rules, rev)
    if r.get("error"):
        return n, {"error": r["error"]}
    previews = {}
    dw = next((f for f in r["files"] if f.endswith("_drawing.dxf")), None)
    if dw:
        previews["drawing"] = _svg(dxfedit.read(os.path.join(outdir, dw)), outdir, "drawing.svg")
    glb = next((f for f in r["files"] if f.endswith(".glb")), None)
    if glb:
        previews["model3d"] = glb
    return n, {"files": r["files"], "checks": r["checks"], "metrics": r["metrics"], "previews": previews}


def _svg(doc, outdir, name, highlight=()):
    write(os.path.join(outdir, name), svg.render(doc, highlight))
    return name


def _csv(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(rows)


def build_ict(ir, outdir, rev, emit, base_dxf=None):
    n, errs = ict.normalize(ir)
    base_doc = None
    if base_dxf:
        base_doc = dxfedit.read(base_dxf)
        ix = dxfedit.index(base_doc)
        zs = dxfedit.zones_as_spaces(ix)
        for f in n["floors"]:
            for s in f["spaces"]:
                if s.get("zone") is not None:
                    z = next((z for z in zs if str(s["zone"]) in z["label"] or z["label"] in str(s["zone"])), None)
                    if z is None:
                        errs.append(f"space {s['id']}: 도면에 존 {s['zone']!r} 없음 (가능: {', '.join(z['label'] for z in zs)})")
                    else:
                        s["rect"] = z["rect"]
                elif not s.get("rect"):
                    errs.append(f"space {s['id']}: 기존 도면 위 배치는 모든 공간에 zone 라벨 필요")
            comm = next((s for s in f["spaces"] if s.get("rect") and ict.STYPES[s["type"]].get("comm")), None)
            if comm and not f.get("riser"):
                x, y, w, h = comm["rect"]
                f["riser"] = [x + w * 0.15, y + h * 0.2]
        n["dxf_coords"] = True
    if errs:
        return n, {"error": "; ".join(errs)}
    emit({"stage": "build", "msg": "공간·장비 배치, 자동 결선, 케이블 경로"})
    m = ict.expand(n)
    checks = ict.validate(m)
    emit({"stage": "build", "msg": "배치도·개황도·랙도 DXF, 일람표, 3D 목업"})
    name = re.sub(r"[^\w가-힣]+", "_", m["name"]).strip("_")[:40] or "ict"
    files, previews = {}, {}
    if base_doc is not None:
        doc = ict_draw.layout_dxf(m, None, rev, base_doc=base_doc)
        p = os.path.join(outdir, f"{name}_layout.dxf")
        doc.saveas(p)
    else:
        doc = ict_draw.layout_dxf(m, os.path.join(outdir, f"{name}_layout.dxf"), rev)
    files[f"{name}_layout.dxf"] = "ICT 배치도"
    previews["layout"] = _svg(doc, outdir, "layout.svg")
    doc = ict_draw.overview_dxf(m, os.path.join(outdir, f"{name}_overview.dxf"), rev)
    files[f"{name}_overview.dxf"] = "시스템 개황도(계통도)"
    previews["overview"] = _svg(doc, outdir, "overview.svg")
    if m["racks"]:
        doc = ict_draw.rack_dxf(m, os.path.join(outdir, f"{name}_racks.dxf"), rev)
        files[f"{name}_racks.dxf"] = "랙 실장도"
        previews["racks"] = _svg(doc, outdir, "racks.svg")
    sc = ict.schedules(m)
    for k, lab in (("equipment", "장비 일람표"), ("cables", "케이블 일람표"), ("bom", "BOM")):
        _csv(os.path.join(outdir, f"{name}_{k}.csv"), sc[k])
        files[f"{name}_{k}.csv"] = lab
    ict_draw.mockup_glb(m, os.path.join(outdir, f"{name}_mockup.glb"))
    files[f"{name}_mockup.glb"] = "3D 디지털 목업"
    previews["model3d"] = f"{name}_mockup.glb"
    m.pop("_draw_offset", None)
    dump_json(os.path.join(outdir, "model.json"), m)
    files["model.json"] = "설계 모델 (IR 확장 결과)"
    cnt = {}
    for e in m["equipment"]:
        cnt[e["type"]] = cnt.get(e["type"], 0) + 1
    metrics = {"equipment": len(m["equipment"]), "by_type": cnt, "racks": len(m["racks"]), "connections": len(m["connections"]),
               "cable_m": {k[0].replace("케이블 ", ""): k[1] for k in sc["bom"] if str(k[0]).startswith("케이블")},
               "spaces": sum(len(f["spaces"]) for f in m["floors"])}
    return n, {"files": files, "checks": checks, "metrics": metrics, "previews": previews, "assumptions": m["assumptions"]}


# ── 계통 조립 (MSR) · 이미지 역설계 ───────────────────────────────────────
def kb_context(text):
    """지식 DB 검색 → 프롬프트용 후보 원형 요약 (상위 3개 전체 + 나머지 목록)."""
    allk = kb.load_all()
    hits = kb.search(text, 3) or ["msr_integral"]
    L = ["\n\n[지식 DB 후보]"] + [kb.brief(allk[h]) for h in hits]
    rest = [f"{a['id']}({a['name']})" for k, a in allk.items() if k not in hits]
    if rest:
        L.append("기타 원형: " + ", ".join(rest))
    return "\n\n".join(L)


def list_gt():
    out = []
    for n in sorted(os.listdir(GT_DIR)) if os.path.isdir(GT_DIR) else []:
        if n.endswith(".json"):
            g = load_json(os.path.join(GT_DIR, n))
            out.append({"id": g["id"], "name": g["name"], "image": g.get("image")})
    return out


def load_gt(gid):
    if not re.fullmatch(r"[\w\-]+", gid or ""):
        raise ValueError("잘못된 GT id")
    return load_json(os.path.join(GT_DIR, gid + ".json"))


def match_gt(img_path):
    """첨부 이미지가 GT 원본과 같은 파일이면 그 GT 로 자동 채점."""
    import hashlib
    h = hashlib.sha256(open(img_path, "rb").read()).hexdigest()
    for g in list_gt():
        gp = os.path.join(GT_DIR, g.get("image") or "")
        if g.get("image") and os.path.exists(gp) and hashlib.sha256(open(gp, "rb").read()).hexdigest() == h:
            return g["id"]
    return None


def summarize_read(rd):
    cnt = {}
    for it in rd.get("instances") or []:
        key = (it.get("type") or "other", it.get("view", 0))
        cnt[key] = cnt.get(key, 0) + 1
    best = {}
    for (t, v), n in cnt.items():
        best[t] = max(best.get(t, 0), n)
    cv = {t: sorted(v) for t, v in (rd.get("count_votes") or {}).items() if v and t not in ("pipe", "other")}
    if cv:
        def fmt(t, v):
            med = v[len(v) // 2]
            return f"{t} {med}" if len(set(v)) == 1 else f"{t} {med}(불확실: 판독 {'/'.join(map(str, v))} — 원리로 판단)"
        cnt_line = "기기 수(판독 투표): " + ", ".join(fmt(t, v) for t, v in cv.items() if any(v))
    else:
        cnt_line = "보이는 기기 수(가장 자세한 뷰 기준): " + ", ".join(f"{t} {n}" for t, n in best.items() if t not in ("pipe", "other"))
    lines = [f"주제: {rd.get('subject', '')}", cnt_line]
    pos = []
    for it in rd.get("instances") or []:
        b = it.get("bbox") or [0, 0, 0, 0]
        if it.get("view", 0) == 0 and len(b) == 4:
            pos.append(f"{it.get('type')}({it.get('label', '')}) 중심 x={(b[0] + b[2]) / 2:.2f} y={(b[1] + b[3]) / 2:.2f} 높이={b[3] - b[1]:.2f}")
    lines += ["위치(0~1, y 아래로 증가): " + "; ".join(pos[:30])] if pos else []
    lines += ["관계: " + "; ".join(rd.get("relations") or [])] if rd.get("relations") else []
    lines += ["불확실: " + "; ".join(rd.get("uncertain") or [])] if rd.get("uncertain") else []
    return "\n".join(lines)


COUNT_Q = ("이 그림에서 각 기기 종류별로 **몇 개** 보이는지 세어라. 좌우 대칭 쌍, 격자로 놓인 탱크, 가려진 것에 주의. 여러 뷰가 있으면 가장 자세한 뷰 기준. "
           "기기 종류 키는 영문 snake_case (예: core, reactor_vessel, steam_generator, primary_pump, heat_exchanger, pressurizer, he_pump, freeze_plug, dump_tank, tank, pool). "
           'JSON: {"counts":{"<종류>":n, ...},"reason":"..."}')


VIEW_VOTES = int(os.environ.get("VIEW_VOTES", "1"))  # 3회 투표는 측정상 더 나빴음 (bench_gt.py, 2026-10-06)
VIEW_Q = ("이 그림에 여러 뷰(단면도·평면도·외관)가 있으면 **뷰마다 따로** 각 기기 개수를 세라. 단면도는 절반이 잘려 일부만 보이므로, 원형 배치 기기는 평면도(위에서 본 원)에서 센다. "
          'JSON: {"views":[{"view":"단면|평면|외관","counts":{"<영문 snake_case 종류>":n, ...}}]}')


def _img_variants(img):
    """원본 + 2배 확대 (작은 그림에서 VLM 개수 판독이 흔들리는 것 완화). Pillow 로컬."""
    import io
    from PIL import Image
    out = [img]
    try:
        im = Image.open(io.BytesIO(img)).convert("RGB")
        if max(im.size) < 1600:
            f = io.BytesIO()
            im.resize((im.size[0] * 2, im.size[1] * 2), Image.LANCZOS).save(f, "PNG")
            out.append(f.getvalue())
    except Exception:
        pass
    return out


def vlm_read(img, model, outdir, emit, r):
    """1) 개체 목록(위치) 1회 + 2) 종류별 개수 질문을 원본·2배 확대로 → 투표. 불일치 항목은 '불확실'로 넘겨 설계 LLM 이 원리로 판단."""
    emit({"stage": "vlm", "msg": "참조 이미지 판독 (VLM, 로컬) — 개체 목록 + 개수 투표"})
    t0 = time.time()
    vm = llm.VISION_MODEL or model
    rd = {}
    try:
        rd = extract_json(llm.chat(prompt("vlm_read"), [{"role": "user", "content": "이 그림을 판독해라."}], vm, json_mode=True, images=[img]))
    except Exception as e:
        emit({"log": f"VLM 개체 판독 실패: {e}"})
    votes = {}
    for k, im in enumerate(_img_variants(img)):
        try:
            c = extract_json(llm.chat("너는 공학 도면 판독기다. JSON 만.", [{"role": "user", "content": COUNT_Q}], vm, json_mode=True, images=[im]))
            for t, n in (c.get("counts") or {}).items():
                votes.setdefault(t, []).append(int(n or 0))
            rd.setdefault("count_reasons", []).append(str(c.get("reason", ""))[:300])
        except Exception as e:
            emit({"log": f"VLM 개수 판독 {k + 1} 실패: {e}"})
    rd["per_view"] = []
    for k in range(VIEW_VOTES):  # 뷰별 개수 × N회 (self-consistency) → 각 회의 뷰 최댓값을 한 표로
        try:
            vv = extract_json(llm.chat("너는 공학 도면 판독기다. JSON 만.", [{"role": "user", "content": VIEW_Q}], vm, json_mode=True, images=[img]))
            mx = {}
            for v in vv.get("views") or []:
                for t, n in (v.get("counts") or {}).items():
                    mx[t] = max(mx.get(t, 0), int(n or 0))
            for t, n in mx.items():
                votes.setdefault(t, []).append(n)
            rd["per_view"].append(vv.get("views"))
        except Exception as e:
            emit({"log": f"VLM 뷰별 판독 {k + 1} 실패: {e}"})
    inst = {}
    for it in rd.get("instances") or []:
        inst.setdefault((it.get("type"), it.get("view", 0)), 0)
        inst[(it.get("type"), it.get("view", 0))] += 1
    for (t, v), n in inst.items():
        if t in votes or t in ("core", "primary_pump", "heat_exchanger", "he_pump", "freeze_plug", "dump_tank"):
            votes.setdefault(t, [])
    for t in list(votes):
        best = max([n for (tt, v), n in inst.items() if tt == t] or [0])
        if best:
            votes[t].append(best)
    rd["count_votes"] = votes
    if not rd and not votes:
        return ""
    dump_json(os.path.join(outdir, "vlm_read.json"), rd)
    r.setdefault("files", {})["vlm_read.json"] = "VLM 이미지 판독 결과 (개체·개수 투표)"
    summ = summarize_read(rd)
    r["vlm_summary"] = summ
    emit({"log": f"VLM {time.time() - t0:.1f}s — " + summ.splitlines()[1][:160]})
    return "\n\n[이미지 판독]\n" + summ


def _msr_from_plant(ir):
    """plant IR (archetype msr_integral) → 전용 MSR 엔진 IR."""
    comps = ir.get("components") or {}
    if isinstance(comps, dict):
        lst = [{"type": k, "count": (v or {}).get("count", 1)} for k, v in comps.items() if (v or {}).get("count", 1)]
    else:
        lst = comps
    if not lst:
        lst = [{"type": t, "count": n} for t, n in {"core": 1, "reflector": 1, "reactor_vessel": 1, "riser": 1, "primary_pump": 2, "heat_exchanger": 2,
                                                     "he_pump": 1, "freeze_plug": 1, "dump_tank": 4, "shield_cell": 1}.items()]
    return {"kind": "assembly", "name": ir.get("name"), "design": ir.get("design") or {}, "components": lst, "arrangement": ir.get("arrangement") or {},
            "notes": ir.get("notes") or []}


def _run_worker(m, name, outdir, rev, densities=None):
    import subprocess
    job = os.path.join(outdir, "_job.json")
    dump_json(job, {"model": m, "name": name, "rev": rev, "densities": densities or {}})
    try:
        subprocess.run([part3d.worker_python(), "-m", "cad.worker_asm", job, outdir], cwd=ROOT, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return {"error": "조립 빌드 600s 초과"}
    finally:
        os.path.exists(job) and os.remove(job)
        os.path.exists(os.path.join(outdir, "_progress")) and os.remove(os.path.join(outdir, "_progress"))
    rp = os.path.join(outdir, "_result.json")
    if not os.path.exists(rp):
        return {"error": "조립 워커 비정상 종료"}
    w = load_json(rp)
    os.remove(rp)
    return w


def build_plant(ir, outdir, rev, emit):
    n, errs, arch = plant.normalize(ir)
    if errs:
        return n, {"error": "; ".join(errs)}
    emit({"stage": "build", "msg": f"지식 DB 원형 '{arch['name']}' — 사이징 → 배치 → 배관·계통"})
    try:
        m = plant.build_model(n, arch)
    except plant.PlantError as e:
        return n, {"error": f"설비 모델: {e}"}
    checks = plant.validate(m, arch)
    m = plant.strip(m)
    name = re.sub(r"[^\w가-힣]+", "_", m["name"]).strip("_")[:40] or "plant"
    emit({"stage": "build", "msg": "OpenCascade 조립체·절개 GLB·간섭·GA 단면도 (서브프로세스)"})
    w = _run_worker(m, name, outdir, rev)
    if w.get("error"):
        return n, {"error": "조립 빌드: " + w["error"]}
    files, previews = dict(w["files"]), {}
    checks += w["checks"]
    ga = next((f for f in files if f.endswith("_GA.dxf")), None)
    if ga:
        previews["ga"] = _svg(dxfedit.read(os.path.join(outdir, ga)), outdir, "ga.svg")
    doc = assembly_draw.flow_dxf_generic(m, os.path.join(outdir, f"{name}_flow.dxf"), rev)
    files[f"{name}_flow.dxf"] = "계통도 DXF"
    previews["flow"] = _svg(doc, outdir, "flow.svg")
    _csv(os.path.join(outdir, f"{name}_components.csv"), assembly_draw.bom_rows(m))
    files[f"{name}_components.csv"] = "기기 목록·치수"
    previews["model3d"] = f"{name}_cutaway.glb"
    dump_json(os.path.join(outdir, "model.json"), m)
    files["model.json"] = "설계 모델 (사이징·배치 결과)"
    e = m["extent"]
    metrics = {"archetype": arch["id"], "power_mwth": m["design"].get("power_mwth"), "height_m": round((e[5] - e[2]) / 1000, 2),
               "width_m": round((e[3] - e[0]) / 1000, 2), "components": len([c for c in m["components"] if c["role"] != "pipe"]), **w.get("metrics", {})}
    if "core" in m["calc"]:
        metrics["core"] = f"Ø{m['calc']['core']['D_mm']}×H{m['calc']['core']['H_mm']}"
    asm = m["assumptions"] + [f"지식 DB 원형: {arch['name']} ({'사용자 수정 반영' if arch.get('edited') else '원본'}) — 출처: {arch.get('source', '-')}"]
    return n, {"files": files, "checks": checks, "metrics": metrics, "previews": previews, "assumptions": asm, "calc": m["calc"],
               "_model": m, "_ga": ga, "principles": arch.get("principles", [])}


def vlm_describe(img, task, model, outdir, emit, r):
    emit({"stage": "vlm", "msg": "첨부 이미지 판독 (VLM, 로컬)"})
    try:
        out = llm.chat(prompt("vlm_describe"), [{"role": "user", "content": f"작업: {task}. 이 그림을 판독해라."}], llm.VISION_MODEL or model, images=[img])
    except Exception as e:
        emit({"log": f"VLM 판독 실패 → 명령만으로 진행: {e}"})
        return ""
    out = re.sub(r"<think>.*?</think>", "", out, flags=re.S).strip()
    write(os.path.join(outdir, "vlm_describe.md"), out)
    r.setdefault("files", {})["vlm_describe.md"] = "VLM 이미지 판독"
    r["vlm_summary"] = out
    emit({"log": "VLM 판독: " + out.replace("\n", " ")[:160]})
    return "\n\n[이미지 판독 — 명령이 우선, 판독은 참고]\n" + out


def build_assembly(ir, outdir, rev, emit):
    if (ir or {}).get("kind") == "plant":
        if ir.get("archetype") != "msr_integral":
            return build_plant(ir, outdir, rev, emit)
        ir = _msr_from_plant(ir)
    n, errs = assembly.normalize(ir)
    if errs:
        return n, {"error": "; ".join(errs)}
    emit({"stage": "build", "msg": "공학 사이징 → 배치 → 계통 연결"})
    m = assembly.build_model(n)
    checks = assembly.validate(m)
    name = re.sub(r"[^\w가-힣]+", "_", m["name"]).strip("_")[:40] or "assembly"
    emit({"stage": "build", "msg": "OpenCascade 조립체·절개 GLB·간섭·GA 단면도 (서브프로세스)"})
    job = os.path.join(outdir, "_job.json")
    dump_json(job, {"model": m, "name": name, "rev": rev, "densities": assembly.LIB["densities_kg_m3"]})
    import subprocess
    try:
        subprocess.run([part3d.worker_python(), "-m", "cad.worker_asm", job, outdir], cwd=ROOT, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return n, {"error": "조립 빌드 600s 초과"}
    finally:
        os.path.exists(job) and os.remove(job)
        os.path.exists(os.path.join(outdir, "_progress")) and os.remove(os.path.join(outdir, "_progress"))
    rp = os.path.join(outdir, "_result.json")
    if not os.path.exists(rp):
        return n, {"error": "조립 워커 비정상 종료"}
    w = load_json(rp)
    os.remove(rp)
    if w.get("error"):
        return n, {"error": "조립 빌드: " + w["error"]}
    files, previews = dict(w["files"]), {}
    checks += w["checks"]
    ga = next((f for f in files if f.endswith("_GA.dxf")), None)
    if ga:
        previews["ga"] = _svg(dxfedit.read(os.path.join(outdir, ga)), outdir, "ga.svg")
    doc = assembly_draw.flow_dxf(m, os.path.join(outdir, f"{name}_flow.dxf"), rev)
    files[f"{name}_flow.dxf"] = "계통도 DXF"
    previews["flow"] = _svg(doc, outdir, "flow.svg")
    _csv(os.path.join(outdir, f"{name}_components.csv"), assembly_draw.bom_rows(m))
    files[f"{name}_components.csv"] = "기기 목록·치수"
    previews["model3d"] = f"{name}_cutaway.glb"
    m.pop("_overlap_pairs", None)
    dump_json(os.path.join(outdir, "model.json"), m)
    files["model.json"] = "설계 모델 (사이징·배치 결과)"
    metrics = {"power_mwth": m["design"]["power_mwth"], "core": f"Ø{m['calc']['core']['D_mm']}×H{m['calc']['core']['H_mm']}",
               "salt_m3": m["calc"]["salt"]["inventory_m3"], "height_m": round((m["extent"][5] - m["extent"][2]) / 1000, 2),
               "components": len([c for c in m["components"] if c["type"] != "pipe"]), **w.get("metrics", {})}
    return n, {"files": files, "checks": checks, "metrics": metrics, "previews": previews, "assumptions": m["assumptions"], "calc": m["calc"],
               "_model": m, "_ga": ga}


def visual_compare(img, outdir, ga, model, emit):
    """설계 결과 GA 를 PNG 로 (Pillow, 로컬) → 참조 이미지와 함께 VLM 에 비교 요청."""
    from . import raster
    png = os.path.join(outdir, "ga_render.png")
    raster.render(dxfedit.read(os.path.join(outdir, ga)), png, 1400, skip_layers=("ICT-TITLE", "ICT-ANNOTATION"))
    emit({"stage": "vlm", "msg": "시각 자기검증: 참조 이미지 vs 설계 단면도 (VLM)"})
    try:
        out = llm.chat(prompt("vlm_compare"), [{"role": "user", "content": "참조(1)와 설계 결과(2)를 비교."}], llm.VISION_MODEL or model,
                       json_mode=True, images=[img, open(png, "rb").read()])
        v = extract_json(out)
        v["match"] = float(v.get("match") or 0)
        return v
    except Exception as e:
        emit({"log": f"시각 비교 실패: {e}"})
        return None


# ── 보고서 ────────────────────────────────────────────────────────────────
def ir_diff(a, b, path=""):
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k.startswith("_"):
                continue
            out += ir_diff(a.get(k), b.get(k), f"{path}.{k}" if path else k)
    elif isinstance(a, list) and isinstance(b, list) and (len(a) == len(b) or all(isinstance(x, dict) for x in a + b)):
        rest = list(b)
        for i, x in enumerate(a):  # 같은 항목은 건너뛰고, 남는 것은 추가/삭제
            if x in rest:
                rest.remove(x)
            elif i < len(b) and len(a) == len(b):
                out += ir_diff(x, b[i], f"{path}[{i}]")
                rest.remove(b[i]) if b[i] in rest else None
            else:
                out.append(f"{path}: − {json.dumps(x, ensure_ascii=False)[:110]}")
        out += [f"{path}: + {json.dumps(y, ensure_ascii=False)[:110]}" for y in rest]
    elif a != b:
        out.append(f"{path}: {json.dumps(a, ensure_ascii=False)[:60]} → {json.dumps(b, ensure_ascii=False)[:60]}")
    return out


def report(p, r):
    L = [f"# {p['title']} — {r['rev']}", "", f"- 작업: {TASK_LABEL[p['task']]}  ·  상태: **{r['status']}**  ·  {r['ts']}  ·  모델 {r.get('model', '-')}",
         f"- 요청: {r.get('prompt', '')}", f"- LLM 시도 {r.get('attempts', 0)}회 (수리 {max(0, r.get('attempts', 1) - 1)}회)", "",
         "> DRAFT — NOT FOR CONSTRUCTION. 엔지니어 검토·승인 전 시공/제작 불가.", ""]
    if r.get("error"):
        L += ["## 실패", "", "```", r["error"], "```", ""]
    ck = r.get("checks") or []
    L += ["## 검증", ""]
    if not ck:
        L.append("규칙 위반 없음.")
    else:
        L += ["| 규칙 | 심각도 | 내용 |", "|---|---|---|"] + [f"| {c['id']} {c.get('name', '')} | {c['severity']} | {c['msg']} |" for c in ck]
    if r.get("assumptions"):
        L += ["", "## 엔진 가정 (검토 필요)", ""] + [f"- {a}" for a in r["assumptions"]]
    if r.get("notes"):
        L += ["", "## LLM 가정·메모", ""] + [f"- {a}" for a in r["notes"]]
    if r.get("calc"):
        L += ["", "## 공학 사이징 (엔진 계산, 근거식 포함)", "", "| 항목 | 값 |", "|---|---|"]
        for k, v in r["calc"].items():
            L.append(f"| {k} | " + ", ".join(f"{kk}={vv}" for kk, vv in v.items()) + " |")
    if r.get("principles"):
        L += ["", "## 적용된 설계 원칙 (지식 DB — 연구원 편집 가능)", ""] + [f"- {x}" for x in r["principles"]]
    if r.get("vlm_summary"):
        L += ["", "## 이미지 판독 (VLM)", "", "```", r["vlm_summary"], "```"]
    if r.get("visual"):
        v = r["visual"]
        L += ["", f"## 시각 자기검증 (참조 이미지 vs 설계 GA, VLM) — 일치 {v.get('match', 0):.0f}", ""]
        L += [f"- ✔ {x}" for x in v.get("same") or []] + [f"- ✘ [{d.get('severity')}] {d.get('what')} → {d.get('fix', '')}" for d in v.get("differences") or []]
    if r.get("gt_md"):
        L += ["", r["gt_md"]]
    if r.get("metrics"):
        L += ["", "## 측정값", "", "```json", json.dumps(r["metrics"], ensure_ascii=False, indent=1), "```"]
    if r.get("ops_log"):
        L += ["", "## 적용 연산", ""] + [f"- {json.dumps(o, ensure_ascii=False)}" for o in r["ops_log"]]
    if r.get("diff"):
        d = r["diff"]
        L += ["", "## 도면 변경 (diff)", "", f"- 추가 {len(d['added'])} · 삭제 {len(d['removed'])} · 이동 {len(d['moved'])} · 속성변경 {len(d['changed'])}"]
        L += [f"  - + {e['id'] or e['block']} @ {e['pos']}" for e in d["added"]] + [f"  - − {e['id'] or e['block']}" for e in d["removed"]]
        L += [f"  - ↦ {e['id']} {e['from']} → {e['to']}" for e in d["moved"]]
    if r.get("ir_diff"):
        L += ["", "## IR 변경 (직전 리비전 대비)", ""] + [f"- {x}" for x in r["ir_diff"][:60]]
    if r.get("files"):
        L += ["", "## 산출물", ""] + [f"- `{k}` — {v}" for k, v in r["files"].items()]
    return "\n".join(L) + "\n"


def finish(p, r, outdir, emit):
    r["files"] = {**(r.get("files") or {}), "report.md": "검증 보고서"}
    write(os.path.join(outdir, "report.md"), report(p, r))
    p["revisions"].append({k: v for k, v in r.items() if k not in ("raw",)})
    save(p)
    emit({"done": {"project": p["id"], "rev": r["rev"], "status": r["status"]}})
    return p


def _status(r):
    if r.get("error"):
        return "failed"
    if any(c["severity"] == "error" for c in r.get("checks") or []):
        return "errors"
    if r.get("checks"):
        return "warnings"
    return "pass"


# ── 메인 실행 ─────────────────────────────────────────────────────────────
def run(text, task="auto", pid=None, upload=None, model=None, emit=lambda e: None, gt=None, owner=None):
    model = model or llm.MODEL
    p = load(pid) if pid else None
    base = None
    if p and p.get("input"):
        base = os.path.join(pdir(p["id"]), "input", p["input"])
    if upload:
        base = upload_path(upload)
    is_img = bool(base and base.lower().endswith(IMG_EXT))
    if p is None:
        if task == "auto":
            task, why = route(text, bool(base) and not is_img, model, emit, has_img=is_img, img=open(base, "rb").read() if is_img else None)
            emit({"stage": "route", "msg": f"작업 분류: {TASK_LABEL[task]} ({why})", "task": task})
        if task in ("dxf_edit", "dxf_analyze") and (not base or is_img):
            raise ValueError("이 작업은 DXF 첨부가 필요합니다")
        if is_img and task.startswith("dxf"):
            raise ValueError("DXF 작업에는 이미지가 아니라 DXF 를 첨부하세요")
        p = new_project(task, text.strip().splitlines()[0] if text.strip() else TASK_LABEL[task], owner)
        emit({"project": p["id"], "task": task})
        if gt:
            p["gt"] = gt
            save(p)
    if upload:
        name = os.path.basename(base).split("__", 1)[1]
        shutil.copy(base, os.path.join(pdir(p["id"]), "input", name))
        p["input"] = name
        base = os.path.join(pdir(p["id"]), "input", name)
    task = p["task"]
    rev = next_rev(p)
    outdir = os.path.join(pdir(p["id"]), rev)
    os.makedirs(outdir, exist_ok=True)
    p["messages"].append({"role": "user", "content": text, "rev": rev})
    r = {"rev": rev, "ts": datetime.datetime.now().isoformat(timespec="seconds"), "prompt": text, "model": model, "source": "llm", "attempts": 0}
    if task == "dxf_analyze":
        return _analyze(p, r, base, text, model, outdir, emit)
    if task == "dxf_edit":
        return _edit(p, r, base, text, model, outdir, emit)
    if gt is None and is_img:
        gt = match_gt(base)
        if gt:
            p["gt"] = gt
            emit({"log": f"첨부 이미지가 GT '{gt}' 의 원본과 같음 → 자동 채점"})
    extra = ""
    img = None
    if p.get("input") and p["input"].lower().endswith(IMG_EXT):  # 이미지 첨부 (선택): 작업에 맞춰 판독해 명령과 함께
        img = open(os.path.join(pdir(p["id"]), "input", p["input"]), "rb").read()
        first = p["revisions"][0]["rev"] if p["revisions"] else None
        if task == "assembly":
            rd = os.path.join(pdir(p["id"]), first, "vlm_read.json") if first else ""
            extra = ("\n\n[이미지 판독]\n" + summarize_read(load_json(rd))) if first and os.path.exists(rd) else vlm_read(img, model, outdir, emit, r)
        else:
            dp = os.path.join(pdir(p["id"]), first, "vlm_describe.md") if first else ""
            extra = ("\n\n[이미지 판독]\n" + read(dp)) if first and os.path.exists(dp) else vlm_describe(img, task, model, outdir, emit, r)
    if task == "ict" and base:
        ix = dxfedit.index(dxfedit.read(base))
        extra = "\n\n[기존 도면 존]\n" + json.dumps([{"label": z["label"], "w_m": round(z["rect"][2] / 1000, 1), "h_m": round(z["rect"][3] / 1000, 1)}
                                                  for z in dxfedit.zones_as_spaces(ix)], ensure_ascii=False)
    if task == "assembly":
        extra += kb_context(text + " " + (r.get("vlm_summary") or ""))
    msgs = _conv(p, text, extra)
    prev_ir = None
    if p["revisions"] and p["revisions"][-1].get("ir"):
        prev_ir = load_json(os.path.join(pdir(p["id"]), p["revisions"][-1]["rev"], "ir.json"))
    ir = None
    for attempt in range(1, MAX_REPAIR + 2):
        r["attempts"] = attempt
        try:
            ir, raw = ask_json(prompt("plant" if task == "assembly" else task), msgs, model, emit, TASK_LABEL[task] + (" (수리)" if attempt > 1 else ""))
        except Exception as e:
            if "JSON" in str(e) and attempt <= MAX_REPAIR:
                msgs = msgs + [{"role": "assistant", "content": "(파싱 불가)"}, {"role": "user", "content": f"[오류]\nJSON 파싱 실패: {e}. JSON 한 개만 다시."}]
                continue
            r.update(error=f"LLM 실패: {type(e).__name__}: {e}", status="failed")
            return finish(p, r, outdir, emit)
        dump_json(os.path.join(outdir, f"attempt{attempt}.json"), ir)  # 수리 이력 (디버깅·감사용)
        res = _build(task, ir, outdir, rev, emit, base)
        problems = _repairable(res)
        if problems:
            write(os.path.join(outdir, f"attempt{attempt}.err.txt"), problems)
        if not problems or attempt > MAX_REPAIR:
            break
        emit({"stage": "repair", "msg": f"수리 {attempt}/{MAX_REPAIR}: " + problems[:200]})
        msgs = [{"role": "user", "content": f"[현재 IR]\n{json.dumps(ir, ensure_ascii=False)}\n\n[원래 요청]\n{text}{extra}\n\n[오류]\n{problems}\n\n오류를 고친 전체 IR을 출력."}]
    if task == "assembly" and not res.get("error"):
        ir, res = post_assembly(p, r, ir, res, img, text, extra, model, outdir, rev, emit)
    r["ir"] = True
    dump_json(os.path.join(outdir, "ir.json"), ir)
    files0 = r.get("files") or {}
    r.update({k: v for k, v in res.items() if k in ("files", "checks", "metrics", "previews", "assumptions", "error", "calc", "principles")})
    r["files"] = {**files0, **(r.get("files") or {})}
    r["notes"] = res.get("notes") or (ir or {}).get("notes") or []
    if prev_ir:
        r["ir_diff"] = ir_diff(prev_ir, ir)
    r["status"] = _status(r)
    r.setdefault("files", {})["ir.json"] = "Project IR (편집 후 재빌드 가능)"
    return finish(p, r, outdir, emit)


VIS_MIN = float(os.environ.get("VIS_MIN", "70"))


def post_assembly(p, r, ir, res, img, text, extra, model, outdir, rev, emit):
    """이미지 역설계: 시각 자기검증(VLM) → 일치도가 낮으면 1회 시각 수리(더 나은 쪽 채택) → GT 채점."""
    if img and res.get("_ga"):
        v = visual_compare(img, outdir, res["_ga"], model, emit)
        if v is not None:
            res["files"]["ga_render.png"] = "설계 GA 렌더 (시각 비교 입력)"
            highs = [d for d in v.get("differences") or [] if d.get("severity") == "high"]
            if v["match"] < VIS_MIN and highs and not p["revisions"]:
                emit({"stage": "repair", "msg": f"시각 수리: 일치 {v['match']:.0f} < {VIS_MIN:.0f} — " + "; ".join(d.get("what", "") for d in highs)[:160]})
                diff = "\n".join(f"- {d.get('what')} → {d.get('fix', '')}" for d in highs)
                msgs = [{"role": "user", "content": f"[현재 IR]\n{json.dumps(ir, ensure_ascii=False)}\n\n[원래 요청]\n{text}{extra}\n\n"
                                                    f"[시각 비교 차이 — 참조 그림과 다름]\n{diff}\n\n그림과 같아지도록 고친 전체 IR을 출력."}]
                try:
                    ir2, _ = ask_json(prompt("plant"), msgs, model, emit, "설비 (시각 수리)")
                    tmp = outdir + "_vis"
                    os.makedirs(tmp, exist_ok=True)
                    res2 = _build("assembly", ir2, tmp, rev, emit, None)
                    v2 = visual_compare(img, tmp, res2["_ga"], model, emit) if res2.get("_ga") and not _repairable(res2) else None
                    if v2 is not None and v2["match"] > v["match"]:
                        for f in os.listdir(tmp):
                            shutil.move(os.path.join(tmp, f), os.path.join(outdir, f))
                        ir, res, v = ir2, res2, v2
                        res["files"]["ga_render.png"] = "설계 GA 렌더 (시각 비교 입력)"
                        r["visual_repair"] = "채택"
                        emit({"log": f"시각 수리 채택: 일치 {v['match']:.0f}"})
                    else:
                        r["visual_repair"] = "기각 (개선 없음)"
                        emit({"log": "시각 수리 기각 — 원래 설계 유지"})
                    shutil.rmtree(tmp, ignore_errors=True)
                except Exception as e:
                    emit({"log": f"시각 수리 실패: {e}"})
            r["visual"] = v
            dump_json(os.path.join(outdir, "visual_compare.json"), v)
            res["files"]["visual_compare.json"] = "시각 자기검증 결과"
    if p.get("gt") and res.get("_model"):
        rep = assembly.gt_score(res["_model"], load_gt(p["gt"]))
        dump_json(os.path.join(outdir, "gt_score.json"), rep)
        res["files"]["gt_score.json"] = "GT 채점 상세"
        r["gt"] = {"id": rep["gt"], "total": rep["total"], "components_f1": rep["components"]["f1"], "flows_f1": rep["flows"]["f1"],
                   "relations": rep["relations"]["score"], "proportions": rep["proportions"]["score"]}
        r["gt_md"] = assembly.gt_markdown(rep)
        emit({"log": f"GT 점수 {rep['total'] * 100:.0f}"})
    res.pop("_model", None)
    res.pop("_ga", None)
    return ir, res


def _build(task, ir, outdir, rev, emit, base):
    try:
        if task == "assembly":
            n, res = build_assembly(ir, outdir, rev, emit)
        else:
            n, res = build_ict(ir, outdir, rev, emit, base) if task == "ict" else build_part(task, ir, outdir, rev, emit)
        res["notes"] = (n or {}).get("notes") or []  # 정규화 단계에서 엔진이 덧붙인 메모 포함
        return res
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"error": f"빌드 예외: {type(e).__name__}: {e}"}


def _repairable(res):
    if res.get("error"):
        return res["error"]
    errs = [c for c in res.get("checks") or [] if c["severity"] == "error" and c.get("repairable", True)]  # 부품 규칙은 전부, ICT 는 repairable 만
    if errs:
        return "\n".join(f"[규칙 위반] {c['id']}: {c['msg']}" for c in errs[:15])
    return ""


def rebuild(pid, ir, emit=lambda e: None):
    """사용자가 직접 고친 IR → LLM 없이 재빌드 (새 리비전)."""
    p = load(pid)
    if p["task"] not in ("part3d", "part2d", "ict", "assembly"):
        raise ValueError("IR 재빌드는 부품/ICT/조립 작업만")
    rev = next_rev(p)
    outdir = os.path.join(pdir(pid), rev)
    os.makedirs(outdir, exist_ok=True)
    base = os.path.join(pdir(pid), "input", p["input"]) if p.get("input") else None
    r = {"rev": rev, "ts": datetime.datetime.now().isoformat(timespec="seconds"), "prompt": "(IR 직접 수정 → 재빌드)", "source": "manual", "attempts": 0,
         "model": "-"}
    prev = load_json(os.path.join(pdir(pid), p["revisions"][-1]["rev"], "ir.json")) if p["revisions"] and p["revisions"][-1].get("ir") else None
    res = _build(p["task"], ir, outdir, rev, emit, base)
    if p["task"] == "assembly" and not res.get("error"):
        ir, res = post_assembly(p, r, ir, res, None, "", "", None, outdir, rev, emit)
    dump_json(os.path.join(outdir, "ir.json"), ir)
    r["ir"] = True
    r.update({k: v for k, v in res.items() if k in ("files", "checks", "metrics", "previews", "assumptions", "error", "calc", "principles")})
    r["notes"] = res.get("notes") or ir.get("notes") or []
    if prev:
        r["ir_diff"] = ir_diff(prev, ir)
    r["status"] = _status(r)
    r.setdefault("files", {})["ir.json"] = "Project IR (편집 후 재빌드 가능)"
    return finish(p, r, outdir, emit)


# ── DXF 분석/수정 ────────────────────────────────────────────────────────
def _current_dxf(p, base):
    """수정은 직전 리비전 결과 위에 누적 (없으면 원본)."""
    for r in reversed(p["revisions"]):
        f = next((k for k in (r.get("files") or {}) if k.endswith(".dxf")), None)
        if f and r.get("status") != "failed":
            return os.path.join(pdir(p["id"]), r["rev"], f)
    return base


def _analyze(p, r, base, text, model, outdir, emit):
    doc = dxfedit.read(base)
    ix = dxfedit.index(doc)
    emit({"stage": "build", "msg": "Semantic Index 작성"})
    r["previews"] = {"drawing": _svg(doc, outdir, "drawing.svg")}
    r["metrics"] = {"entities": ix["n_entities"], "inserts": ix["n_inserts"], "texts": ix["n_texts"], "layers": len(ix["layers"]),
                    "zones": len([z for z in ix["zones"] if z["label"]]), "equipment": len(ix["equipment"])}
    desc = dxfedit.describe(ix)
    ixs = dxfedit.summary_for_llm(ix)
    dump_json(os.path.join(outdir, "index.json"), ixs)
    r["files"] = {"index.json": "Semantic Index (LLM 입력 요약)"}
    try:
        emit({"stage": "llm", "msg": "도면 검토 답변 작성"})
        ans = llm.chat(prompt("dxf_analyze"), [{"role": "user", "content": f"[인덱스]\n{json.dumps(ixs, ensure_ascii=False)}\n\n[질문]\n{text}"}], model,
                       on_token=lambda t: emit({"token": t}))
        r["attempts"] = 1
    except Exception as e:
        ans = f"(LLM 실패: {e})"
    r["answer"] = desc + "\n\n" + re.sub(r"<think>.*?</think>", "", ans, flags=re.S).strip()
    r["checks"] = [{"id": "STD-001", "severity": "warning", "msg": f"표준에 없는 레이어: {l['name']}"} for l in ix["layers"]
                   if l["name"] not in dxfkit.STD["layers"] and l["name"] not in ("0", "DEFPOINTS")][:20]
    r["status"] = _status(r)
    write(os.path.join(outdir, "answer.md"), r["answer"])
    r["files"]["answer.md"] = "분석 답변"
    return finish(p, r, outdir, emit)


def _edit(p, r, base, text, model, outdir, emit):
    src = _current_dxf(p, base)
    doc0 = dxfedit.read(src)
    ix0 = dxfedit.index(doc0)
    ixs = dxfedit.summary_for_llm(ix0)
    emit({"stage": "build", "msg": f"Semantic Index: 존 {len(ixs['zones'])}, 장비 {len(ixs['equipment'])}, 레이어 {len(ixs['layers'])}"})
    msgs = [{"role": "user", "content": f"[인덱스]\n{json.dumps(ixs, ensure_ascii=False)}\n\n[요청]\n{text}"}]
    letter = chr(ord("A") + len([x for x in p["revisions"] if x.get("ops_log") is not None]))
    for attempt in range(1, MAX_REPAIR + 2):
        r["attempts"] = attempt
        try:
            j, _ = ask_json(prompt("dxf_edit"), msgs, model, emit, "편집 연산" + (" (수리)" if attempt > 1 else ""))
        except Exception as e:
            r.update(error=f"LLM 실패: {e}", status="failed")
            return finish(p, r, outdir, emit)
        ops = j.get("ops") or []
        doc = dxfedit.read(src)
        ix = dxfedit.index(doc)
        log, errs = dxfedit.apply_ops(doc, ix, ops, letter)
        if not errs or attempt > MAX_REPAIR:
            break
        emit({"stage": "repair", "msg": f"수리 {attempt}/{MAX_REPAIR}: " + "; ".join(errs)[:200]})
        msgs = msgs + [{"role": "assistant", "content": json.dumps(j, ensure_ascii=False)},
                       {"role": "user", "content": "[오류]\n" + "\n".join(errs) + "\n\n고친 전체 ops 를 다시 출력."}]
    stem = os.path.splitext(p["input"])[0]
    stem = re.sub(r"_rev_[A-Z]$", "", stem)
    name = f"{stem}_rev_{letter}.dxf"
    doc.saveas(os.path.join(outdir, name))
    ix1 = dxfedit.index(doc)
    d = dxfedit.diff(ix0, ix1)
    dump_json(os.path.join(outdir, "ops.json"), {"revision": letter, "source": os.path.basename(src), "ops": ops, "log": log, "errors": errs})
    r.update(ops_log=log, diff={k: v for k, v in d.items()}, summary=j.get("summary", ""))
    r["files"] = {name: f"수정본 (rev {letter}, 원본 보존)", "ops.json": "연산 로그"}
    r["previews"] = {"drawing": _svg(doc, outdir, "drawing.svg", [x["handle"] for x in log if x.get("handle")] + [e["h"] for e in d["added"]])}
    r["checks"] = [{"id": "EDIT-001", "severity": "error", "msg": e} for e in errs]
    if not log and not errs:
        r["checks"].append({"id": "EDIT-002", "severity": "warning", "msg": "적용된 연산이 없음"})
    r["metrics"] = {"ops": len(ops), "applied": len(ops) - len(errs), "items": len(log), "failed": len(errs), "added": len(d["added"]), "removed": len(d["removed"]), "moved": len(d["moved"])}
    r["status"] = _status(r)
    return finish(p, r, outdir, emit)
