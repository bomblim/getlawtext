#!/usr/bin/env python3
"""
대한민국 법령정보(국가법령정보센터 Open API) 조문정보 조회 MCP 서버

제공 도구 (MCP 노출)
  - get_lawfulltext   : 법령·행정규칙 이름으로 검색 → 전체 조문 + 부칙 + 별표·서식을 응답으로 반환 (md/txt/json)

내부 함수 (노출하지 않음): search_law, list_articles, get_article, get_articles

환경변수
  LAW_OC          : 법제처 Open API 인증키(OC, 신청 시 등록한 이메일 ID)  [필수]
  LAW_API_BASE    : API 기본 URL (기본값 https://www.law.go.kr/DRF)
  MCP_TRANSPORT   : stdio | streamable-http | sse  (기본값 stdio)
  MCP_HOST        : HTTP 모드 바인드 주소 (기본값 0.0.0.0)
  MCP_PORT        : HTTP 모드 포트 (기본값 8080)
  LAW_TIMEOUT     : API 타임아웃 초 (기본값 20)
  LAW_WEB_BASE    : 별표·서식 원본 파일 링크 기본 주소 (기본값 https://www.law.go.kr)
  MCP_API_KEY     : MCP 접속 키 (쉼표로 여러 개). 설정하면 HTTP 요청마다 키 확인, 비우면 인증 없음
  MCP_API_KEY_FILE: 접속 키 파일 경로 (한 줄에 키 하나, Docker secret 등)
  MCP_ALLOW_QUERY_KEY : true 이면 URL 쿼리 ?key=<키> 도 허용 (헤더를 못 넣는 클라이언트용, 기본 false)
  MCP_ALLOWED_HOSTS   : 허용할 Host 헤더 값 (쉼표, 예: law.example.go.kr,law.example.go.kr:443). 지정 시 Host 검사 켬
  MCP_ALLOWED_ORIGINS : 허용할 Origin 값 (쉼표, 예: https://law.example.go.kr). MCP_ALLOWED_HOSTS 와 함께 사용
"""
from __future__ import annotations

import hmac
import json
import os
import re
from typing import Any, Optional, Union

StrOrInt = Union[str, int]

import httpx
from mcp.server.fastmcp import FastMCP

API_BASE = os.environ.get("LAW_API_BASE", "https://www.law.go.kr/DRF").rstrip("/")
DEFAULT_OC = os.environ.get("LAW_OC", "")
TIMEOUT = float(os.environ.get("LAW_TIMEOUT", "20"))

def _transport_security():
    """MCP_ALLOWED_HOSTS 가 있으면 Host/Origin 검사(DNS 리바인딩 방어)를 켜고, 없으면 끈다.
    리버스 프록시 뒤에서는 프록시가 보내는 Host 값(예: law.example.go.kr)을 넣거나 비워 두면 된다."""
    from mcp.server.transport_security import TransportSecuritySettings
    hosts = [h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    origins = [o.strip() for o in os.environ.get("MCP_ALLOWED_ORIGINS", "").split(",") if o.strip()]
    if not hosts:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    return TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                     allowed_hosts=hosts, allowed_origins=origins)


mcp = FastMCP(
    "getlawtext",
    host=os.environ.get("MCP_HOST", "0.0.0.0"),
    port=int(os.environ.get("MCP_PORT", "8080")),
    transport_security=_transport_security(),
)

# ---------------------------------------------------------------------------
# 번호 변환 유틸
# ---------------------------------------------------------------------------
_CIRCLED = {chr(0x2460 + i): i + 1 for i in range(20)}  # ①~⑳


def to_jo_code(jo: str) -> str:
    """'제10조의2' / '10의2' / '10-2' / '10' / '001002' -> '001002' (조 4자리 + 가지 2자리)."""
    s = str(jo).strip().replace(" ", "")
    if re.fullmatch(r"\d{6}", s):
        return s
    m = re.fullmatch(r"제?(\d+)조?(?:(?:의|-|_)(\d+))?", s)
    if not m:
        raise ValueError(f"조문 번호 형식을 알 수 없습니다: {jo!r} (예: '제38조', '제10조의2', '003800')")
    return f"{int(m.group(1)):04d}{int(m.group(2) or 0):02d}"


def _unit_code(value: str, unit: str) -> str:
    """항/호 번호 -> 6자리 코드. '제2항'/'②'/'2' -> '000200', '2의3' -> '000203'."""
    s = str(value).strip().replace(" ", "")
    if re.fullmatch(r"\d{6}", s):
        return s
    if s and s[0] in _CIRCLED:
        return f"{_CIRCLED[s[0]]:04d}00"
    m = re.fullmatch(rf"제?(\d+){unit}?(?:(?:의|-)(\d+))?\.?", s)
    if not m:
        raise ValueError(f"{unit} 번호 형식을 알 수 없습니다: {value!r}")
    return f"{int(m.group(1)):04d}{int(m.group(2) or 0):02d}"


def to_mok(value: str) -> str:
    """'다목' / '다.' / '다' -> '다'"""
    s = str(value).strip().replace(" ", "").rstrip(".").removesuffix("목")
    if not re.fullmatch(r"[가-힣]", s):
        raise ValueError(f"목 번호 형식을 알 수 없습니다: {value!r} (예: '가', '다목')")
    return s


def jo_label(code: str) -> str:
    n, b = int(code[:4]), int(code[4:])
    return f"제{n}조" + (f"의{b}" if b else "")


# ---------------------------------------------------------------------------
# API 호출
# ---------------------------------------------------------------------------
class LawApiError(RuntimeError):
    pass


