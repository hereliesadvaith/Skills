# Skills

A collection of Agent Skills for Claude — self-contained folders of instructions that Claude loads when a task calls for them.

Each skill is a directory with a `SKILL.md` file. Claude reads the name and description at startup, and pulls in the full instructions only when they're relevant.

## Skills

| Skill | What it does |
|---|---|
| [`skill-name`](./skills/skill-name) | One line on what it does and when Claude uses it. |
| [`another-skill`](./skills/another-skill) | One line on what it does and when Claude uses it. |

## Install

Clone the repo:

```bash
git clone https://github.com/USERNAME/REPO.git
cd REPO
```

**For all your projects** — copy a skill into your personal skills directory:

```bash
cp -r skills/skill-name ~/.claude/skills/
```

**For one project** — copy it into the project instead, and commit it so your team gets it too:

```bash
mkdir -p .claude/skills
cp -r skills/skill-name .claude/skills/
```

Start a new Claude Code session and the skill is available. Claude invokes it on its own when your request matches the description; you can also ask for it by name.

## Repository layout

```
skills/
  skill-name/
    SKILL.md          # required: frontmatter + instructions
    reference.md      # optional: extra docs, loaded only when needed
    scripts/          # optional: helper scripts Claude can run
```

## Writing a skill

Create a directory under `skills/` and add a `SKILL.md`:

```markdown
---
name: skill-name
description: What this skill does and when Claude should use it. Mention the triggers — file types, tools, or phrases the user is likely to say.
---

# Skill Name

## Instructions

Step-by-step guidance for Claude.

## Examples

Concrete examples of the skill in use.
```

A few things that make a skill work well:

- **The description does the routing.** It's the only part Claude sees before deciding to load the skill, so say both what it does and when to use it.
- **Keep `SKILL.md` short.** Push detail into separate files and reference them; they load only when needed.
- **Names are lowercase, hyphenated, and under 64 characters.**
- **Scripts beat prose** for anything deterministic — Claude runs them and only the output uses context.

Supported frontmatter fields: `name`, `description`, `allowed-tools`, `compatibility`, `license`, `metadata`. Anything else will be rejected.

See the [skill authoring guide](https://docs.claude.com/en/docs/claude-code/skills) for the full reference.

## Contributing

Pull requests welcome. Please:

1. Put each skill in its own directory under `skills/`.
2. Include a clear `description` and at least one usage example.
3. Test the skill in a real session before opening the PR.
4. Add it to the table above.

## License

MIT
