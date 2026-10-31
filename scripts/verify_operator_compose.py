"""Isolated, opt-in Docker Compose acceptance of the optional localhost operator UI.

Never touches an existing Compose project, production service, or external Docker host.
Only synthetic work and ephemeral owned resources are permitted.
"""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import tempfile
import time
from urllib.parse import urlencode, urlsplit
import uuid

from verify_root_stack import ROOT, identity, receipt_digest, run


def localhost_port(value):
    """Fail closed if Docker publishes more than one port or any non-loopback IP."""
    if not isinstance(value, str) or not re.fullmatch(r"127\.0\.0\.1:[0-9]{1,5}", value):
        raise RuntimeError("expected exactly one IPv4 loopback port")
    port = int(value.rsplit(":", 1)[1])
    if port < 1 or port > 65535:
        raise RuntimeError("invalid loopback port")
    return port


def private_password_hash(password):
    """Use exactly the PBKDF2 wire format consumed by qa_lab.console."""
    salt = secrets.token_bytes(16)
    value = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 600000)
    return "pbkdf2_sha256$600000$" + salt.hex() + "$" + value.hex()


def parse_csrf_and_key(html):
    """Extract generated fields from the actual served browser form."""
    csrf = re.search(r'name="csrf" value="([A-Za-z0-9_-]{43})"', html)
    key = re.search(r'name="idempotency_key" value="([0-9a-f]{32})"', html)
    if not csrf or not key:
        raise RuntimeError("expected form fields missing")
    return csrf.group(1), key.group(1)


