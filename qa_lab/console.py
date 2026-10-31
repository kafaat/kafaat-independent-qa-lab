"""Single-operator, loopback-only browser console. No administrative API token in HTML.

This is deliberately not multi-tenant identity, project RBAC, or production SSO.
Only approved recipes can be submitted; execution happens in the existing worker.
"""
from __future__ import annotations

import getpass
import hashlib
import hmac
import html
import json
import os
from pathlib import Path
import re
import secrets
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, ProxyHandler, HTTPRedirectHandler, build_opener

RECIPES = ("python-smoke",)
STATUSES = ("PENDING", "RUNNING", "PASSED", "FAILED", "BLOCKED", "TIMED_OUT")
COOKIE = "qa_console_session"
HASH_RE = re.compile(r"^pbkdf2_sha256\$600000\$[0-9a-f]{32}\$[0-9a-f]{64}$")
SHA_RE = re.compile(r"^[a-f0-9]{40}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
}


def password_hash(password, salt=None):
    if not isinstance(password, str) or len(password) < 12:
        raise ValueError("operator password must contain at least 12 characters")
    salt = secrets.token_bytes(16) if salt is None else salt
    if len(salt) != 16:
        raise ValueError("invalid salt")
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 600000)
    return "pbkdf2_sha256$600000$" + salt.hex() + "$" + digest.hex()


def check_password(password, encoded):
    if not isinstance(password, str) or len(password) > 256 or not HASH_RE.fullmatch(encoded or ""):
        return False
    _, _, salt, expected = encoded.split("$")
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 600000)
    return hmac.compare_digest(actual, bytes.fromhex(expected))


class SessionStore:
    """In-process, fixed-expiry sessions. Logout revokes the token server-side."""
    def __init__(self, ttl=1800):
        self.ttl = ttl
        self.lock = threading.Lock()
        self.sessions = {}
        self.failed = {}

    def create(self):
        token = secrets.token_urlsafe(32)
        record = {"expires": time.time() + self.ttl, "csrf": secrets.token_urlsafe(32)}
        with self.lock:
            now = time.time()
            self.sessions = {k: v for k, v in self.sessions.items() if v["expires"] > now}
            if len(self.sessions) >= 512:
                self.sessions.pop(min(self.sessions, key=lambda k: self.sessions[k]["expires"]))
            self.sessions[hashlib.sha256(token.encode()).digest()] = record
        return token, record

    def get(self, token):
        if not TOKEN_RE.fullmatch(token or ""):
            return None
        with self.lock:
            key = hashlib.sha256(token.encode()).digest()
            record = self.sessions.get(key)
            if record and record["expires"] > time.time():
                return record
            self.sessions.pop(key, None)
            return None

    def revoke(self, token):
        if TOKEN_RE.fullmatch(token or ""):
            with self.lock:
                self.sessions.pop(hashlib.sha256(token.encode()).digest(), None)

    def is_limited(self, peer):
        with self.lock:
            now = time.time()
            recent = [t for t in self.failed.get(peer, ()) if now - t < 300]
            self.failed[peer] = recent
            return len(recent) >= 5

    def failed_login(self, peer):
        with self.lock:
            now = time.time()
            self.failed = {k: [t for t in v if now - t < 300] for k, v in self.failed.items()
                           if any(now - t < 300 for t in v)}
            if len(self.failed) >= 1024 and peer not in self.failed:
                self.failed.pop(next(iter(self.failed)))
            self.failed.setdefault(peer, []).append(now)

    def clear_failures(self, peer):
        with self.lock:
            self.failed.pop(peer, None)


class APIUnavailable(Exception):
    pass


