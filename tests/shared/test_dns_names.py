from shared.dns_names import bucket_fqdn, fip_fqdn, internal_fqdn, lb_fqdn, slug
from shared.dns_validate import normalize_zone_name


def test_names_match_the_spec():
    assert fip_fqdn("203.0.113.10", "cloud.lab") == "203-0-113-10.compute.cloud.lab"
    assert lb_fqdn("My_LB", "abcdef12-0000", "cloud.lab") == "my-lb-abcdef.elb.cloud.lab"
    assert bucket_fqdn("Data", "11112222-3333", "cloud.lab") == "data-11112222.s3.cloud.lab"
    assert internal_fqdn("Web 1", "demo", "cloud.lab") == "web-1.demo.internal.cloud.lab"


def test_slug_is_always_a_valid_label():
    assert slug("My_LB!") == "my-lb"
    assert slug("---") == "x"
    assert slug("") == "x"
    assert len(slug("a" * 200)) <= 40
    for s in ("..", "a.b", "Ünï", " x "):
        normalize_zone_name(f"{slug(s)}.cloud.lab")  # must not raise