async def _call(endpoint: str, params: dict[str, Any], oc: Optional[str]) -> dict:
    key = (oc or DEFAULT_OC).strip()
    if not key:
        raise LawApiError("인증키(OC)가 없습니다. 환경변수 LAW_OC 를 설정하거나 oc 인자를 전달하세요.")
    q = {"OC": key, "type": "JSON", **{k: v for k, v in params.items() if v not in (None, "")}}
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        r = await client.get(f"{API_BASE}/{endpoint}", params=q)
    r.raise_for_status()
    text = r.text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 인증 실패/IP 미등록 시 HTML 이나 안내 문구가 돌아오는 경우가 있음
        snippet = re.sub(r"<[^>]+>", " ", text)
        snippet = re.sub(r"\s+", " ", snippet)[:300]
        raise LawApiError(f"API가 JSON이 아닌 응답을 반환했습니다(인증키·등록 IP/도메인 확인): {snippet}")
    if isinstance(data, dict) and "result" in data and len(data) <= 2:
        raise LawApiError(f"API 오류: {data}")
    return data


# ---------------------------------------------------------------------------
# 응답 파싱
# ---------------------------------------------------------------------------
def _as_list(v: Any) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _text(v: Any) -> str:
    """문자열 / 중첩 리스트 형태의 내용을 한 문자열로 평탄화."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, list):
        return "\n".join(t for t in (_text(x) for x in v) if t)
    return str(v).strip()


def _find(obj: Any, key: str) -> Any:
    """중첩 dict/list 안에서 첫 번째로 나타나는 key 값 찾기."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _find(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find(v, key)
            if r is not None:
                return r
    return None


def _basic_info(data: dict) -> dict:
    info = _find(data, "기본정보") or {}
    def g(k):
        v = info.get(k) if isinstance(info, dict) else None
        if isinstance(v, dict):
            v = v.get("content") or v.get("소관부처명") or next(iter(v.values()), None)
        return v
    return {
        "법령명": g("법령명_한글") or g("법령명한글") or _find(data, "법령명_한글"),
        "법령ID": g("법령ID"),
        "공포일자": g("공포일자"),
        "시행일자": g("시행일자"),
        "소관부처": g("소관부처"),
        "법종구분": g("법종구분"),
    }


def _format_article(a: dict) -> str:
    lines: list[str] = []
    head = _text(a.get("조문내용"))
    if head:
        lines.append(head)
    for hang in _as_list(a.get("항")):
        if not isinstance(hang, dict):
            continue
        t = _text(hang.get("항내용"))
        if t:
            lines.append(t)
        for ho in _as_list(hang.get("호")):
            if not isinstance(ho, dict):
                continue
            t = _text(ho.get("호내용"))
            if t:
                lines.append("  " + t)
            for mok in _as_list(ho.get("목")):
                if isinstance(mok, dict):
                    t = _text(mok.get("목내용"))
                    if t:
                        lines.append("    " + t.replace("\n", "\n    "))
    return "\n".join(lines)


def _article_units(data: dict) -> list[dict]:
    units = _find(data, "조문단위")
    if units is None:
        # lawjosub 응답에서 조문단위 래퍼 없이 조문 객체가 바로 오는 경우
        cand = _find(data, "조문")
        units = cand if cand is not None else []
    return [u for u in _as_list(units) if isinstance(u, dict)]


def _article_title(a: dict) -> str:
    no = str(a.get("조문번호") or "").strip()
    br = str(a.get("조문가지번호") or "").strip()
    label = f"제{no}조" + (f"의{br}" if br and br != "0" else "") if no else ""
    title = _text(a.get("조문제목"))
    return f"{label}({title})" if title else label


def _id_params(mst: Optional[StrOrInt], law_id: Optional[StrOrInt]) -> dict:
    mst = str(mst).strip() if mst not in (None, "") else None
    law_id = str(law_id).strip() if law_id not in (None, "") else None
    if not mst and not law_id:
        raise ValueError("mst(법령일련번호) 또는 law_id(법령ID) 중 하나는 필요합니다. search_law 로 먼저 찾으세요.")
    return {"MST": mst} if mst else {"ID": law_id}


def _err(e: Exception) -> str:
    return f"[오류] {e}"


# ---------------------------------------------------------------------------
# MCP 도구
# ---------------------------------------------------------------------------
# (내부용 — MCP 도구로 노출하지 않음)
async def search_law(query: str, kind: str = "all", display: int = 20, page: int = 1,
                     oc: Optional[str] = None) -> str:
    """법령 또는 행정규칙(훈령·예규·고시·지침 등)을 이름으로 검색합니다.
    법령 결과의 MST는 get_article / list_articles / get_lawfulltext(mst=)에,
    행정규칙 결과의 admrul_id는 get_lawfulltext(admrul_id=)에 사용하세요.

    Args:
        query: 검색어 (예: '근로기준법', '방산원가')
        kind: 'all'(기본, 법령+행정규칙) | 'law'(법령만) | 'admrul'(행정규칙만)
        display: 종류별 결과 개수 (최대 100)
        page: 페이지 번호
        oc: 법제처 Open API 인증키 (생략 시 환경변수 LAW_OC 사용)
    """
    kind = (kind or "all").lower()
    if kind not in ("all", "law", "admrul"):
        return _err(ValueError("kind 는 all, law, admrul 중 하나여야 합니다."))
    n = min(max(display, 1), 100)
    out: list[str] = []
    try:
        if kind in ("all", "law"):
            data = await _call("lawSearch.do", {"target": "law", "query": query, "display": n, "page": page}, oc)
            root = data.get("LawSearch", data)
            items = [x for x in _as_list(root.get("law")) if isinstance(x, dict)]
            out.append(f"[법령] '{query}' — 총 {root.get('totalCnt', len(items))}건 (page {page})")
            for x in items:
                out.append(f"- {_strip_tags(x.get('법령명한글'))} [{x.get('법령구분명', '')}] "
                           f"MST={x.get('법령일련번호')} 법령ID={x.get('법령ID')} "
                           f"시행 {x.get('시행일자', '')} / {x.get('소관부처명', '')}")
        if kind in ("all", "admrul"):
            items, total = await _search_admrul(query, oc, n, page)
            if out:
                out.append("")
            out.append(f"[행정규칙] '{query}' — 총 {total}건 (page {page})")
            for x in items:
                out.append(f"- {x.get('행정규칙명')} [{x.get('행정규칙종류', '')}] "
                           f"admrul_id={x.get('행정규칙일련번호')} 행정규칙ID={x.get('행정규칙ID')} "
                           f"발령 {x.get('발령일자', '')} 제{x.get('발령번호', '')}호 / 시행 {x.get('시행일자', '')} / "
                           f"{x.get('소관부처명', '')} ({x.get('현행연혁구분', '')})")
    except Exception as e:
        return _err(e)
    return "\n".join(out)


