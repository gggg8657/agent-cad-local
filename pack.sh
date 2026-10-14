#!/usr/bin/env bash
# 폐쇄망 반입 번들 (인터넷 되는 PC에서 실행). 기본 linux-x64 / Python 3.12.
#   ./pack.sh                         # → dist-offline/agent-cad-local-linux-x64.tar.gz
#   PYV=3.11 ./pack.sh                # 대상 서버 Python 버전에 맞춰
set -euo pipefail; cd "$(dirname "$0")"
PLAT="${PLAT:-manylinux_2_28_x86_64}"; PYV="${PYV:-3.12}"
STAGE=dist-offline/agent-cad-local-linux-x64; rm -rf "$STAGE"; mkdir -p "$STAGE/wheels"
pip download -q -r requirements.txt -d "$STAGE/wheels" --platform "$PLAT" --platform manylinux2014_x86_64 --python-version "$PYV" --only-binary=:all:
cp -r app.py portal_jobs.py ui.html cad prompts rules library standards static sample gt kb bench_gt.py selftest.py setup.sh requirements.txt README.md GOAL.md NOTICE LICENSE \
      local_unified_cad_agent_architecture.md "$STAGE/"
find "$STAGE" -name __pycache__ -prune -exec rm -rf {} +
cat > "$STAGE/INSTALL.md" <<INS
# 폐쇄망 설치 (agent-cad-local)
tar -xzf agent-cad-local-linux-x64.tar.gz && cd agent-cad-local-linux-x64
# Python $PYV 가 서버에 있어야 함. wheels/ 가 있으면 인터넷 없이 설치
LLM_BASE_URL=http://<gpu-host>:11434 bash setup.sh                 # Ollama
LLM_API=openai LLM_BASE_URL=http://<gpu-host>:8000/v1 bash setup.sh  # vLLM 등
# 외부 통신: LLM 서버 주소뿐 (three.js 동봉, CDN 없음)
INS
( cd dist-offline && tar -czf agent-cad-local-linux-x64.tar.gz agent-cad-local-linux-x64 ); ls -lh dist-offline/*.tar.gz
