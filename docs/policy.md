# Policy file

The file now covers every service's admin-only actions: `auth-api` and the other API services decide who may call 57 actions with one rule per action. 22 of them are `auth-api` routes (the `identity:*` actions); the other 35 are the admin-only routes of compute, block-store, placement, billing, telemetry, networking, object-store and ha. The built-in rules are the checks those routes had before the file existed (admin, or admin and domain admin for some `auth-api` routes). The optional file `/etc/cloudx/policy.yaml` replaces the rule of any action you list in it, so an operator can open or close an action without a code change. This page covers the file, the rule language, the 57 actions, reloading, what happens when the file is wrong, and what the file cannot do.

## Where the file lives

- `/etc/cloudx/policy.yaml`, mode 640, owner `root`, group `cloudx` (the service user is `cloudx`, and the `.deb` packages make `/etc/cloudx` `root:cloudx` 750). The `.deb` packages never create or overwrite this file; it is yours.
- Set the environment variable `CLOUDX_POLICY_FILE` in the service's environment to use another path. `cloudx-manage policy check` without a file name reads the same variable.
- It has to exist, with the same content, on every node that runs any API service (`auth-api`, compute, block-store, placement, billing, telemetry, networking, object-store, ha). Each service reads the file on its own node, and nothing copies it between nodes. A node without the file runs the built-in rules for the services on it. If you already have a file from the time when only `auth-api` read it, copy it to the other API nodes.
- A missing file is normal: the built-in rules apply and nothing is logged.

A commented sample with every built-in rule is installed by `cloudx-common` as `/usr/share/cloudx/policy.yaml.sample`; in the repository and in the release tarball it is `etc/cloudx/policy.yaml.sample`. Copying it unchanged changes nothing, because every line is a comment:

```bash
sudo install -m 640 -o root -g cloudx /usr/share/cloudx/policy.yaml.sample /etc/cloudx/policy.yaml
```

`cloudx-manage policy defaults` prints the same text (see below). Remove the `# ` in front of a line and edit the rule to override that action.

## The file

One YAML mapping: the key is an action, the value is a rule, written as a quoted string.

```yaml
identity:project:delete: "role:admin"
identity:audit-log:list: "!"
```

Only the actions you list change. An action that is not in the file keeps its built-in rule.

### Rule syntax

| Element | Meaning |
|---|---|
| `role:NAME` | the `role` claim of the caller's token is `NAME` (`admin`, `member`, `reader`). Only that claim is compared, not a list of roles |
| `domain_admin:True` | the token carries the domain-admin flag. `domain_admin:False` is true when it does not |
| `rule:NAME` | the result of another entry of the file (a named rule, see below) |
| `@` | always true |
| `!` | always false |
| `and`, `or`, `not`, `( )` | combine the above. Lowercase only, separated from the atoms by spaces; `not` binds tightest, then `and`, then `or` |

A rule longer than 200 tokens (words and parentheses) is an error. An empty rule, an unknown word (`is_admin`), a missing name (`role:`) or an unbalanced parenthesis is an error too; the failure table below says what happens then.

A missing claim is treated as absent: `role:x` is false, `domain_admin:True` is false and `domain_admin:False` is true, so `not role:x` is true. A missing claim never raises an error.

### Quote every rule

YAML reads some bare words as something other than text, so quote every rule. `"!"` and `"@"` have to be quoted, and so does any rule that starts with a YAML indicator character:

```yaml
identity:audit-log:list: "!"        # a string: the action is closed
```

An unquoted `!` or `@` makes the whole file invalid, and so does any YAML tag, anchor, alias or merge key (`<<`). The engine does not try to guess what you meant, because PyYAML would read some of these forms as a different rule: `! role:member` loses its `!` and means `role:member`, and a bare `!` is `null`. With any of them the file is ignored: a first load runs every built-in rule, a reload keeps the previous rules, and the engine logs one ERROR that names the line:

```yaml
identity:audit-log:list: !          # a tag, not the text "!"
identity:user:list: @               # '@' cannot start a YAML value
identity:domain:create: ! role:member
```

`cloudx-manage policy check` shows the first problem it finds; for the first line above:

```
policy file: line 1: tags, anchors and aliases are not allowed; put the rule in double quotes ("!" or "@" included)
```

For the second line PyYAML itself stops with `found character '@' that cannot start any token`. A key that appears twice is invalid in the same way, because YAML would keep only the last one and the first would look as if it were in force:

