# /etc/nginx/getlawtext-proxy.inc — MCP Streamable HTTP(SSE 포함) 프록시 공통 설정
proxy_http_version 1.1;
proxy_set_header Connection "";
proxy_set_header Host              $host;
proxy_set_header X-Real-IP         $remote_addr;
proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
# Authorization / X-API-Key / Mcp-Session-Id 헤더는 기본으로 그대로 전달됨

# 스트리밍 응답(SSE)이 지연·끊김 없이 바로 전달되도록
proxy_buffering off;
proxy_cache off;
proxy_request_buffering off;
chunked_transfer_encoding on;
proxy_read_timeout 3600s;
proxy_send_timeout 3600s;

client_max_body_size 4m;
