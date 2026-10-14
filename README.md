# agent-cad-local — 폐쇄망 로컬 CAD Agent (말하면 IR, 엔진이 그린다)

로컬 LLM은 좌표나 CAD 코드를 쓰지 않고 **Project IR(JSON)만** 씁니다. 형상은 결정론적 엔진(OpenCascade/build123d · ezdxf · 자체 배치·결선 엔진)이 만들고, 한 IR에서 STEP·STL·GLB·DXF·SVG·CSV·검증 보고서가 함께 나옵니다. 규칙 위반은 LLM 수리 루프(최대 3회)로 되돌립니다.

> ⚠️ 연구·내부 시험용 프로토타입입니다(배포·상업 서비스용 아님). 제3자 구성요소의 라이선스는 THIRD_PARTY_NOTICES.md 를 확인하세요.

![3D 부품 화면 — ① 3D 탭 ② 리비전 선택 ③ 대화와 결과 이력](docs/img/cad-part3d.png)

근거 설계: `local_unified_cad_agent_architecture.md` · 구현 범위와 완료 기준: `GOAL.md`

## 무엇을 하나

3D 부품 · 2D 판재 · ICT 배치도 · 기존 DXF 수정 · DXF 분석 · 원자로·원자력 설비, 6가지 작업을 한 화면에서 합니다. 모든 산출물에는 `DRAFT — NOT FOR CONSTRUCTION` 을 표기합니다.

### 작업별 입력·산출물

| 작업 | 입력 예 | 산출물 |
|---|---|---|
| **3D 부품** `part3d` | "L형 모터 브래킷, NEMA17 M3 4개…" | STEP · STL · GLB · 3면도 DXF(제3각법) · 질량/부피 |
| **2D 판재** `part2d` | "200x100 판재 R10, M6 4개, t3 SUS304" | 가공도 DXF(치수·표제란) · 레이저용 cut DXF · 절단 길이/질량 |
| **ICT 배치도·개황도** `ict` | "B2 승강장 CCTV 12, AP 4, 비상통화 4, 통신실 B1, 코어 이중화" (+선택: 건축 DXF) | 배치도 DXF · 개황도(계통도) DXF · 랙 실장도 DXF · 장비/케이블/BOM CSV · 3D 목업 GLB |
| **기존 DXF 수정** `dxf_edit` | DXF + "승강장에 AP 4개 추가, CAM-06 삭제" | `<원본>_rev_A.dxf`(원본 보존) · 연산 로그 · 변경 diff · 하이라이트 미리보기 |
| **DXF 분석** `dxf_analyze` | DXF + "무슨 도면이야? 레이어 문제는?" | Semantic Index · 답변 · 레이어 표준 위반 |
| **원자로·원자력 설비** `assembly` (지식 DB 8종 + custom) | "SMART형 PWR 330 MWth", "풀형 SFR", "핫셀 3칸" (+선택: 개념도 이미지) | STEP 조립체 · 절개 GLB · GA 단면도 DXF · 계통도 DXF · 기기표 CSV · 사이징 · 시각 자기검증 · **GT 점수** |

## 사용 방법

번호는 맨 위 3D 부품 화면의 상자 번호입니다.

1. **작업을 고르거나 '자동 분류'로 둔다** — 3D 부품 · 2D 판재 · ICT 배치도 · DXF 수정 · DXF 분석 · 원자로·원자력 설비. 자동이면 키워드로 나누고, 애매할 때만 LLM이 고릅니다. 왼쪽 예시 버튼으로 시작해도 됩니다.
2. **치수·수량을 말로 쓰고 실행한다** — DXF나 그림(PNG·JPG)을 첨부하면 함께 읽습니다(명령이 우선). 진행 로그에 LLM 작성 → 빌드 → 규칙 검증 → 수리 과정이 실시간으로 나오고, 결과는 오른쪽 탭(도면 · ①3D · IR · 검증 보고서 · 파일 · 지식 DB)에서 봅니다. 3D는 드래그 회전 · 휠 확대 · 우클릭 이동.
3. **같은 칸에 후속 지시를 쓰거나 IR을 직접 고친다** — "벽두께 2.5로"처럼 쓰면 새 리비전(R01…)이 ③ 이력에 쌓이고, ② 리비전 선택으로 각 리비전의 모델·IR·보고서·파일을 불러옵니다. IR 탭에서 값을 고치면 LLM 없이 다시 빌드합니다.