```
policy file: line 2: duplicate entry 'identity:domain:create' (first at line 1)
```

### Named rules

An entry whose key is not one of the 57 actions is a named rule that other rules can use with `rule:NAME`:

```yaml
ops: "role:admin or domain_admin:True"
identity:user:list: "rule:ops"
```

That pair is the same as the built-in rule of `identity:user:list`; it shows how the reference works. A named rule that no rule uses is reported as a probable typo (WARNING). Rules that reference each other in a cycle, a reference to a name that does not exist, and chains of more than 10 `rule:` references in a row are errors.

## The 57 actions

The 22 `identity:*` actions come first, then the 35 actions of the other services, one section each. The `Route` column is the route that the action gates; the policy is checked after the token is validated and before the request body is.

### auth-api: 22 identity actions

| Action | Default rule | Route | Passes by default |
|---|---|---|---|
| `identity:domain:list` | `role:admin or domain_admin:True` | `GET /v3/domains` | admin, domain admin |
| `identity:domain:quota-show` | `role:admin or domain_admin:True` | `GET /v3/domains/{domain_id}/quota` | admin, domain admin |
| `identity:domain:show` | `role:admin or domain_admin:True` | `GET /v3/domains/{domain_id}` | admin, domain admin |
| `identity:project:create` | `role:admin or domain_admin:True` | `POST /projects` | admin, domain admin |
| `identity:project:delete` | `role:admin or domain_admin:True` | `DELETE /projects/{ref}` | admin, domain admin |
| `identity:project:update` | `role:admin or domain_admin:True` | `PUT /projects/{ref}` | admin, domain admin |
| `identity:role-assignment:create` | `role:admin or domain_admin:True` | `PUT /projects/{project}/users/{user}/roles/{role}` | admin, domain admin |
| `identity:role-assignment:delete` | `role:admin or domain_admin:True` | `DELETE /projects/{project}/users/{user}/roles/{role}` | admin, domain admin |
| `identity:user:create` | `role:admin or domain_admin:True` | `POST /users` | admin, domain admin |
| `identity:user:delete` | `role:admin or domain_admin:True` | `DELETE /users/{ref}` | admin, domain admin |
| `identity:user:list` | `role:admin or domain_admin:True` | `GET /users` | admin, domain admin |
| `identity:audit-log:list` | `role:admin` | `GET /audit-logs` | admin |
| `identity:domain:create` | `role:admin` | `POST /v3/domains` | admin |
| `identity:domain:delete` | `role:admin` | `DELETE /v3/domains/{domain_id}` | admin |
| `identity:domain:quota-clear` | `role:admin` | `DELETE /v3/domains/{domain_id}/quota` | admin |
| `identity:domain:quota-set` | `role:admin` | `PUT /v3/domains/{domain_id}/quota` | admin |
| `identity:domain:update` | `role:admin` | `PATCH /v3/domains/{domain_id}` | admin |
| `identity:endpoint:create` | `role:admin` | `POST /v3/endpoints` | admin |
| `identity:endpoint:delete` | `role:admin` | `DELETE /v3/endpoints/{endpoint_id}` | admin |
| `identity:project:quota-set` | `role:admin` | `PUT /projects/{ref}/quota` | admin |
| `identity:service:create` | `role:admin` | `POST /v3/services` | admin |
| `identity:service:delete` | `role:admin` | `DELETE /v3/services/{service_id}` | admin |

"Domain admin" means a token whose `domain_admin` claim is true (a user flagged as domain admin; an admin passes through `role:admin`). The other 11 `identity:*` actions are admin only by default, because they act across domains or on the service catalog.

These checks run before the policy and a rule cannot change them: a missing, invalid, expired or revoked token is `401`, a token with the `reader` role is refused (`403 reader role is read-only`) on every method except `GET`, `HEAD` and `OPTIONS` (logout stays allowed), and a token issued from a trust is refused (`403 trust tokens cannot modify identity`) on every call that changes something.

A denied request answers `403` with `{"detail": "Policy does not allow <action>"}`. This is the text of all 57 actions.

### The other services: 35 admin-only actions

Every one of them has the built-in rule `role:admin`, the exact check the route had before (`role` claim equal to `admin`; `domain_admin` was never looked at). A domain admin who is not an admin gets `403` on all of them until a rule says otherwise, for example `role:admin or domain_admin:True`. The internal token (`X-Internal-Token`, used by the HA monitor) counts as a token with `role: admin` on the two routes that accept it, `compute:service:update` and `compute:server:evacuate`. A service that states its caller's identity in `X-Caller-Claims` (compute, towards networking and image-api) is judged as that identity, not as that token.

