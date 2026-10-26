# getlawtext — 대한민국 법령정보 전문 제공 MCP 서버 (Streamable HTTP)

법제처 국가법령정보센터 Open API(`law.go.kr/DRF`)로 현행 법령·행정규칙의 전문(조문·부칙·별표·서식)을 돌려주는 MCP 서버입니다. MCP 도구는 `get_lawfulltext` 하나만 노출합니다.
Red Hat Enterprise Linux 9.6 UBI 이미지(`registry.access.redhat.com/ubi9/ubi:9.6`) + Python 3.12 기반입니다.

## 1. 사전 준비 — Open API 인증키(OC)

1. https://open.law.go.kr 회원가입 → **OPEN API 신청**에서 "법령" 서비스 신청
2. 신청 시 **서버 IP 또는 도메인**을 등록 (컨테이너가 나가는 공인 IP). 미등록 IP에서는 JSON 대신 안내 HTML이 돌아오며, 서버는 이를 오류 메시지로 보여줍니다.
3. OC 값 = 가입 이메일의 `@` 앞부분 (예: `hong@example.com` → `hong`)

## 2. 빌드 · 실행

오프라인 RHEL 서버에 올릴 때는 `build-offline-image.bat`(인터넷 되는 PC의 Docker Desktop)으로 `dist/` 폴더에 이미지 tar와 배포 파일을 만든 뒤, `dist/오프라인 설치 가이드.md`를 따르세요. 리버스 프록시(`/getlawtext/mcp` → `:8087/mcp`) 설정 예시(nginx·httpd)도 `deploy/`에 있습니다.

로컬·온라인 서버에서 바로 실행할 때:

```bash
LAW_OC=your_oc ./build.sh            # 빌드 + 실행 (docker/podman 자동 감지)
```

직접 실행:

```bash
podman build -t getlawtext .
podman run -d --name getlawtext -p 8087:8087 -e LAW_OC=your_oc getlawtext
```

MCP 엔드포인트: `http://<서버>:8087/mcp` (Streamable HTTP)

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `LAW_OC` | (필수) | 법제처 Open API 인증키 |
| `MCP_TRANSPORT` | `streamable-http` (이미지) / `stdio` (직접 실행) | `stdio`, `streamable-http`, `sse` |
| `MCP_HOST` / `MCP_PORT` | `0.0.0.0` / `8087` (이미지 기본값) | 바인드 주소·포트 |
| `LAW_API_BASE` | `https://www.law.go.kr/DRF` | API 기본 URL |
| `LAW_TIMEOUT` | `20` | API 타임아웃(초) |
| `MCP_API_KEY` | (없음) | MCP 접속 키. 쉼표로 여러 개. 비우면 인증 없음 |
| `MCP_API_KEY_FILE` | (없음) | 접속 키 파일 경로 (한 줄에 하나, `#` 주석 가능) |
| `MCP_ALLOW_QUERY_KEY` | `false` | `true`면 URL 쿼리 `?key=<키>`도 허용 |
| `MCP_ALLOWED_HOSTS` | (없음) | 허용할 Host 헤더 값(쉼표). 지정하면 Host 검사(DNS 리바인딩 방어)를 켬. 비우면 검사 안 함 |
| `MCP_ALLOWED_ORIGINS` | (없음) | 허용할 Origin 값(쉼표). `MCP_ALLOWED_HOSTS`와 함께 사용 |

상태 확인용 `GET /health`는 접속 키 없이 응답합니다(컨테이너 HEALTHCHECK·프록시 점검용).

### MCP 접속 키

`MCP_API_KEY`(또는 `MCP_API_KEY_FILE`)를 설정하면 `/mcp`로 오는 모든 HTTP 요청에서 키를 확인합니다. 키가 없거나 틀리면 `401 Unauthorized`를 돌려줍니다.

| 키를 넣는 위치 | 예 |
|---|---|
| `Authorization` 헤더 (권장) | `Authorization: Bearer <키>` |
| `X-API-Key` 헤더 | `X-API-Key: <키>` |
| URL 쿼리 (`MCP_ALLOW_QUERY_KEY=true`일 때만) | `http://<서버>:8087/mcp?key=<키>` |

- 키를 여러 개 두려면 `MCP_API_KEY=키1,키2`처럼 씁니다. 사용자별로 키를 나눠 주고 하나만 빼서 회수할 때 유용합니다.
- 실행 스크립트(`run-docker.bat`, `run-docker.ps1`, `build.sh`)는 같은 폴더의 `mcp_key.txt` 첫 줄을 키로 씁니다. 파일이 없으면 64자리 무작위 키를 만들어 저장하고, 실행이 끝나면 키를 화면에 보여줍니다. 인증 없이 쓰려면 `mcp_key.txt` 첫 줄에 `none`이라고 적습니다.
- 쿼리 방식은 키가 접속 로그·브라우저 기록에 남을 수 있어 기본으로 꺼져 있습니다. 헤더를 넣을 수 없는 클라이언트에서만 켜세요.
- 키 비교는 타이밍 공격을 막는 상수 시간 비교(`hmac.compare_digest`)로 합니다. 키는 평문 HTTP로 오가므로, 외부에 공개할 때는 앞단에 TLS(HTTPS) 프록시를 두세요.
- stdio 모드에는 적용되지 않습니다(네트워크로 노출되지 않음).

