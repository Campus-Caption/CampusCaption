# 0002: Audio is never written to disk

- **Status:** Accepted (from Epic 2 and Epic 4)
- **Date:** 2026-11-05

## Context

Live speech is personal data under GDPR, even when we don't store it. Lectures also count as non-public spoken word (§201 StGB). Our research showed streaming speech recognition only needs a few seconds of audio at a time anyway.

## Decision

- Live audio stays in RAM only, in a buffer of max 30 seconds, then it's dropped.
- No audio in logs, temp files, Docker volumes or Git.
- Wireless mics with onboard recording (for example RØDE Wireless GO III) have recording turned off.
- Test recordings for benchmarks are only made with written consent and are kept outside the repo.

## Consequences

- Debugging can't rely on replaying live audio. We test with consented recordings instead.
- Any new code that touches audio must be reviewed against this rule.