# (내부용 — MCP 도구로 노출하지 않음)
async def list_articles(mst: Optional[StrOrInt] = None, law_id: Optional[StrOrInt] = None,
                        oc: Optional[str] = None) -> str:
    """법령의 조문 목록(조문번호와 제목)을 조회합니다.

    Args:
        mst: 법령일련번호 (search_law 결과의 MST)
        law_id: 법령ID (mst 대신 사용 가능)
        oc: 법제처 Open API 인증키
    """
    try:
        data = await _call("lawService.do", {"target": "law", **_id_params(mst, law_id)}, oc)
    except Exception as e:
        return _err(e)
    info = _basic_info(data)
    out = [f"{info['법령명']} (시행 {info['시행일자']}, 법령ID {info['법령ID']})"]
    for a in _article_units(data):
        kind = a.get("조문여부")
        if kind and kind != "조문":  # 편·장·절 제목(전문)
            out.append(f"  {_text(a.get('조문내용'))}")
        else:
            out.append(f"    {_article_title(a)}")
    return "\n".join(out) if len(out) > 1 else "조문 정보를 찾지 못했습니다."


# (내부용 — MCP 도구로 노출하지 않음)
async def get_article(jo: StrOrInt, mst: Optional[StrOrInt] = None, law_id: Optional[StrOrInt] = None,
                      hang: Optional[StrOrInt] = None, ho: Optional[StrOrInt] = None, mok: Optional[str] = None,
                      raw: bool = False, oc: Optional[str] = None) -> str:
    """현행 법령의 특정 조문을 조·항·호·목 단위로 조회합니다 (target=lawjosub).

    Args:
        jo: 조문 번호 (예: '제38조', '제10조의2', '38', '003800')
        mst: 법령일련번호 (search_law 결과의 MST)
        law_id: 법령ID (mst 대신 사용 가능)
        hang: 항 번호 (예: '1', '제2항', '②')
        ho: 호 번호 (예: '3', '제3호', '2의2')
        mok: 목 (예: '가', '다목')
        raw: True 이면 API 원본 JSON 반환
        oc: 법제처 Open API 인증키
    """
    try:
        code = to_jo_code(jo)
        params = {"target": "lawjosub", **_id_params(mst, law_id), "JO": code}
        if hang:
            params["HANG"] = _unit_code(hang, "항")
        if ho:
            params["HO"] = _unit_code(ho, "호")
        if mok:
            params["MOK"] = to_mok(mok)
        data = await _call("lawService.do", params, oc)
    except Exception as e:
        return _err(e)

    if raw:
        return json.dumps(data, ensure_ascii=False, indent=2)

    info = _basic_info(data)
    units = _article_units(data)
    if not units:
        return f"{jo_label(code)} 조문을 찾지 못했습니다.\n" + json.dumps(data, ensure_ascii=False)[:1500]
    header = f"■ {info['법령명'] or ''} (시행 {info['시행일자'] or '-'})".strip()
    body = "\n\n".join(_format_article(a) or _article_title(a) for a in units)
    return f"{header}\n{body}"


# (내부용 — MCP 도구로 노출하지 않음)
async def get_articles(jo_list: list[StrOrInt], mst: Optional[StrOrInt] = None, law_id: Optional[StrOrInt] = None,
                       oc: Optional[str] = None) -> str:
    """같은 법령의 여러 조문을 한 번에 조회합니다.

    Args:
        jo_list: 조문 번호 목록 (예: ['제2조', '제17조', '제23조의2'])
        mst: 법령일련번호
        law_id: 법령ID
        oc: 법제처 Open API 인증키
    """
    results = []
    for jo in jo_list[:30]:
        results.append(await get_article(jo=jo, mst=mst, law_id=law_id, oc=oc))
    return "\n\n" + ("\n\n" + "-" * 40 + "\n\n").join(results)


# ---------------------------------------------------------------------------
# 전체 조문 조회
# ---------------------------------------------------------------------------
_FORMATS = ("md", "txt", "json")


def _strip_tags(s: Any) -> str:
    return re.sub(r"<[^>]+>", "", str(s or "")).strip()


def _norm(s: str) -> str:
    return re.sub(r"[\s·ㆍ]", "", s)


async def _search_items(query: str, oc: Optional[str], display: int = 50) -> list[dict]:
    data = await _call("lawSearch.do", {"target": "law", "query": query, "display": display}, oc)
    root = data.get("LawSearch", data)
    items = [x for x in _as_list(root.get("law")) if isinstance(x, dict)]
    for x in items:
        x["법령명한글"] = _strip_tags(x.get("법령명한글"))
    return items


def _pick(items: list[dict], name: str) -> tuple[Optional[dict], list[dict]]:
    """정확히 일치(공백 무시)하는 법령 우선, 없으면 None + 후보 목록."""
    exact = [x for x in items if _norm(x["법령명한글"]) == _norm(name)]
    if exact:
        return exact[0], items
    if len(items) == 1:
        return items[0], items
    return None, items


