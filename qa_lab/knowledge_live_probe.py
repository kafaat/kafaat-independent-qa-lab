"""One-shot private Railway acceptance of the deployed public-doc knowledge service.

Does not execute submitted code, ingest documents, expose HTTP ports, or issue
production approval. No request token or document body is logged. Source hashes
are content references; this probe is not independent host attestation.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

MAX_BODY = 65536
EXPECTED_HOST = "qa-knowledge.railway.internal"
EXPECTED_PORT = 8781
SOURCE_SHA = re.compile(r"[0-9a-f]{40}\Z")


class ProbeFailure(ValueError):
    """A bounded failure code, never a remote response or credential."""


def require(condition, code):
    if not condition:
        raise ProbeFailure(code)


def no_duplicates(pairs):
    output = {}
    for key, value in pairs:
        require(key not in output, "duplicate-json-key")
        output[key] = value
    return output


def decode(raw):
    try:
        return json.loads(raw, object_pairs_hook=no_duplicates,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError, TypeError) as exc:
        raise ProbeFailure("invalid-json-response") from None


def validate_url(value, *, host=EXPECTED_HOST, port=EXPECTED_PORT):
    require(isinstance(value, str), "invalid-target")
    try:
        url = urllib.parse.urlsplit(value)
        valid = (url.scheme == "http" and url.hostname == host and url.port == port
                 and not url.username and not url.password and url.path in ("", "/")
                 and not url.query and not url.fragment and url.netloc == f"{host}:{port}")
    except ValueError:
        valid = False
    require(valid, "unsafe-target")
    return f"http://{host}:{port}"


def validate_token(token):
    require(isinstance(token, str) and 24 <= len(token) <= 512
            and "${{" not in token and not any(c.isspace() for c in token),
            "invalid-token")
    return token


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class PrivateClient:
    def __init__(self, origin, token, *, allowed_host=EXPECTED_HOST, allowed_port=EXPECTED_PORT):
        self.origin = validate_url(origin, host=allowed_host, port=allowed_port)
        self.token = validate_token(token)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def exchange(self, path, *, auth="valid", payload=None, content_type="application/json"):
        require(path in ("/healthz", "/v1/answer", "/v1/search", "/v1/ingest"), "invalid-route")
        headers = {"Accept": "application/json"}
        if auth != "none":
            headers["Authorization"] = "Bearer " + (self.token if auth == "valid" else "incorrect-qa-probe-token")
        data = None
        if payload is not None:
            data = payload if isinstance(payload, bytes) else json.dumps(payload, allow_nan=False).encode()
            headers["Content-Type"] = content_type
        request = urllib.request.Request(self.origin + path, headers=headers, data=data,
                                         method="POST" if data is not None else "GET")
        try:
            response = self.opener.open(request, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        except (OSError, TimeoutError):
            raise ProbeFailure("service-unreachable") from None
        with response:
            status = response.status
            require(status not in (301, 302, 303, 307, 308), "redirect-not-allowed")
            require(response.headers.get("Cache-Control") == "no-store", "missing-no-store")
            require(response.headers.get("X-Content-Type-Options") == "nosniff", "missing-nosniff")
            require(response.headers.get_content_type() == "application/json", "invalid-content-type")
            body = response.read(MAX_BODY + 1)
            require(len(body) <= MAX_BODY, "unbounded-response")
            return status, decode(body)


def pinned_corpus(root):
    root = Path(root).resolve()
    spec_path = root / "docs" / "QA_KNOWLEDGE_SEED.json"
    require(spec_path.is_file() and not spec_path.is_symlink(), "missing-reviewed-manifest")
    raw_manifest = spec_path.read_bytes()
    require(len(raw_manifest) <= 32768, "oversized-reviewed-manifest")
    spec = decode(raw_manifest)
    require(isinstance(spec, dict) and set(spec) == {"project", "documents"}, "bad-reviewed-manifest")
    require(spec["project"] == "qa-public-docs" and isinstance(spec["documents"], list)
            and len(spec["documents"]) == 3, "unexpected-corpus-scope")
    corpus = {}
    for entry in spec["documents"]:
        require(isinstance(entry, dict) and set(entry) == {"document_id", "path", "git_blob_sha"},
                "bad-corpus-entry")
        doc_id, rel, sha = entry["document_id"], entry["path"], entry["git_blob_sha"]
        require(isinstance(doc_id, str) and doc_id not in corpus, "duplicate-document")
        require(isinstance(rel, str) and re.fullmatch(r"docs/[A-Za-z0-9_.-]+\.md", rel),
                "unsafe-corpus-path")
        require(isinstance(sha, str) and SOURCE_SHA.fullmatch(sha), "invalid-corpus-source")
        path = root / rel
        require(path.is_file() and not path.is_symlink(), "missing-corpus-file")
        raw = path.read_bytes()
        require(len(raw) <= 1048576, "oversized-corpus-file")
        git_blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        require(git_blob == sha, "corpus-source-mismatch")
        try:
            text = raw.decode("utf-8")
        except UnicodeError:
            raise ProbeFailure("invalid-corpus-encoding") from None
        corpus[doc_id] = {"source_sha": sha, "text": text,
                          "document_sha256": hashlib.sha256(raw).hexdigest()}
    return corpus, hashlib.sha256(raw_manifest).hexdigest()


def check_answer(data, corpus, *, must_include=None):
    require(isinstance(data, dict) and data.get("status") == "EVIDENCE_FOUND",
            "no-answer-evidence")
    require(data.get("answer_mode") == "extractive-no-llm" and data.get("authority") == "reference-only"
            and data.get("production_accepted") is False, "invalid-answer-authority")
    citations = data.get("citations")
    require(isinstance(citations, list) and 1 <= len(citations) <= 5, "invalid-citation-count")
    ids = set()
    for hit in citations:
        require(isinstance(hit, dict) and hit.get("project") == "qa-public-docs", "invalid-project")
        doc_id = hit.get("document_id")
        require(doc_id in corpus, "unexpected-document")
        original = corpus[doc_id]
        require(hit.get("source_sha") == original["source_sha"]
                and hit.get("document_sha256") == original["document_sha256"],
                "source-version-mismatch")
        lo, hi = hit.get("offset_start"), hit.get("offset_end")
        require(type(lo) is int and type(hi) is int and 0 <= lo < hi <= len(original["text"]),
                "invalid-citation-offset")
        require(hit.get("text") == original["text"][lo:hi], "citation-text-mismatch")
        require(hit["text"] in data.get("answer", ""), "answer-citation-mismatch")
        ids.add(doc_id)
    if must_include:
        require(must_include in ids, "expected-document-missing")
    return {"citation_count": len(citations), "documents": sorted(ids)}


def run_probe(client, corpus, *, source_sha=None, manifest_sha256=None):
    started = dt.datetime.now(dt.timezone.utc)
    passed = []

    def check(name, fn):
        result = fn()
        passed.append(name)
        return result

    check("reject_missing_token", lambda: require(client.exchange("/healthz", auth="none")[0] == 401,
                                                 "unauthenticated-accepted"))
    check("reject_wrong_token", lambda: require(client.exchange("/healthz", auth="wrong")[0] == 401,
                                               "wrong-token-accepted"))
    def valid_health():
        status, data = client.exchange("/healthz")
        require(status == 200 and data == {"status": "ok", "mode": "extractive-no-llm"},
                "invalid-live-health")
    check("authenticated_health", valid_health)

    def exact_answer():
        status, data = client.exchange("/v1/answer", payload={"query": "PostgreSQL"})
        require(status == 200, "answer-http-failed")
        return check_answer(data, corpus, must_include="postgres-acceptance")
    proof = check("version_scoped_extract_with_citations", exact_answer)

    def search_only():
        status, data = client.exchange("/v1/search", payload={"query": "PostgreSQL"})
        require(status == 200 and data.get("mode") == "bm25", "search-invalid")
        hits = data.get("hits")
        require(isinstance(hits, list) and hits, "search-empty")
        require(all(isinstance(h, dict) and h.get("document_id") in corpus and
                    h.get("source_sha") == corpus[h["document_id"]]["source_sha"] for h in hits),
                "unscoped-search")
    check("bounded_bm25_search", search_only)

    def absence():
        status, data = client.exchange("/v1/answer", payload={"query": "nonexistentunicornxyzzynonsense"})
        require(status == 200 and data.get("status") == "INSUFFICIENT_EVIDENCE"
                and not data.get("citations") and not data.get("answer"), "abstention-failed")
    check("abstain_without_evidence", absence)
    check("reject_client_source_override", lambda: require(client.exchange(
        "/v1/answer", payload={"query": "PostgreSQL", "project": "another"})[0] == 400,
        "scope-override-accepted"))
    check("reject_duplicate_query_keys", lambda: require(client.exchange(
        "/v1/answer", payload=b'{"query":"PostgreSQL","query":"override"}')[0] == 400,
        "duplicate-query-accepted"))
    check("reject_unsupported_content_type", lambda: require(client.exchange(
        "/v1/answer", payload=b'{"query":"PostgreSQL"}', content_type="text/plain")[0] == 415,
        "unsupported-content-type-accepted"))
    check("reject_http_ingestion", lambda: require(client.exchange(
        "/v1/ingest", payload={"query": "PostgreSQL"})[0] == 404,
        "http-ingestion-enabled"))
    check("reject_oversized_request", lambda: require(client.exchange(
        "/v1/answer", payload=b"{" + b" " * 8193 + b"}")[0] == 413,
        "oversized-request-accepted"))
    finished = dt.datetime.now(dt.timezone.utc)
    output = {"status": "PASSED", "schema_version": "1.0", "scope": "private-http-knowledge",
              "authority": "execution-evidence-only", "production_accepted": False,
              "model": "bm25-extractive-no-llm", "checks": passed,
              "checks_passed": len(passed), "query_citation_proof": proof,
              "source_attestation": "git-blob-content-reference-only",
              "service_corpus_manifest_sha256": manifest_sha256,
              "probe_source_sha": source_sha,
              "railway_deployment_id": os.environ.get("RAILWAY_DEPLOYMENT_ID"),
              "railway_environment_id": os.environ.get("RAILWAY_ENVIRONMENT_ID"),
              "started_at": started.isoformat(), "finished_at": finished.isoformat()}
    canonical = json.dumps(output, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    output["receipt_sha256"] = hashlib.sha256(canonical).hexdigest()
    return output


def main():
    try:
        token = validate_token(os.environ.get("QA_KNOWLEDGE_TOKEN"))
        origin = validate_url(os.environ.get("QA_KNOWLEDGE_URL"))
        requested_sha = os.environ.get("QA_EXPECTED_PROBE_SOURCE_SHA")
        current_sha = os.environ.get("RAILWAY_GIT_COMMIT_SHA")
        if not os.environ.get("CI"):
            require(isinstance(requested_sha, str) and SOURCE_SHA.fullmatch(requested_sha)
                    and current_sha == requested_sha, "probe-deployment-source-mismatch")
        corpus, manifest_digest = pinned_corpus(os.environ.get("QA_CORPUS_ROOT", "/app"))
        result = run_probe(PrivateClient(origin, token), corpus,
                           source_sha=current_sha, manifest_sha256=manifest_digest)
        print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False), flush=True)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        # Strictly limited failure codes: never log HTTP payloads, credentials or document excerpts.
        code = str(exc) if isinstance(exc, ProbeFailure) else "probe-internal-failure"
        print(json.dumps({"status": "FAILED", "reason_code": code,
                          "authority": "execution-evidence-only", "production_accepted": False}),
              flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