## 예시

모두 gemma4:31b 로 실제 실행한 결과입니다.

### 3D 부품 — 자연어 생성 → 후속 지시 (맨 위 화면)

- **입력 R00**: `L형 카메라 마운트 브래킷: 바닥 다리 60, 세운 다리 45, 폭 40, 두께 4, 알루미늄. 세운 다리 중앙에 Ø6.5 카메라 나사 구멍, 바닥에 Ø5.5 구멍 2개. 부품명: 카메라 마운트 브래킷 (설계 김동주)` → 후속 입력 `바닥 구멍 2개를 Ø8로 키워줘`
- **출력**: `camera_mount_bracket_kim_dong_ju.step` · `.stl` · `.glb` · `camera_mount_bracket_kim_dong_ju_drawing.dxf`(3면도·표제란) · `ir.json` · `report.md`

| 리비전 | 실제 변경 | 외형 · 질량 | 검증 |
|---|---|---|---|
| R00 | 자연어 생성 (검증 실패 후 자동 수리 2회) | 60×40×45 mm · 42.7 g | PASS |
| R01 | 후속 지시 · 바닥 구멍 Ø5.5 → Ø8 | 60×40×45 mm · 42.1 g | PASS |

### 2D 판재 — 가공도 DXF와 레이저 cut DXF

![2D 판재 — ① 결과 탭 ② 도면 팬·줌](docs/img/cad-part2d.png)

- **입력**: `200 x 100 판재, 두께 3 SUS304. 모서리 R10. M6 볼트 구멍 네 개 (모서리에서 12). 중앙에 40x20 사각 구멍 R3`
- **출력(통과)**: `plate_200x100_m6.dxf`(치수·중심선·표제란) · `plate_200x100_m6_cut.dxf`(외곽·구멍만, 레이저/워터젯용) · `ir.json` · `report.md` — 외형 200 × 100 mm, M6 여유 구멍(Ø6.6) 4개, 총 절단 길이 780.6 mm, 계산 질량 451.6 g

### ICT 배치도 — 배치·결선·케이블 자동 산정

![ICT — ① 도면 서브탭 ② 배치도 캔버스 ③ 장비 ID 툴팁 ④ 결과 탭](docs/img/cad-ict.png)

- **입력**: `지하철역 B2 승강장 ICT 배치도 초안 작성. CCTV 12대, AP 4대, 비상통화장치 4대. 통신실은 B1이고 Access SW 2대를 통해 Core SW 이중화.`
- **출력**: 배치도·개황도(계통도)·랙 실장도 DXF 3종, 장비·케이블·BOM CSV 3종, 3D 목업 GLB, `model.json` · `ir.json` · `report.md` — 장비 33대, 19인치 랙(42U) 3개, 결선 27개, 총 케이블 1,227 m. 규칙 ICT-001~011로 검증(이 실행은 경고 1 · 수리 1회).

### 기존 DXF 수정 — 원본 보존 Save-As

![DXF 수정 — ① 도면 하이라이트 ② 결과 탭·리비전 ③ 실행 이력 배지 ④ 원본 DXF 첨부](docs/img/cad-dxf-edit.png)

- **입력**: `station_B2.dxf` 첨부 + `승강장에 AP 4개 추가하고, CAM-06 은 삭제, CAM-03 은 역무실로 이동`
- **LLM이 만든 연산**: `add_equipment(ap ×4, 승강장)` · `delete(CAM-06)` · `move(CAM-03 → 역무실)` — 적용 3/3
- **출력**: `station_B2_rev_A.dxf`(원본은 그대로) · `ops.json` · `report.md`(diff) — AP-01~04 추가, CAM-06 삭제, CAM-03 이동, 변경 위치에 리비전 델타(Δ) 표시

### 원자로·원자력 설비 — 지식 DB 원형으로 SMART형 PWR

