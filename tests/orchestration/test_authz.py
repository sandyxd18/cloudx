import importlib

import pytest
from fastapi import HTTPException

A = {"project_id": "p1", "role": "member"}
B = {"project_id": "p2", "role": "member"}


def test_scaling_group_is_project_scoped(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'orch.sqlite'}")
    import services.orchestration.app.main as m

    m = importlib.reload(m)
    importlib.reload(m.stacks)  # re-register its tables on the fresh Base
    m.Base.metadata.create_all(bind=m.engine)
    with m.SessionLocal() as db:
        g = m.create_scaling_group(A, m.ScalingGroupIn(name="g", image_id="i", flavor_id="f", network_id="n"), db)
        for call in (
            lambda: m.get_scaling_group(B, g.id, db),
            lambda: m.update_scaling_group(B, g.id, m.ScalingGroupUpdate(name="x"), db),
            lambda: m.delete_scaling_group(B, g.id, db),
            lambda: m.manual_scale_out(B, g.id, db),
            lambda: m.manual_scale_in(B, g.id, db),
            lambda: m.list_group_instances(B, g.id, db),
            lambda: m.get_scaling_history(B, g.id, db),
        ):
            with pytest.raises(HTTPException) as e:
                call()
            assert e.value.status_code == 404
        assert m.get_scaling_group(A, g.id, db).id == g.id
        assert m.get_scaling_group({"project_id": "x", "role": "admin"}, g.id, db).id == g.id
