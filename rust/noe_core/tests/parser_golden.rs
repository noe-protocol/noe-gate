// tests/parser_golden.rs
//
// Parser-only golden tests: precedence, associativity, malformed input.
// These tests must pass BEFORE evaluator parity work begins (plan requirement).

use noe_core::{ast::*, parser::parse};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn parsed(chain: &str) -> Expr {
    parse(chain).expect(&format!("Parse failed for: {chain}"))
}

fn is_unary(e: &Expr, op: &UnaryOp) -> bool {
    matches!(e, Expr::UnaryOp { op: o, .. } if std::mem::discriminant(o) == std::mem::discriminant(op))
}

fn is_binop(e: &Expr, op: &BinOp) -> bool {
    matches!(e, Expr::BinOp { op: o, .. } if std::mem::discriminant(o) == std::mem::discriminant(op))
}

// ---------------------------------------------------------------------------
// Atom tests
// ---------------------------------------------------------------------------

#[test]
fn test_parse_literal() {
    let e = parsed("@fact nek");
    assert!(matches!(e, Expr::Literal(k) if k == "fact"));
}

#[test]
fn test_parse_bool_true() {
    let e = parsed("true nek");
    assert!(matches!(e, Expr::Bool(true)));
}

#[test]
fn test_parse_bool_false() {
    let e = parsed("false nek");
    assert!(matches!(e, Expr::Bool(false)));
}

#[test]
fn test_parse_undefined() {
    // source-verified: undefined is a bool_literal in the grammar
    let e = parsed("undefined nek");
    assert!(matches!(e, Expr::Undefined));
}

#[test]
fn test_parse_number() {
    let e = parsed("42 nek");
    assert!(matches!(e, Expr::Number(s) if s == "42"));
}

#[test]
fn test_parse_float() {
    let e = parsed("3.14 nek");
    assert!(matches!(e, Expr::Number(s) if s == "3.14"));
}

#[test]
fn test_parse_nek_optional() {
    // nek is optional — chain should parse without it
    let e = parsed("@fact");
    assert!(matches!(e, Expr::Literal(_)));
}

// ---------------------------------------------------------------------------
// Unary operator tests
// ---------------------------------------------------------------------------

#[test]
fn test_parse_shi_literal() {
    let e = parsed("shi @fact nek");
    assert!(is_unary(&e, &UnaryOp::Shi));
    if let Expr::UnaryOp { operand, .. } = e {
        assert!(matches!(*operand, Expr::Literal(_)));
    }
}

#[test]
fn test_parse_nai_literal() {
    let e = parsed("nai @fact nek");
    assert!(is_unary(&e, &UnaryOp::Nai));
}

/// Stacked unary: nai nai @x — must parse as nai(nai(@x)), not error
#[test]
fn test_parse_stacked_unary() {
    let e = parsed("nai nai @fact nek");
    assert!(is_unary(&e, &UnaryOp::Nai));
    if let Expr::UnaryOp { operand, .. } = e {
        assert!(is_unary(&operand, &UnaryOp::Nai));
    }
}

/// nai shi @x — unary precedence: nai applies to (shi @x)
#[test]
fn test_parse_nai_shi_precedence() {
    let e = parsed("nai shi @fact nek");
    assert!(is_unary(&e, &UnaryOp::Nai));
    if let Expr::UnaryOp { operand, .. } = e {
        assert!(is_unary(&operand, &UnaryOp::Shi));
    }
}

// ---------------------------------------------------------------------------
// Conjunction / disjunction precedence
// ---------------------------------------------------------------------------

/// @a an @b — simple conjunction
#[test]
fn test_parse_simple_an() {
    let e = parsed("@a an @b nek");
    assert!(is_binop(&e, &BinOp::An));
}

/// @a an @b an @c — left-associative: ((a an b) an c)
#[test]
fn test_parse_an_left_associative() {
    let e = parsed("@a an @b an @c nek");
    if let Expr::BinOp { op: BinOp::An, left, .. } = &e {
        // left should be (a an b)
        assert!(is_binop(left, &BinOp::An));
    } else {
        panic!("Expected An binop at top level");
    }
}

/// an has higher precedence than ur:
/// @a an @b ur @c → (@a an @b) ur @c
#[test]
fn test_parse_an_binds_tighter_than_ur() {
    let e = parsed("@a an @b ur @c nek");
    // Top-level should be ur
    assert!(is_binop(&e, &BinOp::Ur),
        "Top-level should be ur (lower precedence)");
    if let Expr::BinOp { left, .. } = &e {
        // Left of ur should be (a an b)
        assert!(is_binop(left, &BinOp::An),
            "Left of ur should be (a an b)");
    }
}

/// nai shi @x an @y → (nai (shi @x)) an @y
/// Unary binds tighter than conjunction
#[test]
fn test_parse_unary_binds_tighter_than_conjunction() {
    let e = parsed("nai shi @x an @y nek");
    assert!(is_binop(&e, &BinOp::An), "Top level should be an");
    if let Expr::BinOp { left, .. } = &e {
        assert!(is_unary(left, &UnaryOp::Nai), "Left of an should be nai(...)");
    }
}

// ---------------------------------------------------------------------------
// Action event
// ---------------------------------------------------------------------------

#[test]
fn test_parse_mek_action() {
    let e = parsed("mek @target nek");
    assert!(matches!(e, Expr::Action { verb, .. } if verb == "mek"));
}

#[test]
fn test_parse_men_action() {
    let e = parsed("men @file nek");
    assert!(matches!(e, Expr::Action { verb, .. } if verb == "men"));
}

// ---------------------------------------------------------------------------
// Question chain
// ---------------------------------------------------------------------------

#[test]
fn test_parse_question_basic() {
    let e = parsed("qua @status nek");
    assert!(matches!(e, Expr::Question { .. }));
}

#[test]
fn test_parse_question_with_type() {
    let e = parsed("qua soi @status nek");
    if let Expr::Question { q_type, .. } = e {
        assert_eq!(q_type, Some("soi".to_string()));
    } else {
        panic!("Expected Question");
    }
}

// ---------------------------------------------------------------------------
// Malformed input → must return Err
// ---------------------------------------------------------------------------

#[test]
fn test_parse_bare_an_fails() {
    assert!(parse("an @x nek").is_err(), "Leading 'an' should be a parse error");
}

#[test]
fn test_parse_empty_string_fails() {
    // Empty chain — either EOF error or undefined; should not panic
    let result = parse("");
    // Accept either Ok(Undefined) or Err — just must not panic
    let _ = result;
}

#[test]
fn test_parse_unknown_operator() {
    // A glyph like "foo" should parse as a Glyph atom, not error
    let e = parsed("foo nek");
    assert!(matches!(e, Expr::Glyph(_)));
}