_ADDENDA_MODES = ("all", "latest", "none")


def _raw_lines(v: Any) -> list[str]:
    """중첩 리스트를 줄 목록으로 평탄화 (들여쓰기 유지)."""
    if v is None:
        return []
    if isinstance(v, list):
        return [ln for x in v for ln in _raw_lines(x)]
    return str(v).split("\n")


def _addenda_units(data: dict) -> list[dict]:
    """법령.부칙.부칙단위[] → [{공포일자, 공포번호, 제목, 내용}] (API 순서: 오래된 것 → 최신)."""
    b = _find(data, "부칙")
    units = _find(b, "부칙단위") if b is not None else None
    out = []
    for u in _as_list(units):
        if not isinstance(u, dict):
            continue
        lines = [ln.rstrip() for ln in _raw_lines(u.get("부칙내용")) if ln.strip()]
        title = lines[0].strip() if lines and lines[0].lstrip().startswith("부칙") else ""
        body = "\n".join(lines[1:] if title else lines)
        if not title:
            title = f"부칙 <제{u.get('부칙공포번호', '')}호,{u.get('부칙공포일자', '')}>"
        out.append({"공포일자": u.get("부칙공포일자"), "공포번호": u.get("부칙공포번호"),
                    "제목": title, "내용": body})
    return out


def _select_addenda(adds: list[dict], mode: str) -> list[dict]:
    if mode == "none":
        return []
    if mode == "latest":
        return sorted(adds, key=lambda x: (str(x.get("공포일자") or ""), str(x.get("공포번호") or "")))[-1:]
    return adds


LAW_WEB_BASE = os.environ.get("LAW_WEB_BASE", "https://www.law.go.kr").rstrip("/")
_ANNEX_MODES = ("annex", "all", "list", "none")


def _annex_units(data: dict) -> list[dict]:
    """법령.별표.별표단위[] → 별표·서식 목록 (별표구분: '별표' | '서식')."""
    b = _find(data, "별표")
    units = _find(b, "별표단위") if isinstance(b, (dict, list)) else None
    out = []
    for u in _as_list(units):
        if not isinstance(u, dict):
            continue
        kind = str(u.get("별표구분") or "별표").strip()
        lines = [ln.rstrip() for ln in _raw_lines(u.get("별표내용"))]
        while lines and not lines[-1].strip():
            lines.pop()
        while lines and not lines[0].strip():
            lines.pop(0)
        m = re.search(r"[\[【]\s*(별[^\]】]+?)\s*[\]】]", lines[0]) if lines else None
        if m:
            label = m.group(1)
        else:
            n, br = int(str(u.get("별표번호") or 0) or 0), int(str(u.get("별표가지번호") or 0) or 0)
            label = (f"별표 {n}" if kind == "별표" else f"별지 제{n}호서식") + (f"의{br}" if br else "")
        def link(k):
            v = str(u.get(k) or "").strip()
            return (LAW_WEB_BASE + v if v.startswith("/") else v) or None
        out.append({"구분": kind, "번호": label, "제목": _text(u.get("별표제목")),
                    "HWP": link("별표서식파일링크"), "PDF": link("별표서식PDF파일링크"),
                    "이미지": link("별표서식이미지파일링크"), "내용": "\n".join(lines)})
    return out


def _select_annexes(items: list[dict], mode: str) -> list[dict]:
    """annex: 별표 본문 + 서식은 목록만 / all: 모두 본문 / list: 모두 목록만 / none: 제외"""
    if mode == "none":
        return []
    res = []
    for x in items:
        with_body = mode == "all" or (mode == "annex" and x["구분"] != "서식")
        res.append(x if with_body else {**x, "내용": None})
    return res


def _render_annexes(items: list[dict], md: bool) -> list[str]:
    out: list[str] = []
    for kind in ("별표", "서식"):
        group = [x for x in items if (x["구분"] == "서식") == (kind == "서식")]
        if not group:
            continue
        out += ["", f"## {kind}" if md else f"[{kind}]", ""]
        for x in group:
            out.append(f"### [{x['번호']}] {x['제목']}" if md else f"[{x['번호']}] {x['제목']}")
            files = [(n, x[n]) for n in ("HWP", "PDF") if x.get(n)]
            if files:
                out.append(("- 원본: " + " · ".join(f"[{n}]({u})" for n, u in files)) if md
                           else "원본: " + " / ".join(f"{n} {u}" for n, u in files))
            if x.get("내용"):
                out += (["```text", x["내용"], "```"] if md else ["", x["내용"]])
            out.append("")
    return out