The same checks run before the policy on every service and a rule cannot change them: an invalid or missing token is `401`, a token with the `reader` role gets `403 reader role is read-only` on `POST`, `PUT`, `PATCH` and `DELETE` (so no rule can open one of these actions to a reader), and a domain-scoped token is refused with `403 project-scoped token required` by every service except `auth-api` and billing, unless that service's environment sets `ACCEPT_DOMAIN_SCOPE=true`. Where it is refused, a domain admin can use `domain_admin:True` in a rule only with a project-scoped token.

#### compute

| Action | Route | Built-in rule |
|---|---|---|
| `compute:flavor:create` | `POST /flavors` | `role:admin` |
| `compute:flavor:delete` | `DELETE /flavors/{ref}` | `role:admin` |
| `compute:flavor-extra-spec:set` | `POST /flavors/{ref}/os-extra_specs` | `role:admin` |
| `compute:flavor-extra-spec:delete` | `DELETE /flavors/{ref}/os-extra_specs/{key}` | `role:admin` |
| `compute:aggregate:create` | `POST /os-aggregates` | `role:admin` |
| `compute:aggregate:update` | `PUT /os-aggregates/{agg_ref}` | `role:admin` |
| `compute:aggregate:delete` | `DELETE /os-aggregates/{agg_ref}` | `role:admin` |
| `compute:aggregate:action` | `POST /os-aggregates/{agg_ref}/action` | `role:admin` |
| `compute:quota:set` | `PUT /os-quota-sets/{project}` | `role:admin` |
| `compute:service:update` | `PUT /os-services/{host}` | `role:admin` |
| `compute:server:migrate` | `POST /servers/{ref}/migrate` | `role:admin` |
| `compute:server:evacuate` | `POST /servers/{ref}/evacuate` | `role:admin` |

`compute:aggregate:action` is one action for the three sub-actions of that route (`add_host`, `remove_host`, `set_metadata`).

#### block-store

| Action | Route | Built-in rule |
|---|---|---|
| `block-store:volume-type:create` | `POST /volume-types` | `role:admin` |
| `block-store:volume-type:update` | `PATCH /volume-types/{ref}` | `role:admin` |
| `block-store:volume-type:delete` | `DELETE /volume-types/{ref}` | `role:admin` |
| `block-store:quota:set` | `PUT /os-quota-sets/{project_id}` | `role:admin` |
| `block-store:quota:clear` | `DELETE /os-quota-sets/{project_id}` | `role:admin` |
| `block-store:volume:force-detach` | `POST /volumes/{ref}/force-detach` | `role:admin` |
| `block-store:volume:force-delete` | `POST /volumes/{ref}/action` with `os-force_delete` | `role:admin` |
| `block-store:volume:reset-status` | `POST /volumes/{ref}/action` with `os-reset_status` | `role:admin` |

The last two are sub-actions of one route; its other sub-actions (`os-migrate_volume`, `os-extend_volume`, `revert`, `os-retype`) are not in the file and keep whatever checks they had (ownership, volume state). The two are decided after the volume is found, so a member who asks for another project's volume still gets `404`, and `403 Policy does not allow block-store:volume:force-delete` (or `...:reset-status`) on a volume of their own.

#### placement

| Action | Route | Built-in rule |
|---|---|---|
| `placement:resource-provider:create` | `POST /resource_providers` | `role:admin` |
| `placement:resource-provider:delete` | `DELETE /resource_providers/{rp_uuid}` | `role:admin` |
| `placement:inventory:set` | `PUT /resource_providers/{rp_uuid}/inventories` | `role:admin` |
| `placement:allocation:create` | `POST /allocations` | `role:admin` |
| `placement:allocation:delete` | `DELETE /allocations/{consumer_uuid}` | `role:admin` |

#### billing

| Action | Route | Built-in rule |
|---|---|---|
| `billing:rating-sheet:create` | `POST /billing/rating-sheets` | `role:admin` |
| `billing:rating-sheet:update` | `PATCH /billing/rating-sheets/{sheet_id}` | `role:admin` |
| `billing:usage-record:create` | `POST /billing/usage-records` | `role:admin` |

