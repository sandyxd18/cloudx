def test_ipv6_drop_acl_has_no_negated_nominal_predicate():
    from pathlib import Path

    src = Path(__file__).parents[2].joinpath('services/networking/app/ovn_cli.py').read_text(encoding='utf-8')
    assert '!nd' not in src.replace('# no `!nd`', '')  # ovn-controller cannot compile it, the drop flow is silently lost