def _render_law(info: dict, units: list[dict], fmt: str, src: dict,
                addenda: Optional[list[dict]] = None, addenda_total: int = 0,
                annexes: Optional[list[dict]] = None) -> str:
    addenda = addenda or []
    annexes = annexes or []
    title = info.get("법령명") or src.get("법령명한글") or ""
    meta = {
        "법령명": title,
        "법령구분": src.get("법령구분명") or info.get("법종구분"),
        "법령ID": info.get("법령ID") or src.get("법령ID"),
        "법령일련번호(MST)": str(src.get("법령일련번호") or "") or None,
        "공포일자": info.get("공포일자") or src.get("공포일자"),
        "시행일자": info.get("시행일자") or src.get("시행일자"),
        "소관부처": src.get("소관부처명") or info.get("소관부처"),
        "출처": "국가법령정보센터(law.go.kr) Open API",
    }
    if fmt == "json":
        arts = []
        for a in units:
            if a.get("조문여부") and a.get("조문여부") != "조문":
                arts.append({"구분": "제목", "내용": _text(a.get("조문내용"))})
            else:
                arts.append({"구분": "조문", "조문": _article_title(a), "본문": _format_article(a)})
        return json.dumps({**meta, "조문수": sum(1 for x in arts if x["구분"] == "조문"),
                           "조문": arts, "부칙수": addenda_total, "부칙": addenda,
                           "별표수": sum(1 for x in annexes if x["구분"] != "서식"),
                           "서식수": sum(1 for x in annexes if x["구분"] == "서식"),
                           "별표서식": annexes},
                          ensure_ascii=False, indent=2)

    md = fmt == "md"
    out = [f"# {title}" if md else title, ""]
    for k, v in meta.items():
        if v and k != "법령명":
            out.append(f"- {k}: {v}" if md else f"{k}: {v}")
    out.append("")
    for a in units:
        if a.get("조문여부") and a.get("조문여부") != "조문":
            head = _text(a.get("조문내용"))
            if head:
                level = "##" if re.match(r"^\s*제\s*\d+\s*(편|장)", head) else "###"
                out += ["", f"{level} {head}" if md else f"[{head}]", ""]
            continue
        body = _format_article(a) or _article_title(a)
        if md:
            m = re.match(r"^(제\s*\d+\s*조(?:의\s*\d+)?(?:\([^)]*\))?)(.*)$", body, re.S)
            out.append(f"**{m.group(1)}**{m.group(2)}" if m else body)
        else:
            out.append(body)
        out.append("")
    if addenda:
        label = "부칙" if len(addenda) == addenda_total else f"부칙 (전체 {addenda_total}개 중 최신 {len(addenda)}개)"
        out += ["", f"## {label}" if md else f"[{label}]", ""]
        for ad in addenda:
            out.append(f"### {ad['제목']}" if md else ad["제목"])
            if ad["내용"]:
                out.append(ad["내용"])
            out.append("")
    out += _render_annexes(annexes, md)
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------------------
# 행정규칙 (훈령·예규·고시·지침 등) — target=admrul
# ---------------------------------------------------------------------------
_KOR_MOK = "가나다라마바사아자차카타파하"
# '…한다.' 처럼 문장 끝 '다.'를 목 표시로 오인하지 않도록, 목 앞 글자가 이 용언 어미면 제외
_VERB_END = set("한된있없같른진는이였했하니렵받될할된함됨음임")
_IMG_RE = re.compile(r'<img\s+id="?(\d+)"?\s*/?>\s*(?:</img>)?', re.I)
_HO_RE = re.compile(r"(?<!\d)(\d{1,3})(?:의(\d{1,2}))?\.(?!\d)")
_MOK_RE = re.compile(r"([가-힣])\.(?!\d)")
_HEADING_RE = re.compile(r"^\s*제\s*\d+\s*(편|장|절|관)(\s|$)")


def _file_url(seq: str) -> str:
    return f"{LAW_WEB_BASE}/LSW/flDownload.do?flSeq={seq}"


def _abs_link(u: Any) -> Optional[str]:
    u = str(u or "").strip()
    if not u:
        return None
    if u.startswith("/"):
        return LAW_WEB_BASE + u
    return re.sub(r"^https?://(www\.)?law\.go\.kr", LAW_WEB_BASE, u)


def _replace_imgs(s: str, md: bool) -> str:
    def rep(m):
        url = _file_url(m.group(1))
        return f"[수식 이미지]({url})" if md else f"[수식 이미지: {url}]"
    return _IMG_RE.sub(rep, s)


def _cut(s: str, cuts: list[int]) -> list[str]:
    pts = [0] + sorted(set(cuts)) + [len(s)]
    return [s[a:b].strip() for a, b in zip(pts, pts[1:]) if s[a:b].strip()]


def _split_ho(seg: str) -> list[str]:
    """항(또는 조) 문자열을 [본문, 1호, 2호, …]로 분리. 호 번호가 1,2,3… 순서로 나올 때만 끊는다."""
    cuts, expect = [], 1
    for m in _HO_RE.finditer(seg):
        if m.start() == 0:
            continue
        n, br = int(m.group(1)), m.group(2)
        if br:
            if n != expect - 1:
                continue
        else:
            if n != expect:
                continue
            if n == 1:
                prev = seg[:m.start()].rstrip()[-1:]
                has2 = any(int(x.group(1)) == 2 and not x.group(2) for x in _HO_RE.finditer(seg, m.end()))
                if prev not in ".:)」" and not has2:
                    continue
            expect = n + 1
        cuts.append(m.start())
    return _cut(seg, cuts)


def _split_mok(seg: str) -> list[str]:
    """호 문자열을 [본문, 가목, 나목, …]로 분리. 가·나·다… 순서로 나올 때만 끊는다."""
    cuts, idx = [], 0
    for m in _MOK_RE.finditer(seg):
        if idx >= len(_KOR_MOK) or m.start() == 0:
            continue
        if m.group(1) != _KOR_MOK[idx]:
            continue
        if idx == 0:
            if _KOR_MOK[1] + "." not in seg[m.end():]:
                continue
        elif seg[m.start() - 1] in _VERB_END:
            continue
        cuts.append(m.start())
        idx += 1
    return _cut(seg, cuts)


def _format_admrul_article(s: str, md: bool) -> str:
    s = _replace_imgs(_text(s), md)
    parts = [p for p in re.split(r"(?=[①-⑳])", s) if p.strip()]
    lines: list[str] = []
    for i, part in enumerate(parts):
        hos = _split_ho(part)
        lines.append(hos[0])
        for ho in hos[1:]:
            moks = _split_mok(ho)
            lines.append("  " + moks[0])
            lines += ["    " + mk for mk in moks[1:]]
    return "\n".join(lines)


