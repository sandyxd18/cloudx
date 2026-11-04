import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "services"))

from shared.policy_rules import MAX_TOKENS, RuleError, evaluate, parse, references  # noqa: E402


def no_rules(name):
    raise AssertionError(f"unexpected rule lookup: {name}")


def check(text, token, resolve=no_rules):
    return evaluate(parse(text), token, resolve)


def test_atoms():
    assert check("role:admin", {"role": "admin"}) is True
    assert check("role:admin", {"role": "member"}) is False
    assert check("role:domain-admin", {"role": "domain-admin"}) is True
    assert check("domain_admin:True", {"domain_admin": True}) is True
    assert check("domain_admin:True", {"domain_admin": False}) is False
    assert check("domain_admin:False", {}) is True
    assert check("domain_admin:False", {"domain_admin": False}) is True
    assert check("domain_admin:False", {"domain_admin": True}) is False
    assert check("@", {}) is True
    assert check("!", {"role": "admin"}) is False


def test_precedence():
    # a or b and c  ==  a or (b and c)
    text = "role:a or role:b and domain_admin:True"
    assert check(text, {"role": "a"}) is True  # (a or b) and c would be False
    assert check(text, {"role": "b", "domain_admin": True}) is True
    assert check(text, {"role": "b"}) is False
    assert check(text, {"role": "x", "domain_admin": True}) is False
    assert check("@ or ! and !", {}) is True  # @ or (! and !); the other reading is False
    # not a and b  ==  (not a) and b
    text = "not role:a and domain_admin:True"
    assert check(text, {"role": "x", "domain_admin": True}) is True
    assert check(text, {"role": "a", "domain_admin": True}) is False
    assert check(text, {"role": "x"}) is False
    # not (a and b) would be true here; (not a) and b is false
    assert check("not ! and !", {}) is False


def test_parentheses():
    assert check("(@ or !) and !", {}) is False
    assert check("@ or ! and !", {}) is True
    assert check("not (role:a or role:b)", {"role": "c"}) is True
    assert check("not (role:a or role:b)", {"role": "b"}) is False
    assert check("((role:a))", {"role": "a"}) is True
    assert check("(role:a)and(role:b)", {"role": "a"}) is False
    assert check("not not @", {}) is True


def test_rule_reference_calls_resolve():
    seen = []

    def resolve(name):
        seen.append(name)
        return name == "admin_required"

    token = {"role": "member"}
    assert evaluate(parse("rule:admin_required"), token, resolve) is True
    assert evaluate(parse("rule:other"), token, resolve) is False
    assert evaluate(parse("role:member and not rule:admin_required"), token, resolve) is False
    assert seen == ["admin_required", "other", "admin_required"]


def test_resolve_errors_are_not_swallowed():
    def resolve(name):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        evaluate(parse("rule:x"), {}, resolve)


def test_references_lists_names():
    assert references(parse("role:admin")) == set()
    assert references(parse("rule:a")) == {"a"}
    expr = parse("rule:a or (role:x and not rule:b-c) or rule:a")
    assert references(expr) == {"a", "b-c"}


def test_missing_claims_are_false_and_never_raise():
    for token in ({}, {"role": None}, {"role": None, "domain_admin": None}):
        assert check("role:admin", token) is False
        assert check("domain_admin:True", token) is False
        assert check("domain_admin:False", token) is True
        assert check("not role:admin", token) is True


def test_operators_are_case_sensitive_lowercase():
    for text in ("role:a AND role:b", "role:a Or role:b", "NOT role:a"):
        with pytest.raises(RuleError):
            parse(text)


@pytest.mark.parametrize("text", [
    "",
    "   ",
    "role:",
    "rule:",
    "role:admin and",
    "or role:admin",
    "not",
    "(role:admin",
    "role:admin)",
    "()",
    "foo:bar",
    "domain_admin:yes",
    "domain_admin:true",
    "role:admin role:member",
    "and",
])
def test_malformed_rules_raise_rule_error(text):
    with pytest.raises(RuleError):
        parse(text)


def test_rule_error_messages_say_what_is_wrong():
    cases = {
        "": "empty rule",
        "foo:bar": "unknown atom 'foo:bar'",
        "(role:admin": "unbalanced parenthesis",
        "role:admin)": "unbalanced parenthesis",
        "role:admin and": "operator without operand",
    }
    for text, message in cases.items():
        with pytest.raises(RuleError, match=message):
            parse(text)


def test_rule_error_is_a_value_error():
    assert issubclass(RuleError, ValueError)


@pytest.mark.parametrize("text", [
    "(" * 300 + "@" + ")" * 300,
    "(" * 300,
    "not " * 1000 + "@",
    " and ".join(["@"] * 1000),
])
def test_oversized_rules_raise_rule_error_not_recursion_error(text):
    with pytest.raises(RuleError, match="rule too long"):
        parse(text)


def test_rule_of_exactly_max_tokens_still_works():
    assert MAX_TOKENS == 200
    chain = "not @" + " or @" * 99  # 200 tokens, left-nested chain
    assert len(chain.split()) == MAX_TOKENS
    expr = parse(chain)
    assert references(expr) == set()
    assert evaluate(expr, {}, no_rules) is True
    nots = "not " * 199 + "@"  # 200 tokens, deepest unary nesting
    assert check(nots, {}) is False
    nested = "(" * 99 + "@" + ")" * 99  # 199 tokens, deepest parenthesis nesting
    assert check(nested, {}) is True
    with pytest.raises(RuleError, match="rule too long"):
        parse(chain + " or @")