class RejectRedirect(HTTPRedirectHandler):
    """A backend redirect must never forward the privileged bearer token."""
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class LabAPI:
    """Server-side only; never serialize the bearer token in responses."""
    def __init__(self, base_url, token):
        parts = urlsplit(base_url)
        if (parts.scheme != "http" or not parts.hostname or not parts.port
                or parts.username or parts.password or parts.path not in ("", "/")
                or parts.query or parts.fragment or not token):
            raise ValueError("invalid internal API configuration")
        self.base = base_url.rstrip("/")
        self.token = token
        self.opener = build_opener(ProxyHandler({}), RejectRedirect())  # No proxy or redirects may carry the bearer.

    def request(self, method, path, payload=None):
        if not path.startswith("/") or "//" in path or "\r" in path or "\n" in path:
            raise ValueError("invalid API path")
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = Request(self.base + path, data=body, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=4) as response:
                raw = response.read(262145)
                if len(raw) > 262144:
                    raise APIUnavailable()
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise APIUnavailable()
                return result
        except HTTPError as exc:
            if exc.code == 404:
                raise KeyError("run not found") from None
            if exc.code in (400, 413):
                raise ValueError("request rejected") from None
            raise APIUnavailable() from None
        except (OSError, URLError, UnicodeError, json.JSONDecodeError, TimeoutError):
            raise APIUnavailable() from None


def esc(value):
    return html.escape(str(value), quote=True)


def page(title, body, session=None, refresh=False):
    nav = ('<nav aria-label="القائمة الرئيسية"><a href="/">التشغيلات</a>'
           '<a href="/new">إرسال اختبار</a>'
           '<form method="post" action="/logout"><input type="hidden" name="csrf" value="' +
           esc(session["csrf"]) + '"><button>تسجيل الخروج</button></form></nav>') if session else ""
    reload = '<meta http-equiv="refresh" content="8">' if refresh else ""
    return ('<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">' + reload +
            '<title>' + esc(title) + ' — المختبر المستقل</title>'
            '<link rel="stylesheet" href="/static/console.css"></head><body><header>'
            '<div class="brand">المختبر المستقل <small>واجهة المشغّل المحلي</small></div>' +
            nav + '</header><main><h1>' + esc(title) + '</h1>' + body +
            '<p class="boundary">أدلة التنفيذ فقط؛ لا يملك المختبر صلاحية إغلاق الحالات أو الموافقة على النشر.</p>'
            '</main></body></html>')


def query_options(query):
    if len(query) > 1024:
        raise ValueError("invalid query")
    values = parse_qs(query, keep_blank_values=True, max_num_fields=5)
    if set(values) - {"status", "project", "q", "offset"} or any(len(v) != 1 for v in values.values()):
        raise ValueError("invalid query")
    params = {key: v[0] for key, v in values.items()}
    if params.get("status", "") not in ("", *STATUSES):
        raise ValueError("invalid status")
    for key in ("project", "q"):
        value = params.get(key, "")
        if len(value) > 128 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("invalid query")
    offset = params.get("offset", "0")
    if not offset.isascii() or not offset.isdecimal() or int(offset) > 10000:
        raise ValueError("invalid offset")
    params["offset"] = str(int(offset))
    return params


