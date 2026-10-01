#!/usr/bin/env python3
"""Validate a config stored in a Kubernetes ConfigMap with the binary from the image that runs it.

    configmap_test.py <configmap.yaml> <data key> <path in container> <image> <cmd...>
    configmap_test.py k8s/web/configmap.yaml nginx.conf /etc/nginx/nginx.conf nginx:1.26 nginx -t

Path in container: the volume's `mountPath` when mounted with `subPath`, else `<mountPath>/<key>`; image —
from the workload that mounts the ConfigMap. The key must be a block scalar (`key: |`) and occur once in
the file. Exit code is the command's; its output is passed through.
"""

import os
import re
import subprocess
import sys
import tempfile


def extract(path, key):
    lines = open(path, encoding="utf-8").read().splitlines()
    heads = [i for i, l in enumerate(lines) if re.match(rf"^\s+{re.escape(key)}:\s*\|[-+]?\s*$", l)]
    if len(heads) != 1:
        sys.exit(f"{path}: block-scalar key {key!r} found {len(heads)} times, need exactly 1")
    body = lines[heads[0] + 1:]
    first = next((l for l in body if l.strip()), "")
    indent = len(first) - len(first.lstrip())
    out = []
    for l in body:
        if l.strip() and len(l) - len(l.lstrip()) < indent:
            break
        out.append(l[indent:])
    return "\n".join(out).rstrip("\n") + "\n"


def main():
    if len(sys.argv) < 6:
        sys.exit(__doc__)
    cm, key, target, image, cmd = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5:]
    with tempfile.TemporaryDirectory() as d:
        conf = os.path.join(d, os.path.basename(target))
        open(conf, "w", encoding="utf-8").write(extract(cm, key))
        r = subprocess.run(["docker", "run", "--rm", "-v", f"{conf}:{target}:ro",
                            "--entrypoint", cmd[0], image, *cmd[1:]])
    print(f"{cm} [{key}]: {'OK' if r.returncode == 0 else 'FAIL'}")
    sys.exit(r.returncode)


if __name__ == "__main__":
    main()
