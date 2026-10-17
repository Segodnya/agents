# Publishing questions as Figma comments

## Facts

- The Figma MCP has no comment tool, and the Plugin API (`use_figma`) cannot create comments. Use the REST API: `POST https://api.figma.com/v1/files/<fileKey>/comments` with `{message, client_meta: {node_id, node_offset: {x, y}}}`. `node_offset` is measured from the node's top-left corner.
- Token: `~/.config/figma/token`. It needs the scope `file_comments:write`. A 403 from `/v1/me` is only a missing profile scope and does not block posting. Never ask for the token in chat.
- The API cannot edit a comment. To change one, delete it (`DELETE …/comments/<id>`, or the user deletes it in Figma) and post again.
- Post with [scripts/post_comments.py](scripts/post_comments.py) through Bash. It skips texts already in the file, so a re-run is safe.
- `SKILL_DIR` is the path from the harness line `Base directory for this skill:`. Substitute it literally.

## Steps

1. Write `comments.json` next to the screenshots: `[{"n", "node", "x", "y", "msg"}]`. Each `msg` reads on its own, without the report. Take `x, y` from the screenshot pixel point × the scale `original / rendered`, aimed at the control the question is about.
2. Show the user a table (n → frame → what the pin points at). Get an explicit «ок»: the designer gets a notification for every comment.
3. Post one comment first: `python3 -I -u "SKILL_DIR/scripts/post_comments.py" <fileKey> <comments.json> <n>`. Ask the user to check the pin position, the text and the author in Figma.
4. Post the rest with the same command without `<n>`.

Done when the script prints `verified N of N | mismatch: []`. Report the count, and say that pin positions are checked only by the API, not by eye.