## 3. 제공 도구

MCP로 노출되는 도구는 `get_lawfulltext` 하나입니다.

| 인자 | 설명 |
|---|---|
| `law_name` | 법령명 또는 행정규칙명 (예: `근로기준법`, `방산원가대상물자의 원가계산에 관한 시행세칙`) |
| `mst` / `law_id` | 법령일련번호 / 법령ID로 바로 조회 (검색 생략) |
| `admrul_id` | 행정규칙일련번호로 바로 조회 (검색 생략) |
| `kind` | `auto`(기본, 법령 → 행정규칙 순) · `law` · `admrul` |
| `format` | `md`(기본) · `txt` · `json` |
| `addenda` | 부칙: `all`(기본) · `latest` · `none` |
| `annex` | 별표·서식: `annex`(기본) · `all` · `list` · `none` |
| `oc` | 호출별 인증키 (생략 시 환경변수 `LAW_OC`) |

이름이 정확히 일치하는 것(공백 무시)을 고릅니다. 일치하는 것이 없고 후보가 여럿이면 후보 목록(`mst` 또는 `admrul_id` 포함)을 돌려주므로, 그 값을 넣어 다시 호출하면 됩니다. 민법처럼 조문이 많은 법령은 응답이 수십만 자가 될 수 있습니다.

부칙은 별도 호출 없이 같은 본문 조회(`lawService.do?target=law`) 응답의 `법령.부칙.부칙단위[]`(부칙공포일자·부칙공포번호·부칙내용)에서 가져옵니다. 순서는 API와 같이 제정 부칙부터 최신 부칙까지입니다.

| `addenda` | 동작 |
|---|---|
| `all` (기본) | 모든 부칙 포함 |
| `latest` | 가장 최근 부칙 1개만 (오래된 법령은 부칙이 수백 개라 응답을 줄일 때) |
| `none` | 부칙 제외 |

별표·서식도 같은 응답의 `법령.별표.별표단위[]`에 들어 있습니다. `별표구분` 값이 `별표` 또는 `서식`이고, 텍스트 본문(`별표내용`, 괘선 표 형태)과 원본 HWP·PDF 파일 링크(`https://www.law.go.kr/LSW/flDownload.do?flSeq=...`)가 함께 옵니다. 서식은 본문이 커서(근로기준법 시행규칙의 별표·서식 26개 합계 약 13만 자) 기본값에서는 목록과 링크만 넣습니다.

| `annex` | 별표 | 서식 |
|---|---|---|
| `annex` (기본) | 본문 + 원본 링크 | 제목 + 원본 링크 |
| `all` | 본문 + 원본 링크 | 본문 + 원본 링크 |
| `list` | 제목 + 원본 링크 | 제목 + 원본 링크 |
| `none` | 제외 | 제외 |