#### telemetry

| Action | Route | Built-in rule |
|---|---|---|
| `telemetry:metric:create` | `POST /telemetry/metrics` | `role:admin` |
| `telemetry:alarm:evaluate` | `POST /telemetry/alarms/evaluate` | `role:admin` |

#### networking

| Action | Route | Built-in rule |
|---|---|---|
| `networking:qos-policy:delete` | `DELETE /qos/policies/{policy_id}` | `role:admin` |
| `networking:subnet-pool:delete` | `DELETE /subnetpools/{pool_id}` | `role:admin` |

Creating a QoS policy or a subnet pool stays open to members; only the delete is in the file.

#### object-store

| Action | Route | Built-in rule |
|---|---|---|
| `object-store:bucket-type:create` | `POST /admin/bucket-types` | `role:admin` |
| `object-store:bucket-type:delete` | `DELETE /admin/bucket-types/{ref}` | `role:admin` |

#### ha

| Action | Route | Built-in rule |
|---|---|---|
| `ha:host:reset` | `POST /hosts/{host}/reset` | `role:admin` |


## Examples

Domain admins can no longer delete projects (admins still can):

```yaml
identity:project:delete: "role:admin"
```

Close the audit log for everyone, admins included:

```yaml
identity:audit-log:list: "!"
```

`GET /audit-logs` then answers `403 Policy does not allow identity:audit-log:list` to an admin as well.

A named rule and a reference to it (same effect as the default):

```yaml
ops: "role:admin or domain_admin:True"
identity:user:list: "rule:ops"
```

Let members delete QoS policies of networking (admins still can):

```yaml
networking:qos-policy:delete: "role:admin or role:member"
```

The file is the same for every service: a node that runs `networking-api` needs it, an `auth-api` node reads the line (`GET /policy` reports it) but enforces nothing on it. Without the line a member gets `403 Policy does not allow networking:qos-policy:delete`; the networking tests check that a rule of `role:member` for this action turns that into `204`.

The first three examples were run together against `auth-api`: a domain admin got `403 Policy does not allow identity:project:delete` on `DELETE /projects/{ref}`, an admin got 403 on `GET /audit-logs`, and `GET /users` answered 200 to an admin and to a domain admin and 403 to a member.

## Reload without a restart

The service looks at the file at most every 2 seconds (on the first request after that interval). It compares a stamp of the file (modification time, change time, size and inode) with the one it remembers and re-reads the file when the stamp differs. An edit, an atomic replace, a truncation and a permission fix (`chmod`, `chown`) are all noticed; no restart is needed.

If the file disappears, the next check goes back to the built-in rules. If it is there again later, it is loaded again.

Run `cloudx-manage policy check` before you save the file where the service reads it.

Every worker process of every node keeps its own clock and checks the file on its own (at most every 2 seconds), so after an edit workers and nodes can disagree for about two seconds, and each worker process logs a file error once.

## When the file is wrong

