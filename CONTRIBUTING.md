# How we work

Short rules so everyone works the same way. If something here doesn't work for us, we change it in the retro.

## Where things live

| What | Where |
|---|---|
| Backlog: epics and tasks | [Issues](https://github.com/Campus-Caption/CampusCaption/issues). Epics have the `epic` label, their tasks are sub-issues |
| Sprints | Milestones (`Sprint 1`, `Sprint 2` …) |
| Who does what, status | The organization's project board |
| Code, Docker, scripts | This repo |
| Decisions we made and why | [`docs/decisions/`](docs/decisions/) |
| Research results | [`research/`](research/), linked from the issue |

Everything lives on GitHub, nothing in Jira ([decision 0001](docs/decisions/0001-github-for-backlog-and-code.md)).

## Labels

| Label | Meaning |
|---|---|
| `epic` | A big goal that groups smaller issues |
| `research` | A spike: find something out and write down a decision |
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
6. Research issues are done when the result is written down (in `research/` or a decision record), not when code is merged.

Nobody pushes straight to `main`.

## Definition of Done

A ticket is done when:

- it does what the "Done when" list in the issue says
- it's merged into `main` and `docker compose up --build` still starts
- someone else reviewed it
- anything the next person needs to know is written down (README, a decision record, or a comment on the issue)

## Never commit

- **Audio recordings** of any kind (`.wav`, `.mp3`, `.m4a` …). They contain people's voices and are personal data. Keep test recordings in the agreed private folder, never in Git.
- Passwords, tokens or `.env` files.
- Model files (they are big and get downloaded automatically).

## Decision records

When we make a decision that the next team should understand (which model, which frontend library, why no login), add a short file to `docs/decisions/`. Copy [`0000-template.md`](docs/decisions/0000-template.md). Half a page is enough.
