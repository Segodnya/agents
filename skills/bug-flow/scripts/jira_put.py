#!/usr/bin/env python3
"""Перезаписать комментарий или описание Jira-тикета wiki-разметкой через REST v2.

  jira_put.py comment <KEY> <commentId> <file>
  jira_put.py description <KEY> <file>
"""
import base64, json, os, re, subprocess, sys, urllib.request

raw = open(os.path.expanduser('~/.config/.jira/.config.yml')).read()
server = re.search(r'^server:\s*(\S+)', raw, re.M).group(1)
login = re.search(r'^login:\s*(\S+)', raw, re.M).group(1)
token = os.environ.get('JIRA_API_TOKEN') or subprocess.check_output(
    ['security', 'find-generic-password', '-s', 'jira-cli', '-w']).decode().strip()

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
    'Authorization': 'Basic ' + base64.b64encode(f'{login}:{token}'.encode()).decode(),
})
print(urllib.request.urlopen(req).status)
