"""Jira REST shared by the skills' scripts: server and login from jira-cli's config,
token from $JIRA_API_TOKEN, else from the keychain entry jira-cli keeps."""
import base64
import functools
import json
import os
import re
import subprocess
import sys
import urllib.request


@functools.cache
def auth():
    """→ (server without a trailing slash, 'Basic …' header value)."""
    cfg = open(os.path.expanduser("~/.config/.jira/.config.yml"), encoding="utf-8").read()
    server = re.search(r"^server:\s*(\S+)", cfg, re.M).group(1).rstrip("/")
    login = re.search(r"^login:\s*(\S+)", cfg, re.M).group(1)
    token = os.environ.get("JIRA_API_TOKEN")
    if not token:
        r = subprocess.run(["security", "find-generic-password", "-s", "jira-cli", "-w"],
                           capture_output=True, text=True)
        token = r.stdout.strip() or sys.exit("no JIRA_API_TOKEN and no jira-cli keychain entry")
    return server, "Basic " + base64.b64encode(f"{login}:{token}".encode()).decode()


def request(method, path, body=None):
    """path from /rest/… → parsed JSON (None on an empty response); any failure exits naming the call."""
    server, authorization = auth()
    req = urllib.request.Request(f"{server}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": authorization, "Accept": "application/json",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
    except Exception as e:
        sys.exit(f"{method} {path}: {e}")
    return json.loads(raw) if raw else None