![원자로·원자력 설비 — ① GA/계통도 서브탭 ② GA 단면도](docs/img/cad-assembly.png)

- **입력**: `SMART 같은 일체형 가압경수로 단면도. 열출력 330 MWth, 증기발생기 8기`
- **출력(pass)**: 원형 `pwr_integral`, 기기 16개 — `.step` · 절개 `.glb` · GA·계통도 `.dxf` · 기기표 `.csv` · `model.json` · `report.md`

## 설치·실행

```bash
bash setup.sh                    # venv(build123d·ezdxf·shapely) → LLM 탐색 → selftest → http://localhost:8778
bash setup.sh stop
LLM_API=openai LLM_BASE_URL=http://gpu:8000/v1 bash setup.sh       # vLLM 등
venv/bin/python app.py --cli "직경 40 길이 120 강철 축, 양 끝 C1, 끝면 M8 탭 15" part3d   # 결과 폴더 경로 출력
venv/bin/python selftest.py      # LLM 없이 5개 작업 + 수리 루프 + 재빌드 + 리비전 (35항목, ~7초)
./pack.sh                        # 폐쇄망 반입 번들 (wheels 포함)
```

| 환경변수 | 기본 | 설명 |
|---|---|---|
| `LLM_API` / `LLM_BASE_URL` / `LLM_MODEL` | `ollama` / `:11434` / `qwen3:8b` | 30B 급 권장. 포털로 띄우면 로컬 Ollama `gemma4:31b` (실측 모델) |
| `VISION_MODEL` | (`LLM_MODEL` 과 같음) | 그림 판독용 VLM |
| `LLM_API_KEY` | (없음) | OpenAI 호환 서버 키 |
| `NUM_CTX` | `32768` | Ollama 컨텍스트 |
| `PORT` | `8778` | |
| `WORKSPACE` | `./_workspace` | 프로젝트·리비전 저장 (포털은 `_data/agent-cad-local`) |
| `BIND` | (모든 인터페이스) | 듣는 주소. 포털은 `127.0.0.1` 로 띄워 포털을 거쳐서만 접근 |
| `MAX_REPAIR` | `3` | 수리 루프 최대 횟수 |

프로젝트·업로드는 사용자별로 따로 저장합니다(포털 접속자 기준, 포털이 넘기는 `X-Agent-User` 헤더 → `$WORKSPACE/u/<사용자>/projects`·`uploads`). 서버 주인(헤더 없음·`owner`)은 `$WORKSPACE` 그대로라 예전 기록도 보입니다. 지식 DB 수정본(`kb_overrides`)은 팀이 함께 쓰는 기준이라 **공유됨**.

포털에서 띄우면 생성·후속 지시·IR 재빌드의 시작/완료/실패를 포털 **작업 알림(🔔)**에 알립니다(`portal_jobs.py`, 포털이 주는 `PORTAL_JOBS_*` 환경변수가 없으면 아무 일도 안 함). 알림을 누르면 이 화면에서 그 프로젝트·리비전(`/#<project>/R03`)이 열립니다.

## 구조

```
요청 ─▶ 라우터(키워드 → 애매하면 LLM) ─▶ LLM: prompts/<task>.md 계약으로 IR(JSON)만
      ─▶ 정규화(cad/<task>.py: 파라미터 수식 평가·스키마 검사·CAD 관례 보정)
      ─▶ 결정론 빌드 ─▶ 규칙 검증(rules/*.json) ─▶ error 면 [현재 IR]+[오류] 로 LLM 수리 (≤3)
      ─▶ 리비전 저장 R00/R01… (ir.json · 산출물 · report.md · attempt*.json 수리 이력)
```

