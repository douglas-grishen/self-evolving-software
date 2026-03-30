# Instance Operations

This document covers the minimum hardening workflow for creating and operating a
new instance without relying on tracked private overlays.

## Before First Deploy

Run the deploy preflight from the repository root:

```bash
make preflight-instance
```

The preflight checks:

- `INSTANCE_KEY` and `CONNECTION_ARN`
- tracked validity for `framework_invariants.yaml`, `genesis.yaml`, and `contracts.example.yaml`
- dangerous defaults such as `INSTANCE_KEY=base` or an open `SSH_CIDR`
- whether you are still using the generic open-source contracts baseline

## Creating a New Instance

Use the scaffold script from the repository root:

```bash
bash scripts/create_instance.sh \
  --instance-key market-radar \
  --connection-arn arn:aws:codeconnections:us-east-1:123456789012:connection/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

This creates a private local overlay under `instances/<instance_key>/`, activates
the instance in `infra/deploy.env`, and runs `make preflight-instance`.

The script does not create a Purpose seed. The first business Purpose must be
defined from the UI after the instance boots.

By default the scaffold script now uses the current checkout's git `origin`
owner/repo and the current local branch as the deploy source. That avoids
silently creating a new instance from `main` while you are still working on a
different branch.

## Runtime Contract Starter Kit

Use [contracts.example.yaml](../contracts.example.yaml) as the starting point for product-specific runtime contracts.

Add contracts when:

- a mounted app has a summary, list, search, or action route the UI depends on
- a payload shape must not silently drift
- the frontend app only works if a backend file and its route markers continue to exist

Recommended minimum:

1. One summary/dashboard probe per mounted app
2. One list or search probe for every app that renders tabular or paginated data
3. One command/action probe for every app that triggers side effects

## Backup

Create a local backup bundle:

```bash
make backup-instance
```

Or choose a target directory:

```bash
make backup-instance BACKUP_DIR=/path/to/backups
```

The backup bundle contains:

- `/opt/evolved-app`
- `/opt/evolved-app/.instance-state`
- `/mnt/pgdata/data`

## Restore

Restores are destructive. Stop using the instance before running them.

```bash
make restore-instance BACKUP_DIR=/path/to/backup FORCE=1
```

If you need to keep the stack running while unpacking files for manual work,
set `STOP_STACK=0` when calling the restore script directly.

## External Alerts

The operational backend can forward critical notifications to an external
webhook. Configure these environment variables in the live deployment:

```bash
APP_NOTIFICATION_WEBHOOK_URL=https://alerts.example.test/endpoint
APP_NOTIFICATION_WEBHOOK_BEARER_TOKEN=optional-token
APP_NOTIFICATION_WEBHOOK_MIN_SEVERITY=critical
APP_NOTIFICATION_WEBHOOK_TIMEOUT_SECONDS=5
```

This is intended for Slack/webhook relays, incident bridges, or automation
systems that should react when the engine becomes blocked.

## Validation Flow For a New Instance

1. Set `INSTANCE_KEY`, `CONNECTION_ARN`, and `SSH_CIDR`.
2. Run `make preflight-instance`.
3. Run `make test-infra`.
4. Deploy the disposable/test instance.
5. Open the UI and define the first Purpose from the Welcome/Purpose screen.
6. Confirm the backlog appears and no unexpected critical blocker notification
   remains after the Purpose is saved.
