@echo off
chcp 65001 >nul
REM 법령 조문 MCP 서버 - Docker Desktop 빌드/실행 (더블클릭으로 실행)
REM 인증키는 같은 폴더의 law_oc.txt 첫 줄에 OC 값을 적어 두면 자동으로 사용합니다.
cd /d "%~dp0"

set "LAW_OC="
if exist law_oc.txt set /p LAW_OC=<law_oc.txt

REM MCP 접속 키: mcp_key.txt 첫 줄. 파일이 없으면 무작위 키를 만들어 저장합니다.
REM 인증 없이 쓰려면 mcp_key.txt 첫 줄에 none 이라고 적으세요.
set "MCP_API_KEY="
if not exist mcp_key.txt (
  for /f %%k in ('powershell -NoProfile -Command "[guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')"') do echo %%k>mcp_key.txt
  echo [안내] MCP 접속 키를 새로 만들어 mcp_key.txt 에 저장했습니다.
)
set /p MCP_API_KEY=<mcp_key.txt
if /i "%MCP_API_KEY%"=="none" set "MCP_API_KEY="

docker info >nul 2>&1
if errorlevel 1 (
  echo [오류] Docker Desktop이 실행 중이 아닙니다. Docker Desktop을 켠 뒤 다시 실행하세요.
  pause
  exit /b 1
)

echo === 이미지 빌드 ===
docker build -t getlawtext:latest . || (echo [오류] 빌드 실패 & pause & exit /b 1)

echo === 컨테이너 실행 ===
docker rm -f getlawtext >nul 2>&1
REM 이전 이름(korean-law-mcp) 컨테이너가 있으면 포트가 겹치므로 정리
docker rm -f korean-law-mcp >nul 2>&1
docker run -d --name getlawtext --restart unless-stopped -p 8080:8087 -e LAW_OC=%LAW_OC% -e MCP_API_KEY=%MCP_API_KEY% getlawtext:latest || (echo [오류] 실행 실패 & pause & exit /b 1)

timeout /t 4 >nul
docker ps --filter name=getlawtext
docker logs --tail 5 getlawtext
echo.
if "%LAW_OC%"=="" echo [주의] law_oc.txt 가 없어 인증키 없이 실행했습니다. 키를 넣고 다시 실행하세요.
echo MCP 엔드포인트: http://localhost:8080/mcp
if "%MCP_API_KEY%"=="" (
  echo [주의] MCP 접속 키 없이 실행했습니다.
) else (
  echo MCP 접속 키: %MCP_API_KEY%
  echo   요청 헤더에 넣으세요:  Authorization: Bearer %MCP_API_KEY%
)
pause
