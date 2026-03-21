# Security Policy

## Reporting a Vulnerability
If you discover a security issue, do not open a public issue.

Please contact the maintainers privately with:
- Description of the issue
- Reproduction steps
- Impact assessment
- Suggested mitigation (if available)

## Secrets Handling
- Never commit API keys/tokens.
- Use `.scholarfetch.env` locally (already gitignored).
- Rotate keys if leakage is suspected.

## Third-Party APIs
This project depends on external APIs that may change behavior, schema, and entitlement rules.
Validate assumptions against provider docs and fail safely.
