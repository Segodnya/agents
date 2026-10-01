#!/usr/bin/env python3
"""Перезаписать комментарий или описание Jira-тикета wiki-разметкой через REST v2.

  jira_put.py comment <KEY> <commentId> <file>
  jira_put.py description <KEY> <file>
"""
import json, os, sys, urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), '../../_lib'))
from jira import auth  # noqa: E402

server, authorization = auth()

mode = sys.argv[1]
if mode == 'comment':
    key, cid, path = sys.argv[2:5]
    url = f'{server}/rest/api/2/issue/{key}/comment/{cid}'
    payload = {'body': open(path).read()}
elif mode == 'description':
    key, path = sys.argv[2:4]
    url = f'{server}/rest/api/2/issue/{key}'
    payload = {'fields': {'description': open(path).read()}}
else:
    sys.exit(__doc__)

req = urllib.request.Request(url, data=json.dumps(payload).encode(), method='PUT', headers={
    'Content-Type': 'application/json',
    'Authorization': authorization,
})
print(urllib.request.urlopen(req).status)