The service never fails to start because of this file. Problems go to the logger `cloudx-policy` (ERROR or WARNING, so in the journal of the service's unit, for example `journalctl -u cloudx-auth-api`). Each process logs them once for each change of the file: a broken file is not logged again at every check.

| Problem | What applies | Logged |
|---|---|---|
| A rule does not parse (unknown word, missing name, unbalanced parenthesis, more than 200 tokens), refers to an unknown rule, is part of a cycle or is nested too deeply | the built-in rule of that action (a named rule is dropped, and so is every rule that uses it); the other entries still apply | ERROR |
| A value is not a string (a number, `null`, a list) | the built-in rule of that action | ERROR (it names the type of the value, never the value, and says to put the rule in quotes) |
| A key is not a string | that entry is ignored | ERROR |
| An entry whose name is not an action and that no rule uses | nothing (a typo in an action name leaves the default in force) | WARNING |
| The file cannot be read (permissions, not UTF-8) | the built-in rules on the first load; on a reload, the last good rules | ERROR |
| The path is not a regular file (a directory, a named pipe), or the file is larger than 1 MiB | as for an unreadable file; the file is not opened (not parsed, for the size) | ERROR |
| The file is not valid YAML (an unquoted `@`, for example) | as for an unreadable file | ERROR |
| The file has a YAML tag, anchor or alias (an unquoted `!`, `! role:member`, `!!str`, `&a`, `*a`), a merge key (`<<`), or the same key twice | as for an unreadable file: the whole file is ignored | ERROR (it names the line) |
| The file is valid YAML but not a mapping (a list, a plain string) | as for an unreadable file | ERROR |
| The file is empty, or only comments | the built-in rules | nothing |
| The file does not exist | the built-in rules | nothing |

## `cloudx-manage policy`

`cloudx-manage policy check [FILE]` loads a file the way the service does and prints one line per problem, then one `differs from the default:` line for each action whose rule in the file is in effect and not the same as the built-in one. Without `FILE` it checks the configured file (`CLOUDX_POLICY_FILE` or `/etc/cloudx/policy.yaml`). The exit status is 1 when it printed a problem line (an ERROR or a WARNING of the table above) and 0 otherwise. It does not log, and it does not change the rules of a running service. `/etc/cloudx` is `root:cloudx` with mode 750, so run the command with `sudo` (or as a user in the `cloudx` group) to check the installed file: a plain user gets `cannot be read: [Errno 13] Permission denied` followed by a hint line, and exit status 1.

A good file (the three examples above):

```
$ cloudx-manage policy check good.yaml
differs from the default: identity:user:list: rule:ops
differs from the default: identity:project:delete: role:admin
differs from the default: identity:audit-log:list: !
$ echo $?
0
```

A file with mistakes (an unknown word, a misspelled action, a number as a rule, and a good rule):

```
$ cloudx-manage policy check bad.yaml
policy file: rule for 'identity:user:create' ignored, the default applies: unknown atom 'is_admin'
policy file: rule for 'identity:domain:list' ignored, the default applies: the value is of type int, not a string; put the rule in double quotes, for example "role:admin"
policy file: 'identity:user:lst' is not an action and no rule uses it (typo?)
differs from the default: identity:project:delete: role:admin
differs from the default: identity:audit-log:list: !
$ echo $?
1
```

A file that does not exist, and an empty one:

```
$ cloudx-manage policy check /etc/cloudx/policy.yaml
/etc/cloudx/policy.yaml: not found, the defaults apply
$ cloudx-manage policy check empty.yaml
empty.yaml: no overrides
```

`cloudx-manage policy defaults` prints the built-in rules as a commented YAML file, the same text as the sample. The last lines are the rules:

```
$ cloudx-manage policy defaults | tail -3
# telemetry:metric:create: "role:admin"
# telemetry:alarm:evaluate: "role:admin"
# ha:host:reset: "role:admin"
```

Run `check` on every node that runs an API service after you change the file there.

`check` reads the file with the permissions of whoever runs it. Run as root it cannot see a permission problem of the service user (`cloudx`, the user of the service units): a file that root can read but `cloudx` cannot would be reported as fine, while the service logs `cannot be read`. Check as the service user:

```
sudo -u cloudx cloudx-manage policy check
```

## The API

`GET /policy` and `POST /policy/enforce` are routes of `auth-api`. They answer for all 57 actions, those of the other services included: `auth-api` reads the same file and evaluates the rule of the action, without calling the other service. `GET /policy` returns the effective rule of each of the 57 actions and where it comes from:

```json
{"policy": {"identity:user:create": "role:admin or domain_admin:True", "compute:flavor:create": "role:admin", "...": "..."},
 "sources": {"identity:user:create": "default", "compute:flavor:create": "default", "...": "..."}}
```

`sources` is `"file"` for an action whose rule comes from the file (even when the text equals the built-in rule) and `"default"` otherwise. Named rules are not listed. The answer is what the file on the `auth-api` node says; a node of another service with a different file would decide differently (see above).

`POST /policy/enforce` says whether the caller's own token may perform an action. It takes a valid token like every `auth-api` route, and as a `POST` it is also subject to the checks above: a token with the `reader` role gets `403 reader role is read-only`, and a token issued from a trust is refused (`403 trust tokens cannot modify identity`). `GET /policy` has no such limit: any valid token, including a reader's and a trust's, can read it.

```json
{"action": "identity:user:create"}
```

```json
{"action": "identity:user:create", "allowed": false, "role": "member"}
```

The same call with `{"action": "compute:flavor:create"}` evaluates the rule of that action against the caller's token and answers the same way, `{"action": "compute:flavor:create", "allowed": false, "role": "member"}` for a member. It does not call compute or look at its route: it only evaluates the rule against the token you send. So it answers for the action alone (not for a second door such as placement's `/internal/...` routes), and it cannot tell what the internal token would get on `compute:service:update`, because it only takes a user token.

