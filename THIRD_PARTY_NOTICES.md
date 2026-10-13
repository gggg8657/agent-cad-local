# 제3자 구성요소 (Third-party notices) — agent-cad-local

조사일 2026-10-12. 실제 파일(`requirements.txt`, `setup.sh`, `pack.sh`, `cad/*.py`·`app.py` import, `static/`, `standards/`, `kb/`, `library/`, `gt/`, `sample/`) 기준.
버전은 이 저장소의 `venv` 에 실제 설치된 값(`requirements.txt` 는 하한만 고정). 라이선스는 패키지 메타데이터·휠 안 LICENSE·SBOM·GitHub LICENSE·모델 카드에서 확인했다.
이 표는 고지용 정리이며 법률 자문이 아니다. 상업 이용 전에는 원문을 다시 확인할 것.

## 동봉 (저장소에 들어 있음)

| 이름 | 버전 | 쓰임 | 라이선스 | 링크 | 상업 이용 | 비고 |
|---|---|---|---|---|---|---|
| three.js (`static/three.min.js`, `static/GLTFLoader.js`, `static/OrbitControls.js`) | r128 | 3D(GLB) 뷰어 | MIT | https://github.com/mrdoob/three.js | 가능 | 원문 `static/LICENSE-three.txt`. 수정 없음 |
| 나눔고딕 NanumGothic (`static/fonts/NanumGothic.ttf`) | — | 도면 → PNG 렌더(`cad/raster.py`) 한글 글꼴 | SIL Open Font License 1.1 | https://hangeul.naver.com/font | 가능 | 원문 `static/fonts/OFL.txt` (Copyright (c) 2010 NHN Corporation, Reserved Font Name Nanum…). 글꼴만 따로 판매 금지, 수정하면 이름 변경 필요 |

## 설치 (`setup.sh` → pip, 동봉 안 함 — `pack.sh` 는 휠을 번들에 넣음)