- `cad/part3d.py` + `cad/worker3d.py` — Feature IR(base: box/cylinder/tube/extrude/revolve, features: hole·pocket·boss·add/cut·shell·fillet·chamfer, 패턴 corners/grid/polar/line, 나사 여유/탭 표) → **서브프로세스**에서 build123d. LLM 생성 코드는 실행하지 않으므로 sandbox 대신 타임아웃만.
- `cad/part2d.py` — Profile IR → shapely 검증(외곽 이탈·겹침·웹·판두께 대비 구멍) → ezdxf 가공도/cut.
- `cad/ict.py` — 요구 IR(층·공간·장비 수량) → 공간 배치 → 장비 배치 규칙(CCTV 2열 교차 화각, AP 격자, 비상통화 벽측) → **엔진 자동 산정**(코어 2대, 층별 접속 스위치 수 = 포트·PoE 여유 기준, UTP 90 m 안 들면 통신분기실 추가, NVR 64ch, ODF·패치·UPS) → 최근접·포트·PoE 기준 결선 → 트레이·TPS 경유 케이블 길이. 엔진이 채운 것은 전부 보고서 "가정"에.
- `cad/ict_draw.py` — 같은 설계 모델에서 배치도·개황도·랙도·GLB 를 파생 (수량·ID 불일치 없음).
- `cad/dxfedit.py` — DXF → Semantic Index(레이어·블록·라벨 존·장비·텍스트) → LLM 은 요약만 보고 typed ops(add_equipment/move/delete/rotate/set_attr/add_text/connect) → 보호 레이어(`A-*`) 거부 → save-as + 리비전 델타(Δ) 표시.
- `cad/svg.py` — DXF → SVG 자체 렌더러 (미리보기 = DXF 그대로). `static/` three.js r128 동봉 (CDN 없음).
- `rules/ict.json`(ICT-001~011), `rules/part.json`, `standards/ict_v1.json`(레이어·색·보호 패턴), `library/ict.json`(심볼·포트·PoE·U·3D 크기) — **코드 수정 없이** 회사 표준에 맞춰 바꾼다.

## 원자로·원자력 설비 — 지식 DB (kb/)

처음엔 MSR 전용이었다. 2026-10-06 점검에서 SMART형 PWR·풀형 SFR·연구로 요청이 **전부 MSR 로 잘못 그려지고 "통과"로 표시**되는 문제가 확인돼, 노형 지식을 DB 로 분리하고 범용 엔진(`cad/plant.py`)을 만들었다.

- `kb/archetypes/*.json` — 원형 8종: 일체형 PWR · 루프형 PWR · 풀형 SFR · 블록형 HTGR · 개방수조 연구로 · MSR(전용 엔진) · 핫셀 · 사용후핵연료 저장조.
  각 원형 = 설계 변수(기본값·범위·단위) + 사이징 식 + 기기 배치 문법(at/ring/grid/line/beside/contain) + 배관 + 계통 루프 + **설계 원칙** + 검증(배치 관계·수식) + 출처
- `kb/components.json`(형상 템플릿), `kb/coolants.json`(대표 물성). 수치는 공개 교과서 수준 대표값 — 사내 기준으로 교체 전제.
- 에이전트: 요청 → `kb.search`(별칭·키워드) → 상위 3개 원형 요약·원칙을 프롬프트에 → LLM 은 원형·설계 변수·수량·추가 기기만 → 엔진이 사이징·배치·3D·GA·계통도·검증.
  맞는 원형이 없으면 `custom` 으로 같은 배치 문법을 써서 직접 조합.
- **연구원 편집**: UI "📚 지식 DB" 탭 / `POST /api/kb/<id>` — 원칙·기본값·검증·키워드 수정, 수정자·사유 이력, 원본 복원, 복제로 사내안 추가.
  수정본은 `$WORKSPACE/kb_overrides/` (원본 파일 불변). 다음 요청부터 반영 — 실측: SFR 에 "[사내] 펌프 수 = IHX 수" 원칙+검증 추가 → LLM 이 펌프 4대로 설계, 보고서에 적용 원칙 기록.

| 요청 (gemma4:31b 실측) | 이전 | 지금 |
|---|---|---|
| SMART형 일체형 PWR 330 MWth | MSR 로 잘못 그림 | pwr_integral, pass (11 s) |
| 풀형 SFR | MSR 로 잘못 그림 | sfr_pool, pass |
| 개방수조 연구로 | MSR 로 잘못 그림 | research_pool, pass |
| 핫셀 3칸 | ICT 배치도로 오분류 | hot_cell, pass |
| 고온가스로 350 MWth / APR1400 4루프 / 사용후핵연료 저장조 5×4 / MSR 20 MWth | — | 각 원형, 전부 pass |

