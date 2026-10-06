# GOAL — agent-cad-local (폐쇄망 로컬 CAD Agent)

> 근거 문서: `local_unified_cad_agent_architecture.md` (전체 비전, 47절).
> 이 파일은 그 비전 중 **지금 이 폴더에서 실제로 동작하게 만드는 범위(MVP)** 와 완료 기준을 정한다.

## 한 줄 목표

**로컬 LLM은 JSON(Project IR)만 쓰고, 기하는 결정론적 엔진(build123d·ezdxf·자체 배치/그래프 엔진)이 만든다.**
한 IR에서 STEP·STL·GLB·DXF·SVG·CSV·검증 보고서가 함께 나오고, 규칙 엔진 오류는 LLM 수리 루프(최대 3회)로 되돌린다.

## 핵심 원칙 (문서 §2, §44 계승)

1. LLM은 좌표 수천 개나 CAD 코드를 쓰지 않는다 → **Feature/Requirement IR(JSON)** 만 출력. 생성 코드 실행이 없으므로 sandbox 대신 *빌드 워커 subprocess + 타임아웃·메모리 제한* 으로 충분.
2. IR이 canonical. 사용자는 IR을 직접 고쳐 **LLM 없이 재빌드** 할 수 있다 (파라메트릭 수정).
3. 생성보다 검증 — 규칙은 `rules/*.json` 으로 분리, 재학습 없이 수정.
4. 원본 DXF는 절대 덮어쓰지 않는다 (save-as `_rev_A`), 건축 레이어(`A-*`)는 보호.
5. 모든 산출물 `DRAFT — NOT FOR CONSTRUCTION`. 엔진이 넣은 **가정(assumption)** 은 보고서에 전부 적는다.
6. 외부 통신 0: CDN 없음(three.js 동봉), 외부 API 없음, LLM은 `LLM_BASE_URL` 하나.

## MVP 범위 (문서 §41)

| 작업 유형 | 입력 | 엔진 | 산출물 |
|---|---|---|---|
| `part3d` 3D 단일 부품 | 자연어 | Feature IR → build123d(OCC) | STEP, STL, GLB, 3면도 DXF/SVG, 질량·부피 |
| `part2d` 2D 판재/가공도 | 자연어 | Profile IR → ezdxf | DXF(외곽·구멍·치수), SVG |
| `ict` ICT 배치도 + 개황도 | 자연어 (+선택: 건축 DXF) | Requirement IR → 공간 배치·장비 배치·자동 결선·케이블 경로 | 배치도 DXF/SVG, 개황도(계통도) DXF/SVG, 랙 실장도 SVG, 장비/케이블 일람표 CSV, 3D 목업 GLB |
| `dxf_edit` 기존 도면 수정 | DXF + 자연어 | Semantic Index → Typed Operation → ezdxf | `_rev_X.dxf`, 변경 diff, 작업 로그 |
| `dxf_analyze` 도면 분석 | DXF | Semantic Index | 레이어/블록/존/텍스트 요약 |
| `assembly` 원자로·원자력 설비 (2026-10-06, 지식 DB 8종 + 연구원 편집 원칙) | 자연어 (+선택: 이미지) | 지식 DB 검색 → 설비 IR → 사이징·배치 문법 → build123d | STEP 조립체, 절개 GLB, GA 단면도, 계통도, 시각 자기검증, GT 점수 |

라우터: UI에서 유형을 고르거나 `자동`이면 규칙(키워드) → 애매하면 LLM 분류.

## 검증 규칙 (기본 세트)

- 3D: 유효 솔리드, 단일 바디, 부피>0, 구멍-모서리 최소 거리, 기대 외형치수(bbox)
- 2D: 외곽 폐곡선, 구멍이 외곽 내부, 구멍 간 겹침/최소 웹, 최소 구멍경(판두께 이상)
- ICT: 코어 도달성(ICT-001), 코어 이중화·이중 귀속(002), 스위치 포트 여유(003), ID 중복(004), UTP 90 m(005), 랙 U 여유(006), 공간 내부 배치(007), AP 커버리지(008), PoE 예산(009)

## 완료 기준 (Definition of Done)

- [ ] `bash setup.sh` 한 번으로 venv·의존성·selftest·서버(:8778) 기동, 포털 tools.json 등록
- [ ] `python selftest.py` 가 **LLM 없이** 5개 유형 전 경로(IR 정규화 → 빌드 → 검증 → 저장)를 통과
- [ ] gemma4:31b 실측: 문서 §29~§32 예시 프롬프트(브래킷, 판재, 지하철역 ICT, DXF에 AP 추가)가 수리 루프 포함 성공
- [ ] UI: 프롬프트·유형·DXF 첨부 → 진행 상황 스트리밍 → 2D SVG / 3D 뷰어 / IR 편집·재빌드 / 검증 보고서 / 파일 내려받기 / 리비전 목록
- [ ] 다듬기 대화("구멍 8 mm로") → 새 리비전(R01…), IR diff 기록
- [ ] 폐쇄망: `pack.sh` 로 wheel 번들, UI는 로컬 자산만

## 범위 밖 (다음 단계)

VLM 스캔도면 인식, DWG(ODA 변환), 어셈블리 구속, 정식 치수공차(GD&T), 다중 사용자 ACL/승인 워크플로, 실제 건축도면 기반 경로 탐색(벽 회피).
