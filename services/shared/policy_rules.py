"""Policy rule language (no I/O): parse a rule string into a tree and evaluate it against a token.

Atoms: role:NAME, domain_admin:True|False, rule:NAME, @ (always true), ! (always false).
Operators: not, and, or (lowercase only) and parentheses. `not` binds tightest, then `and`, then `or`.
"""
import re
from typing import Callable

Expr = tuple  # opaque: ("role", name) | ("domain_admin", bool) | ("rule", name) | ("const", bool)
              #         | ("not", e) | ("and", a, b) | ("or", a, b)


class RuleError(ValueError):
    pass


_TOKEN = re.compile(r"\(|\)|[^\s()]+")
_OPERATORS = {"and", "or", "not"}


def _tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text)


def _atom(word: str) -> Expr:
    if word == "@":
        return ("const", True)
    if word == "!":
        return ("const", False)
    kind, sep, value = word.partition(":")
    if sep and kind in ("role", "rule"):
        if not value:
            raise RuleError(f"missing name in '{word}'")
        return (kind, value)
    if sep and kind == "domain_admin" and value in ("True", "False"):
        return ("domain_admin", value == "True")
    raise RuleError(f"unknown atom '{word}'")


def parse(text: str) -> Expr:
    tokens = _tokenize(text)
    if not tokens:
        raise RuleError("empty rule")
    pos = 0

    def peek() -> str | None:
        return tokens[pos] if pos < len(tokens) else None

    def take() -> str:
        nonlocal pos
        pos += 1
        return tokens[pos - 1]

    def parse_or() -> Expr:
        left = parse_and()
        while peek() == "or":
            take()
            left = ("or", left, parse_and())
        return left

    def parse_and() -> Expr:
        left = parse_not()
        while peek() == "and":
            take()
            left = ("and", left, parse_not())
        return left

    def parse_not() -> Expr:
        if peek() == "not":
            take()
            return ("not", parse_not())
        return parse_primary()

    def parse_primary() -> Expr:
        word = peek()
        if word is None or word in _OPERATORS or word == ")":
            raise RuleError("operator without operand")
        take()
        if word == "(":
            inner = parse_or()
            if peek() != ")":
                raise RuleError("unbalanced parenthesis")
            take()
            return inner
        return _atom(word)

    expr = parse_or()
    if pos < len(tokens):
        if tokens[pos] == ")":
            raise RuleError("unbalanced parenthesis")
        raise RuleError(f"unexpected '{tokens[pos]}' (missing and/or)")
    return expr


def references(expr: Expr) -> set[str]:
    """Names used by every rule:NAME atom in the tree."""
    kind = expr[0]
    if kind == "rule":
        return {expr[1]}
    if kind in ("not", "and", "or"):
        return set().union(*(references(e) for e in expr[1:]))
    return set()


def evaluate(expr: Expr, token: dict, resolve: Callable[[str], bool]) -> bool:
    """Missing or None claims never raise: they are simply false. Errors from resolve() propagate."""
    kind = expr[0]
    if kind == "const":
        return expr[1]
    if kind == "role":
        return token.get("role") == expr[1]
    if kind == "domain_admin":
        return bool(token.get("domain_admin")) is expr[1]
    if kind == "rule":
        return bool(resolve(expr[1]))
    if kind == "not":
        return not evaluate(expr[1], token, resolve)
    if kind == "and":
        return evaluate(expr[1], token, resolve) and evaluate(expr[2], token, resolve)
    return evaluate(expr[1], token, resolve) or evaluate(expr[2], token, resolve)
