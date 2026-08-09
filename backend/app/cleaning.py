# -*- coding: utf-8 -*-
"""文本清洗（spec §3.7）：去控制字符 / 空白 / HTML 残留噪音。

MIN_TEXT_LEN：清洗后有效文本下限（spec：<50 字符标记 failed 不入库）。
"""
import re

MIN_TEXT_LEN = 50

# 控制字符（保留 \n \t 等常见空白类）
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# 多余空白（含全角空格、连续换行压缩）
_WS = re.compile(r"[ \t\u3000]+")
# HTML 标签残留与脚本/样式块
_TAG = re.compile(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<[^>]+>")
# 常见导航/页脚噪音行
_NOISE = re.compile(
    r"^\s*(首页|上一页|下一页|返回顶部|版权所有|Copyright|免责声明|联系我们|"
    r"导航|菜单|登录|注册|分享到|点赞|评论|关注我们|ICP备|友情链接)\s*[:：]?.*$",
    re.IGNORECASE,
)


def clean_text(raw: str) -> str:
    """清洗：去 HTML → 去控制字符 → 去噪音行 → 压缩空白 → 统一换行。"""
    text = _TAG.sub("", raw or "")
    text = _CTRL.sub("", text)
    lines = [_NOISE.sub("", ln).strip() for ln in text.splitlines()]
    text = "\n".join(ln for ln in lines if ln)
    text = _WS.sub(" ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)  # 压缩连续空行
    return text.strip()
