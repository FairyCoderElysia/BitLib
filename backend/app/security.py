# -*- coding: utf-8 -*-
"""安全工具：bcrypt 密码哈希 + JWT 签发/校验（pyjwt）。"""
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt

from .config import settings

# JWT 算法
_JWT_ALG = "HS256"


def hash_password(password: str) -> str:
    """bcrypt 哈希密码。"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """校验明文密码与哈希是否匹配。"""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(user_id: int, username: str, role: str) -> str:
    """签发 JWT，默认过期 jwt_expire_minutes（12h）。"""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=_JWT_ALG)


def decode_token(token: str) -> Optional[dict]:
    """校验并解码 JWT；无效/过期返回 None。"""
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[_JWT_ALG])
    except jwt.PyJWTError:
        return None
