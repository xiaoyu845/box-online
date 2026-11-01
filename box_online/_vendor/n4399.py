# -*- coding: utf-8 -*-
# ============================================================================
# 内置副本（vendored）——请勿直接改本文件
#
# 来源：内置的 4399 登录客户端（纯标准库实现）
# 上游 SHA-256：26711a12a54708cfa0b99b733728480d3af28c1e3981113fe48715470d99e10b
#
# 为什么内置而不是外部依赖
#   1. 该模块零第三方依赖、零 外部包引用（纯 stdlib），复制成本极低
#   2. box-online 是冻结单文件 exe，运行时机器上不一定有 外部组件 源码
#   3. 走 外部包会连带拉起 http_client/netease/wpf_launcher/x19，
#      那才是有 requests/crypto 依赖的重包
#
# 同步方式：重跑 box-online/tools/sync_vendor.py
# ============================================================================

"""4399 OAuth2 login → uni_sauth (aligned with 404Launcher Sauth4399Client)."""

from __future__ import annotations

import base64
import gzip
import json
import os
import re
import secrets
import time
import uuid
import zlib
from dataclasses import dataclass
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener

LOGIN_BASE_URL = "https://ptlogin.4399.com/"
LOGIN_URL = (
    "https://ptlogin.4399.com/oauth2/loginAndAuthorize.do"
    "?channel=&sdk=op&sdk_version=3.12.2.503"
)
API_BASE_URL = "https://m.4399api.com/"
OAUTH_CALLBACK_URL = (
    "https://m.4399api.com/openapi/oauth-callback.html?gamekey=44770&game_key=115716"
)
OAUTH_DEVICE_URL = "https://m.4399api.com/openapiv2/oauth.html"
UNI_SAUTH_URL = "https://mgbsdk.matrix.netease.com/x19/sdk/uni_sauth"
CAPTCHA_URL = "https://ptlogin.4399.com/ptlogin/captcha.do?captchaId="

BIZ_ID = "2100001792"
CLIENT_ID = "40f9e9b95d6c71ba5c6e0bd14c0abeff"
GAME_KEY = "115716"
CHANNEL = "4399com"
SDK_VERSION = "3.12.2"
# Keep same fingerprint as 404Launcher 4399com channel client.
CLIENT_LOGIN_SN = "BakaGuimc"

DEVICE_FILE = Path(os.path.expanduser("~")) / ".box-online" / "4399-device.cds"

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0"
)
_UNI_UA = "WPFLauncher/0.0.0.0"

_CAPTCHA_ID_RE = re.compile(
    r"captchaId=(captchaReq[0-9a-zA-Z]+|[0-9a-zA-Z]+)|"
    r"captcha[_-]?id[\"'\s:=]+[\"']?([0-9a-zA-Z_-]+)",
    re.I,
)


class LoginError(RuntimeError):
    pass


class CaptchaRequired(LoginError):
    def __init__(self, captcha_id: str, message: str = "需要输入验证码") -> None:
        super().__init__(message or "需要输入验证码")
        self.captcha_id = (captcha_id or "").strip()

    @property
    def captcha_url(self) -> str:
        return CAPTCHA_URL + self.captcha_id


class SecondaryVerificationRequired(LoginError):
    pass


@dataclass
class CaptchaImage:
    captcha_id: str
    image_bytes: bytes
    content_type: str = "image/png"


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _default_headers(url: str) -> Dict[str, str]:
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else "https://www.4399.com"
    return {
        "Accept": "text/html,application/xhtml+xml,application/json,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "Origin": "https://www.4399.com" if "4399" in origin else origin,
        "Referer": "https://www.4399.com/",
        "User-Agent": _BROWSER_UA,
    }


def _random_udid_sn() -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(16))


def _generate_identifier() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y%m%d%H%M") + secrets.token_hex(25)


