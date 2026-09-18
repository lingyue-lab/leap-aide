"""
Shared HTTP helpers for the API-level integration test files.

Each helper preserves the return-tuple shape used by the original
per-file copies, so test bodies do not have to change when they
adopt these helpers. Variants are kept distinct instead of being
collapsed into one, because the callers rely on the tuple arity:

  get_body(path)               -> dict                 (1-tuple)
  get(path)                    -> (dict, int)          (2-tuple, the common case)
  get_with_headers(path, h=)   -> (dict, int, dict)    (3-tuple for security-header assertions)
  post(path, body=None)        -> (dict, int)
  post_with_headers(path, b=, h=) -> (dict, int)       (test_sprint19/29 add custom headers)
  get_raw(path)                -> (bytes, str, int)    (raw content-type + status for image preview)
  get_raw_with_headers(path)   -> (bytes, dict, int)   (raw bytes + full headers + status)
  post_multipart(path, fields, files) -> (dict, int)   (multipart/form-data upload)

  make_session_tracked(list, ws=None) -> (sid, pathlib.Path)   (test_sprint1/2/3/7)
  make_session(list, ws=None)         -> (sid, dict)           (test_sprint3+/12/13/14/15/17/23/28)
"""
import json
import pathlib
import urllib.error
import urllib.request
import uuid

from tests._pytest_port import BASE


# ── 1-tuple: body only ──────────────────────────────────────────────────────

def get_body(path, headers=None):
    """GET. Returns decoded JSON body. HTTPError raised for non-2xx."""
    req = urllib.request.Request(BASE + path)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


# ── 2-tuple: body + status ──────────────────────────────────────────────────

def get(path, headers=None):
    """GET. Returns (body_dict, status).

    Note: HTTPError is NOT caught — callers that need to assert on
    error responses should wrap this in `try/except urllib.error.HTTPError`.
    This matches the original per-file helpers in test_sprint{3,11,...}.py
    which relied on the exception to surface the status code.
    """
    req = urllib.request.Request(BASE + path)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read()), r.status


def post(path, body=None, headers=None):
    """POST JSON. Returns (body_dict, status).

    `headers` is optional and merged on top of the default Content-Type header,
    so callers can pass e.g. `headers={"Origin": "..."}` for CSRF assertions
    without losing the JSON content-type.
    """
    data = json.dumps(body or {}).encode()
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(BASE + path, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read()), e.code


def post_with_timeout(path, body=None, headers=None, timeout=60):
    """POST JSON with a caller-controlled timeout (default 60s).

    Use this for endpoints that synchronously boot heavy subsystems
    and take longer than the default 10s canary window.  Today the
    only such endpoint is POST /api/session/pin, whose server-side
    SessionDisplay.start() takes 10-15s on cold start (Xvfb + dbus
    + fcitx5 + openbox + x11vnc + Chrome), then 3-6s on warm starts.

    Do NOT use this helper for the common case -- the default post()'s
    10s timeout is a deliberate canary for hung servers and should
    not be relaxed globally.
    """
    data = json.dumps(body or {}).encode()
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(BASE + path, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read()), e.code


# ── 3-tuple: body + status + headers (security assertions) ─────────────────

def get_with_headers(path, headers=None):
    """GET. Returns (body_dict, status, headers_dict)."""
    req = urllib.request.Request(BASE + path)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read()), r.status, dict(r.headers)


def post_with_headers(path, body=None, headers=None):
    """POST JSON with extra headers. Returns (body_dict, status)."""
    data = json.dumps(body or {}).encode()
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(BASE + path, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read()), e.code


# ── Raw byte responses (image preview, Content-Disposition checks) ─────────

def get_raw(path):
    """GET. Returns (bytes, content_type_str, status)."""
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return r.read(), r.headers.get('Content-Type', ''), r.status


def get_raw_with_headers(path):
    """GET. Returns (bytes, headers_dict, status)."""
    req = urllib.request.Request(BASE + path)
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read(), dict(r.headers.items()), r.status


# ── Multipart upload (test_sprint1) ────────────────────────────────────────

def post_multipart(path, fields, files):
    """POST multipart/form-data. fields: {name: str}; files: {name: (filename, bytes)}."""
    boundary = uuid.uuid4().hex.encode()
    body = b""
    for name, value in fields.items():
        body += b"--" + boundary + b"\r\n"
        body += f"Content-Disposition: form-data; name=\"{name}\"\r\n\r\n".encode()
        body += value.encode() + b"\r\n"
    for name, (filename, data) in files.items():
        body += b"--" + boundary + b"\r\n"
        body += f"Content-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\n".encode()
        body += b"Content-Type: application/octet-stream\r\n\r\n"
        body += data + b"\r\n"
    body += b"--" + boundary + b"--\r\n"
    req = urllib.request.Request(
        BASE + path, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary.decode()}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read()), e.code


# ── Session creation ───────────────────────────────────────────────────────

def make_session_tracked(created_list, ws=None):
    """POST /api/session/new; register sid for cleanup. Returns (sid, Path)."""
    body = {}
    if ws:
        body["workspace"] = str(ws)
    d, _ = post("/api/session/new", body)
    sid = d["session"]["session_id"]
    created_list.append(sid)
    return sid, pathlib.Path(d["session"]["workspace"])


def make_session(created_list, ws=None):
    """POST /api/session/new; register sid for cleanup. Returns (sid, dict)."""
    body = {}
    if ws:
        body["workspace"] = str(ws)
    d, _ = post("/api/session/new", body)
    sid = d["session"]["session_id"]
    created_list.append(sid)
    return sid, d["session"]
