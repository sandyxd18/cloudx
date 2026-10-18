from shared.migrate import services_dir


def test_services_dir_prefers_cwd_over_nested_copy(tmp_path, monkeypatch):
    for d in (tmp_path, tmp_path / "services"):
        (d / "migrations").mkdir(parents=True)
        (d / "migrations" / "registry.py").write_text("")
    monkeypatch.delenv("CLOUDX_SERVICES_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert services_dir() == tmp_path.resolve()