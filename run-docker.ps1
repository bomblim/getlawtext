# 법령 조문 MCP 서버 - Docker Desktop(Windows) 빌드 및 실행
# 사용법 (PowerShell):  .\run-docker.ps1 -Oc 발급받은OC
param(
    [string]$Oc = $env:LAW_OC,
    [string]$McpKey = $env:MCP_API_KEY,   # MCP 접속 키. 비우면 mcp_key.txt 사용/생성, "none" 이면 인증 없음
    [int]$Port = 8080
)
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (-not $Oc) { Write-Warning "LAW_OC(인증키)가 비어 있습니다. 서버는 뜨지만 법령 API 호출은 실패합니다." }

$keyFile = Join-Path $PSScriptRoot "mcp_key.txt"
if (-not $McpKey) {
    if (-not (Test-Path $keyFile)) {
        ([guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")) | Set-Content -Encoding ascii $keyFile
        Write-Host "[안내] MCP 접속 키를 새로 만들어 mcp_key.txt 에 저장했습니다."
    }
    $McpKey = (Get-Content $keyFile -TotalCount 1).Trim()
}
if ($McpKey -eq "none") { $McpKey = "" }

docker build -t getlawtext:latest .
docker rm -f getlawtext 2>$null | Out-Null
docker rm -f korean-law-mcp 2>$null | Out-Null   # 이전 이름 컨테이너 정리
docker run -d --name getlawtext --restart unless-stopped `
    -p "${Port}:8087" -e "LAW_OC=$Oc" -e "MCP_API_KEY=$McpKey" getlawtext:latest

Start-Sleep -Seconds 3
docker ps --filter name=getlawtext
docker logs --tail 5 getlawtext
Write-Host "MCP 엔드포인트: http://localhost:${Port}/mcp"
if ($McpKey) { Write-Host "MCP 접속 키: $McpKey  (헤더: Authorization: Bearer $McpKey)" }
else { Write-Warning "MCP 접속 키 없이 실행했습니다." }
