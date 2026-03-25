// src/ast.rs
//
// Noe AST nodes — minimal stage-1 set.
// Only includes nodes exercised by the 80 conformance vectors.

/// Binary operator
#[derive(Debug, Clone, PartialEq)]
pub enum BinOp {
    An,   // logical AND (conjunction)
    Ur,   // logical OR (disjunction)
    // Temporal
    Kos, Til, Nel, Tel, Xel,
    // Spatial
    En, Lef, Rai, Sup, Bel, Fai, Ban,
    // Relational
    Rel,
    // Other
    Kra, Tra, Fra, Noq,
    // Numeric comparisons
    Lt, Gt, Le, Ge, Eq,
}

/// Unary operator
#[derive(Debug, Clone, PartialEq)]
pub enum UnaryOp {
    Nai, Nex,            // logic NOT, XOR
    Shi, Vek, Sha,       // epistemic: knowledge, belief, certainty
    Tor, Da,             // modal: possibility, necessity
    Nau, Ret, Tri,       // temporal: now, past, future
    Qer, Eni, Sem,       // deontic: permitted, obligatory, forbidden
    Mun, Fiu,            // normative: value alignment
    Vus, Vel,            // delivery
}

/// Expression AST node
#[derive(Debug, Clone)]
pub enum Expr {
    /// @literal — key to look up in context.literals
    Literal(String),
    /// true / false
    Bool(bool),
    /// undefined (surface token)
    Undefined,
    /// number (raw string to preserve Python repr for hashing)
    Number(String),
    /// bare glyph (non-keyword identifier)
    Glyph(String),
    /// demonstrative: dia or doq
    Demonstrative(String),

    /// unary_op operand
    UnaryOp { op: UnaryOp, operand: Box<Expr> },

    /// left op right
    BinOp { op: BinOp, left: Box<Expr>, right: Box<Expr> },

    /// mek/men target
    Action { verb: String, target: Box<Expr> },

    /// qua [q_type] body nek
    Question { q_type: Option<String>, body: Box<Expr> },

    /// cond khi sek guard sek
    Conditional { cond: Box<Expr>, guard: Box<Expr> },
}