def _split_addenda_body(body: str) -> str:
    body = re.sub(r"(?<=\S)\s*(?=[①-⑳])", "\n", body.strip())
    cuts, exp = [], 1
    for m in re.finditer(r"제\s*(\d+)\s*조(?:의\s*\d+)?\s*\(", body):
        if int(m.group(1)) == exp:
            if m.start() > 0:
                cuts.append(m.start())
            exp += 1
    out = []
    for piece in _cut(body, cuts):
        out += [ln.strip() for ln in piece.split("\n") if ln.strip()]
    return "\n".join(out)


def _admrul_addenda(root: dict) -> list[dict]:
    b = root.get("부칙") or {}
    if not isinstance(b, dict):
        return []
    dates, nums = _as_list(b.get("부칙공포일자")), _as_list(b.get("부칙공포번호"))
    out = []
    for i, c in enumerate(_as_list(b.get("부칙내용"))):
        t = _text(c)
        m = re.match(r"^\s*(부\s*칙\s*(?:\([^)]*\))?\s*<[^>]*>)\s*(.*)$", t, re.S)
        d = dates[i] if i < len(dates) else ""
        n = nums[i] if i < len(nums) else ""
        title, body = (m.group(1), m.group(2)) if m else (f"부칙 <제{n}호, {d}>", t)
        out.append({"공포일자": d, "공포번호": n, "제목": title.strip(), "내용": _split_addenda_body(body)})
    return out


def _admrul_files(root: dict) -> list[dict]:
    f = root.get("첨부파일") or {}
    if not isinstance(f, dict):
        return []
    links, names = _as_list(f.get("첨부파일링크")), _as_list(f.get("첨부파일명"))
    return [{"파일명": names[i] if i < len(names) else f"첨부{i + 1}", "링크": _abs_link(u)}
            for i, u in enumerate(links)]


def _render_admrul(info: dict, arts: list[str], fmt: str, src: dict, addenda: list[dict],
                   addenda_total: int, annexes: list[dict], files: list[dict]) -> str:
    md = fmt == "md"
    title = info.get("행정규칙명") or src.get("행정규칙명") or ""
    meta = {
        "행정규칙명": title,
        "구분": f"행정규칙({info.get('행정규칙종류') or src.get('행정규칙종류') or ''})",
        "발령번호": info.get("발령번호") or src.get("발령번호"),
        "발령일자": info.get("발령일자") or src.get("발령일자"),
        "시행일자": info.get("시행일자") or src.get("시행일자"),
        "소관부처": info.get("소관부처명") or src.get("소관부처명"),
        "담당부서": info.get("담당부서기관명"),
        "행정규칙일련번호(admrul_id)": info.get("행정규칙일련번호") or src.get("행정규칙일련번호"),
        "행정규칙ID": info.get("행정규칙ID") or src.get("행정규칙ID"),
        "출처": "국가법령정보센터(law.go.kr) Open API",
    }
    if fmt == "json":
        items = []
        for a in arts:
            t = _text(a)
            if _HEADING_RE.match(t) and "조(" not in t:
                items.append({"구분": "제목", "내용": t.strip()})
            else:
                body = _format_admrul_article(t, False)
                items.append({"구분": "조문", "조문": re.match(r"^(제\s*\d+\s*조(?:의\s*\d+)?(?:\([^)]*\))?)", body).group(1)
                              if re.match(r"^제\s*\d+\s*조", body) else "", "본문": body})
        return json.dumps({**meta, "조문수": sum(1 for x in items if x["구분"] == "조문"), "조문": items,
                           "부칙수": addenda_total, "부칙": addenda,
                           "별표수": sum(1 for x in annexes if x["구분"] != "서식"),
                           "서식수": sum(1 for x in annexes if x["구분"] == "서식"),
                           "별표서식": annexes, "첨부파일": files}, ensure_ascii=False, indent=2)

    out = [f"# {title}" if md else title, ""]
    for k, v in meta.items():
        if v and k != "행정규칙명":
            out.append(f"- {k}: {v}" if md else f"{k}: {v}")
    if files:
        out.append(("- 전문 파일: " + " · ".join(f"[{x['파일명']}]({x['링크']})" for x in files)) if md
                   else "전문 파일: " + " / ".join(f"{x['파일명']} {x['링크']}" for x in files))
    out.append("")
    for a in arts:
        t = _text(a)
        if not t:
            continue
        if _HEADING_RE.match(t) and "조(" not in t:
            head = t.strip()
            level = "##" if re.match(r"^제\s*\d+\s*(편|장)", head) else "###"
            out += ["", f"{level} {head}" if md else f"[{head}]", ""]
            continue
        body = _format_admrul_article(t, md)
        if md:
            m = re.match(r"^(제\s*\d+\s*조(?:의\s*\d+)?(?:\([^)]*\))?)(.*)$", body, re.S)
            out.append(f"**{m.group(1)}**{m.group(2)}" if m else body)
        else:
            out.append(body)
        out.append("")
    if addenda:
        label = "부칙" if len(addenda) == addenda_total else f"부칙 (전체 {addenda_total}개 중 최신 {len(addenda)}개)"
        out += ["", f"## {label}" if md else f"[{label}]", ""]
        for ad in addenda:
            out.append(f"### {ad['제목']}" if md else ad["제목"])
            if ad["내용"]:
                out.append(ad["내용"])
            out.append("")
    out += _render_annexes(annexes, md)
    return "\n".join(out).rstrip() + "\n"


async def _search_admrul(query: str, oc: Optional[str], display: int = 50, page: int = 1) -> tuple[list[dict], Any]:
    data = await _call("lawSearch.do", {"target": "admrul", "query": query, "display": display, "page": page}, oc)
    root = data.get("AdmRulSearch", data)
    items = [x for x in _as_list(root.get("admrul")) if isinstance(x, dict)]
    for x in items:
        x["행정규칙명"] = _strip_tags(x.get("행정규칙명"))
    return items, root.get("totalCnt", len(items))