## 이미지 (선택 입력, 모든 작업)

그림을 첨부하면 작업에 맞게 판독(부품: 치수·구멍 / 판재: 외곽·구멍 / 배치: 공간·장비 / 설비: 기기 개수·배치)해서 명령과 함께 LLM 에 준다. **명령이 우선**.
명령에 작업 단서가 없으면("이거 CAD로") VLM 이 그림을 보고 작업을 고른다. 실측: 판재 가공도 그림 + "이거 CAD로" → 2D 판재로 분류, 200×100 R10·t5·Ø20·M6×4·장공까지 복원 (장공 방향만 틀림).

## 이미지 역설계 · GT (MSR)

```
그림 ─▶ VLM 판독 (gemma4:31b, 로컬): 개체 목록 + 종류별 개수 질문(원본·2배 확대) + 뷰별 개수 → 투표, 갈리면 "불확실" 표시
     ─▶ LLM: 판독 + MSR 원리(일체형, 펌프=열교환기 쌍, 중력 배수, He 오프가스)로 내부 추정 → 조립 IR
     ─▶ 엔진 사이징: V=P/q (노심), ṁ=P/(cp·ΔT) (배관·펌프), A=P/(U·ΔTlm) (열교환기), 염 인벤토리×여유 (덤프탱크)
     ─▶ build123d 조립체 (서브프로세스) · 정밀 간섭 · 절개 GLB · GA 단면도 ─▶ 규칙(rules/msr.json MSR-001~009) ─▶ 수리 루프
     ─▶ 시각 자기검증: GA 를 Pillow 로 PNG 렌더 → 원본과 함께 VLM 비교 (일치 < 70 + high 차이면 1회 시각 수리, 나아질 때만 채택)
     ─▶ GT 채점 (gt/*.json): 구성 F1 · 흐름 F1 · 배치 관계(위/아래·동축·대칭·포함·배열) · 비율 → 가중 합
```

- `gt/msr_ureca_like.json` — 사용자 제공 개념도를 사람이 판독한 GT (치수 없음, 위상·관계·비율만). 첨부 이미지가 GT 원본과 같으면 자동 채점.
- `gt/msr_3loop_synth.json` — 글자 없는 3루프 단면·평면 합성 GT (이미지 판독 능력 대조 시험용).
- `venv/bin/python bench_gt.py [gt_id] [n]` — 이미지 역설계 vs 텍스트 설계를 GT 별로 반복 측정.

실측 (gemma4:31b, 2026-10-06):

| GT | 이미지 역설계 | 텍스트만 | 메모 |
|---|---|---|---|
| msr_ureca_like (원본 그림) | **1.00** (2/2회) | 1.00 | 전형적 일체형 MSR 이라 텍스트 사전지식만으로도 맞음 — 이 GT 로는 판독 기여를 구분 못 함 |
| msr_3loop_synth (글자 없음) | 0.89 (2/2회) | 0.94 ("30 MWth"만 준 텍스트) | VLM 이 라벨 없는 원형 배치(펌프·열교환기 3)를 2로 셈 → **판독이 병목** |

해 본 것: 단일 판독(0.82, 개수 오판) → 종류별 개수 질문+확대 투표(1.00/0.89) → 뷰별 3회 투표(0.94/0.90, 오히려 하락 → 기본 1회) → llama4:scout(느리고 더 부정확).

## UI

프롬프트·작업 유형·DXF 첨부 → 진행 스트리밍 → 탭: **도면**(팬/줌, 장비 ID 툴팁) · **3D**(GLB 뷰어) · **IR**(직접 고쳐 *LLM 없이 재빌드*) · **검증 보고서** · **파일**. 결과 뒤 같은 칸에 "벽두께 2.5로" → 새 리비전, IR diff 기록.

## 실측 (gemma4:31b, Ollama, H100)

문서 §29~§32 예시 + 확장 11건 전부 pass/warning (failed 0). 요청당 5~25 s.
- 지하철역 ICT: 사용자가 Access SW 를 B1 통신실에 두게 함 → UTP 90 m 위반 14건 검출 → 수리 1회로 B2 분기실 분산 → pass
- 케이스: "벽두께 2.5로 + 반대편 USB 구멍" 다듬기 → `depth:"T+1"` 파라미터 참조로 관통 유지

