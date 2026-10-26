@echo off
chcp 65001 >nul
REM ============================================================
REM  오프라인 서버 배포용 이미지 빌드 (인터넷이 되는 PC의 Docker Desktop에서 실행)
REM  결과: dist\getlawtext_1.0_amd64.tar  (+ .sha256, 배포 설정 파일, 가이드)
REM  진행 기록: build-offline-image.log
REM ============================================================
cd /d "%~dp0"
set "IMAGE=localhost/getlawtext:1.0"
set "TAR=getlawtext_1.0_amd64.tar"
set "LOG=%~dp0build-offline-image.log"

echo [%date% %time%] 시작 > "%LOG%"
echo 진행 기록: %LOG%

docker info >nul 2>&1
if errorlevel 1 (
  echo [오류] Docker Desktop이 실행 중이 아닙니다. >> "%LOG%"
  echo [오류] Docker Desktop이 실행 중이 아닙니다. Docker Desktop을 켠 뒤 다시 실행하세요.
  echo FAILED >> "%LOG%"
  pause
  exit /b 1
)

echo === 1/3 이미지 빌드 (linux/amd64) ===
echo === 1/3 build === >> "%LOG%"
docker build --pull --platform linux/amd64 -t %IMAGE% . >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [오류] 빌드 실패 - %LOG% 확인
  echo FAILED build >> "%LOG%"
  pause
  exit /b 1
)

echo === 2/3 이미지 저장 (docker save) ===
echo === 2/3 save === >> "%LOG%"
if not exist dist mkdir dist
if exist "dist\%TAR%" del /q "dist\%TAR%"
docker save -o "dist\%TAR%" %IMAGE% >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [오류] 저장 실패 - %LOG% 확인
  echo FAILED save >> "%LOG%"
  pause
  exit /b 1
)

echo === 3/3 체크섬, 배포 파일 복사 ===
echo === 3/3 checksum === >> "%LOG%"
REM certutil 로 SHA256 계산 (Windows 기본 포함). 두 번째 줄이 해시값
set "SHA="
for /f "skip=1 delims=" %%h in ('certutil -hashfile "dist\%TAR%" SHA256 ^| findstr /v /c:"CertUtil"') do if not defined SHA set "SHA=%%h"
if defined SHA set "SHA=%SHA: =%"
if not defined SHA (echo [경고] 체크섬 계산 실패 >> "%LOG%") else ((echo %SHA%  %TAR%)> "dist\%TAR%.sha256")
if exist deploy xcopy /y /q deploy\* dist\ >> "%LOG%" 2>&1
docker image inspect %IMAGE% --format "image id: {{.Id}}  size: {{.Size}}  arch: {{.Architecture}}" >> "%LOG%" 2>&1
for %%F in ("dist\%TAR%") do echo tar size: %%~zF bytes >> "%LOG%"
type "dist\%TAR%.sha256" >> "%LOG%"
echo [%date% %time%] DONE >> "%LOG%"

echo.
echo 완료: %~dp0dist
dir /b dist
echo.
echo dist 폴더 전체를 오프라인 서버로 옮긴 뒤 "오프라인 설치 가이드.md" 를 따라 설치하세요.
pause
