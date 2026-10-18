import pytest

from shared.dns_validate import check_cname_rules, check_record, normalize_name, normalize_zone_name


def test_zone_name_is_normalized_and_validated():
    assert normalize_zone_name("Example.COM.") == "example.com"
    for bad in ("", "com", "-a.com", "a..com", "a_b.com", "x" * 64 + ".com", "a b.com", "a.com\nb"):
        with pytest.raises(ValueError):
            normalize_zone_name(bad)


def test_record_name_is_relative():
    assert normalize_name("WWW") == "www"
    assert normalize_name("@") == "@"
    assert normalize_name("*.api") == "*.api"
    assert normalize_name("_sip._tcp") == "_sip._tcp"
    for bad in ("www.", "a b", "a..b", "-x", "", "w\nww", "a\n@ IN A 1.1.1.1"):
        with pytest.raises(ValueError):
            normalize_name(bad)


def test_record_data_per_type():
    assert check_record("A", "www", ["1.2.3.4", "1.2.3.4"]) == ["1.2.3.4"]
    assert check_record("AAAA", "www", ["2001:DB8::1"]) == ["2001:db8::1"]
    assert check_record("CNAME", "www", ["Target.Example.com"]) == ["target.example.com."]
    assert check_record("MX", "@", ["10 Mail.example.com."]) == ["10 mail.example.com."]
    assert check_record("SRV", "_sip._tcp", ["1 2 5060 sip.example.com"]) == ["1 2 5060 sip.example.com."]
    assert check_record("NS", "sub", ["ns1.example.com"]) == ["ns1.example.com."]
    assert check_record("PTR", "4", ["host.example.com"]) == ["host.example.com."]
    for rtype, data in (("A", "999.1.1.1"), ("A", "::1"), ("AAAA", "1.2.3.4"), ("MX", "x mail.example.com"),
                        ("MX", "70000 mail.example.com"), ("SRV", "1 2 3"), ("CNAME", "a b"),
                        ("TXT", "line1\nline2"), ("TXT", "tab\there"), ("TXT", "caf\u00e9"), ("MX", "10 a.com\n@ IN A 1.1.1.1")):
        with pytest.raises(ValueError):
            check_record(rtype, "x", [data])
    with pytest.raises(ValueError):
        check_record("CNAME", "x", ["a.com", "b.com"])
    with pytest.raises(ValueError):
        check_record("A", "x", [])
    with pytest.raises(ValueError):
        check_record("SOA", "x", ["whatever"])


def test_txt_keeps_quotes_semicolons_and_backslashes_verbatim():
    raw = 'v=spf1 "quoted" ; back\\slash'
    assert check_record("TXT", "@", [raw]) == [raw]


def test_cname_rules():
    check_cname_rules({}, "www", "CNAME", apex=False)
    with pytest.raises(ValueError):
        check_cname_rules({}, "@", "CNAME", apex=True)
    with pytest.raises(ValueError):
        check_cname_rules({"www": {"A"}}, "www", "CNAME", apex=False)
    with pytest.raises(ValueError):
        check_cname_rules({"www": {"CNAME"}}, "www", "A", apex=False)
    check_cname_rules({"www": {"A"}}, "www", "AAAA", apex=False)