출력 순서는 조문 → 부칙 → 별표 → 서식입니다. 마크다운에서는 괘선 표가 깨지지 않도록 별표·서식 본문을 ```` ```text ```` 블록으로 감쌉니다.

### 행정규칙(훈령·예규·고시·지침 등)

이름에 "시행세칙", "지침", "고시", "예규", "규정"이 붙은 규정은 대부분 법령이 아니라 행정규칙이라 법령 검색(`target=law`)으로는 나오지 않습니다. 행정규칙은 `target=admrul`로 검색·조회합니다.

- `get_lawfulltext(law_name=...)`는 `kind="auto"`(기본)일 때 법령에서 이름이 일치하는 것을 먼저 찾고, 없으면 행정규칙에서 찾습니다. 행정규칙만 찾으려면 `kind="admrul"`을 씁니다.
- 검색 결과의 `admrul_id`(행정규칙일련번호)로 바로 조회할 수 있습니다: `get_lawfulltext(admrul_id="2100000272968")`
- 응답 머리말에 종류(훈령 등)·발령번호·발령일자·시행일자·소관부처·담당부서와 전문 HWP·PDF 파일 링크가 붙습니다.

행정규칙 본문은 법령과 구조가 다릅니다. 조문 하나가 문자열 하나로 오고, 항(①②)·호(1. 2.)·목(가. 나.)이 줄바꿈 없이 붙어 있습니다(예: "…말한다.1. 계약상대자가…건물2. 계약상대자의…"). 서버가 이것을 다시 나눕니다.

- 항: 원문자 ①~⑳ 앞에서 줄바꿈
- 호: 번호가 1, 2, 3…(2의2 포함) 순서로 나올 때만 끊음. "5.0%", "0.65" 같은 소수와 "제2조제1호"는 끊지 않음
- 목: 가, 나, 다… 순서로 나올 때만 끊음. "…한다."처럼 문장 끝의 "다."나 "가목", "다목에" 같은 참조는 끊지 않음
- 수식: `<img id=…>`는 `[수식 이미지](https://www.law.go.kr/LSW/flDownload.do?flSeq=…)` 링크로 바꿈
- 부칙: 공포일자·공포번호·내용 병렬 배열을 묶고, 부칙 안의 제1조·제2조…와 ①②를 줄 단위로 나눔

자동 분리는 규칙 기반이라 드물게 원문과 다르게 끊길 수 있습니다. 정확한 원문이 필요하면 머리말의 전문 PDF를 확인하세요.

`server.py` 안의 `search_law`, `list_articles`, `get_article`(조·항·호·목 단위 조회), `get_articles`는 내부 함수로 남아 있지만 MCP 도구로는 노출하지 않습니다. 다시 노출하려면 해당 함수 위에 `@mcp.tool()`을 붙이면 됩니다.

## 4. MCP 클라이언트 연결

Claude Code:

```bash
claude mcp add --transport http getlawtext http://<서버>:8087/mcp \
  --header "Authorization: Bearer <MCP 접속 키>"
```

설정 파일 형식(클라이언트 공통):

```json
{
  "mcpServers": {
    "getlawtext": {
      "type": "http",
      "url": "http://<서버>:8087/mcp",
      "headers": { "Authorization": "Bearer <MCP 접속 키>" }
    }
  }
}
```

헤더를 설정할 수 없는 클라이언트는 서버에 `MCP_ALLOW_QUERY_KEY=true`를 주고 URL을 `http://<서버>:8087/mcp?key=<MCP 접속 키>`로 넣습니다.

로컬 stdio 모드로 쓰려면:

```json
{
  "mcpServers": {
    "getlawtext": {
      "command": "python3",
      "args": ["/path/to/server.py"],
      "env": { "LAW_OC": "your_oc" }
    }
  }
}
```

## 5. MCP Inspector로 검증

Node.js 18 이상이 필요합니다. 서버(컨테이너)를 먼저 띄운 뒤 실행합니다.

### 웹 UI

```bash
npx @modelcontextprotocol/inspector
```

1. 브라우저에서 표시된 주소(기본 `http://localhost:6274`, 터미널에 출력되는 토큰 포함 URL)를 엽니다.
2. **Transport Type**: `Streamable HTTP`
3. **URL**: `http://<서버>:8087/mcp`
4. 접속 키를 설정했다면 **Authentication**(또는 Custom Headers)에 헤더 이름 `Authorization`, 값 `Bearer <MCP 접속 키>`를 넣고 **Connect**
5. **Tools** 탭 → **List Tools** → 도구를 선택하고 인자를 넣어 **Run Tool**

원격 RHEL 서버에서 Inspector를 띄우고 PC 브라우저로 볼 때는 SSH 터널을 씁니다.

```bash
ssh -L 6274:localhost:6274 -L 6277:localhost:6277 user@rhel-server
```

### CLI (자동화·스크립트용)

```bash
URL=http://localhost:8087/mcp
AUTH="Authorization: Bearer $(cat mcp_key.txt)"     # 접속 키를 설정한 경우
npx @modelcontextprotocol/inspector --cli $URL --transport http --header "$AUTH" --method tools/list

npx @modelcontextprotocol/inspector --cli $URL --transport http --header "$AUTH" --method tools/call \
  --tool-name get_lawfulltext --tool-arg law_name=근로기준법

npx @modelcontextprotocol/inspector --cli $URL --transport http --header "$AUTH" --method tools/call \
  --tool-name get_lawfulltext --tool-arg "law_name=방산원가대상물자의 원가계산에 관한 시행세칙" --tool-arg addenda=latest

npx @modelcontextprotocol/inspector --cli $URL --transport http --header "$AUTH" --method tools/call \
  --tool-name get_lawfulltext --tool-arg mst=<MST> --tool-arg format=json --tool-arg annex=list
```

`--tool-arg mst=265959`처럼 숫자로 넘어가도 서버가 문자열로 받아 처리합니다.

## 6. 참고

- MCP Python SDK는 1.x로 고정했습니다(`mcp>=1.9,<2`). 2.x에서는 `FastMCP`가 `MCPServer`로 바뀌어 코드 수정이 필요합니다.
- 외부에 노출할 때는 MCP 접속 키를 설정하고, 리버스 프록시(nginx 등)로 TLS를 앞단에 두는 것을 권장합니다.
- API 응답 구조가 예상과 다를 때는 `format=json`으로 파싱 결과를 확인하세요.
