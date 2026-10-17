from .models import AccessKey, Group, Project, User


def _project_to_dict(p: Project) -> dict:
    return {
        "id": p.id, "name": p.name, "description": p.description,
        "enabled": p.enabled, "domain_id": p.domain_id, "created_at": p.created_at.isoformat(),
    }


def _user_to_dict(u: User) -> dict:
    return {
        "id": u.id, "username": u.username, "email": u.email,
        "project_id": u.project_id, "role": u.role,
        "domain_id": u.domain_id, "is_domain_admin": u.is_domain_admin, "enabled": u.enabled, "created_at": u.created_at.isoformat(),
    }


def _group_to_dict(g: Group, members: list[str] = []) -> dict:
    return {
        "id": g.id, "name": g.name, "description": g.description,
        "project_id": g.project_id, "members": members,
        "created_at": g.created_at.isoformat(),
    }


def _key_to_dict(k: AccessKey, secret: str | None = None) -> dict:
    d = {
        "id": k.id, "access_key_id": k.access_key_id,
        "name": k.name, "user_id": k.user_id,
        "project_id": k.project_id, "enabled": k.enabled,
        "created_at": k.created_at.isoformat(),
    }
    if secret:
        d["secret_access_key"] = secret
    return d
