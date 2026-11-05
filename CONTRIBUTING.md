# How we work

Short rules so everyone works the same way. If something here doesn't work for us, we change it in the retro.

## Where things live

| What | Where |
|---|---|
| Backlog, sprints, who does what | Jira (project `SCRUM`) |
| Code, Docker, scripts | This repo |
| Decisions we made and why | [`docs/decisions/`](docs/decisions/) |
| Research notes and docs | Shared folder, linked from the Jira epic |

We track work in Jira only. GitHub is for code, so we don't keep a second board here.

## Workflow

1. Pick a ticket in Jira and move it to **In Progress**.
2. Create a branch from `main` that starts with the Jira key:
   `SCRUM-12-websocket-broadcast`
3. Commit with the key in the message, so Jira shows the commit on the ticket:
   `SCRUM-12 add websocket endpoint`
4. Push and open a pull request into `main`. Fill in the short template.
5. One other person reviews and approves, then merges.
6. Move the ticket to **Done** when it meets the Definition of Done.

Nobody pushes straight to `main`.

## Definition of Done

A ticket is done when:

- it does what the acceptance criteria in Jira say
- it's merged into `main` and `docker compose up --build` still starts
- someone else reviewed it
- anything the next person needs to know is written down (README, a decision record, or a comment on the ticket)

## Never commit

- **Audio recordings** of any kind (`.wav`, `.mp3`, `.m4a` …). They contain people's voices and are personal data. Keep test recordings in the agreed private folder, never in Git.
- Passwords, tokens or `.env` files.
- Model files (they are big and get downloaded automatically).

## Decision records

When we make a decision that the next team should understand (which model, which frontend library, why no login), add a short file to `docs/decisions/`. Copy [`0000-template.md`](docs/decisions/0000-template.md). Half a page is enough.
