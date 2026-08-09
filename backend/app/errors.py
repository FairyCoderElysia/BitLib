# -*- coding: utf-8 -*-
"""统一业务异常与业务码。

错误响应结构（spec §10.1）：{"code": <业务码>, "message": "<可读错误>", "detail": {...}}
HTTP 状态码：400 参数错误 / 401 未认证 / 403 无权限 / 404 不存在 / 409 冲突 / 500 内部错误。
"""

# 业务码
CODE_BAD_REQUEST = 40000
CODE_UNAUTHORIZED = 40100
CODE_FORBIDDEN = 40300
CODE_NOT_FOUND = 40400
CODE_CONFLICT = 40900
CODE_INTERNAL = 50000


class BizError(Exception):
    """业务异常：由 main.py 的全局异常处理器转为统一错误响应。"""

    def __init__(self, status_code: int = 400, code: int = CODE_BAD_REQUEST,
                 message: str = "请求失败", detail=None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.detail = detail or {}
        super().__init__(message)


def bad_request(message: str, detail=None) -> BizError:
    return BizError(400, CODE_BAD_REQUEST, message, detail)


def unauthorized(message: str = "未登录或登录已过期") -> BizError:
    return BizError(401, CODE_UNAUTHORIZED, message)


def forbidden(message: str = "无权限执行该操作") -> BizError:
    return BizError(403, CODE_FORBIDDEN, message)


def not_found(message: str = "资源不存在") -> BizError:
    return BizError(404, CODE_NOT_FOUND, message)


def conflict(message: str, detail=None) -> BizError:
    return BizError(409, CODE_CONFLICT, message, detail)
