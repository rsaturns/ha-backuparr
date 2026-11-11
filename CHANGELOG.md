# Changelog

## [Unreleased]

### Added
- Backuparr in Home Assistant: an All backups problem sensor, one per app and for Backuparr's own backup, a Failed backups count and a Last backup time per app (off by default).
- Set up from the UI with Backuparr's address and token; a new token is asked for when Backuparr refuses the old one, and Reconfigure changes the address.
- Backuparr's self-signed HTTPS certificate is trusted after you compare its fingerprint with Backuparr's card, then checked before every request; Backuparr's renewals are followed, and a new key raises a repair to trust it (#1).
- Diagnostics without Backuparr's address or token.
