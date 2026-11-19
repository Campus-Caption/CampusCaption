# How we work

Short rules so everyone works the same way. If something here doesn't work for us, we change it in the retro.

## Where things live

| What | Where |
|---|---|
| Backlog: epics and tasks | [Issues](https://github.com/Campus-Caption/CampusCaption/issues). Epics have the `epic` label, their tasks are sub-issues |
| Sprints | Milestones (`Sprint 1`, `Sprint 2` …) |
| Who does what, status | The organization's project board |
| Code, Docker, scripts | This repo |

Everything lives on GitHub, nothing in Jira.

## Labels

| Label | Meaning |
|---|---|
| `epic` | A big goal that groups smaller issues |
| `task` | A concrete piece of work |
| `priority: high` | Candidate for the next sprint |
| `area: …` | Which part of the system (ai-pipeline, backend, frontend, ux, privacy, translation, speakers) |

## Workflow

1. Pick an issue from the current sprint, assign yourself and move it to **In progress** on the board.
2. Create a branch from `main` that starts with the issue number:
   `12-websocket-broadcast`
3. Commit with the issue number in the message, so it shows up on the issue:
   `#12 add websocket endpoint`
4. Push and open a pull request into `main`. Write `Closes #12` in the template.
5. One other person reviews and approves, then merges. The issue closes and moves to **Done**.

Nobody pushes straight to `main`.

## Definition of Done

A ticket is done when:

- it does what the "Done when" list in the issue says
- it's merged into `main` and `docker compose up --build` still starts
- someone else reviewed it
- anything the next person needs to know is written down (README or a comment on the issue)

## Never commit

- **Audio recordings** of any kind (`.wav`, `.mp3`, `.m4a` …). They contain people's voices and are personal data. Keep test recordings in the agreed private folder, never in Git.
- Passwords, tokens or `.env` files.
- Model files (they are big and get downloaded automatically).

