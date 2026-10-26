# getlawtext — 대한민국 법령정보 전문 제공 MCP 서버
# Red Hat Enterprise Linux 9.6 (UBI) 기반
FROM registry.access.redhat.com/ubi9/ubi:9.6

LABEL name="getlawtext" \
      version="1.0" \
      description="getlawtext - 법제처 국가법령정보센터 Open API 법령·행정규칙 전문 MCP 서버 (RHEL 9.6 UBI)"

# MCP Python SDK는 Python 3.10+ 필요 → RHEL 9 AppStream의 python3.12 사용
# 주의: "dnf -y update" 를 하면 UBI 저장소의 최신 마이너 버전(예: 9.8)으로 OS 전체가 올라가므로 하지 않음.
#       필요한 패키지만 설치하고, 빌드 마지막에 9.6 유지 여부를 확인한다.
RUN dnf -y install --setopt=install_weak_deps=False --setopt=tsflags=nodocs python3.12 python3.12-pip && \
    dnf clean all && rm -rf /var/cache/dnf && \
    cat /etc/redhat-release && \
    grep -q "release 9.6" /etc/redhat-release || (echo "ERROR: base is no longer RHEL 9.6" && exit 1)

RUN useradd -r -u 1001 -m -d /app appuser
WORKDIR /app

COPY requirements.txt .
RUN python3.12 -m pip install --no-cache-dir -r requirements.txt

COPY server.py .
RUN chown -R appuser:appuser /app

USER 1001

ENV PYTHONUNBUFFERED=1 \
    MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8087
# 실행 시 주입:
#   -e LAW_OC=<법제처 Open API 인증키>
#   -e MCP_API_KEY=<MCP 접속 키, 쉼표로 여러 개>   (비우면 인증 없음)
#   -e MCP_API_KEY_FILE=/run/secrets/mcp_key      (파일로 줄 때)
#   -e MCP_ALLOW_QUERY_KEY=true                   (?key= 쿼리 허용, 기본 false)

EXPOSE 8087

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3.12 -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('MCP_PORT','8087'),timeout=4)" || exit 1
CMD ["python3.12", "/app/server.py"]