class Console(BaseHTTPRequestHandler):
    api = None
    sessions = None
    password_hash_value = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(7)

    def log_message(self, format, *args):
        # Never log passwords, session cookies, requests, or receipt contents.
        pass

    def respond(self, status, body, content_type="text/html; charset=utf-8", headers=None):
        raw = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        for name, value in HEADERS.items():
            self.send_header(name, value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(raw)

    def redirect(self, location, cookie=None):
        headers = {"Location": location}
        if cookie is not None:
            headers["Set-Cookie"] = cookie
        self.respond(303, b"", headers=headers)

    def cookie_token(self):
        headers = self.headers.get_all("Cookie", [])
        if len(headers) != 1:
            return ""
        cookie = SimpleCookie()
        try:
            cookie.load(headers[0])
        except Exception:
            return ""
        return cookie[COOKIE].value if COOKIE in cookie else ""

    def session(self):
        return self.sessions.get(self.cookie_token())

    def cookie_header(self, token, expiry=1800):
        value = COOKIE + "=" + token + "; HttpOnly; SameSite=Strict; Path=/; Max-Age=" + str(expiry)
        if os.environ.get("QA_UI_COOKIE_SECURE") == "1":
            value += "; Secure"
        return value

    def validate_host(self):
        hosts = self.headers.get_all("Host", [])
        if len(hosts) != 1:
            return False
        try:
            host = urlsplit("http://" + hosts[0]).hostname
        except ValueError:
            return False
        return host in ("127.0.0.1", "localhost", "::1")

    def form(self):
        if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Type") != "application/x-www-form-urlencoded":
            raise ValueError("invalid encoding")
        sizes = self.headers.get_all("Content-Length", [])
        if len(sizes) != 1 or not sizes[0].isascii() or not sizes[0].isdecimal():
            raise ValueError("missing content length")
        size = int(sizes[0])
        if not 1 <= size <= 4096:
            raise ValueError("invalid form size")
        params = parse_qs(self.rfile.read(size).decode("utf-8"), strict_parsing=True,
                          keep_blank_values=True, max_num_fields=8)
        if any(len(v) != 1 for v in params.values()):
            raise ValueError("duplicate form field")
        return {key: values[0] for key, values in params.items()}

    def allowed_origin(self):
        origin = self.headers.get("Origin")
        site = self.headers.get("Sec-Fetch-Site")
        if site == "cross-site":
            return False
        if not origin:
            return True  # Non-browser clients still need session + synchronizer CSRF.
        scheme = "https" if os.environ.get("QA_UI_COOKIE_SECURE") == "1" else "http"
        return origin.isascii() and hmac.compare_digest(origin, scheme + "://" + self.headers["Host"])

    def login_page(self, message=""):
        note = '<p role="alert" class="error">' + esc(message) + '</p>' if message else ""
        return page("تسجيل دخول المشغّل", note +
                    '<form method="post" action="/login" class="panel">'
                    '<label for="password">كلمة مرور المشغّل</label>'
                    '<input id="password" name="password" type="password" required autocomplete="current-password">'
                    '<button type="submit">دخول</button></form>')

    def list_page(self, session, params):
        opts = {key: val for key, val in params.items() if val and not (key == "offset" and val == "0")}
        opts["limit"] = "25"
        data = self.api.request("GET", "/v1/runs?" + urlencode(opts))
        items = data.get("runs")
        if not isinstance(items, list):
            raise APIUnavailable()
        body = ['<form method="get" action="/" class="filters">'
                '<label>بحث باسم المشروع أو رقم التشغيل<input name="q" value="' +
                esc(params.get("q", "")) + '"></label><label>المشروع<input name="project" value="' +
                esc(params.get("project", "")) + '"></label><label>الحالة<select name="status">'
                '<option value="">الكل</option>']
        for status in STATUSES:
            selected = " selected" if params.get("status") == status else ""
            body.append('<option value="' + status + '"' + selected + '>' + status + '</option>')
        body.append('</select></label><button>بحث وتصفية</button></form>')
        body.append('<div class="table-scroll"><table><thead><tr><th>التشغيل</th><th>المشروع</th><th>الوصفة</th><th>الحالة</th></tr></thead><tbody>')
        for entry in items:
            rid = str(entry.get("run_id", ""))
            try:
                if str(uuid.UUID(rid)) != rid:
                    raise ValueError()
            except ValueError:
                raise APIUnavailable() from None
            body.append('<tr><td><a href="/runs/' + rid + '">' + esc(rid) +
                        '</a></td><td>' + esc(entry.get("project", "")) + '</td><td>' +
                        esc(entry.get("recipe", "")) + '</td><td>' + esc(entry.get("status", "")) + '</td></tr>')
        body.append('</tbody></table></div>')
        if not items:
            body.append('<p>لا توجد تشغيلات مطابقة.</p>')
        if data.get("next_offset") is not None:
            next_query = urlencode({**opts, "offset": str(data["next_offset"])})
            body.append('<p><a href="/?' + esc(next_query) + '">الصفحة التالية</a></p>')
        return page("التشغيلات", "".join(body), session)

    def new_page(self, session):
        recipe = "".join('<option value="' + esc(r) + '">' + esc(r) + '</option>' for r in RECIPES)
        return page("إرسال اختبار معتمد",
                    '<form method="post" action="/runs" class="panel">'
                    '<input type="hidden" name="csrf" value="' + esc(session["csrf"]) + '">'
                    '<input type="hidden" name="idempotency_key" value="' + secrets.token_hex(16) + '">'
                    '<label>اسم المشروع<input name="project" required maxlength="128"></label>'
                    '<label>Git SHA (40 حرفًا)<input name="source_sha" dir="ltr" required minlength="40" maxlength="40" pattern="[a-f0-9]{40}"></label>'
                    '<label>الوصفة المعتمدة<select name="recipe">' + recipe + '</select></label>'
                    '<button type="submit">إرسال إلى قائمة الانتظار</button></form>'
                    '<p>الطلب لا ينفّذ الشيفرة في المتصفح ولا يعتمد النتائج تلقائيًا.</p>', session)

    def run_page(self, session, rid):
        if str(uuid.UUID(rid)) != rid:
            raise ValueError("invalid run")
        run = self.api.request("GET", "/v1/runs/" + rid)
        evidence = self.api.request("GET", "/v1/evidence/" + rid)
        verified = evidence.get("verified") is True
        receipt = run.get("receipt")
        flag = "سليمة حسابيًا" if verified else "غير متحققة"
        info = ('<dl><dt>المشروع</dt><dd>' + esc(run.get("project", "")) + '</dd>'
                '<dt>الحالة</dt><dd>' + esc(run.get("status", "")) + '</dd>'
                '<dt>الوصفة</dt><dd>' + esc(run.get("recipe", "")) + '</dd>'
                '<dt>مرجع الشيفرة</dt><dd dir="ltr">' + esc(run.get("source_sha", "")) + '</dd>'
                '<dt>سلامة الإيصال</dt><dd>' + esc(flag) + '</dd></dl>')
        if receipt is None:
            info += "<p>لم يصدر إيصال بعد.</p>"
        else:
            info += "<h2>الإيصال</h2><pre>" + esc(json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False)) + "</pre>"
        info += '<p>مطابقة البصمة ليست إثباتًا مستقلًا للمضيف أو قبولًا للإصدار.</p>'
        return page("تفاصيل التشغيل " + rid, info, session, refresh=run.get("status") in ("PENDING", "RUNNING"))

    def do_GET(self):
        if not self.validate_host():
            return self.respond(400, page("طلب غير صالح", "<p>عنوان المضيف غير مسموح.</p>"))
        path = urlsplit(self.path)
        if path.path == "/healthz" and not path.query:
            return self.respond(200, '{"status":"ok"}', "application/json; charset=utf-8")
        if path.path == "/static/console.css" and not path.query:
            css = (Path(__file__).parent / "static" / "console.css").read_bytes()
            return self.respond(200, css, "text/css; charset=utf-8")
        session = self.session()
        if path.path == "/login" and not path.query:
            return self.redirect("/") if session else self.respond(200, self.login_page())
        if not session:
            return self.redirect("/login")
        try:
            if path.path == "/":
                return self.respond(200, self.list_page(session, query_options(path.query)))
            if path.path == "/new" and not path.query:
                return self.respond(200, self.new_page(session))
            if path.path.startswith("/runs/") and not path.query:
                return self.respond(200, self.run_page(session, path.path.removeprefix("/runs/")))
        except (ValueError, TypeError):
            return self.respond(400, page("طلب غير صالح", "<p>المعاملات غير صحيحة.</p>", session))
        except KeyError:
            return self.respond(404, page("غير موجود", "<p>لم يُعثر على التشغيل.</p>", session))
        except (APIUnavailable, OSError):
            return self.respond(503, page("الخدمة غير متاحة", "<p>تعذّر الاتصال بخدمة المختبر.</p>", session))
        return self.respond(404, page("غير موجود", "<p>المسار غير معروف.</p>", session))

    def do_POST(self):
        if not self.validate_host() or not self.allowed_origin():
            return self.respond(403, page("مرفوض", "<p>مصدر الطلب غير مسموح.</p>"))
        path = urlsplit(self.path)
        if path.query:
            return self.respond(400, page("طلب غير صالح", "<p>معاملات غير مدعومة.</p>"))
        try:
            params = self.form()
        except (UnicodeError, ValueError):
            return self.respond(400, page("طلب غير صالح", "<p>نموذج غير صالح.</p>"))
        if path.path == "/login":
            peer = self.client_address[0]
            if set(params) != {"password"}:
                return self.respond(400, self.login_page("نموذج غير صالح."))
            if self.sessions.is_limited(peer):
                return self.respond(429, self.login_page("محاولات كثيرة. حاول لاحقًا."))
            if not check_password(params["password"], self.password_hash_value):
                self.sessions.failed_login(peer)
                return self.respond(401, self.login_page("تعذر تسجيل الدخول."))
            self.sessions.clear_failures(peer)
            token, _ = self.sessions.create()
            return self.redirect("/", self.cookie_header(token))
        session = self.session()
        if not session:
            return self.respond(401, page("غير مصرح", "<p>سجّل الدخول أولًا.</p>"))
        if not hmac.compare_digest(params.get("csrf", ""), session["csrf"]):
            return self.respond(403, page("مرفوض", "<p>رمز الحماية غير صالح.</p>", session))
        if path.path == "/logout":
            if set(params) != {"csrf"}:
                return self.respond(400, page("طلب غير صالح", "<p>نموذج غير صالح.</p>", session))
            self.sessions.revoke(self.cookie_token())
            return self.redirect("/login", self.cookie_header("", 0))
        if path.path != "/runs":
            return self.respond(404, page("غير موجود", "<p>المسار غير معروف.</p>", session))
        if set(params) != {"csrf", "idempotency_key", "project", "source_sha", "recipe"}:
            return self.respond(400, page("طلب غير صالح", "<p>حقول غير صالحة.</p>", session))
        req = {key: params[key] for key in ("idempotency_key", "project", "source_sha", "recipe")}
        if (not 1 <= len(req["project"]) <= 128 or not req["project"].strip()
                or len(req["idempotency_key"]) != 32
                or not re.fullmatch(r"[a-f0-9]{32}", req["idempotency_key"])
                or not SHA_RE.fullmatch(req["source_sha"])
                or req["recipe"] not in RECIPES):
            return self.respond(400, page("طلب غير صالح", "<p>الطلب لا يطابق الوصفات المعتمدة.</p>", session))
        try:
            result = self.api.request("POST", "/v1/runs", req)
            rid = str(result["run_id"])
            if str(uuid.UUID(rid)) != rid:
                raise APIUnavailable()
        except (ValueError, TypeError, KeyError):
            return self.respond(400, page("طلب مرفوض", "<p>رفضت الخدمة الطلب؛ تحقق من البيانات ومفتاح التكرار.</p>", session))
        except APIUnavailable:
            return self.respond(503, page("غير متاح", "<p>تعذّر إرسال الطلب؛ تحقق من حالته قبل إعادة المحاولة.</p>", session))
        return self.redirect("/runs/" + rid)