def _json_or_raise(text: str, url: str = "") -> Any:
    raw = (text or "").strip()
    if not raw:
        raise LoginError(f"服务返回空响应：{url or 'unknown'}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        preview = raw[:120].replace("\n", " ")
        raise LoginError(f"服务返回非 JSON（HTTP 200）：{preview}") from exc


def _extract_captcha_id(text: str) -> str:
    if not text:
        return ""
    m = _CAPTCHA_ID_RE.search(text)
    if not m:
        return ""
    return (m.group(1) or m.group(2) or "").strip()


def _needs_captcha(text: str) -> bool:
    """Only true when server actually demands a captcha — not when HTML merely has captcha UI."""
    if not text:
        return False
    low = text.lower()
    if _extract_captcha_id(text):
        # captchaId in body alone is not enough (login page always has captcha field);
        # require an explicit demand phrase nearby or known error messages.
        pass
    demand = (
        "需要输入验证码" in text
        or "请输入验证码" in text
        or "请填写验证码" in text
        or "验证码错误" in text
        or "验证码不正确" in text
        or "验证码已过期" in text
        or "要求图片验证" in text
    )
    if demand:
        return True
    # JSON-ish captcha challenges
    if ("captchaid" in low.replace("_", "") or "captcha_id" in low) and any(
        k in text for k in ("需要", "请输入", "错误", "过期", "失败")
    ):
        return True
    return False


def _looks_like_session_token(state: str, csrf: str = "") -> bool:
    """True when state is OAuth result.state for uni_sauth — not CSRF device state."""
    s = (state or "").strip()
    if not s:
        return False
    if csrf and s == csrf.strip():
        return False
    if "|" in s:
        return True
    return len(s) >= 32


def _auth_fields(obj: Any) -> Tuple[str, str]:
    if not isinstance(obj, dict):
        return "", ""
    uid = str(obj.get("uid") or obj.get("sdkuid") or obj.get("userId") or "").strip()
    state = str(
        obj.get("state") or obj.get("sessionid") or obj.get("access_token") or ""
    ).strip()
    return uid, state


def _looks_like_auth_result(obj: Any, csrf: str = "") -> bool:
    uid, state = _auth_fields(obj)
    return bool(uid and _looks_like_session_token(state, csrf))


def _unwrap_result(obj: Any) -> Any:
    if not isinstance(obj, dict):
        return obj
    nested = obj.get("result")
    if isinstance(nested, str):
        text = nested.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                nested = json.loads(text)
            except Exception:
                q = parse_qs(text)
                if q:
                    nested = {k: v[0] for k, v in q.items() if v}
        elif "uid=" in text or "state=" in text or "access_token=" in text:
            q = parse_qs(text)
            if q:
                nested = {k: v[0] for k, v in q.items() if v}
    return nested if nested is not None else obj


def _normalize_auth_result(obj: Any, csrf: str = "") -> Optional[Dict[str, Any]]:
    """Unwrap oauth-callback envelopes like {code:100, result:{uid,state,...}}."""
    if not isinstance(obj, dict):
        return None
    candidates = [obj]
    nested = _unwrap_result(obj)
    if isinstance(nested, dict) and nested is not obj:
        candidates.append(nested)
    for item in candidates:
        if _looks_like_auth_result(item, csrf):
            uid, state = _auth_fields(item)
            return {"uid": uid, "state": state}
    return None


def _coerce_json(text: str) -> Optional[Any]:
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("{") or raw.startswith("["):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    decoder = json.JSONDecoder()
    for i, ch in enumerate(raw):
        if ch != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(raw[i:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _decode_response_text(body: bytes) -> str:
    if not body:
        return ""
    for enc in ("utf-8", "gbk", "gb2312"):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", "replace")


def _maybe_decompress(headers: Dict[str, str], body: bytes) -> bytes:
    if not body:
        return body
    enc = str(headers.get("Content-Encoding") or headers.get("content-encoding") or "").lower()
    try:
        if "gzip" in enc or body[:2] == b"\x1f\x8b":
            return gzip.decompress(body)
        if "deflate" in enc:
            try:
                return zlib.decompress(body)
            except zlib.error:
                return zlib.decompress(body, -zlib.MAX_WBITS)
    except Exception:
        return body
    return body


def _html_error_tip(text: str) -> str:
    raw = text or ""
    for pat in (
        r"id=[\"']login_err_msg[\"'][^>]*>\s*([^<]+)",
        r"__errorCallback\(\s*['\"]([^'\"]+)['\"]\s*\)",
        r"login_err_tip\">([^<]+)",
    ):
        m = re.search(pat, raw)
        if not m:
            continue
        tip = (m.group(1) or "").strip()
        if tip:
            return tip
    return ""


def _json_error_message(text: str) -> str:
    obj = _coerce_json(text or "")
    if not isinstance(obj, dict):
        return ""
    return str(obj.get("message") or obj.get("msg") or obj.get("error") or "").strip()


def _is_rate_limited(status: int, text: str) -> bool:
    if status in (429, 202):
        return True
    blob = text or ""
    if "登录请求被临时限制" in blob:
        return True
    msg = _json_error_message(blob)
    tip = _html_error_tip(blob)
    hay = " ".join(x for x in (msg, tip) if x)
    return any(k in hay for k in ("请稍后再试", "临时限制"))


class Sauth4399Client:
    def __init__(self, device_file: Optional[Path] = None, timeout: float = 30.0) -> None:
        self.device_file = Path(device_file) if device_file else DEVICE_FILE
        self.timeout = timeout
        self.cookies = CookieJar()
        self.login_opener = build_opener(HTTPCookieProcessor(self.cookies), _NoRedirectHandler)
        self.api_opener = build_opener(HTTPCookieProcessor(self.cookies))
        self.device_identifier = ""
        self.device_identifier_sm = ""
        self.udid = ""
        self.state = ""
        self._ensure_device()

    def _request(
        self,
        url: str,
        *,
        method: str = "GET",
        data: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
        allow_redirects: bool = True,
        opener=None,
    ) -> Tuple[int, Dict[str, str], bytes, str]:
        hdrs = dict(_default_headers(url))
        if headers:
            hdrs.update(headers)
        req = Request(url, data=data, headers=hdrs, method=method.upper())
        use = opener or (self.login_opener if not allow_redirects else self.api_opener)
        try:
            resp = use.open(req, timeout=self.timeout)
            body = resp.read() or b""
            status = getattr(resp, "status", None) or resp.getcode() or 200
            final_url = resp.geturl()
            resp_headers = {k: v for k, v in (resp.headers.items() if resp.headers else [])}
            return int(status), resp_headers, _maybe_decompress(resp_headers, body), final_url
        except HTTPError as exc:
            body = exc.read() if exc.fp is not None else b""
            headers_map = {k: v for k, v in (exc.headers.items() if exc.headers else [])}
            return int(exc.code), headers_map, _maybe_decompress(headers_map, body or b""), exc.geturl() or url

    def _ensure_device(self) -> None:
        loaded = False
        if self.device_file.is_file():
            try:
                data = json.loads(self.device_file.read_text(encoding="utf-8"))
                self.device_identifier = str(data.get("deviceIdentifier") or "").strip()
                self.device_identifier_sm = str(data.get("deviceIdentifierSm") or "").strip()
                self.udid = str(data.get("deviceUdid") or "").strip()
                self.state = str(data.get("deviceState") or "").strip()
                loaded = bool(self.device_identifier and self.device_identifier_sm and self.udid)
            except Exception:
                loaded = False
        if not loaded:
            self.device_identifier = _generate_identifier()
            self.device_identifier_sm = _generate_identifier()
            self.udid = str(uuid.uuid4())
            self.state = ""
            self._persist_device()
        if not self.state:
            self._refresh_device_state()

    def _persist_device(self) -> None:
        self.device_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "deviceIdentifier": self.device_identifier,
            "deviceIdentifierSm": self.device_identifier_sm,
            "deviceUdid": self.udid,
            "deviceState": self.state,
        }
        self.device_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _device_payload(self) -> Dict[str, str]:
        return {
            "deviceIdentifier": self.device_identifier,
            "deviceIdentifierSm": self.device_identifier_sm,
            "udid": self.udid,
        }

    def _oauth_device(self) -> str:
        form = urlencode(
            {
                "usernames": "",
                "top_bar": "1",
                "state": self.state or "",
                "device": json.dumps(self._device_payload(), ensure_ascii=False, separators=(",", ":")),
            }
        ).encode("utf-8")
        status, _, body, _ = self._request(
            OAUTH_DEVICE_URL,
            method="POST",
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            allow_redirects=True,
        )
        text = body.decode("utf-8", "replace")
        if status >= 400:
            raise LoginError(f"oauth device 失败: http {status}")
        return text

    def _extract_state(self, text: str) -> str:
        if not text:
            return ""
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                for key in ("state", "deviceState", "device_state"):
                    val = str(obj.get(key) or "").strip()
                    if val:
                        return val
        except Exception:
            pass
        m = re.search(r'["\']?state["\']?\s*[:=]\s*["\']([0-9a-fA-F]{16,})["\']', text)
        if m:
            return m.group(1)
        m = re.search(r"\bstate=([0-9a-fA-F]{16,})", text)
        if m:
            return m.group(1)
        return ""

    def _request_state(self) -> str:
        status, _, body, _ = self._request(OAUTH_CALLBACK_URL, method="GET", allow_redirects=True)
        text = body.decode("utf-8", "replace")
        state = self._extract_state(text)
        if not state:
            # Fallback: random 16-byte hex like 404 when page has no state.
            state = secrets.token_hex(16)
        self.state = state
        self._persist_device()
        return state

    def _refresh_device_state(self) -> None:
        try:
            self._oauth_device()
        except Exception:
            pass
        self._request_state()

    def _build_login_parameters(
        self,
        username: str,
        password: str,
        captcha_id: str = "",
        captcha_code: str = "",
    ) -> Dict[str, str]:
        params = {
            "_d": self.device_identifier,
            "access_token": "",
            "aid": "",
            "auth_action": "ORILOGIN",
            "auto_scroll": "",
            "autoCreateAccount": "",
            "bizId": BIZ_ID,
            "cid": "",
            "client_id": CLIENT_ID,
            "css": "",
            "expand_ext_login_list": "",
            "game_key": GAME_KEY,
            "isInputRealname": "false",
            "isValidRealname": "false",
            "password": password,
            "redirect_uri": OAUTH_CALLBACK_URL,
            "ref": json.dumps({"game": GAME_KEY, "channel": ""}, ensure_ascii=False, separators=(",", ":")),
            "reg_mode": "reg_phone",
            "response_type": "TOKEN",
            "scope": "basic",
            "sec": "1",
            "show_4399": "",
            "show_back_button": "",
            "show_close_button": "",
            "show_ext_login": "",
            "show_forget_password": "",
            "show_topbar": "false",
            "state": self.state,
            "uid": "",
            "username_history": "",
            "username": username,
        }
        if captcha_id:
            params["captcha_id"] = captcha_id
            params["captcha"] = captcha_code or ""
        return params

    def get_captcha(self, captcha_id: str) -> CaptchaImage:
        cid = (captcha_id or "").strip()
        if not cid:
            raise LoginError("缺少 captcha_id")
        status, headers, body, _ = self._request(CAPTCHA_URL + cid, method="GET", allow_redirects=True)
        if status >= 400 or not body:
            raise LoginError(f"获取验证码失败: http {status}")
        ctype = headers.get("Content-Type") or headers.get("content-type") or "image/png"
        if "jpeg" in ctype or "jpg" in ctype or body[:2] == b"\xff\xd8":
            ctype = "image/jpeg"
        elif "png" in ctype or body[:8] == b"\x89PNG\r\n\x1a\n":
            ctype = "image/png"
        return CaptchaImage(captcha_id=cid, image_bytes=body, content_type=ctype)

    def _raise_known_login_error(self, text: str, fallback_url: str = "") -> None:
        raw = (text or "").strip()
        if not raw:
            return
        # JSON message
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                msg = str(obj.get("message") or obj.get("msg") or obj.get("error") or "").strip()
                code = str(obj.get("code") or "")
                if _needs_captcha(raw) or _needs_captcha(msg):
                    cid = _extract_captcha_id(raw) or _extract_captcha_id(msg)
                    if not cid:
                        raise LoginError("4399com 要求图片验证，但未返回有效验证码 ID，请稍后重试。")
                    raise CaptchaRequired(cid, msg or "需要输入验证码")
                if msg:
                    if any(k in msg for k in ("密码", "账号或密码", "用户名或密码")):
                        raise LoginError("账号或密码错误。")
                    if any(k in msg for k in ("二次", "手机验证", "短信验证", "实名")):
                        raise SecondaryVerificationRequired(msg)
                    if "不存在" in msg:
                        raise LoginError(msg if msg.endswith("。") else msg + "。")
                    raise LoginError(msg)
                if code and code not in ("0", "100", "200"):
                    raise LoginError(f"4399com 登录失败：未知错误")
        except LoginError:
            raise
        except Exception:
            pass

        if _needs_captcha(raw):
            cid = _extract_captcha_id(raw)
            if not cid:
                raise LoginError("4399com 要求图片验证，但未返回有效验证码 ID，请稍后重试。")
            raise CaptchaRequired(cid, "需要输入验证码")

        tip_patterns = [
            r"id=[\"']login_err_msg[\"'][^>]*>\s*([^<]+)",
            r"__errorCallback\(\s*['\"]([^'\"]+)['\"]\s*\)",
            r"login_err_tip\">([^<]+)",
            r"(用户名不存在[。.]?)",
            r"(用户不存在[。.]?)",
            r"(密码错误[。.]?)",
            r"(账号或密码错误[。.]?)",
        ]
        for pat in tip_patterns:
            m = re.search(pat, raw)
            if not m:
                continue
            tip = (m.group(1) if m.lastindex else m.group(0)).strip()
            if "密码" in tip:
                raise LoginError("账号或密码错误。")
            if tip:
                raise LoginError(tip if tip.endswith("。") else tip + "。")

        if any(k in raw for k in ("二次验证", "手机验证", "短信验证")):
            raise SecondaryVerificationRequired("需要二次验证，请改用官方登录或 SAUTH")

    def _create_and_validate_sauth(self, sdk_uid: str, session_id: str) -> str:
        if not _looks_like_session_token(session_id, self.state):
            raise LoginError(
                "4399com 登录返回的会话令牌无效（疑似 CSRF state）。请稍后重试。"
            )
        udid = _random_udid_sn()
        payload = {
            "aim_info": json.dumps(
                {"aim": "127.0.0.1", "country": "CN", "tz": "+0800", "tzid": ""},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "realname": json.dumps({"realname_type": 2}, ensure_ascii=False, separators=(",", ":")),
            "app_channel": CHANNEL,
            "platform": "ad",
            "client_login_sn": CLIENT_LOGIN_SN,
            "gameid": "x19",
            "login_channel": CHANNEL,
            "sdk_version": SDK_VERSION,
            "sdkuid": str(sdk_uid),
            "sessionid": str(session_id),
            "udid": udid,
            "deviceid": CLIENT_LOGIN_SN,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        status, _, resp_body, _ = self._request(
            UNI_SAUTH_URL,
            method="POST",
            data=body,
            headers={
                "User-Agent": _UNI_UA,
                "Content-Type": "application/json",
            },
            allow_redirects=True,
            opener=self.api_opener,
        )
        text = _decode_response_text(resp_body)
        try:
            result = json.loads(text) if text else {}
        except json.JSONDecodeError as exc:
            raise LoginError(f"uni_sauth 返回非 JSON：{text[:120]}") from exc
        code = str(result.get("code", ""))
        if status >= 400 or code not in ("200", "0"):
            detail = result
            dbg = str(result.get("debug_message") or "").strip()
            if dbg:
                try:
                    pad = "=" * (-len(dbg) % 4)
                    decoded = base64.b64decode(dbg + pad).decode("utf-8", "replace")
                    detail = {**result, "debug_decoded": decoded}
                except Exception:
                    pass
            raise LoginError(f"uni_sauth 校验失败：{detail}")
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def _load_callback(self, location: str, post_body: str = "") -> Optional[Any]:
        obj = _coerce_json(post_body)
        if obj is not None and (
            _normalize_auth_result(obj, self.state) is not None
            or (isinstance(obj, dict) and (obj.get("uid") or obj.get("result") or obj.get("access_token")))
        ):
            return obj
        q = parse_qs(urlparse(location or "").query)
        obj2 = {k: v[0] for k, v in q.items() if v}
        if _normalize_auth_result(obj2, self.state) is not None:
            return obj2
        json_headers = {
            "Accept": "application/json, text/javascript, */*;q=0.8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": LOGIN_URL,
        }
        _st, _hdrs, body2, url2 = self._request(
            location,
            method="GET",
            headers=json_headers,
            allow_redirects=True,
            opener=self.api_opener,
        )
        raw2 = _decode_response_text(body2)
        if "登录状态已失效" in raw2:
            return {"_expired": True}
        obj = _coerce_json(raw2)
        if obj is None:
            q = parse_qs(urlparse(url2 or location).query)
            if q:
                obj = {k: v[0] for k, v in q.items() if v}
        return obj

    def _finish_login(self, auth_obj: Any) -> str:
        if isinstance(auth_obj, str):
            text = auth_obj.strip()
            coerced = _coerce_json(text)
            if coerced is not None:
                auth_obj = coerced
            elif "access_token=" in text or text.startswith("http"):
                # Treat as URL / query and fetch JSON callback when needed.
                if text.startswith("http"):
                    status, _, body, final_url = self._request(text, method="GET", allow_redirects=True)
                    raw = body.decode("utf-8", "replace")
                    try:
                        auth_obj = _json_or_raise(raw, final_url or text)
                    except LoginError:
                        q = parse_qs(urlparse(final_url or text).query)
                        auth_obj = {k: v[0] for k, v in q.items() if v}
                else:
                    q = parse_qs(text)
                    auth_obj = {k: v[0] for k, v in q.items() if v}
            else:
                raise LoginError("4399com 登录返回的用户信息格式异常。")

        if not isinstance(auth_obj, dict):
            raise LoginError("4399com 登录返回的用户信息格式异常。")

        # oauth-callback often wraps user info in result.
        code = str(auth_obj.get("code") or "")
        normalized = _normalize_auth_result(auth_obj, self.state)
        if normalized is None:
            if code and code not in ("0", "100", "200", ""):
                msg = str(auth_obj.get("message") or auth_obj.get("msg") or "未知错误")
                raise LoginError(f"4399com 登录失败：{msg}")
            raise LoginError("4399com 登录返回缺少 uid/state。")

        uid = str(normalized.get("uid") or "").strip()
        # result.state is the long pipe token for uni_sauth — NOT the CSRF device state.
        session_state = str(normalized.get("state") or "").strip()
        if not uid or not session_state:
            raise LoginError("4399com 登录返回缺少 uid/state。")
        # Keep device CSRF state intact; only use OAuth result.state as sessionid.
        return self._create_and_validate_sauth(uid, session_state)

    def _login_and_authorize(
        self,
        username: str,
        password: str,
        captcha_id: str = "",
        captcha_code: str = "",
        *,
        retry_left: int = 2,
    ) -> str:
        self._ensure_device()
        if not self.state:
            self._refresh_device_state()

        form = urlencode(
            self._build_login_parameters(username, password, captcha_id, captcha_code)
        ).encode("utf-8")
        status, headers, body, final_url = self._request(
            LOGIN_URL,
            method="POST",
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            allow_redirects=False,
            opener=self.login_opener,
        )
        text = _decode_response_text(body)
        location = headers.get("Location") or headers.get("location") or ""
        if location:
            location = urljoin(final_url or LOGIN_URL, location)

        if status in (301, 302, 303, 307, 308) and location:
            obj = self._load_callback(location, text)
            if isinstance(obj, dict) and obj.get("_expired"):
                if retry_left > 0:
                    time.sleep(0.8)
                    self._refresh_device_state()
                    return self._login_and_authorize(
                        username, password, captcha_id, captcha_code, retry_left=retry_left - 1
                    )
            if obj is not None:
                if _normalize_auth_result(obj, self.state) is not None:
                    return self._finish_login(obj)
                try:
                    return self._finish_login(obj)
                except LoginError:
                    pass
            q = parse_qs(urlparse(location).query)
            obj2 = {k: v[0] for k, v in q.items() if v}
            if _normalize_auth_result(obj2, self.state) is not None:
                return self._finish_login(obj2)
            raise LoginError("4399com 登录回调缺少有效用户令牌。")

        if retry_left > 0 and ("登录状态已失效" in text):
            time.sleep(0.8)
            self._refresh_device_state()
            return self._login_and_authorize(
                username, password, captcha_id, captcha_code, retry_left=retry_left - 1
            )

        # Do not refresh OAuth device while rate-limited — extra hits keep the block alive.
        if _is_rate_limited(status, text):
            if retry_left > 0:
                time.sleep(6.0 if retry_left >= 2 else 12.0)
                return self._login_and_authorize(
                    username, password, captcha_id, captcha_code, retry_left=retry_left - 1
                )
            raise LoginError("请稍后再试。")

        self._raise_known_login_error(text, final_url or LOGIN_URL)

        # Direct JSON success without redirect.
        if text.strip().startswith("{") or "uid" in text:
            obj = _coerce_json(text)
            if obj is not None and _normalize_auth_result(obj, self.state) is not None:
                return self._finish_login(obj)

        raise LoginError("4399com 登录失败，服务端未返回授权跳转地址。")

    def login(
        self,
        username: str,
        password: str,
        captcha_id: str = "",
        captcha_code: str = "",
    ) -> str:
        user = username.strip().lower()
        cid = (captcha_id or "").strip()
        code = (captcha_code or "").strip()
        try:
            return self._login_and_authorize(user, password, cid, code, retry_left=2)
        except LoginError as exc:
            msg = str(exc)
            # uni_sauth auth-fail is often a stale device/CSRF race — one full retry.
            if "uni_sauth" in msg and ("auth fail" in msg.lower() or "认证失败" in msg):
                time.sleep(0.8)
                self._refresh_device_state()
                return self._login_and_authorize(user, password, cid, code, retry_left=1)
            raise


def get_captcha(captcha_id: str) -> CaptchaImage:
    return Sauth4399Client().get_captcha(captcha_id)


def login_with_password(
    username: str,
    password: str,
    captcha_id: str = "",
    captcha_code: str = "",
) -> str:
    return Sauth4399Client().login(username, password, captcha_id, captcha_code)


def captcha_image_to_data_url(image: CaptchaImage) -> str:
    b64 = base64.b64encode(image.image_bytes).decode("ascii")
    return f"data:{image.content_type};base64,{b64}"
