"""원자력 설비 지식 DB — kb/archetypes/*.json (원본) + kb/overrides/<id>.json (사용자 수정·추가, 이력 포함).

에이전트: search() 로 요청에 맞는 원형을 찾아 brief() 를 프롬프트에 넣는다 (벡터 RAG 대신 구조화 메타데이터 검색, 설계문서 §38).
사람: UI '지식 DB' 탭에서 원칙·기본값·검증을 고치면 save_override() 로 저장 → 다음 요청부터 바로 반영. 원본 파일은 건드리지 않는다.
"""
import copy
import datetime
import json
import os
import re

from .util import ROOT, dump_json, load_json

KB = os.path.join(ROOT, "kb")
ARCH = os.path.join(KB, "archetypes")
# 연구원 수정본은 사용자 데이터 → 데이터 폴더(WORKSPACE)에 둔다 (도구 업데이트로 덮어쓰이지 않게). 없으면 kb/overrides
OVR = os.environ.get("KB_OVERRIDES") or (os.path.join(os.environ["WORKSPACE"], "kb_overrides") if os.environ.get("WORKSPACE") else os.path.join(KB, "overrides"))
EDITABLE = ("name", "summary", "aliases", "principles", "checks", "design", "source", "sizing", "components", "connections", "loops", "kind", "coolant")


def components():
    return load_json(os.path.join(KB, "components.json"))


def coolants():
    return load_json(os.path.join(KB, "coolants.json"))


def _merge(base, ov):
    a = copy.deepcopy(base)
    for k, v in ov.items():
        if k in ("history", "new", "id", "created_by"):
            continue
        if k == "design" and isinstance(v, dict):
            d = a.setdefault("design", {})
            for dk, dv in v.items():
                if isinstance(dv, dict):
                    d[dk] = {**d.get(dk, {}), **dv}
                else:
                    d.setdefault(dk, {})["v"] = dv
        else:
            a[k] = v
    a["edited"] = bool(ov.get("history"))
    a["history"] = ov.get("history", [])
    return a


def load_all():
    out = {}
    for n in sorted(os.listdir(ARCH)):
        if n.endswith(".json"):
            a = load_json(os.path.join(ARCH, n))
            out[a["id"]] = a
    if os.path.isdir(OVR):
        for n in sorted(os.listdir(OVR)):
            if n.endswith(".json"):
                ov = load_json(os.path.join(OVR, n))
                aid = n[:-5]
                if aid in out:
                    out[aid] = _merge(out[aid], ov)
                elif ov.get("new"):
                    a = {k: v for k, v in ov.items() if k != "new"}
                    a["id"] = aid
                    a["user_added"] = True
                    out[aid] = a
    return out


def get(aid):
    a = load_all().get(aid)
    if not a:
        raise KeyError(f"지식 DB 에 원형 {aid!r} 없음")
    return a


def search(text, k=3):
    t = (text or "").lower()
    scored = []
    for a in load_all().values():
        s = 0
        for al in a.get("aliases", []) + [a.get("name", "")]:
            al = al.lower()
            if al and al in t:
                s += 3 + len(al) / 10
        for w in re.findall(r"[\w가-힣]{2,}", t):
            if w in (a.get("summary", "") + a.get("name", "")).lower():
                s += 0.3
        scored.append((s, a["id"]))
    scored.sort(reverse=True)
    return [aid for s, aid in scored[:k] if s > 0.5]


def brief(a, full=True):
    """LLM 프롬프트용 요약."""
    L = [f"### 원형 `{a['id']}` — {a['name']}", a.get("summary", "")]
    if a.get("design"):
        L.append("설계 변수 (기본값 [범위] 단위): " + "; ".join(f"{k}={d.get('v')} [{'~'.join(map(str, d.get('range', [])))}] {d.get('unit', '')} ({d.get('label', '')})"
                                                     for k, d in a["design"].items()))
    if full and a.get("components"):
        L.append("기기: " + ", ".join(f"{c['id']}({c['type']}" + (f"×{c['count']}" if c.get("count") else "") + ")" for c in a["components"]))
    elif a.get("components_note"):
        L.append("기기: " + a["components_note"])
    if a.get("principles"):
        L.append("설계 원칙:\n" + "\n".join(f"- {p}" for p in a["principles"]))
    return "\n".join(L)