def _exact(items: list[dict], key: str, name: str) -> Optional[dict]:
    ex = [x for x in items if _norm(str(x.get(key) or "")) == _norm(name)]
    ex.sort(key=lambda x: 0 if str(x.get("현행연혁구분", "현행")).startswith("현행") else 1)
    return ex[0] if ex else None


# ---------------------------------------------------------------------------
# 전체 본문 조회 (법령 + 행정규칙)
# ---------------------------------------------------------------------------
def _check_modes(fmt: str, addenda: str, annex: str) -> tuple[str, str, str]:
    fmt, addenda, annex = (fmt or "md").lower(), (addenda or "all").lower(), (annex or "annex").lower()
    if fmt not in _FORMATS:
        raise ValueError(f"format 은 {', '.join(_FORMATS)} 중 하나여야 합니다.")
    if addenda not in _ADDENDA_MODES:
        raise ValueError(f"addenda 는 {', '.join(_ADDENDA_MODES)} 중 하나여야 합니다.")
    if annex not in _ANNEX_MODES:
        raise ValueError(f"annex 는 {', '.join(_ANNEX_MODES)} 중 하나여야 합니다.")
    return fmt, addenda, annex


async def _law_doc(mst, law_id, src: dict, fmt, addenda, annex, oc) -> dict:
    data = await _call("lawService.do", {"target": "law", **_id_params(mst, law_id)}, oc)
    info = _basic_info(data)
    units = _article_units(data)
    if not units:
        raise LawApiError("조문 데이터가 비어 있습니다.")
    src = dict(src)
    src.setdefault("법령일련번호", mst)
    all_adds = _addenda_units(data)
    adds = _select_addenda(all_adds, addenda)
    annexes = _select_annexes(_annex_units(data), annex)
    return {"kind": "law", "id": str(mst or law_id),
            "content": _render_law(info, units, fmt, src, adds, len(all_adds), annexes)}


async def _admrul_doc(serial, src: dict, fmt, addenda, annex, oc) -> dict:
    data = await _call("lawService.do", {"target": "admrul", "ID": str(serial).strip()}, oc)
    root = data.get("AdmRulService", data)
    info = root.get("행정규칙기본정보") or {}
    arts = [a for a in _as_list(root.get("조문내용")) if _text(a)]
    if not arts:
        raise LawApiError("행정규칙 본문(조문내용)이 비어 있습니다. 첨부파일 형태로만 제공되는 규칙일 수 있습니다.")
    all_adds = _admrul_addenda(root)
    adds = _select_addenda(all_adds, addenda)
    annexes = _select_annexes(_annex_units(root), annex)
    return {"kind": "admrul", "id": str(serial),
            "content": _render_admrul(info, arts, fmt, src, adds, len(all_adds), annexes, _admrul_files(root))}


async def _fetch_fulltext(name, mst, law_id, fmt, oc, addenda="all", annex="annex",
                          kind="auto", admrul_id=None) -> dict:
    fmt, addenda, annex = _check_modes(fmt, addenda, annex)
    kind = (kind or "auto").lower()
    if kind not in ("auto", "law", "admrul"):
        raise ValueError("kind 는 auto, law, admrul 중 하나여야 합니다.")
    if admrul_id:
        return await _admrul_doc(admrul_id, {}, fmt, addenda, annex, oc)
    if mst or law_id:
        return await _law_doc(mst, law_id, {}, fmt, addenda, annex, oc)
    if not name:
        raise ValueError("law_name, mst/law_id, admrul_id 중 하나가 필요합니다.")

    law_items: list[dict] = []
    if kind in ("auto", "law"):
        law_items = await _search_items(name, oc)
        hit = _exact(law_items, "법령명한글", name)
        if hit:
            r = await _law_doc(hit.get("법령일련번호"), None, hit, fmt, addenda, annex, oc)
            r["related"] = [("law", x) for x in law_items if x is not hit]
            return r
    adm_items: list[dict] = []
    if kind in ("auto", "admrul"):
        adm_items, _ = await _search_admrul(name, oc)
        hit = _exact(adm_items, "행정규칙명", name)
        if hit:
            r = await _admrul_doc(hit.get("행정규칙일련번호"), hit, fmt, addenda, annex, oc)
            r["related"] = [("admrul", x) for x in adm_items if x is not hit][:10] + \
                           [("law", x) for x in law_items][:10]
            return r
    cands = [("law", x) for x in law_items] + [("admrul", x) for x in adm_items]
    if not cands:
        where = {"auto": "법령·행정규칙", "law": "법령", "admrul": "행정규칙"}[kind]
        raise LawApiError(f"'{name}' 에 해당하는 {where} 검색 결과가 없습니다.")
    if len(cands) == 1:
        k, x = cands[0]
        if k == "law":
            return await _law_doc(x.get("법령일련번호"), None, x, fmt, addenda, annex, oc)
        return await _admrul_doc(x.get("행정규칙일련번호"), x, fmt, addenda, annex, oc)
    return {"ambiguous": True, "candidates": cands}


def _cand_line(k: str, x: dict) -> str:
    if k == "law":
        return (f"- {x.get('법령명한글')} [법령·{x.get('법령구분명', '')}] mst={x.get('법령일련번호')} "
                f"시행 {x.get('시행일자', '')} / {x.get('소관부처명', '')}")
    return (f"- {x.get('행정규칙명')} [행정규칙·{x.get('행정규칙종류', '')}] admrul_id={x.get('행정규칙일련번호')} "
            f"시행 {x.get('시행일자', '')} / {x.get('소관부처명', '')} ({x.get('현행연혁구분', '')})")


