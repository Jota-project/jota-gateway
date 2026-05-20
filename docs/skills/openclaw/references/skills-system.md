# OpenClaw — Skills System

## What a Skill Is

A `SKILL.md` file injected into the agent's system prompt. It teaches the agent
**when and how** to use tools — not a plugin, just context + instructions.

## Directory Structure

```
~/.openclaw/workspace/skills/
└── my-skill/
    └── SKILL.md
```

Or in a project workspace:
```
<workspace>/skills/my-skill/SKILL.md
```

## SKILL.md Format

```markdown
---
name: my-skill                     # unique id: lowercase, digits, hyphens
description: >
  One-line description shown to the agent — this is the trigger condition.
  Be specific: "Use when the user asks to X or mentions Y."
metadata:
  openclaw:
    os: ["linux", "darwin"]        # optional: filter by OS
    requires:
      bins: ["ffmpeg", "git"]      # optional: required binaries in PATH
      config: ["models.ollama"]    # optional: required config keys
---

# My Skill Title

Instructions for the agent in plain Markdown.
Tell it WHEN to activate, WHAT to do, HOW to use specific tools.
```

## Loading Precedence (highest → lowest)

1. `<workspace>/skills/`
2. `<workspace>/.agents/skills/`
3. `~/.agents/skills/`
4. `~/.openclaw/skills/`
5. Bundled (shipped with OpenClaw)
6. `skills.load.extraDirs` in config

## Workflow

```bash
# Create
mkdir -p ~/.openclaw/workspace/skills/my-skill
# write SKILL.md

# Verify
openclaw skills list

# Test (new session picks up skills)
/new
openclaw agent --message "trigger phrase"

# Install from ClawHub
openclaw skills install <name>
```

## Best Practices

- **Description is the trigger**: the agent uses it to decide when to activate the skill
- Keep the body focused on WHAT to do, not on how to be an AI
- If using `exec`, explicitly prevent command injection from untrusted input
- One skill per capability domain
- Use `os` filter when tools are platform-specific

## Security Warning

Skills from ClawHub can contain prompt injections or data exfiltration.
Always review `SKILL.md` before installing. Cisco reported exfiltration cases in 2026.
Check scan results on the clawhub.ai skill page before installing.
