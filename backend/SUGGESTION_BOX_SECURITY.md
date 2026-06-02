# Suggestion Box Security Hardening (Local Only)

This document covers findings, risk assessment, and a production readiness checklist for `/suggest`.

## Findings (Before)

- No bot mitigation (no rate limit, no CAPTCHA, no honeypot).
- Minimal validation (email contains `@`/`.`, message length check only).
- No persistence (submissions not stored in SQLite).
- No abuse telemetry (no IP/user-agent/result logging).
- Telegram forwards raw untrusted text.
- No blocklist support.

## Mitigations Implemented (Now)

- Input validation:
  - `name` required, max 100
  - `email` required, max 255, regex format validation
  - `message` required, max 1000
  - whitespace trimmed, empty rejected
- Security filtering (server-side):
  - reject if payload contains any of:
    - `<script`, `javascript:`, `<iframe`, `onerror=`, `onload=`
  - blocked requests are logged
- Rate limiting (server-side, per IP):
  - 1 submission per 30 seconds
  - max 5 submissions per hour
  - returns HTTP 429 on exceed
- Auto-blocking (server-side, per IP):
  - if IP reaches 20 submissions in 1 hour, added to blocklist for 24 hours
- Honeypot:
  - hidden `company` field in the form
  - reject if filled
- Cloudflare Turnstile support:
  - frontend token submission (optional)
  - backend verification when `TURNSTILE_SECRET` is configured
  - when `TURNSTILE_REQUIRED=1`, missing/failed verification is rejected
- SQLite:
  - all queries are parameterized
  - new tables:
    - `suggestion_events` (accepted/rejected attempts with metadata)
    - `ip_blocklist`
- Telegram safety:
  - control characters stripped
  - message clamped and marked `[TRUNCATED]` when too large
  - no `parse_mode` used (plain text)

## Risk Assessment

- High: bot spam (fixed with rate limiting + Turnstile + honeypot).
- Medium: XSS probe payloads (mitigated with server-side substring filter + no HTML rendering).
- Medium: Telegram abuse (mitigated by sanitization and truncation).
- Low: SQL injection (mitigated by parameterized queries; no string-built SQL in this path).

## Production Readiness Checklist

- Configure Cloudflare Turnstile:
  - Set `TURNSTILE_SECRET` and optionally `TURNSTILE_REQUIRED=1` in `backend/.env`
  - Set `turnstileEnabled=true` and `turnstileSiteKey` in `frontend/site-config.js`
- Confirm Nginx forwarding:
  - ensure `X-Forwarded-For` and `X-Real-IP` are set (already in deploy config)
- Verify DB location:
  - `market.db` is writable by the live server user
- Monitor logs:
  - check `suggestion_events` volume and blocklist growth
- Load testing:
  - simulate bursts from single IP and verify 429 behavior
- Incident response:
  - blocklist can be extended or made permanent by setting `blocked_until_utc` null

