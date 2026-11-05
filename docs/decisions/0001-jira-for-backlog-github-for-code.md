# 0001: Jira for the backlog, GitHub for the code

- **Status:** Proposed
- **Date:** 2026-11-05

## Context

The epics and Sprint 0 already live in Jira. We also have a GitHub organization for the code. Tracking tasks in both places means they drift apart and nobody knows which one is right.

## Decision

- Jira is the only place for the backlog, sprints and task status.
- GitHub is for code, reviews and Docker.
- We connect them with the "GitHub for Jira" app. Branches, commits and pull requests that contain a Jira key (for example `SCRUM-12`) then show up on the Jira ticket.

## Consequences

- No GitHub project board and no GitHub issues for planning.
- Everyone puts the Jira key in their branch name and commit messages.
