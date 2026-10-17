"""Post pinned Figma comments from comments.json, skip ones already published, verify.

usage: post_comments.py <fileKey> <comments.json> [n]
"""
import json, os, sys, time, urllib.request, urllib.error

file_key, path = sys.argv[1], sys.argv[2]
only = int(sys.argv[3]) if len(sys.argv) > 3 else None
base = f"https://api.figma.com/v1/files/{file_key}/comments"
H = {"X-Figma-Token": open(os.path.expanduser("~/.config/figma/token")).read().strip()}


def listing():
    req = urllib.request.Request(base, headers=H)
    return json.load(urllib.request.urlopen(req, timeout=20))["comments"]


def meta(c):
    return {"node_id": c["node"], "node_offset": {"x": c["x"], "y": c["y"]}}


def pin(client_meta):
    return {k: client_meta.get(k) for k in ("node_id", "node_offset")}


todo = [c for c in json.load(open(path)) if only is None or c["n"] == only]
posted = {c["message"]: c["id"] for c in listing()}

for c in todo:
    if c["msg"] in posted:
        print(f"skip {c['n']}: already published")
        continue
    body = json.dumps({"message": c["msg"], "client_meta": meta(c)}, ensure_ascii=False).encode()
    req = urllib.request.Request(base, data=body, method="POST", headers={**H, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            posted[c["msg"]] = json.load(r)["id"]
            print(f"ok {c['n']}: {posted[c['msg']]}")
    except urllib.error.HTTPError as e:
        print(f"FAIL {c['n']}: HTTP {e.code} {e.read().decode()[:200]}")
    except Exception as e:
        print(f"FAIL {c['n']}: {type(e).__name__} {e}")
    time.sleep(0.5)

byid = {c["id"]: c for c in listing()}
bad = []
for c in todo:
    lc = byid.get(posted.get(c["msg"]))
    if lc is None or lc["message"] != c["msg"] or pin(lc["client_meta"]) != meta(c):
        bad.append(c["n"])
print(f"comments in file: {len(byid)} | verified {len(todo) - len(bad)} of {len(todo)} | mismatch: {bad}")
