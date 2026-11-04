# Policy file

`auth-api` decides who may call 22 of its routes with one rule per action. The built-in rules are the checks those routes had before the file existed (admin, or admin and domain admin). The optional file `/etc/cloudx/policy.yaml` replaces the rule of any action you list in it, so an operator can open or close an action without a code change. This page covers the file, the rule language, the 22 actions, reloading, what happens when the file is wrong, and what the file cannot do.

## Where the file lives

- `/etc/cloudx/policy.yaml`, mode 640, owner `root`, group `cloudx` (the service user is `cloudx`, and the `.deb` packages make `/etc/cloudx` `root:cloudx` 750). The `.deb` packages never create or overwrite this file; it is yours.
- Set the environment variable `CLOUDX_POLICY_FILE` in the service's environment to use another path. `cloudx-manage policy check` without a file name reads the same variable.
- It has to exist, with the same content, on every node that runs `auth-api`. Nothing copies it between nodes. A node without the file runs the built-in rules.
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

An entry whose key is not one of the 22 actions is a named rule that other rules can use with `rule:NAME`:

```yaml
ops: "role:admin or domain_admin:True"
identity:user:list: "rule:ops"
```

That pair is the same as the built-in rule of `identity:user:list`; it shows how the reference works. A named rule that no rule uses is reported as a probable typo (WARNING). Rules that reference each other in a cycle, a reference to a name that does not exist, and chains of more than 10 `rule:` references in a row are errors.

## The 22 actions

All of them are `auth-api` routes. The `Route` column is the route that the action gates; the policy is checked after the token is validated and before the request body is.

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

"Domain admin" means a token whose `domain_admin` claim is true (a user flagged as domain admin; an admin passes through `role:admin`). The other 11 actions are admin only by default, because they act across domains or on the service catalog.

These checks run before the policy and a rule cannot change them: a missing, invalid, expired or revoked token is `401`, a token with the `reader` role is refused (`403 reader role is read-only`) on every method except `GET`, `HEAD` and `OPTIONS` (logout stays allowed), and a token issued from a trust is refused (`403 trust tokens cannot modify identity`) on every call that changes something.

A denied request answers `403` with `{"detail": "Policy does not allow <action>"}`.

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

These three examples were run together against `auth-api`: a domain admin got `403 Policy does not allow identity:project:delete` on `DELETE /projects/{ref}`, an admin got 403 on `GET /audit-logs`, and `GET /users` answered 200 to an admin and to a domain admin and 403 to a member.

## Reload without a restart

The service looks at the file at most every 2 seconds (on the first request after that interval). It compares a stamp of the file (modification time, change time, size and inode) with the one it remembers and re-reads the file when the stamp differs. An edit, an atomic replace, a truncation and a permission fix (`chmod`, `chown`) are all noticed; no restart is needed.

If the file disappears, the next check goes back to the built-in rules. If it is there again later, it is loaded again.

Run `cloudx-manage policy check` before you save the file where the service reads it.

Every worker process of every node keeps its own clock and checks the file on its own (at most every 2 seconds), so after an edit workers and nodes can disagree for about two seconds, and each worker process logs a file error once.

## When the file is wrong

The service never fails to start because of this file. Problems go to the logger `cloudx-policy` (ERROR or WARNING, so in `journalctl -u cloudx-auth-api` for a systemd unit). Each process logs them once for each change of the file: a broken file is not logged again at every check.

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

`cloudx-manage policy check [FILE]` loads a file the way the service does and prints one line per problem, then one `differs from the default:` line for each action whose rule in the file is in effect and not the same as the built-in one. Without `FILE` it checks the configured file (`CLOUDX_POLICY_FILE` or `/etc/cloudx/policy.yaml`). The exit status is 1 when it printed a problem line (an ERROR or a WARNING of the table above) and 0 otherwise. It does not log, and it does not change the rules of a running service.

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
# identity:endpoint:delete: "role:admin"
# identity:project:quota-set: "role:admin"
# identity:audit-log:list: "role:admin"
```

Run `check` on every node after you change the file there.

`check` reads the file with the permissions of whoever runs it. Run as root it cannot see a permission problem of the service user (`cloudx`, the user of the `auth-api` unit): a file that root can read but `cloudx` cannot would be reported as fine, while the service logs `cannot be read`. Check as the service user:

```
sudo -u cloudx cloudx-manage policy check
```

## The API

`GET /policy` returns the effective rule of each of the 22 actions and where it comes from:

```json
{"policy": {"identity:user:create": "role:admin or domain_admin:True", "...": "..."},
 "sources": {"identity:user:create": "default", "...": "..."}}
```

`sources` is `"file"` for an action whose rule comes from the file (even when the text equals the built-in rule) and `"default"` otherwise. Named rules are not listed.

`POST /policy/enforce` says whether the caller's own token may perform an action. It takes a valid token like every `auth-api` route, and as a `POST` it is also subject to the checks above: a token with the `reader` role gets `403 reader role is read-only`, and a token issued from a trust is refused (`403 trust tokens cannot modify identity`). `GET /policy` has no such limit: any valid token, including a reader's and a trust's, can read it.

```json
{"action": "identity:user:create"}
```

```json
{"action": "identity:user:create", "allowed": false, "role": "member"}
```

It answers `400 {"detail": "'action' is required"}` when `action` is missing or empty, and `400 {"detail": "unknown action '...'"}` for a name that is not one of the 22 actions or is not a string. A `target_project_id` in the body is accepted and ignored: rules do not look at a target.

## What the file does not do

- It applies to `auth-api` only. The other services keep their own checks, and their actions are not in the table; a later change brings them in.
- It decides whether the caller may call the route. What the handler then returns stays in code and is not in the file: which rows a list shows, "owner or admin" checks, scoping to the caller's domain. There is no per-project policy and no rule that looks at the owner of a resource.
- A rule can open an action to callers for whom the handler has no further scoping. Read the handler before you loosen a rule. For example, `GET /audit-logs` does not filter by caller, so `identity:audit-log:list: "role:member"` lets every member read the audit log of every project; and `GET /users` is scoped by the caller's domain, not by the caller, so opening `identity:user:list` to members shows each member every user of their domain.

  The 11 admin-only actions are worse: their handlers have no domain scoping at all, because they were written for the system admin. Opening one to domain admins lets them act on every domain. For example, `identity:project:quota-set: "role:admin or domain_admin:True"` lets a domain admin change the quota of a project in any domain, and the same goes for `identity:domain:update`, `identity:domain:quota-set` and `identity:domain:quota-clear`, which then work on any domain, and for the catalog and audit-log actions, which are global.
- You can lock yourself out. `"!"`, or a typo in a role name (`role:admn`), closes an action for everyone, admins included, and the change is live within seconds. Keep a root shell open on the node while you edit, and run `cloudx-manage policy check` first: an API session cannot undo a lock-out, because the policy cannot be changed through the API, so only a shell on the node can edit the file back.
- The policy cannot be changed through the API; edit the file.
