# Instance Overlays

This folder is intentionally generic in the open-source repo.

If you use private instance overlays, place them locally under
`instances/<instance_key>/` and keep them out of version control.

A private overlay can define the instance-specific baseline that the shared
framework promotes into production:

- `instance.env`: non-secret deploy settings and naming
- `genesis.yaml`: seed Genesis for first bootstrap
- `contracts.yaml`: seed runtime smoke checks for mounted apps
- `seed/operational-plane/`: optional app/domain files copied into the
  instance-local operational plane on first bootstrap

At deploy time the framework copies those seeds into the instance-local
`.instance-state/` directory. After bootstrap, the live instance-specific
state and code live on the EC2 host, not in the shared framework repo.

When no private overlay is present, the framework falls back to the tracked
open-source defaults in the repository root:

- `framework_invariants.yaml`
- `genesis.yaml`
- `contracts.example.yaml`

Business Purpose is not seeded from the repository. The first Purpose for an
instance must be created from the UI after bootstrap.

Recommended workflow for a private instance overlay:

1. Create `instances/<new-key>/`
2. Add `instance.env` and, if needed, `genesis.yaml` and `contracts.yaml`
3. Add any domain-specific bootstrap files under `seed/operational-plane/`
4. Deploy with `INSTANCE_KEY=<new-key> make cdk-deploy`