| 이름 | 버전 | 쓰임 | 라이선스 | 링크 | 상업 이용 | 비고 |
|---|---|---|---|---|---|---|
| build123d | 0.13.0 | 파라메트릭 3D, STEP/STL/GLB 내보내기 | Apache-2.0 | https://github.com/gumyr/build123d | 가능 | |
| cadquery-ocp-novtk / cadquery-ocp-proxy (OCP) | 8.0.1.1.0 | build123d 의 OCCT 파이썬 바인딩 | Apache-2.0 | https://github.com/CadQuery/OCP | 가능 | |
| └ Open CASCADE Technology (휠 안 `libTK*.so`) | 8.0.1 | CAD 커널 | LGPL-2.1 + Open CASCADE exception | https://github.com/Open-Cascade-SAS/OCCT | **조건부** | [#1](https://github.com/gggg8657/agent-cad-local/issues/1) |
| └ OCP 휠에 함께 묶인 시스템 라이브러리 | 휠 SBOM 기준 | 이미지·글꼴 입출력 등 | FreeImage (GPL-2/GPL-3 또는 FreeImage Public License), LibRaw (LGPL-2.1 또는 CDDL-1.0), libgomp (GPL-3 + GCC Runtime Library Exception), OpenEXR·libjpeg-turbo·libpng·libtiff·openjpeg2·jasper·jxrlib·lcms2·freetype·fontconfig·fmt·bzip2·xz·libwebp (각자 허용형) | 휠의 `cadquery_ocp_novtk-*.dist-info/sboms/auditwheel.cdx.json` | **조건부** | [#1](https://github.com/gggg8657/agent-cad-local/issues/1) |
| ezdxf | 1.4.4 | DXF 읽기·쓰기·블록·치수 | MIT | https://github.com/mozman/ezdxf | 가능 | |
| shapely | 2.1.2 | 2D 검증 | BSD-3-Clause (휠 안 GEOS: LGPL-2.1) | https://github.com/shapely/shapely | 조건부 | GEOS 는 동적 링크. [#1](https://github.com/gggg8657/agent-cad-local/issues/1) 에 함께 기록 |
| Pillow | 12.2.0 | 도면 → PNG | MIT-CMU (HPND) | https://github.com/python-pillow/Pillow | 가능 | |
| build123d 의존성 | venv 기준 | — | numpy (BSD-3 등), scipy (BSD-3), sympy (BSD-3), scikit-learn (BSD-3), anytree · ocp_gordon · threejs-materials · bd_materials · ocpsvg (Apache-2.0), lib3mf (BSD-3), pygltflib · svgpathtools · svgelements · svgwrite · trianglesolver · ezdxf (MIT), webcolors (BSD-3), ipython (BSD-3), requests (Apache-2.0), certifi (MPL-2.0) 등 | PyPI 각 패키지 | 가능 | certifi 는 MPL-2.0(파일 단위 약한 카피레프트, 수정 없이 사용) |

## 외부 프로그램·서비스

| 이름 | 쓰임 | 라이선스 | 링크 | 상업 이용 | 비고 |
|---|---|---|---|---|---|
| Ollama | LLM 실행기(있으면 모델 pull) | MIT | https://github.com/ollama/ollama | 가능 | vLLM 등 OpenAI 호환 서버로 바꿀 수 있음 |
| 맑은 고딕 (`malgun.ttf`) | DXF 문자 스타일의 **글꼴 이름만** 지정 (`standards/ict_v1.json`, `cad/dxfkit.py`) | Microsoft 글꼴 | — | 해당 없음 | 글꼴 파일은 들어 있지 않음. 도면을 여는 PC 의 글꼴을 씀 |

## 모델 가중치 (저장소에 없음 — 사용자가 내려받음)

| 이름 | 쓰임 | 라이선스 | 링크 | 상업 이용 | 비고 |
|---|---|---|---|---|---|
| Qwen3-8B (`qwen3:8b`) | `setup.sh`·`cad/llm.py` 기본 모델 | Apache-2.0 | https://huggingface.co/Qwen/Qwen3-8B | 가능 | `MODEL=`/`LLM_MODEL=` 로 변경 |
| Gemma 4 31B (`gemma4:31b`) | 포털 배포 기본 모델(텍스트·VLM 판독) | Apache-2.0 | https://ai.google.dev/gemma/docs/gemma_4_license | 가능 | |
| Llama 4 Scout (`llama4:scout`) | README 실측 비교에만 등장, 기본값 아님 | Llama 4 Community License | https://www.llama.com/llama4/license/ | 조건부 | 쓰지 않으면 해당 없음 |

## 참고만 한 것 (코드 복사 없음)

| 이름 | 쓰임 | 라이선스 | 링크 |
|---|---|---|---|
| earthtojake/text-to-cad | 설계 참고(3D/DXF CAD 에이전트 스킬 구성) | MIT | https://github.com/earthtojake/text-to-cad |
| jeremylongshore/cad-ai-agent | 설계 참고("LLM 은 타입 있는 작업을 계획, 결정론 엔진이 DXF 편집" 방식) | Apache-2.0 | https://github.com/jeremylongshore/cad-ai-agent |
| ISO 273 (여유 구멍 지름), KS B ISO 2768 (일반 공차) | `cad/part2d.py`, `cad/worker3d.py`, `prompts/part2d.md` 에서 **표준 번호와 수치만** 사용 | 표준 문서는 ISO 저작물 | 표준 본문·표 사본은 들어 있지 않음 |
| 원자력 설비 대표값 (`kb/`, `library/`) | 노형별 대표 범위(IAEA SMR 개요, MSRE 규모 등 공개 개요 자료 수준) | 이 저장소에서 작성 | 원문 인용 없음. 실제 설계값 아님 |

## 데이터 (저장소에 들어 있음)

| 파일 | 출처 | 비고 |
|---|---|---|
| `sample/station_B2.dxf` | `sample/make_sample.py` 로 생성 | 안의 `OpenSans-*.ttf` 는 ezdxf 기본 문자 스타일의 글꼴 이름일 뿐 글꼴 파일 아님 |
| `gt/msr_3loop_synth.png`, `gt/msr_3loop_synth.json` | 이 도구가 생성한 합성 GT | |
| `gt/msr_ureca_like.json` | 사용자 제공 개념도를 사람이 판독 (원본 그림 `gt/msr_ureca_like.png` 는 `.gitignore` 로 제외, git 이력에도 없음) | **출처 미확인** → [#2](https://github.com/gggg8657/agent-cad-local/issues/2) |
| `standards/ict_v1.json` | 레이어·색·선종류 표 (`"standard": "KAERI_ICT_V1"`) | 기관명이 들어 있어 내부 표준에서 왔는지 **확인 필요** → [#2](https://github.com/gggg8657/agent-cad-local/issues/2) |

## 확인 필요

- **Open CASCADE·OCP 휠의 네이티브 라이브러리·GEOS — LGPL-2.1 등** → [#1](https://github.com/gggg8657/agent-cad-local/issues/1).
  pip 로 각자 설치하는 내부 사용은 해당 패키지 조건 그대로. `pack.sh` 로 만든 폐쇄망 번들(`wheels/` 포함)을 남에게 주면 바이너리 재배포가 되므로 휠 안 라이선스 파일을 지우지 말고 이 파일과 소스 위치 안내를 같이 줄 것.
- **GT·표준 데이터 출처 — `gt/msr_ureca_like.json`, `standards/ict_v1.json`** → [#2](https://github.com/gggg8657/agent-cad-local/issues/2).
  원본 그림 출처가 공개 문헌이면 `source` 에 적고, 비공개 자료이면 공개 저장소에서 뺀다. `ict_v1.json` 은 새로 만든 예시라면 기관명 없는 이름으로 바꾼다.

## 이 저장소의 원저작물

`app.py`, `ui.html`, `cad/*.py`, `prompts/*.md`, `rules/*.json`, `library/*.json`, `standards/*.json`, `kb/`, `sample/make_sample.py`, `gt/*.json`, `bench_gt.py`, `selftest.py`, `setup.sh`, `pack.sh`, `README.md`, `GOAL.md`, `local_unified_cad_agent_architecture.md` — DongJu Kim (gggg8657), PolyForm Noncommercial 1.0.0 (`LICENSE`). 단 `gt/msr_ureca_like.json` 의 바탕이 된 그림은 위 "확인 필요" 참고.
