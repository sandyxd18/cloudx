import check_doc_commands as chk


def test_every_cloudx_command_in_the_docs_exists():
    assert chk.problems(chk.default_files()) == []


def test_the_checker_catches_a_wrong_command_and_option(tmp_path):
    doc = tmp_path / "x.md"
    doc.write_text("```bash\ncloudx server frobnicate web\ncloudx network create n --cidr 10.0.0.0/24\n"
                   "cloudx -o json server list\ncloudx floating-ip create public-net\n```\n")
    chk.ROOT = tmp_path  # problems() prints paths relative to the repo root
    found = chk.problems([doc])
    assert len(found) == 2
    assert "no subcommand 'frobnicate'" in found[0]
    assert "no option ['--cidr']" in found[1]
