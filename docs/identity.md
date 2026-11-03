# Identity: token scopes, application credential rotation and trusts

These are the `auth-api` calls added after the basic login, access keys and application credentials. Examples use `curl` against the auth API (port 8005 on the controller; set `AUTH=http://<controller>:8005`) and a token in `$TOKEN`. The tables in [feature-comparison.md](feature-comparison.md) list the other Identity endpoints. Two migrations belong to this page (`auth_0008`, `auth_0009`): run `cloudx-manage db sync` after upgrading.

## Token scopes

`POST /auth/tokens` takes an optional `scope` with exactly one of `project`, `domain` or `system`:

```bash
# project scope (the same as the older top-level "project" field)
curl -s -X POST $AUTH/auth/tokens -H 'Content-Type: application/json' \
  -d '{"username": "alice", "password": "...", "scope": {"project": "web"}}'

# domain scope
curl -s -X POST $AUTH/auth/tokens -H 'Content-Type: application/json' \
  -d '{"username": "bob", "password": "...", "scope": {"domain": "d1"}}'

# system scope
curl -s -X POST $AUTH/auth/tokens -H 'Content-Type: application/json' \
  -d '{"username": "admin", "password": "...", "scope": {"system": true}}'
```

`scope.domain` is a domain name or id. The top-level `domain` field is a different thing: it only picks between users of the same name in several domains.

| Scope | Who may ask | The token |
|---|---|---|
| `project` | a member of that project, or an admin (as before) | `scope_type` `project`, the project's id and name |
| `domain` | a domain admin of that domain, or an admin | `scope_type` `domain`, `domain_id` set, `domain_admin` true, **no project** (`project_id` is empty); `role` is the user's own |
| `system` | an admin | `scope_type` `system`, `role` `admin`, the user's home project |

The domain response has `"scope": {"type": "domain", "domain_id": "..."}`; the system response has `"scope": {"type": "system", ...}` next to the usual `project`.

Errors:

- 400: `scope` with none or more than one of the three fields; a top-level `project` together with a domain or system scope; a top-level `project` that differs from `scope.project`.
- 403: a domain scope from a user who is neither admin nor domain admin of that domain. A domain admin who names a domain that does not exist gets the same 403 as for another domain, so the answer does not show which domains exist. An admin gets 404 for an unknown domain. A disabled domain gives 403. A system scope from a non-admin gives 403.

A domain-scoped token has no project, so a service that works per project cannot use it. `auth-api` and `billing-api` accept it; every other service answers `403 project-scoped token required`. On `auth-api`, `POST /groups` and `POST /access-keys` also refuse it. (A service opts in with `ACCEPT_DOMAIN_SCOPE=true` in its environment; `billing-api` sets it itself.)

## Rotating an application credential

```bash
curl -s -X POST $AUTH/v3/users/alice/application_credentials/ci/rotate \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"grace_seconds": 300}'
```

`grace_seconds` is optional (0 to 86400, default 0). The user (name or id) and the credential (name or id) come from the path; a user can rotate their own credentials, an admin anyone's. The answer is the credential as in `GET`, plus the new `secret`, which is shown only here:

```json
{"id": "...", "name": "ci", "user_id": "...", "roles": ["member"], "enabled": true, "secret": "...", ...}
```

During the grace period `POST /auth/tokens/application-credential` accepts the new secret and the one just replaced. With the default 0 the old secret stops working at once. Rotating again inside a grace period ends the older window: only the secret that was current at that moment keeps working.

CLI:

```bash
cloudx auth appcred rotate alice ci --grace-seconds 300
```

It prints the new secret once. This is the only `appcred` command; creating, listing and deleting credentials go through the API (`/v3/users/{user}/application_credentials`).

## Trusts

A trust lets one user (the trustor) hand some of their roles in one project to another user (the trustee). The trustee exchanges it for a token that acts as the trustor.

Create a trust as the trustor:

```bash
curl -s -X POST $AUTH/v3/OS-TRUST/trusts -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"trustee_user_id": "bob", "project_id": "web", "roles": ["member"],
       "expires_at": "2026-12-01T00:00:00Z", "remaining_uses": 5}'
```

| Field | Meaning |
|---|---|
| `trustee_user_id` | user id or username of the trustee |
| `project_id` | project name or id; defaults to the project of the caller's token |
| `roles` | non-empty list without duplicates from `admin`, `member`, `reader`; the first becomes the `role` of the issued token |
| `expires_at` | optional, ISO 8601, in the future |
| `remaining_uses` | optional, at least 1; each exchange uses one. Left out, the trust can be used any number of times |

The answer (201) holds `id`, `trustor_user_id`, `trustee_user_id`, `project_id`, `roles`, `expires_at`, `remaining_uses` and `created_at`.

Rules at creation:

- The trustor is the caller. The caller must be a member of the project, or be an admin (the user's own `role` is `admin`).
- Every role in the list must be one the trustor holds in that project (`reader` < `member` < `admin`): 403 otherwise.
- The trustee must be another, enabled user in the trustor's domain; an admin may name a user of any domain.

The trustee exchanges the trust with their own token:

```bash
curl -s -X POST $AUTH/auth/tokens/trust -H "Authorization: Bearer $TRUSTEE_TOKEN" -H 'Content-Type: application/json' \
  -d '{"trust_id": "<id>"}'
```

The answer (201) is `{"token": "...", "expires_in": ..., "method": "trust"}`. In the token, `sub` is the trustor and the claims `trust_id` and `trustee_id` name the trust and the trustee. The roles are those of the trust that the trustor still holds when the exchange happens; the trustor's domain-admin right is not carried over.

| Answer | When |
|---|---|
| 403 | the caller is not the trustee |
| 401 | the trust id is unknown, or the trust is revoked, expired or out of uses, or the trustor or project is disabled, or the trustor left the project or lost every role of the trust |

Every successful exchange writes an audit entry `trust.token` with the trustor's and the trustee's user ids (`GET /audit-logs`). A request that fails a check does not use up a use.

What a trust token can do:

- On other services it works as the trustor within the delegated roles.
- On `auth-api` it is read-only. Any call other than `GET`, `HEAD` or `OPTIONS` answers `403 trust tokens cannot modify identity`, except logout (`DELETE /auth/tokens/current`). A trust token therefore cannot create trusts, change users, roles or credentials, or exchange another trust.

Other calls:

```bash
curl -s $AUTH/v3/OS-TRUST/trusts -H "Authorization: Bearer $TOKEN"                # trusts you gave or received (an admin sees all)
curl -s $AUTH/v3/OS-TRUST/trusts/<id> -H "Authorization: Bearer $TOKEN"           # trustor, trustee or admin
curl -s -X DELETE $AUTH/v3/OS-TRUST/trusts/<id> -H "Authorization: Bearer $TOKEN" # trustor or admin; 204
```

`DELETE` revokes the trust: it no longer shows in the list and cannot be exchanged. **Tokens already issued from the trust are not revoked.** They stay valid until they expire (`AUTH_TOKEN_EXPIRE_HOURS`, default 24). To bound that, give the trust a short life and a small `remaining_uses`.
