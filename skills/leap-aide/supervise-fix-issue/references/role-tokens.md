---
title: Role tokens
---

<div align="center">

**English** | [中文](role-tokens.zh-CN.md)

</div>

# Role tokens

The supervise loop gives each of its five roles its own GitHub credential. Two things follow from that: a comment on the issue says which phase wrote it, and a role that goes wrong cannot act as the supervisor.

## The five roles

| Role | What it writes on GitHub | What it needs |
|---|---|---|
| `supervisor` | The plan, the phase verdicts, the closing summary on the issue. | Issues: write. Pull requests: read. |
| `planner` | The implementation plan, as a comment on the issue. | Issues: write. |
| `implementer` | The branch, the commits, the pull request, the implementation report. | Issues: write. Pull requests: write. Contents: write. |
| `tester` | The test report on the pull request. | Pull requests: write. Contents: read. |
| `reviewer` | The review verdict and its inline findings. | Pull requests: write. Contents: read. |

The preflight checks all five before the loop starts: a role without a working credential is an error, not a warning.

## The config file

The credentials live in the workspace, under a directory that git ignores:

```
<workspace>/.leap-aide/secrets/github/config.yaml    (mode 0600)
<workspace>/.leap-aide/.gitignore                     (ignores the whole tree)
```

`gh_roles.py init` writes both files. It never overwrites an existing config unless `--force` is passed.

The file is a YAML mapping with one entry per role:

```yaml
version: 1
roles:
  supervisor:
    kind: pat
    token: "ghp_..."
  implementer:
    kind: app
    private_key_path: /abs/path/to/app.pem
    app_id: "000000"
    installation_id: "00000000"
```

`kind` selects how the credential is obtained: `pat` reads the `token` field, `app` mints an installation token from the App's fields.

## Personal access tokens

A classic PAT needs the `repo` scope, which covers issues, pull requests and pushing to a private repository. Add `read:org` only when the repository lives in an organization with SSO enabled.

A fine-grained PAT is narrower and easier to revoke. Give it access to the one repository the loop works on, and these permissions:

| What the role does | Repository permission |
|---|---|
| Comment on an issue | Issues: Read and write |
| Comment on, or approve, a pull request | Pull requests: Read and write |
| Push a branch | Contents: Read and write |

Metadata is always granted and does not need to be listed.

## GitHub App installations

An App is the better credential when several people share one repository: the installation token is short-lived, the App's own permissions are the ceiling, and revoking the installation revokes every token at once.

A role switches to an App with `kind: app` and three fields:

| Field | Where it comes from |
|---|---|
| `private_key_path` | The app's settings page, "Private key" → "Generate a private key". Keep the `.pem` outside the repository, mode 0600. |
| `app_id` | The same settings page, "App ID". |
| `installation_id` | "Installations" on the app's settings page, or `gh token installations --key <pem> --app-id <id>`. |

The token itself is never stored. It is minted on demand, each time a command runs as that role:

```bash
gh token generate --key <pem> --app-id <app_id> --installation-id <installation_id> --token-only
```

The `gh-token` extension ships in the LeapAide runtime image. An installation token is valid for one hour, which is long enough for a phase and short enough that a leaked one expires on its own.

## How a credential reaches GitHub

The wrapper injects the credential into the environment of the command it runs, and only there:

```bash
GH_TOKEN=<role token>
GITHUB_TOKEN=<role token>
GH_CONFIG_DIR=<workspace>/.leap-aide/secrets/github/gh/<role>
GH_ROLE=<role name>
```

`GH_TOKEN` is what `gh` authenticates with, `GITHUB_TOKEN` is what the bundled GitHub skills look for, `GH_CONFIG_DIR` keeps each role's `gh` state in a directory of its own, and `GH_ROLE` tells a child which role it is acting as. The token is never printed, never written into the conversation and never stored in a file the agent can read back.

`gh_roles.py show` prints the config with every secret replaced by a placeholder. It is the only way to look at the file from the conversation:

```bash
python3 "${HERMES_SKILL_DIR}/scripts/gh_roles.py" show
```

## Rotation

Rotating a credential is an edit of the config file: nothing is cached, and the next command picks the new value up.

For a PAT, paste the new token into the role's `token:` field and re-run the doctor. For an App, the config does not change while the installation stands — the token is minted per command. When the App's private key is regenerated, point `private_key_path` at the new `.pem` file.

```bash
python3 "${HERMES_SKILL_DIR}/scripts/gh_roles.py" doctor
```

The doctor checks every role against GitHub and prints, for each failure, the page to open and the permission to tick.

## Rules

- The credential never enters the model's context. Use `gh_roles.py run --role <ROLE> -- <command>`; never read the config file, never echo a token, never paste one into a message, a commit or a pull request body.
- The config file stays under `.leap-aide/`, mode 0600, in a directory git ignores. A token that reaches a commit is a leaked token.
- The `GH_TOKEN` in the container's own environment is a separate thing: it belongs to the human who runs LeapAide, not to a role. A role's commands always run through the wrapper, which overrides it.
- Five roles, five identities. One token may be shared between roles, but then every comment carries the same name, and the attribution the loop exists for is gone.