@mcp.tool()
async def get_lawfulltext(law_name: Optional[str] = None, format: str = "md",
                          mst: Optional[StrOrInt] = None, law_id: Optional[StrOrInt] = None,
                          addenda: str = "all", annex: str = "annex",
                          kind: str = "auto", admrul_id: Optional[StrOrInt] = None,
                          oc: Optional[str] = None) -> str:
    """법령 또는 행정규칙(훈령·예규·고시·지침 등)의 전체 조문(편·장·절 제목 포함), 부칙, 별표·서식을 응답으로 돌려줍니다.
    이름으로 법령을 먼저 찾고, 이름이 일치하는 법령이 없으면 행정규칙에서 찾습니다(kind='auto').
    정확히 일치하는 것이 없고 후보가 여러 개면 후보 목록(mst 또는 admrul_id 포함)을 보여주며, 그 값을 넣어 다시 호출하면 됩니다.

    Args:
        law_name: 법령명 또는 행정규칙명 (예: '근로기준법', '방산원가대상물자의 원가계산에 관한 시행세칙')
        format: 응답 형식 'md'(기본) | 'txt' | 'json'
        mst: 법령일련번호 (지정 시 검색 없이 법령 바로 조회)
        law_id: 법령ID (mst 대신 사용 가능)
        addenda: 부칙 포함 방식 'all'(기본, 제정~최신 전부) | 'latest'(가장 최근 부칙만) | 'none'(제외)
        annex: 별표·서식 포함 방식 'annex'(기본, 별표는 본문 / 서식은 제목·원본 링크만) |
               'all'(별표·서식 모두 본문) | 'list'(모두 제목·원본 링크만) | 'none'(제외)
        kind: 검색 대상 'auto'(기본, 법령 → 행정규칙 순) | 'law'(법령만) | 'admrul'(행정규칙만)
        admrul_id: 행정규칙일련번호 (지정 시 검색 없이 행정규칙 바로 조회)
        oc: 법제처 Open API 인증키
    """
    try:
        r = await _fetch_fulltext(law_name, mst, law_id, format, oc, addenda, annex, kind, admrul_id)
    except Exception as e:
        return _err(e)
    if r.get("ambiguous"):
        lines = [f"'{law_name}'과 이름이 정확히 일치하는 법령·행정규칙이 없습니다. "
                 "아래 후보 중 하나를 골라 mst 또는 admrul_id 로 다시 호출하세요."]
        lines += [_cand_line(k, x) for k, x in r["candidates"][:30]]
        return "\n".join(lines)
    if (format or "md").lower() == "json":
        return r["content"]
    rel = r.get("related") or []
    note = ""
    if rel:
        note = "\n\n---\n검색된 관련 항목:\n" + "\n".join(_cand_line(k, x) for k, x in rel[:10])
    return r["content"].rstrip() + note


# ---------------------------------------------------------------------------
# 상태 확인 (HTTP 모드) — 접속 키 없이 응답, 컨테이너 HEALTHCHECK·프록시 점검용
# ---------------------------------------------------------------------------
from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "getlawtext", "law_oc_set": bool(DEFAULT_OC)})


# ---------------------------------------------------------------------------
# MCP 접속 키 (HTTP 모드)
# ---------------------------------------------------------------------------
def _load_api_keys() -> list[str]:
    """MCP_API_KEY(쉼표로 여러 개) 또는 MCP_API_KEY_FILE(한 줄에 하나)에서 접속 키를 읽는다."""
    keys = [k.strip() for k in os.environ.get("MCP_API_KEY", "").split(",") if k.strip()]
    path = os.environ.get("MCP_API_KEY_FILE", "").strip()
    if path:
        with open(path, encoding="utf-8") as f:
            keys += [ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    return keys


class ApiKeyMiddleware:
    """요청마다 접속 키를 확인하는 ASGI 미들웨어.
    허용 위치: Authorization: Bearer <키> / X-API-Key: <키> / (MCP_ALLOW_QUERY_KEY=true 일 때) ?key=<키>"""

    def __init__(self, app, keys: list[str], allow_query: bool = False):
        self.app = app
        self.keys = [k.encode() for k in keys]
        self.allow_query = allow_query

    def _ok(self, candidate: Optional[str]) -> bool:
        if not candidate:
            return False
        c = candidate.strip().encode()
        return any(hmac.compare_digest(c, k) for k in self.keys)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") == "OPTIONS" or scope.get("path") == "/health":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        cand = [auth[7:] if auth.lower().startswith("bearer ") else None, headers.get("x-api-key")]
        if self.allow_query:
            from urllib.parse import parse_qs
            cand += parse_qs(scope.get("query_string", b"").decode()).get("key", [])
        if any(self._ok(c) for c in cand):
            return await self.app(scope, receive, send)
        body = json.dumps({"error": "unauthorized",
                           "message": "유효한 MCP 접속 키가 필요합니다 (Authorization: Bearer <키> 또는 X-API-Key 헤더)."},
                          ensure_ascii=False).encode()
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json; charset=utf-8"),
                                (b"www-authenticate", b'Bearer realm="getlawtext"'),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        mcp.run(transport="stdio")
        return
    import uvicorn
    app = mcp.streamable_http_app() if transport == "streamable-http" else mcp.sse_app()
    keys = _load_api_keys()
    if keys:
        allow_query = os.environ.get("MCP_ALLOW_QUERY_KEY", "").lower() in ("1", "true", "yes")
        app = ApiKeyMiddleware(app, keys, allow_query)
        print(f"[getlawtext] MCP 접속 키 인증 사용 (키 {len(keys)}개"
              f"{', ?key= 쿼리 허용' if allow_query else ''})", flush=True)
    else:
        print("[getlawtext] 경고: MCP_API_KEY 가 없어 인증 없이 실행합니다.", flush=True)
    uvicorn.run(app, host=mcp.settings.host, port=mcp.settings.port,
                log_level=mcp.settings.log_level.lower())


if __name__ == "__main__":
    main()
