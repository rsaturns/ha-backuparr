# Security Policy

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Instead, use GitHub's [private vulnerability reporting](https://github.com/rsaturns/ha-backuparr/security/advisories/new) (Security tab → Report a vulnerability). This opens a private conversation with the maintainer so a fix can be prepared before details are public.

Include what you'd include in a bug report: affected version/commit, your Home Assistant and Backuparr versions, steps to reproduce, and the impact.

A vulnerability in Backuparr itself goes to [Backuparr's private reporting](https://github.com/rsaturns/backuparr/security/advisories/new) instead.

## Known, accepted risks

A few things are intentionally not "fixed" - they're documented trade-offs, not oversights:

- The Backuparr token is stored in Home Assistant's config entry, like the credentials of every other integration. Anyone who can read Home Assistant's configuration folder or backups can read it. The token only reads backup status from Backuparr's `/api/ha/` endpoints: it can't change settings, run a backup or restore, or read app credentials. To revoke it, create a new token in Backuparr.
- Turning off **Verify SSL certificate** lets the integration talk to a Backuparr with a self-signed certificate, but then anything between Home Assistant and Backuparr could read the token. Leave it on unless Backuparr is on a network you trust.

If you believe either of these has a worse impact than described, or you've found something else, please still report it privately first.

## Supported versions

Only the newest release receives fixes. There's no long-term support branch.
