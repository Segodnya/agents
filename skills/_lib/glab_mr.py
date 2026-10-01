"""GitLab access via glab, shared by the skills' scripts.

A script in skills/<name>/scripts/ imports it by its own real path (skills are symlinked):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "../../_lib"))
"""
import json
import subprocess
import sys
from urllib.parse import quote, urlencode, urlparse

MARKER = "/-/merge_requests/"
# prefix of mr-ready's threads and replies; audit-reply finds them by it
SELF_REVIEW = "🤖 self-review"
# service accounts GitLab doesn't flag as bots
SYSTEM_BOTS = {"deployer", "bundle_size_analyzer", "gitlab-ci-token"}
BOT_PATTERNS = ("bot", "deployer", "ci-", "gitlab-")


def is_bot(author, by_name=True):
    """author — a GitLab user dict or a bare username. by_name=False trusts only the API flag and the
    exact service accounts: a substring like `bot` would also hide a human reviewer's thread."""
    if isinstance(author, dict):
        if author.get("bot"):
            return True
        author = author.get("username")
    name = (author or "").lower()
    return name in SYSTEM_BOTS or (by_name and any(p in name for p in BOT_PATTERNS))


def parse_url(url):
    """https://HOST/group/sub/project/-/merge_requests/41 -> (host, 'group/sub/project', '41')."""
    p = urlparse(url)
    if MARKER not in p.path:
        sys.exit(f"not an MR url (no '{MARKER}'): {url}")
    project, rest = p.path.split(MARKER, 1)
    return p.netloc, project.strip("/"), rest.strip("/").split("/")[0].split("?")[0]


def mr_endpoint(project_path, iid):
    return f"projects/{quote(project_path, safe='')}/merge_requests/{iid}"


def api(host, method, endpoint, body=None, soft=False):
    """One glab call. A JSON body goes as `--input -` with the header: form data → 415 / dropped fields.
    Failure → exit with the error, or {"error": ...} when soft."""
    cmd = ["glab", "api", "--hostname", host, "-X", method, endpoint]
    stdin = None
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "--input", "-"]
        stdin = json.dumps(body)
    r = subprocess.run(cmd, input=stdin, capture_output=True, text=True)
    if r.returncode != 0:
        if soft:
            return {"error": r.stderr.strip()}
        sys.exit(f"glab {method} {endpoint}: {r.stderr.strip()}")
    try:
        return json.loads(r.stdout) if r.stdout.strip() else {}
    except json.JSONDecodeError:
        sys.exit(f"glab {method} {endpoint}: non-JSON reply: {r.stdout[:200]}")


def get(host, endpoint, fields=None):
    """Best-effort GET for stats scripts: a failure is logged to stderr → None."""
    result = api(host, "GET", f"{endpoint}?{urlencode(fields)}" if fields else endpoint, soft=True)
    if isinstance(result, dict) and "error" in result:
        sys.stderr.write(f"glab api error {endpoint}: {result['error']}\n")
        return None
    return result


def paginate(host, endpoint, soft=False):
    """All pages of a list endpoint; a failed page exits — a silently cut list looks complete.
    soft: a failed page is logged and ends the list with what was fetched."""
    out, page = [], 1
    sep = "&" if "?" in endpoint else "?"
    while True:
        url = f"{endpoint}{sep}per_page=100&page={page}"
        chunk = get(host, url) if soft else api(host, "GET", url)
        if chunk is None:
            return out
        out += chunk
        if len(chunk) < 100:
            return out
        page += 1


def discussions(host, project_path, iid):
    return paginate(host, f"{mr_endpoint(project_path, iid)}/discussions")
