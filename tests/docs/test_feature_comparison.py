import check_feature_comparison as chk


def _services(tmp_path, **routes):
    for name, source in routes.items():
        app = tmp_path / "services" / name / "app"
        app.mkdir(parents=True)
        (app / "main.py").write_text(source)
    return chk.collect_routes(tmp_path)


def test_every_endpoint_in_the_comparison_is_a_route():
    assert chk.problems() == []


def test_the_checker_still_reads_the_page():
    # a parser that quietly finds nothing would make the test above pass for the wrong reason
    found = list(chk.endpoints())
    assert len(found) > 150
    assert any(methods == [None] for _, _, methods, _ in found)
    assert any(span.startswith("POST /servers/{ref}/start") for _, span, _, _ in found)


def test_the_checker_catches_a_missing_route_and_a_wrong_method(tmp_path):
    routes = _services(tmp_path, compute='''
@router.post("/servers/{ref}/start")
@router.get("/servers/{id}")
@router.post(
    "/servers/{ref}/confirmResize")
@router.post("/servers/{ref}/revertResize")
@router.get("/v3/domains")
''')
    doc = tmp_path / "page.md"
    doc.write_text("""# page
| Feature | Endpoint | Status |
|---|---|:---:|
| start | `POST /servers/{name}/start` | ok |
| show or delete | `GET\\|DELETE /servers/{ref}` | ok |
| resize | `POST /servers/{ref}/confirmResize\\|revertResize` | ok |
| resize | `POST /servers/{ref}/confirmResize\\|dropResize` | gone |
| list | `/v3/domains` | ok |
| list | `/v3/regions` | gone |
| wrong method | `DELETE /servers/{ref}/start` | gone |
| query string | `GET /servers/{ref}?details=true` | ok |
""")
    found = chk.problems(doc, routes)
    assert [f.split(":")[1] for f in found] == ["7", "9", "10"]
    assert "dropResize" in found[0] and "/v3/regions" in found[1]
    assert "path exists with another method" in found[2]


def test_prose_and_other_columns_are_not_read_as_endpoints(tmp_path):
    routes = _services(tmp_path, auth='@router.get("/v3")')
    doc = tmp_path / "page.md"
    doc.write_text("""| Feature | Notes |
|---|---|
| a | the file `/etc/cloudx/cloudx.conf` and `GET` alone and `AUTH_TOKEN_EXPIRE_HOURS` |

Mount `/dev/vg/lv` here.

| Feature | Endpoint | Status |
|---|---|:---:|
| b | Startup | ok |
| c | `AUTH_TOKEN_EXPIRE_HOURS` | ok |
""")
    assert chk.problems(doc, routes) == []
