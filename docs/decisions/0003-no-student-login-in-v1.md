# 0003: No student login in version 1

- **Status:** Proposed, to discuss in Sprint 1 planning
- **Date:** 2026-11-05

## Context

The first starter tasks suggested a login with the school email, access and refresh tokens, and a classroom list. But students only need to read captions. Every account we create is personal data we have to protect and explain to the data protection officer.

## Decision

- **Students** join a live room with a room code or QR code. No account, no personal data.
- **Lecturers** (the person starting and pausing captions) get a simple protected control page. A shared secret or one login is enough for the prototype.
- Real university login (school email / SSO) moves to a later sprint, when we know we need it.

## Consequences

- The backend can start with rooms and WebSockets instead of auth.
- The lecturer control page must not be open to everyone, even in the prototype.
- If we later save transcripts per student, we revisit this decision.
