#!/usr/bin/env bash
# agent-cad-local — 원샷 설치·실행 (Linux / macOS)
#   bash setup.sh          # venv + build123d/ezdxf + LLM 서버 탐색 + selftest + 웹 서버(:8778)
#   bash setup.sh stop
# 환경변수: LLM_BASE_URL / LLM_API (서버 강제 지정), MODEL (Ollama 에 없을 때 pull, 기본 qwen3:8b — 권장 gemma4:31b 급), PORT (8778), WORKSPACE (데이터)
set -euo pipefail
MODEL="${MODEL:-${LLM_MODEL:-qwen3:8b}}"; PORT="${PORT:-8778}"
if [ -t 1 ]; then B=$'\033[1m'; D=$'\033[2m'; C=$'\033[36m'; G=$'\033[32m'; R=$'\033[31m'; N=$'\033[0m'; else B= D= C= G= R= N=; fi
STEP=0; step() { STEP=$((STEP+1)); printf '  %s[%d/6]%s %s%-14s%s ' "$D" "$STEP" "$N" "$B" "$1" "$N"; }
ok() { printf '%s✔%s %s\n' "$G" "$N" "${1:-}"; }; skip() { printf '%s–%s %s\n' "$D" "$N" "${1:-}"; }
die() { printf '%s✘ %s%s\n\n' "$R" "$*" "$N" >&2; exit 1; }
has() { command -v "$1" >/dev/null 2>&1; }; probe() { curl -fsS -m 2 "$1" >/dev/null 2>&1; }
wait_for() { for _ in $(seq 1 "${2:-30}"); do probe "$1" && return 0; sleep 1; done; return 1; }
printf '\n%s  agent-cad-local%s  말하면 IR, 엔진이 그린다 — 3D 부품 · 2D 판재 · ICT 배치도/개황도 · DXF 수정 (로컬 LLM)\n\n' "$B" "$N"
cd "$(dirname "${BASH_SOURCE[0]:-.}")"

if [ "${1:-}" = "stop" ]; then [ -f .server.pid ] && kill "$(cat .server.pid)" 2>/dev/null && rm -f .server.pid && ok "웹 서버 종료" || skip "실행 중인 서버 없음"; exit 0; fi

step "OS"; case "$(uname -s)" in Darwin*) OS=mac ;; Linux*) OS=linux ;; *) die "지원하지 않는 OS: $(uname -s)" ;; esac; ok "$OS ($(uname -m))"

step "Python venv"
PY=""; for c in python3.12 python3.11 python3.13 python3.10 python3; do has "$c" && "$c" -c 'import sys; sys.exit(0 if (3,10) <= sys.version_info < (3,14) else 1)' 2>/dev/null && { PY=$c; break; }; done
[ -n "$PY" ] || die "Python 3.10~3.13 필요 (OpenCascade 휠 지원 범위)"
if [ ! -x venv/bin/python ]; then if has uv; then uv venv --python "$(command -v $PY)" venv -q; else "$PY" -m venv venv; fi; fi
if ! venv/bin/python -c "import build123d, ezdxf, shapely" 2>/dev/null; then
  if [ -d wheels ]; then venv/bin/pip install -q --no-index --find-links wheels -r requirements.txt   # 폐쇄망 번들
  elif has uv; then VIRTUAL_ENV="$PWD/venv" uv pip install -q -r requirements.txt; else venv/bin/pip install -q -r requirements.txt; fi
fi
ok "$(venv/bin/python --version) + $(venv/bin/python -c 'import build123d,ezdxf;print("build123d",build123d.__version__,"· ezdxf",ezdxf.__version__)')"

step "LLM 서버"
export LLM_API="${LLM_API:-}" LLM_BASE_URL="${LLM_BASE_URL:-}"
if [ -n "$LLM_BASE_URL" ]; then [ -n "$LLM_API" ] || { case "$LLM_BASE_URL" in *1143*) LLM_API=ollama ;; *) LLM_API=openai ;; esac; }; ok "지정됨 → $LLM_API $LLM_BASE_URL"
elif probe http://localhost:11434/api/tags; then LLM_API=ollama LLM_BASE_URL=http://localhost:11434; ok "Ollama (:11434)"
else for p in 8000 1234 8080; do probe "http://localhost:$p/v1/models" && { LLM_API=openai LLM_BASE_URL="http://localhost:$p/v1"; ok "OpenAI 호환 (:$p)"; break; }; done; fi
if [ -z "$LLM_BASE_URL" ]; then
  has ollama || die "LLM 서버가 없습니다. LLM_BASE_URL=http://gpu:8000/v1 bash setup.sh  (Ollama 는 LLM_BASE_URL=http://host:11434)"
  nohup ollama serve >/dev/null 2>&1 & wait_for http://localhost:11434/api/tags 30 || die "Ollama 기동 실패"
  LLM_API=ollama LLM_BASE_URL=http://localhost:11434; ok "Ollama 기동"
fi

step "모델"
if [ "$LLM_API" = ollama ]; then
  curl -fsS "$LLM_BASE_URL/api/tags" | grep -q '"name"' || { has ollama && ollama pull "$MODEL"; }
  curl -fsS "$LLM_BASE_URL/api/tags" | grep -q "\"name\":\"$MODEL\"" || MODEL=$(curl -fsS "$LLM_BASE_URL/api/tags" | venv/bin/python -c 'import json,sys;print(json.load(sys.stdin)["models"][0]["name"])')
else MODEL=$(curl -fsS "$LLM_BASE_URL/models" | venv/bin/python -c 'import json,sys;print(json.load(sys.stdin)["data"][0]["id"])'); fi
ok "$MODEL"

step "자가검증"; venv/bin/python selftest.py >/dev/null || die "selftest 실패 (venv/bin/python selftest.py 로 확인)"; ok "3D·2D·ICT·DXF 수정·분석 + 수리 루프 (LLM 없이)"
step "웹 서버"
[ -f .server.pid ] && kill "$(cat .server.pid)" 2>/dev/null || true
LLM_API=$LLM_API LLM_BASE_URL=$LLM_BASE_URL LLM_MODEL=$MODEL PORT=$PORT nohup venv/bin/python app.py > server.log 2>&1 & echo $! > .server.pid
wait_for "http://localhost:$PORT/api/models" 20 || { cat server.log; die "웹 서버 기동 실패"; }
URL="http://localhost:$PORT"; ok "$URL"
case "$OS" in mac) open "$URL" ;; linux) [ -n "${DISPLAY:-}" ] && has xdg-open && xdg-open "$URL" >/dev/null 2>&1 || true ;; esac
printf '\n  %s준비 완료%s  %s%s%s   LLM: %s %s   종료: bash setup.sh stop   로그: server.log\n\n' "$B" "$N" "$C" "$URL" "$N" "$LLM_API" "$MODEL"
