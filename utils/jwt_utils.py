"""Arqon Bot — JWT helpers (PyJWT)."""
import time
from typing import Optional, Dict, Any

import jwt as pyjwt
from jwt import PyJWTError

import config


def create_jwt_token(data: Dict[str, Any], expires_minutes: int = None) -> str:
    to_encode = data.copy()
    now = int(time.time())
    to_encode.update({
        "exp": now + int((expires_minutes or config.settings.JWT_EXPIRE_MINUTES) * 60),
        "iat": now,
    })
    return pyjwt.encode(to_encode, config.settings.JWT_SECRET,
                        algorithm=config.settings.JWT_ALGORITHM)


def decode_jwt_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        return pyjwt.decode(token, config.settings.JWT_SECRET,
                            algorithms=[config.settings.JWT_ALGORITHM])
    except PyJWTError:
        return None


def verify_jwt_token(token: str) -> bool:
    return decode_jwt_token(token) is not None


def create_node_token(node_id: int, node_name: str) -> str:
    return create_jwt_token({"type": "node", "node_id": node_id, "node_name": node_name})


def create_user_token(user_id: int, permissions: list = None) -> str:
    return create_jwt_token({"type": "user", "user_id": user_id, "permissions": permissions or []})


def create_api_token(api_key_id: int, user_id: int, permissions: list = None) -> str:
    return create_jwt_token({
        "type": "api",
        "api_key_id": api_key_id,
        "user_id": user_id,
        "permissions": permissions or [],
    })