def serve(host="127.0.0.1", port=8780):
    encoded = os.environ.get("QA_UI_PASSWORD_HASH", "")
    if not HASH_RE.fullmatch(encoded):
        raise RuntimeError("QA_UI_PASSWORD_HASH must be a PBKDF2 hash; see docs/OPERATOR_CONSOLE_AR.md")
    token = os.environ.get("QA_API_TOKEN", "")
    if len(token) < 24:
        raise RuntimeError("QA_API_TOKEN must be at least 24 characters")
    api = LabAPI(os.environ.get("QA_UI_API_BASE", "http://127.0.0.1:8765"), token)
    handler = type("OperatorConsole", (Console,), {
        "api": api, "sessions": SessionStore(), "password_hash_value": encoded,
    })
    with ThreadingHTTPServer((host, port), handler) as httpd:
        httpd.serve_forever()


if __name__ == "__main__":
    if sys.argv[1:] == ["hash-password"]:
        first = getpass.getpass("Local operator password (minimum 12 characters): ")
        second = getpass.getpass("Confirm password: ")
        if first != second:
            raise SystemExit("password mismatch")
        print(password_hash(first))
    elif not sys.argv[1:] or sys.argv[1:] == ["serve"]:
        serve(os.environ.get("QA_BIND_HOST", "127.0.0.1"), int(os.environ.get("PORT", "8780")))
    else:
        raise SystemExit("usage: python -m qa_lab.console [hash-password|serve]")