def http_request(port, path, *, method="GET", form=None, json_body=None,
                 cookie=None, credential=None, origin=None, host=None):
    if form is not None and json_body is not None:
        raise ValueError("one request body only")
    headers = {}
    body = None
    if form is not None:
        body = urlencode(form).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if cookie is not None:
        headers["Cookie"] = cookie
    if credential is not None:
        headers["Authorization"] = "Bearer " + credential
    if origin is not None:
        headers["Origin"] = origin
    if host is not None:
        headers["Host"] = host
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=6)
    try:
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        raw = response.read(262145)
        if len(raw) > 262144:
            raise RuntimeError("response exceeded fixed limit")
        return response.status, dict(response.getheaders()), raw
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if output == ROOT or ROOT in output.parents:
        raise SystemExit("Evidence output must be outside the source checkout")
    if os.environ.get("QA_OPERATOR_COMPOSE_OPT_IN") != "isolated-ci":
        raise SystemExit("Set QA_OPERATOR_COMPOSE_OPT_IN=isolated-ci on an isolated Linux Docker host")
    if os.environ.get("DOCKER_HOST") or os.environ.get("DOCKER_CONTEXT"):
        raise SystemExit("Remote or explicitly selected Docker contexts are not accepted")
    before = identity()
    if not before["clean"]:
        raise SystemExit("A clean committed checkout is required")
    endpoint = run(["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"])
    if not endpoint.startswith("unix://"):
        raise SystemExit("Local Unix-socket Docker endpoint required")

    project = "qa-operator-" + uuid.uuid4().hex[:16]
    private = Path(tempfile.mkdtemp(prefix="qa-operator-"))
    workspace = private / "workspace"
    api_token = secrets.token_urlsafe(32)
    password = secrets.token_urlsafe(32)
    password_hash = private_password_hash(password)
    env = dict(os.environ, QA_API_TOKEN=api_token,
               QA_UI_PASSWORD_HASH=password_hash, QA_API_PORT="0",
               QA_UI_PORT="0", QA_WORKSPACE_PATH=str(workspace),
               QA_UI_COOKIE_SECURE="0")
    command = ["docker", "compose", "-p", project, "-f",
               str(ROOT / "compose.yaml"), "--profile", "operator-ui"]
    checks = {}
    proof = {
        "schema_version": "1",
        "status": "FAIL",
        "scope": "isolated-local-operator-compose-only",
        "source_before": before,
        "checks": checks,
        "authority": "execution-evidence-only",
        "production_accepted": False,
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
    }
    started = False
    stage = "workspace"

    def compose(*parts, timeout=240):
        return run(command + list(parts), env=env, timeout=timeout)

    def require(name, condition):
        checks[name] = condition is True
        if not checks[name]:
            raise RuntimeError(name)

    def resources():
        label = "label=com.docker.compose.project=" + project
        return {
            "containers": run(["docker", "ps", "-aq", "--filter", label]),
            "volumes": run(["docker", "volume", "ls", "-q", "--filter", label]),
            "networks": run(["docker", "network", "ls", "-q", "--filter", label]),
        }

    def request(port, path, **kwargs):
        result = http_request(port, path, **kwargs)
        # Do not emit any browser body or Set-Cookie into public evidence.
        for secret in (api_token, password, password_hash):
            require("no_secrets_in_browser_body", secret.encode() not in result[2])
        return result

    def terminal(rid, api_port):
        for _ in range(60):
            status, _, body = http_request(api_port, "/v1/runs/" + rid, credential=api_token)
            if status == 200:
                data = json.loads(body)
                if data["status"] not in ("PENDING", "RUNNING"):
                    return data
            time.sleep(1)
        raise RuntimeError("worker failed to reach terminal state")

    try:
        old_umask = os.umask(0o022)
        try:
            run(["git", "clone", "--quiet", "--no-hardlinks", str(ROOT), str(workspace)])
            run(["git", "checkout", "--quiet", "--detach", before["commit"]], cwd=workspace)
        finally:
            os.umask(old_umask)
        require("no_preexisting_owned_resources", not any(resources().values()))
        require("workspace_clean", not run(["git", "status", "--porcelain",
                                             "--untracked-files=all"], cwd=workspace))
        proof["docker_version"] = run(["docker", "version", "--format", "{{.Server.Version}}"])
        proof["compose_version"] = compose("version", "--short")
        stage = "compose_config"
        compose("config", "--quiet")
        checks["profile_config_valid"] = True
        stage = "build_start"
        started = True
        compose("up", "-d", "--build", "--wait", "--wait-timeout", "120", timeout=600)
        checks["fresh_operator_profile_started"] = True
        ui_port = localhost_port(compose("port", "operator-ui", "8780"))
        api_port = localhost_port(compose("port", "api", "8765"))
        checks["ui_only_loopback"] = True
        checks["api_only_loopback"] = True
        proof["published_port_scopes"] = {"operator_ui": "loopback", "api": "loopback"}

        stage = "container_boundaries"
        # This runs in the UI container, not on the host; never prints secrets.
        script = """import errno,json,os
flags={'nonroot':os.getuid()==10001,
       'no_data_mount':not os.path.exists('/data/lab.sqlite3'),
       'no_workspace_checkout':not os.path.exists('/workspace/.git')}
try:
    fd=os.open('/app/forbidden-qa-write',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    os.close(fd)
    flags['readonly_root']=False
except OSError as exc:
    flags['readonly_root']=exc.errno==errno.EROFS
print(json.dumps(flags))
"""
        flags = json.loads(compose("exec", "-T", "operator-ui", "python", "-c", script))
        require("nonroot_readonly_ui_no_source_or_db_mount", flags == {
            "nonroot": True, "readonly_root": True,
            "no_data_mount": True, "no_workspace_checkout": True,
        })

        stage = "unauthenticated"
        status, _, _ = request(ui_port, "/healthz")
        require("ui_http_ready", status == 200)
        status, _, _ = request(api_port, "/healthz")
        require("api_http_ready", status == 200)
        status, headers, _ = request(ui_port, "/")
        require("unauthenticated_browser_redirects_to_login",
                status == 303 and headers.get("Location") == "/login")
        status, headers, page = request(ui_port, "/login")
        require("browser_login_html_and_security_headers",
                status == 200 and b"password" in page and
                headers.get("X-Frame-Options") == "DENY" and
                "frame-ancestors 'none'" in headers.get("Content-Security-Policy", "") and
                headers.get("Cache-Control") == "no-store")
        status, headers, css = request(ui_port, "/static/console.css")
        require("packaged_css_served", status == 200 and len(css) > 100 and
                headers.get("Content-Type", "").startswith("text/css"))
        status, _, _ = request(ui_port, "/", host="evil.example")
        require("dns_rebinding_host_rejected", status == 400)
        status, _, _ = request(api_port, "/v1/runs")
        require("api_catalog_requires_bearer", status == 401)
        status, _, _ = request(ui_port, "/runs/" + str(uuid.uuid4()))
        require("unauthenticated_run_detail_rejected", status == 303)

        stage = "login"
        status, headers, _ = request(ui_port, "/login", method="POST", form={"password": "wrong"})
        require("wrong_operator_password_rejected", status == 401 and "Set-Cookie" not in headers)
        status, headers, _ = request(ui_port, "/login", method="POST", form={"password": password})
        raw_cookie = headers.get("Set-Cookie", "")
        cookie = raw_cookie.partition(";")[0]
        require("cookie_is_httponly_samesite_and_scoped",
                status == 303 and headers.get("Location") == "/" and
                raw_cookie.startswith("qa_console_session=") and
                "HttpOnly" in raw_cookie and "SameSite=Strict" in raw_cookie and
                "Path=/" in raw_cookie and "Secure" not in raw_cookie and
                api_token not in raw_cookie and password not in raw_cookie)
        status, _, form_html = request(ui_port, "/new", cookie=cookie)
        require("authenticated_submission_form", status == 200 and b'python-smoke' in form_html)
        csrf, idem = parse_csrf_and_key(form_html.decode("utf-8"))
        form = {"csrf": csrf, "idempotency_key": idem, "project": "isolated-operator-ui",
                "source_sha": before["commit"], "recipe": "python-smoke"}

        stage = "authorization"
        status, _, _ = request(ui_port, "/runs", method="POST", form=form)
        require("unauthenticated_submission_rejected", status == 401)
        status, _, _ = request(ui_port, "/runs", method="POST",
                               form={**form, "csrf": "invalid"}, cookie=cookie)
        require("missing_csrf_rejected", status == 403)
        status, _, _ = request(ui_port, "/runs", method="POST", form=form, cookie=cookie,
                               origin="https://untrusted.example")
        require("cross_origin_submission_rejected", status == 403)
        status, _, _ = request(ui_port, "/runs", method="POST",
                               form={**form, "recipe": "shell"}, cookie=cookie)
        require("unapproved_command_recipe_rejected", status == 400)
        status, _, _ = request(ui_port, "/runs", method="POST",
                               form={**form, "source_sha": "0" * 40 + "invalid"}, cookie=cookie)
        require("malformed_sha_rejected", status == 400)

        stage = "submit_and_worker"
        status, headers, _ = request(ui_port, "/runs", method="POST", form=form, cookie=cookie)
        location = headers.get("Location", "")
        require("submitted_through_browser", status == 303 and
                re.fullmatch(r"/runs/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", location) is not None)
        rid = location.rsplit("/", 1)[1]
        status, headers, _ = request(ui_port, "/runs", method="POST", form=form, cookie=cookie)
        require("idempotent_browser_retry_no_duplicate",
                status == 303 and headers.get("Location") == location)
        result = terminal(rid, api_port)
        receipt = result["receipt"]
        require("approved_worker_executed_pinned_sha",
                result["status"] == "PASSED" and receipt["source_sha"] == before["commit"]
                and receipt["recipe"] == "python-smoke" and receipt["exit_code"] == 0
                and receipt["stdout_tail"].strip() == "qa-smoke-ok")
        require("independent_receipt_digest_verified",
                receipt_digest(receipt) == receipt["sha256"])
        proof["receipt_sha256"] = receipt["sha256"]
        proof["synthetic_run_id"] = rid

        stage = "browser_receipt"
        status, _, html = request(ui_port, location, cookie=cookie)
        require("browser_receipt_matches_actual_worker",
                status == 200 and b"qa-smoke-ok" in html and
                "سليمة حسابيًا".encode("utf-8") in html and receipt["sha256"].encode() in html)
        status, _, listed = request(ui_port, "/?project=isolated-operator-ui&status=PASSED",
                                   cookie=cookie)
        require("browser_filtered_listing", status == 200 and rid.encode() in listed)
        status, _, _ = request(ui_port, "/?status=INVALID", cookie=cookie)
        require("invalid_query_rejected", status == 400)

        stage = "logout_and_restart"
        status, headers, _ = request(ui_port, "/logout", method="POST",
                                     form={"csrf": csrf}, cookie=cookie)
        require("logout_revokes_session",
                status == 303 and "Max-Age=0" in headers.get("Set-Cookie", "") and
                request(ui_port, "/", cookie=cookie)[0] == 303)
        status, headers, _ = request(ui_port, "/login", method="POST", form={"password": password})
        cookie = headers.get("Set-Cookie", "").partition(";")[0]
        require("new_session_created", status == 303)
        compose("restart", "operator-ui")
        ui_port = localhost_port(compose("port", "operator-ui", "8780"))
        proof["ui_port_after_restart_scope"] = "loopback"
        for _ in range(30):
            try:
                if request(ui_port, "/healthz")[0] == 200:
                    break
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(1)
        else:
            raise RuntimeError("ui_restart_readiness_timeout")
        require("sessions_expire_after_ui_restart",
                request(ui_port, "/", cookie=cookie)[0] == 303)
        status, headers, _ = request(ui_port, "/login", method="POST", form={"password": password})
        require("operator_can_login_after_restart", status == 303)
        cookie = headers["Set-Cookie"].partition(";")[0]
        status, _, html = request(ui_port, location, cookie=cookie)
        require("persisted_receipt_visible_after_restart",
                status == 200 and receipt["sha256"].encode() in html)

        require("workspace_unchanged",
                not run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=workspace))
        proof["image_ids"] = sorted(set(compose("images", "-q").splitlines()))
        proof["status"] = "PASS"
    except Exception as exc:
        proof["failure"] = {"stage": stage, "type": type(exc).__name__}
    finally:
        try:
            if started:
                compose("down", "-v", "--remove-orphans", timeout=120)
            checks["owned_docker_resources_removed"] = not any(resources().values())
        except Exception:
            checks["owned_docker_resources_removed"] = False
        try:
            shutil.rmtree(private)
            checks["private_checkout_removed"] = not private.exists()
        except Exception:
            checks["private_checkout_removed"] = False
        try:
            proof["source_after"] = identity()
            checks["source_unchanged"] = proof["source_after"] == before
        except Exception:
            checks["source_unchanged"] = False
        if not all(checks.values()):
            proof["status"] = "FAIL"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": proof["status"], "checks_passed": sum(checks.values()),
                      "checks_total": len(checks), "failure": proof.get("failure")}))
    return 0 if proof["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
