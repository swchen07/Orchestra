import os
from fastapi import HTTPException, Request


def require_origin(request: Request):
    origin = request.headers.get('origin')
    allowed = {
        x.strip().rstrip('/')
        for x in os.environ.get(
            'WORKBENCH_ALLOWED_ORIGINS',
            'http://localhost:5173;http://localhost:8080;http://127.0.0.1:5173;http://127.0.0.1:8080',
        ).split(';')
    }
    if origin and origin.rstrip('/') not in allowed:
        raise HTTPException(403, '不允许此页面来源发起本机框架请求')
    if not origin and not request.headers.get('authorization'):
        raise HTTPException(401, '需要 Bearer 认证或明确的同源来源')
