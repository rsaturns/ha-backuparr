# Security Policy

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Instead, use GitHub's [private vulnerability reporting](https://github.com/rsaturns/ha-backuparr/security/advisories/new) (Security tab → Report a vulnerability). This opens a private conversation with the maintainer so a fix can be prepared before details are public.

Include what you'd include in a bug report: affected version/commit, your Home Assistant and Backuparr versions, steps to reproduce, and the impact.

A vulnerability in Backuparr itself goes to [Backuparr's private reporting](https://github.com/rsaturns/backuparr/security/advisories/new) instead.

## Known, accepted risks

A few things are intentionally not "fixed" - they're documented trade-offs, not oversights:

- The Backuparr token is stored in Home Assistant's config entry, like the credentials of every other integration. Anyone who can read Home Assistant's configuration folder or backups can read it. The token only reads backup status from Backuparr's `/api/ha/` endpoints: it can't change settings, run a backup or restore, or read app credentials. To revoke it, create a new token in Backuparr.
- Backuparr's self-signed certificate is trusted on first use: setup shows its fingerprint, and the user compares it with Backuparr's card. Someone already in the middle at that moment, and a user who doesn't compare, would get the token. After that the certificate is pinned (its public key, so Backuparr's own renewals are followed) and checked before the token is sent.
- Turning off **Verify SSL certificate** accepts any certificate, so anything between Home Assistant and Backuparr could read the token; so does plain `http://`. Leave it on.

If you believe either of these has a worse impact than described, or you've found something else, please still report it privately first.

## Supported versions

Only the newest release receives fixes. There's no long-term support branch.
