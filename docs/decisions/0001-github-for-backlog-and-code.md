# 0001: GitHub for the backlog and the code

- **Status:** Accepted (replaces the earlier proposal to use Jira for the backlog)
- **Date:** 2026-11-05

## Context

The epics and Sprint 0 first lived in Jira, while the code lives in the GitHub organization. Tracking work in two places means they drift apart and nobody knows which one is right. The team decided to keep everything in one place.

## Decision

- **GitHub is the only place** for the backlog, sprints, task status, code, reviews and Docker.
- **Epics** are issues with the `epic` label. Their tasks are **sub-issues**, so the epic shows its progress.
- **Sprints** are milestones (`Sprint 1`, `Sprint 2` …) with the sprint's last day as due date.
- **The board** is a GitHub Project in the organization (Backlog, Sprint backlog, In progress, In review, Done).
- Pull requests say `Closes #<issue>`, so the issue closes and moves to Done when the PR is merged.
- The first backlog is created from `.github/backlog/backlog.json` by the "Set up backlog" workflow.

## Consequences

- Jira is no longer used. Anything still only in Jira is copied over once.
- Everyone needs a GitHub account in the organization, which we have anyway.
- Branch names and commits use the issue number instead of a Jira key.
