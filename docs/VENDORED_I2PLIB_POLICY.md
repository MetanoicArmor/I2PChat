# Vendored i2plib Security Policy

## Purpose

This project intentionally vendors `i2plib` instead of installing it from PyPI.
The goal is deterministic behavior across supported Python versions, but it also
moves patch responsibility to this repository.

## Required Maintenance Workflow

1. Review upstream `i2plib` releases and security advisories at least monthly.
2. Compare local `vendor/i2plib/` against upstream baseline and document notable diffs.
3. Re-validate local asyncio compatibility changes after each upstream review.
4. Update `vendor/i2plib/VENDORED_UPSTREAM.json` with the latest review date.

## Provenance Record

The machine-readable provenance file is:

- `vendor/i2plib/VENDORED_UPSTREAM.json`

It must include:

- upstream repository URL and baseline version;
- vendoring strategy marker;
- review cadence and advisory sources;
- last review timestamp in UTC.

## Nix Input Update Policy

For release and CI reproducibility, `flake.lock` is the source of truth for
locked Nix inputs.

When updating Nix dependencies:

1. Run `nix flake lock --update-input nixpkgs --update-input flake-utils`.
2. Review `flake.lock` diff (revisions and timestamps).
3. Run `nix flake check --print-build-logs`.
4. Include a short changelog note in the PR description.
