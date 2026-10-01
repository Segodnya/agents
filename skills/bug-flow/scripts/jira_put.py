#!/usr/bin/env python3
"""Перезаписать комментарий или описание Jira-тикета wiki-разметкой через REST v2.

  jira_put.py comment <KEY> <commentId> <file>
  jira_put.py description <KEY> <file>
"""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), '../../_lib'))
from jira import request  # noqa: E402

mode = sys.argv[1]
if mode == 'comment':
    key, cid, path = sys.argv[2:5]
    url = f'/rest/api/2/issue/{key}/comment/{cid}'
    payload = {'body': open(path, encoding='utf-8').read()}
elif mode == 'description':
    key, path = sys.argv[2:4]
    url = f'/rest/api/2/issue/{key}'
    payload = {'fields': {'description': open(path, encoding='utf-8').read()}}
else:
    sys.exit(__doc__)

request('PUT', url, payload)
print('ok')