## 한계 (다음 단계)

VLM 스캔도면 인식 · DWG(ODA 변환) · 어셈블리 구속 · GD&T · 벽 회피 경로 탐색 · 다중 사용자 승인 워크플로. 모든 산출물은 `DRAFT — NOT FOR CONSTRUCTION`.

## 출처·감사 (Credits)

- [build123d](https://github.com/gumyr/build123d) (Apache-2.0) — 파라메트릭 3D, STEP/STL/GLB
  - [OCP / cadquery-ocp](https://github.com/CadQuery/OCP) (Apache-2.0), [Open CASCADE Technology](https://dev.opencascade.org) (LGPL-2.1 + OCCT exception) — `pack.sh` 번들을 남에게 줄 때는 휠의 라이선스 파일을 지우지 말 것 ([#1](https://github.com/gggg8657/agent-cad-local/issues/1))
- [ezdxf](https://github.com/mozman/ezdxf) (MIT) — DXF 읽기·쓰기
- [shapely](https://github.com/shapely/shapely) (BSD-3-Clause, GEOS: LGPL-2.1) — 2D 검증
- [Pillow](https://github.com/python-pillow/Pillow) (MIT-CMU/HPND) — 도면 PNG 렌더
- 동봉: [three.js](https://github.com/mrdoob/three.js) r128 + GLTFLoader·OrbitControls (MIT, `static/LICENSE-three.txt`)
- 동봉: 나눔고딕 NanumGothic.ttf (SIL Open Font License 1.1, Copyright (c) 2010 NHN Corporation(현 NAVER), `static/fonts/OFL.txt`)
- 설계 참고(코드 복사 없음): [earthtojake/text-to-cad](https://github.com/earthtojake/text-to-cad), [jeremylongshore/cad-ai-agent](https://github.com/jeremylongshore/cad-ai-agent)
- `gt/msr_ureca_like.png`(사용자 제공 개념도)는 출처 확인 전이라 저장소에 넣지 않았습니다(출처 확인 중 — [#2](https://github.com/gggg8657/agent-cad-local/issues/2), `standards/ict_v1.json` 의 이름 포함). 판독 정답(`gt/msr_ureca_like.json`)만 있고, 그림이 없으면 selftest 의 이미지 역설계 검사는 건너뜁니다.
- **LLM 실행** — OpenAI 호환 API 로 호출합니다(모델 가중치는 동봉하지 않음). 기본 배포는 [Ollama](https://github.com/ollama/ollama) (MIT) 위의 Google [Gemma 4](https://ai.google.dev/gemma) `gemma4:31b` ([Apache-2.0](https://ai.google.dev/gemma/docs/gemma_4_license)).
- 이 도구는 [agent-page-portal](https://github.com/gggg8657/agent-page-portal) 에 연결해 쓰도록 만들었습니다(단독 실행도 됨).

버전·라이선스·상업 이용 여부 전체 표는 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md), 저작권 표기는 `NOTICE` 를 보세요.

## 라이선스

[PolyForm Noncommercial License 1.0.0](LICENSE) — Copyright 2026 DongJu Kim (gggg8657).

- 비상업 목적(개인 연구·학습·시험, 교육기관·공공 연구기관·정부기관·비영리 단체 등)이면 쓰고 고치고 다시 배포할 수 있습니다. 다시 배포할 때는 `LICENSE` 와 `Required Notice:` 줄을 함께 주세요.
- 상업적 이용은 저작자의 별도 허락이 필요합니다.
- 2026-10-13 이전 버전(커밋 `9f80875` 까지)을 MIT 로 받아 간 사본에는 MIT 가 계속 적용됩니다. 이 변경은 이후 버전에만 적용됩니다.
- 제3자 구성요소는 각자의 원래 라이선스를 따르며 이 라이선스로 바뀌지 않습니다(`THIRD_PARTY_NOTICES.md`, `NOTICE`).

## 만든 사람

DongJu Kim (gggg8657) — 설계·구현
