# frontend-design skill kit

An sbx mixin kit that adds Anthropic's
[`frontend-design`](https://github.com/anthropics/skills/tree/main/skills/frontend-design)
skill to a sandbox. Used by `../../features-ui-skill.yaml` to compare a run
with the skill against one without it, for both `claude` and `codex`.

The layout follows
[`mrzarquon/fost-talk-kits/planning-with-files`](https://github.com/mrzarquon/fost-talk-kits/tree/main/planning-with-files):
everything under `files/workspace/` is copied into the sandbox workspace at
create time, so the skill shows up as a project skill in two places, one per
agent: `.claude/skills/frontend-design/` (Claude Code) and
`.agents/skills/frontend-design/` (Codex). Both copies are identical. Each
directory carries a `.gitignore` of
`*`, so the skill never lands in the agent's diff. (A startup command writing
`.git/info/exclude` was tried first; it runs before the workspace copy and
outside the repo, so it had no effect.)

`SKILL.md` and `LICENSE.txt` are vendored unchanged from anthropics/skills at
commit `dbd4588f9e10` (Apache-2.0). To refresh:

```bash
for d in files/workspace/{.claude,.agents}/skills/frontend-design; do
  for f in SKILL.md LICENSE.txt; do
    curl -sfL "https://raw.githubusercontent.com/anthropics/skills/main/skills/frontend-design/$f" -o "$d/$f"
  done
done
sbx kit validate .
```
