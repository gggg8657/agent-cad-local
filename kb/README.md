# 원자력 설비 지식 DB (kb/)

에이전트가 원자로·원자력 설비를 그릴 때 **찾아 쓰는** 구조화 지식. 사람(연구원)이 읽고 고치는 것이 전제다.

- `archetypes/*.json` — 노형·설비 원형: 기기 구성, 배치 문법, 사이징 식, 계통 루프, **설계 원칙**, 검증 관계, 출처
- `components.json` — 기기 형상 템플릿 (vessel·pump·hx·pool…) 과 레이어·색
- `coolants.json` — 냉각재 대표 물성 (밀도·비열 — 운전 조건 대표값)
- 사용자 수정본 — UI "지식 DB" 탭에서 고친 내용 (원본 위에 덮어씀, 이력 포함). 데이터 폴더 `$WORKSPACE/kb_overrides/<id>.json` (포털: `_data/agent-cad-local/kb_overrides`), WORKSPACE 가 없으면 `kb/overrides/`. 원본 파일은 건드리지 않는다.

**수치 주의**: 모든 값은 공개 교과서·개요 자료 수준의 *대표 범위*이며 특정 실제 설계값이 아니다. 사내 설계 기준·검증 자료로 교체해 쓰는 것을 전제로 한다 (`source` 필드에 근거를 남길 것).

## 배치 문법 (components[].place)
모든 z 는 기기 **바닥** 높이(mm). 수식에는 설계 변수와 앞서 배치된 기기의 기하 변수(`<id>_top` `<id>_bot` `<id>_R` `<id>_D` `<id>_H` `<id>_x` `<id>_y`)를 쓸 수 있다.
| 종류 | 예 | 의미 |
|---|---|---|
| `at` | `{"at": {"z": "0", "xy": [0, 0]}}` | 절대 위치 |
| `ring` | `{"ring": {"r": "core_R + sg_D/2 + 200", "z": "core_top + 500", "a0": 0}}` | count 개를 원주 배치 |
| `grid` | `{"grid": {"nx": 2, "ny": 2, "pitch": "tank_D*1.4", "z": "-6000"}}` | 격자 |
| `line` | `{"line": {"pitch": "cell_W", "z": "0", "dir": "x"}}` | 일렬 |
| `beside` | `{"beside": {"of": "rpv", "dir": "+x", "gap": "2000", "z": "rpv_bot"}}` | 옆 |
| `contain` | `{"contain": {"ids": ["core", "sg"], "pad": "300", "pad_top": "800", "pad_bot": "600"}}` | 감싸는 용기 (크기 자동) |