def _owner_of(aid):
    """수정 권한 주인: 기본 원형(코드에 든 것)은 관리자(owner)만, 사용자가 추가한 원형은 만든 사람(created_by).
    만든 사람 기록이 없는 예전 추가 원형도 관리자만."""
    if os.path.exists(os.path.join(ARCH, aid + ".json")):
        return "owner"
    p = os.path.join(OVR, aid + ".json")
    ov = load_json(p) if os.path.exists(p) else {}
    return ov.get("created_by") or "owner"


def can_edit(aid, user):
    """user = 포털의 X-Agent-User. 아직 없는 id(새 원형 추가)는 누구나."""
    if user == "owner":
        return True
    if not os.path.exists(os.path.join(ARCH, aid + ".json")) and not os.path.exists(os.path.join(OVR, aid + ".json")):
        return True
    return _owner_of(aid) == user


def public(a, user):
    """응답용: 만든 사람 id 는 관리자에게만, 대신 can_edit 표시."""
    a = dict(a)
    by = a.pop("created_by", None)
    a["can_edit"] = user == "owner" or (bool(by) and by == user and not os.path.exists(os.path.join(ARCH, a["id"] + ".json")))
    if user == "owner":
        a["created_by"] = by
    else:
        a["history"] = [{k: v for k, v in h.items() if k != "user"} for h in a.get("history", [])]
    return a


def _check(aid, user, what):
    if not can_edit(aid, user):
        raise PermissionError(f"이 원형은 {'기본 지식이라 관리자만' if _owner_of(aid) == 'owner' else '만든 사람이나 관리자만'} {what} 수 있습니다")


def save_override(aid, patch, author="", note="", user="owner"):
    """patch: EDITABLE 필드의 새 값. 새 원형이면 patch 에 id·name·kind 등 전체. 이력에 누가·언제·무엇을·왜."""
    if not re.fullmatch(r"[a-z0-9_]{2,40}", aid or ""):
        raise ValueError("원형 id 는 영소문자·숫자·_ 2~40자")
    if not isinstance(patch, dict):
        raise ValueError("patch 는 객체여야 함")
    _check(aid, user, "고칠")
    os.makedirs(OVR, exist_ok=True)
    p = os.path.join(OVR, aid + ".json")
    ov = load_json(p) if os.path.exists(p) else {}
    base_exists = os.path.exists(os.path.join(ARCH, aid + ".json"))
    if not base_exists and not ov:
        if not isinstance(patch.get("name"), str) or not patch["name"].strip():
            raise ValueError("새 원형에는 name 이 필요")
        ov["new"] = True
        ov["created_by"] = user
    bad = [k for k in patch if k not in EDITABLE]
    if bad:
        raise ValueError(f"수정할 수 없는 필드: {bad}")
    if "principles" in patch and not (isinstance(patch["principles"], list) and all(isinstance(x, str) and x.strip() for x in patch["principles"])):
        raise ValueError("principles 는 비어 있지 않은 문자열 목록")
    if "design" in patch and not isinstance(patch["design"], dict):
        raise ValueError("design 은 객체여야 함")
    for k, v in patch.items():
        ov[k] = v
    ov.setdefault("history", []).append({"ts": datetime.datetime.now().isoformat(timespec="seconds"), "author": str(author or "")[:40] or "익명",
                                         "user": user, "note": str(note or "")[:300], "fields": sorted(patch)})
    dump_json(p, ov)
    return get(aid)


def reset_override(aid, author="", user="owner"):
    if not os.path.exists(os.path.join(ARCH, aid + ".json")) and not os.path.exists(os.path.join(OVR, aid + ".json")):
        raise KeyError(aid)
    _check(aid, user, "되돌릴")
    p = os.path.join(OVR, aid + ".json")
    if os.path.exists(p):
        os.rename(p, p + "." + datetime.datetime.now().strftime("%Y%m%d%H%M%S") + ".bak")
    return get(aid) if os.path.exists(os.path.join(ARCH, aid + ".json")) else None
