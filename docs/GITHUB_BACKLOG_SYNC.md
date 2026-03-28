## GitHub backlog sync

This repository includes `scripts/sync_github_backlog.py` to create labels,
milestones, and issues from the roadmap/backlog planning docs.

### What the script creates

- labels used by the backlog
- milestones:
  - `0.6.5 - UX polish`
  - `0.7.0 - Contacts and conversations`
  - `0.8.0 - Trust, delivery, offline clarity`
  - `0.9.0 - Portability, privacy, hardening`
- issue drafts from `ISSUE_BACKLOG.md`

The script is idempotent for bundled titles:
- existing labels are reused;
- existing milestones are reused;
- existing issues are skipped by exact title.

### Required token type

Use a GitHub Personal Access Token or another token that can write to the
target repository issues and milestones.

Recommended options:

- **Classic PAT**
  - scope: `repo`

- **Fine-grained PAT**
  - repository access: select the target repository
  - repository permissions:
    - **Issues**: `Read and write`
    - **Metadata**: `Read-only`

If you want to reuse the same token for other repository automation, broader
permissions may be acceptable, but the script itself only needs enough access
to read the repo and create/update issue-management objects.

### Quick checklist

Before running the script, verify:

- you are authenticated with a token that has write access to the repository;
- issues are enabled for the repository;
- you are targeting the correct repository;
- you are comfortable with the bundled milestone titles and issue titles.

### Run against the default repository

```bash
GITHUB_TOKEN=ghp_your_token_here python3 scripts/sync_github_backlog.py
```

By default, the script targets:

```text
MetanoicArmor/I2PChat
```

### Run against another repository

```bash
GITHUB_REPOSITORY=owner/repo GITHUB_TOKEN=ghp_your_token_here python3 scripts/sync_github_backlog.py
```

### Expected result

If the token has the right permissions, the script will:

1. create any missing labels,
2. create any missing milestones,
3. create any missing issues from the prepared backlog.

### Troubleshooting

#### `403 Forbidden`

Usually means the token does not have sufficient repository permissions for
issues/milestones, or the token belongs to an integration with restricted
write scope.

Check:

- token type and scopes;
- whether the repository is the one intended by `GITHUB_REPOSITORY`;
- whether issues are enabled in repository settings.

#### `404 Not Found`

Usually means one of:

- the repository name is wrong;
- the token does not have access to that repository;
- the repository is private and the token cannot see it.

#### `401 Unauthorized`

Usually means the token is missing, invalid, expired, or malformed.

### Notes

- GitHub milestones are created through the Issues API surface.
- The cloud environment used for planning may have enough permission to push
  commits but still not enough permission to create issues or milestones.
- For that reason, this script is included in the repository as the reliable
  path to apply the prepared planning docs to GitHub.