It answers `400 {"detail": "'action' is required"}` when `action` is missing or empty, and `400 {"detail": "unknown action '...'"}` for a name that is not one of the 57 actions or is not a string. A `target_project_id` in the body is accepted and ignored: rules do not look at a target.

## What the file does not do

- It decides whether the caller may call the route. What the handler then returns stays in code and is not in the file: which rows a list shows, "owner or admin" checks, scoping to the caller's domain. There is no per-project policy and no rule that looks at the owner of a resource.
- Only pure role gates are in the file: the 22 `identity:*` actions and the 35 actions above. Everything else stays in code and no rule changes it:
  - ownership and visibility checks (a member sees and changes the resources of their own project);
  - state checks that an admin bypasses (a locked server, a quota that its own project may read);
  - the internal routes (`/internal/...`, `X-Internal-Token`), which workers and monitors call;
  - the four bulk routes of billing and telemetry (`POST /billing/usage-records/batch`, `POST /billing/cleanup`, `POST /telemetry/metrics/batch`, `DELETE /telemetry/metrics/cleanup`). They accept an admin or the internal token and are service-to-service calls: closing them would stop the billing and telemetry workers, so they have no action.
- A rule can open an action to callers for whom the handler has no further scoping. Read the handler before you loosen a rule. For example, `GET /audit-logs` does not filter by caller, so `identity:audit-log:list: "role:member"` lets every member read the audit log of every project; and `GET /users` is scoped by the caller's domain, not by the caller, so opening `identity:user:list` to members shows each member every user of their domain.

  The 11 admin-only `identity:*` actions are worse: their handlers have no domain scoping at all, because they were written for the system admin. Opening one to domain admins lets them act on every domain. For example, `identity:project:quota-set: "role:admin or domain_admin:True"` lets a domain admin change the quota of a project in any domain, and the same goes for `identity:domain:update`, `identity:domain:quota-set` and `identity:domain:quota-clear`, which then work on any domain, and for the catalog and audit-log actions, which are global.

  The 35 actions of the other services have the same property: they were written for the system admin, have no domain scoping, and a domain admin who is allowed acts on every project and host of the cloud.
- You can lock yourself out. `"!"`, or a typo in a role name (`role:admn`), closes an action for everyone, admins included, and the change is live within seconds. Keep a root shell open on the node while you edit, and run `cloudx-manage policy check` first: an API session cannot undo a lock-out, because the policy cannot be changed through the API, so only a shell on the node can edit the file back.
- The policy cannot be changed through the API; edit the file.

### Lock-outs that break the platform

The rules are what they say: `"!"` closes an action for the internal token as well, and no rule is refused for being unwise. `cloudx-manage policy check` lists every rule that differs from the default. Four cases are worth knowing before you edit:

- **HA recovery uses the internal token.** The HA monitor calls `PUT /os-services/{host}` (`compute:service:update`, to mark a dead host `forced_down`) and then `POST /servers/{ref}/evacuate` (`compute:server:evacuate`) with the internal token. The token is turned into claims with `role: admin` and evaluated by the same rule as a user's token. A rule of `"!"`, or one without `role:admin` (`role:operator`, `role:member`), makes both calls answer `403`, and a failed host is no longer fenced or evacuated. Keep `role:admin` in these two rules, for example `"role:admin or role:operator"`.
- **`compute:quota:set` needs `identity:project:quota-set` too.** `PUT /os-quota-sets/{project}` on compute checks its own action, then forwards the caller's `Authorization` header to `auth-api` `PUT /projects/{ref}/quota`, which is `identity:project:quota-set`. Both rules have to allow the caller. If you open one and not the other, the call answers `403` from whichever door is closed; when compute allows and `auth-api` refuses, compute passes the `auth-api` answer on.
- **Placement has a second door.** Placement's `/internal/...` routes (`InternalAuth`, called by compute-api and the compute worker with the internal token) write the same resource providers, inventories and allocations as the public routes of `placement:*`. The file governs the public routes only. Closing `placement:inventory:set` does not stop anything from setting inventories through `PUT /internal/resource_providers/{name}/inventories`, and the internal routes keep working whatever the file says.
- **The file has to be the same on every node.** A node that lacks the file, or has an older copy, answers by the built-in rules while the others answer by the file. `GET /policy` shows what the `auth-api` node it reaches has loaded; it says nothing about the other nodes.
