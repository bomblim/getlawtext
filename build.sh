#!/usr/bin/env bash
# 법령 조문 MCP 서버 이미지 빌드 및 실행 (docker 또는 podman)
# 사용법: LAW_OC=발급받은_OC ./build.sh
set -euo pipefail

IMAGE="getlawtext:latest"
NAME="getlawtext"
PORT="${PORT:-8087}"
ENGINE="$(command -v docker || command -v podman)"

: "${LAW_OC:?LAW_OC 환경변수(법제처 Open API 인증키)를 설정하세요. 예: LAW_OC=myid ./build.sh}"

# MCP 접속 키: MCP_API_KEY 환경변수 > mcp_key.txt > 새로 생성. "none" 이면 인증 없음
if [ -z "${MCP_API_KEY:-}" ]; then
  if [ ! -f mcp_key.txt ]; then
    head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > mcp_key.txt
    echo "[안내] MCP 접속 키를 새로 만들어 mcp_key.txt 에 저장했습니다."
  fi
  MCP_API_KEY="$(head -n1 mcp_key.txt | tr -d '[:space:]')"
fi
[ "$MCP_API_KEY" = "none" ] && MCP_API_KEY=""

"$ENGINE" build -t "$IMAGE" .
"$ENGINE" rm -f "$NAME" 2>/dev/null || true
"$ENGINE" rm -f korean-law-mcp 2>/dev/null || true   # 이전 이름 컨테이너 정리
"$ENGINE" run -d --name "$NAME" --restart unless-stopped \
  -p "${PORT}:8087" -e LAW_OC="$LAW_OC" -e MCP_API_KEY="$MCP_API_KEY" "$IMAGE"

sleep 3
echo "MCP 엔드포인트: http://localhost:${PORT}/mcp"
"$ENGINE" logs --tail 5 "$NAME"
if [ -n "$MCP_API_KEY" ]; then echo "MCP 접속 키: $MCP_API_KEY  (헤더: Authorization: Bearer $MCP_API_KEY)"; else echo "[주의] MCP 접속 키 없이 실행했습니다."; fi
