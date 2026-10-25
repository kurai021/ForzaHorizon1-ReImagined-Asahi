# Archived upstream automation

These files preserve upstream's release, Pages, Discord announcement and scheduled
public-server probe workflows. GitHub Actions only discovers workflow YAML under
`.github/workflows/`; this directory is intentionally outside it.

They remain inactive throughout the Asahi preservation/publication audit. Moving
them back requires a separate review of release contents, target branches,
repository URLs, environments, secrets and external service destinations.

The active workflows are tests/build checks. They do not publish a release, deploy
Pages or send release announcements. The local preservation process does not
change the destination fork's remote Actions/Pages settings or upload anything.
