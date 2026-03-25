// src/parser.rs  —  Stage 1
//
// Hand-written recursive descent parser for Noe chains.
// Mirrors the Arpeggio PEG grammar in noe_parser.py.
//
// Operator sets sourced from operator_lexicon.py (single source of truth).
//
// Stage 1 implements:
//   atoms:          @literal, true, false, undefined, integers, floats, bare glyphs
//   unary ops:      shi, vek, sha, nai, nex, tor, da, nau, ret, tri, qer, eni, sem, mun, fiu, vus, vel
//   action verbs:   mek, men (action_event)
//   binary (conj):  an, ur, numeric comparisons (<, >, <=, >=, =), kos, til, nel, tel, xel,
//                   en, kra, tra, fra, noq, lef, rai, sup, bel, fai, ban, rel
//   termination:    nek (optional)
//   disjunction:    ur (lower precedence than an)
//   question head:  qua
//
// Stage 2+: demonstratives, guards, scoped expressions, morphology, conditionals

use crate::ast::*;

// ---------------------------------------------------------------------------
// AST nodes (minimal, stage 1)
// ---------------------------------------------------------------------------

// (defined in ast.rs, included here for reference)

// ---------------------------------------------------------------------------
// Tokenizer
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq)]
pub enum Token {
    // Literals
    AtLiteral(String),   // @foo
    BoolTrue,
    BoolFalse,
    Undefined,
    Number(String),      // raw string to preserve Python repr format
    Glyph(String),       // bare word (non-keyword)

    // Unary operators
    Nai, Nex,
    Shi, Vek, Sha,
    Tor, Da,
    Nau, Ret, Tri,
    Qer, Eni, Sem,
    Mun, Fiu,
    Vus, Vel,

    // Action verbs
    Mek, Men,

    // Binary / conjunction operators
    An, Ur,
    Kos, Til, Nel, Tel, Xel,
    En, Kra, Tra, Fra, Noq,
    Lef, Rai, Sup, Bel, Fai, Ban, Rel,
    Lt, Gt, Le, Ge, Eq,  // <, >, <=, >=, =

    // Demonstratives (Stage 2)
    Dia, Doq,

    // Guard / scope
    Khi, Sek,

    // Question
    Qua, Soi, Fek, Kru,

    // Termination
    Nek,

    // Morphology (Stage 4)
    Dot,         // .  (fusion separator)
    MiddleDot,   // ·  (fusion)
    Nei,         // ·nei (inversion suffix)
    Tok,         // tok
    // Al is a glyph (treated as such)

    // Intensity (Stage 4)
    DegreeSign,  // °
    DoubleQuote, // "
    SingleQuote, // '

    // Punctuation
    LParen,
    RParen,
    EOF,
}

pub struct Lexer {
    input: Vec<char>,
    pos: usize,
}

impl Lexer {
    pub fn new(input: &str) -> Self {
        // NFKC normalise + collapse whitespace (matches run_noe_logic entry)
        let normalised = input.split_whitespace().collect::<Vec<_>>().join(" ");
        Self {
            input: normalised.chars().collect(),
            pos: 0,
        }
    }

    fn peek(&self) -> Option<char> {
        self.input.get(self.pos).copied()
    }

    fn advance(&mut self) -> Option<char> {
        let c = self.input.get(self.pos).copied();
        self.pos += 1;
        c
    }

    fn skip_whitespace(&mut self) {
        while self.peek().map(|c| c.is_whitespace()).unwrap_or(false) {
            self.advance();
        }
    }

    fn read_while<F: Fn(char) -> bool>(&mut self, f: F) -> String {
        let mut s = String::new();
        while let Some(c) = self.peek() {
            if f(c) {
                s.push(c);
                self.advance();
            } else {
                break;
            }
        }
        s
    }

    pub fn next_token(&mut self) -> Result<Token, ParseError> {
        self.skip_whitespace();

        match self.peek() {
            None => Ok(Token::EOF),
            Some('@') => {
                self.advance();
                let name = self.read_while(|c| c.is_alphanumeric() || c == '_');
                if name.is_empty() {
                    return Err(ParseError::UnexpectedChar('@'));
                }
                Ok(Token::AtLiteral(name))
            }
            Some('(') => { self.advance(); Ok(Token::LParen) }
            Some(')') => { self.advance(); Ok(Token::RParen) }
            Some('°') => { self.advance(); Ok(Token::DegreeSign) }
            Some('"') => { self.advance(); Ok(Token::DoubleQuote) }
            Some('\'') => { self.advance(); Ok(Token::SingleQuote) }
            Some('<') => {
                self.advance();
                if self.peek() == Some('=') { self.advance(); Ok(Token::Le) } else { Ok(Token::Lt) }
            }
            Some('>') => {
                self.advance();
                if self.peek() == Some('=') { self.advance(); Ok(Token::Ge) } else { Ok(Token::Gt) }
            }
            Some('=') => { self.advance(); Ok(Token::Eq) }
            Some('·') => {
                self.advance();
                // Check for ·nei
                let saved = self.pos;
                let word = self.read_while(|c| c.is_alphabetic());
                if word == "nei" {
                    Ok(Token::Nei)
                } else {
                    // Rewind and emit MiddleDot
                    self.pos = saved;
                    Ok(Token::MiddleDot)
                }
            }
            Some(c) if c == '+' || c == '-' || c.is_ascii_digit() => {
                self.lex_number()
            }
            Some(c) if c.is_alphabetic() => self.lex_word(),
            Some(c) => {
                self.advance();
                Err(ParseError::UnexpectedChar(c))
            }
        }
    }

    fn lex_number(&mut self) -> Result<Token, ParseError> {
        let mut s = String::new();
        if let Some(c @ ('+' | '-')) = self.peek() {
            s.push(c);
            self.advance();
        }
        s.push_str(&self.read_while(|c| c.is_ascii_digit()));
        if self.peek() == Some('.') {
            s.push('.');
            self.advance();
            s.push_str(&self.read_while(|c| c.is_ascii_digit()));
        }
        if let Some(e @ ('e' | 'E')) = self.peek() {
            s.push(e);
            self.advance();
            if let Some(sign @ ('+' | '-')) = self.peek() {
                s.push(sign);
                self.advance();
            }
            s.push_str(&self.read_while(|c| c.is_ascii_digit()));
        }
        Ok(Token::Number(s))
    }

    fn lex_word(&mut self) -> Result<Token, ParseError> {
        let word = self.read_while(|c| c.is_alphabetic() || c == '_');
        Ok(match word.as_str() {
            "true" => Token::BoolTrue,
            "false" => Token::BoolFalse,
            "undefined" => Token::Undefined,
            "nai" => Token::Nai, "nex" => Token::Nex,
            "shi" => Token::Shi, "vek" => Token::Vek, "sha" => Token::Sha,
            "tor" => Token::Tor, "da" => Token::Da,
            "nau" => Token::Nau, "ret" => Token::Ret, "tri" => Token::Tri,
            "qer" => Token::Qer, "eni" => Token::Eni, "sem" => Token::Sem,
            "mun" => Token::Mun, "fiu" => Token::Fiu,
            "vus" => Token::Vus, "vel" => Token::Vel,
            "mek" => Token::Mek, "men" => Token::Men,
            "an" => Token::An, "ur" => Token::Ur,
            "kos" => Token::Kos, "til" => Token::Til,
            "nel" => Token::Nel, "tel" => Token::Tel, "xel" => Token::Xel,
            "en" => Token::En, "kra" => Token::Kra,
            "tra" => Token::Tra, "fra" => Token::Fra,
            "noq" => Token::Noq,
            "lef" => Token::Lef, "rai" => Token::Rai, "sup" => Token::Sup,
            "bel" => Token::Bel, "fai" => Token::Fai, "ban" => Token::Ban,
            "rel" => Token::Rel,
            "dia" => Token::Dia, "doq" => Token::Doq,
            "khi" => Token::Khi, "sek" => Token::Sek,
            "qua" => Token::Qua,
            "soi" => Token::Soi, "fek" => Token::Fek, "kru" => Token::Kru,
            "nek" => Token::Nek,
            "nei" => Token::Nei,
            "tok" => Token::Tok,
            _ => Token::Glyph(word),
        })
    }

    /// Tokenize all tokens (for debugging)
    pub fn tokenize_all(&mut self) -> Result<Vec<Token>, ParseError> {
        let mut tokens = Vec::new();
        loop {
            let t = self.next_token()?;
            let done = t == Token::EOF;
            tokens.push(t);
            if done { break; }
        }
        Ok(tokens)
    }
}

// ---------------------------------------------------------------------------
// Parser errors
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub enum ParseError {
    UnexpectedToken(String),
    UnexpectedChar(char),
    UnexpectedEOF,
    InvalidNumber(String),
}

impl std::fmt::Display for ParseError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ParseError::UnexpectedToken(s) => write!(f, "Unexpected token: {s}"),
            ParseError::UnexpectedChar(c) => write!(f, "Unexpected character: {c}"),
            ParseError::UnexpectedEOF => write!(f, "Unexpected end of input"),
            ParseError::InvalidNumber(s) => write!(f, "Invalid number: {s}"),
        }
    }
}

// ---------------------------------------------------------------------------
// Parser — recursive descent
// ---------------------------------------------------------------------------

pub struct Parser {
    tokens: Vec<Token>,
    pos: usize,
}

impl Parser {
    pub fn new(input: &str) -> Result<Self, ParseError> {
        let mut lexer = Lexer::new(input);
        let tokens = lexer.tokenize_all()?;
        Ok(Self { tokens, pos: 0 })
    }

    fn peek(&self) -> &Token {
        self.tokens.get(self.pos).unwrap_or(&Token::EOF)
    }

    fn advance(&mut self) -> &Token {
        let t = &self.tokens[self.pos];
        self.pos += 1;
        t
    }

    /// Consume the next token if it matches expected, else return a parse error.
    /// Scaffolding for future grammar extensions.
    #[allow(dead_code)]
    fn expect(&mut self, expected: &Token) -> Result<(), ParseError> {
        if self.peek() == expected {
            self.advance();
            Ok(())
        } else {
            Err(ParseError::UnexpectedToken(format!("{:?}", self.peek())))
        }
    }

    /// Parse a full chain:
    ///   chain = question_chain | (expression nek? EOF)
    pub fn parse_chain(&mut self) -> Result<Expr, ParseError> {
        if self.peek() == &Token::Qua {
            return self.parse_question_chain();
        }
        let expr = self.parse_expression()?;
        // Optional nek
        if self.peek() == &Token::Nek {
            self.advance();
        }
        if self.peek() != &Token::EOF {
            return Err(ParseError::UnexpectedToken(format!(
                "Expected EOF, got {:?}", self.peek()
            )));
        }
        Ok(expr)
    }

    /// question_chain = "qua" question_body nek EOF
    fn parse_question_chain(&mut self) -> Result<Expr, ParseError> {
        self.advance(); // consume qua
        let q_type = match self.peek() {
            Token::Soi => { self.advance(); Some("soi") }
            Token::Fek => { self.advance(); Some("fek") }
            Token::Kru => { self.advance(); Some("kru") }
            _ => None,
        };
        let body = self.parse_expression()?;
        if self.peek() == &Token::Nek {
            self.advance();
        }
        Ok(Expr::Question { q_type: q_type.map(|s| s.to_string()), body: Box::new(body) })
    }

    /// expression = conditional
    fn parse_expression(&mut self) -> Result<Expr, ParseError> {
        self.parse_conditional()
    }

    /// conditional = disjunction ("khi" sek_scope)?
    fn parse_conditional(&mut self) -> Result<Expr, ParseError> {
        let base = self.parse_disjunction()?;
        if self.peek() == &Token::Khi {
            self.advance();
            // expect: sek expression sek
            if self.peek() != &Token::Sek {
                return Err(ParseError::UnexpectedToken(format!("Expected sek after khi")));
            }
            self.advance(); // sek
            let guard = self.parse_expression()?;
            if self.peek() != &Token::Sek {
                return Err(ParseError::UnexpectedToken(format!("Expected closing sek")));
            }
            self.advance(); // sek
            return Ok(Expr::Conditional { cond: Box::new(base), guard: Box::new(guard) });
        }
        Ok(base)
    }

    /// disjunction = conjunction ("ur" conjunction)*
    fn parse_disjunction(&mut self) -> Result<Expr, ParseError> {
        let mut left = self.parse_conjunction()?;
        while self.peek() == &Token::Ur {
            self.advance();
            let right = self.parse_conjunction()?;
            left = Expr::BinOp { op: BinOp::Ur, left: Box::new(left), right: Box::new(right) };
        }
        Ok(left)
    }

    /// conjunction = unary (conjunction_op unary | unary)*
    /// Note: implicit juxtaposition (unary unary) is treated as list/structural.
    fn parse_conjunction(&mut self) -> Result<Expr, ParseError> {
        let mut left = self.parse_unary()?;
        loop {
            if let Some(op) = self.peek_conjunction_op() {
                self.advance();
                let right = self.parse_unary()?;
                left = Expr::BinOp { op, left: Box::new(left), right: Box::new(right) };
            } else {
                break;
            }
        }
        Ok(left)
    }

    fn peek_conjunction_op(&self) -> Option<BinOp> {
        match self.peek() {
            Token::An => Some(BinOp::An),
            Token::Kos => Some(BinOp::Kos), Token::Til => Some(BinOp::Til),
            Token::Nel => Some(BinOp::Nel), Token::Tel => Some(BinOp::Tel), Token::Xel => Some(BinOp::Xel),
            Token::En => Some(BinOp::En), Token::Kra => Some(BinOp::Kra),
            Token::Tra => Some(BinOp::Tra), Token::Fra => Some(BinOp::Fra),
            Token::Noq => Some(BinOp::Noq),
            Token::Lef => Some(BinOp::Lef), Token::Rai => Some(BinOp::Rai),
            Token::Sup => Some(BinOp::Sup), Token::Bel => Some(BinOp::Bel),
            Token::Fai => Some(BinOp::Fai), Token::Ban => Some(BinOp::Ban),
            Token::Rel => Some(BinOp::Rel),
            Token::Lt => Some(BinOp::Lt), Token::Gt => Some(BinOp::Gt),
            Token::Le => Some(BinOp::Le), Token::Ge => Some(BinOp::Ge),
            Token::Eq => Some(BinOp::Eq),
            _ => None,
        }
    }

    /// unary = unary_op* primary
    fn parse_unary(&mut self) -> Result<Expr, ParseError> {
        if let Some(op) = self.peek_unary_op() {
            self.advance();
            // Check for action verb (mek/men) which is part of unary in the grammar
            // mek/men consume a unary (not an action_event inside unary) 
            let operand = self.parse_unary()?;
            return Ok(Expr::UnaryOp { op, operand: Box::new(operand) });
        }
        self.parse_primary()
    }

    fn peek_unary_op(&self) -> Option<UnaryOp> {
        match self.peek() {
            Token::Nai => Some(UnaryOp::Nai), Token::Nex => Some(UnaryOp::Nex),
            Token::Shi => Some(UnaryOp::Shi), Token::Vek => Some(UnaryOp::Vek), Token::Sha => Some(UnaryOp::Sha),
            Token::Tor => Some(UnaryOp::Tor), Token::Da => Some(UnaryOp::Da),
            Token::Nau => Some(UnaryOp::Nau), Token::Ret => Some(UnaryOp::Ret), Token::Tri => Some(UnaryOp::Tri),
            Token::Qer => Some(UnaryOp::Qer), Token::Eni => Some(UnaryOp::Eni), Token::Sem => Some(UnaryOp::Sem),
            Token::Mun => Some(UnaryOp::Mun), Token::Fiu => Some(UnaryOp::Fiu),
            Token::Vus => Some(UnaryOp::Vus), Token::Vel => Some(UnaryOp::Vel),
            _ => None,
        }
    }

    /// primary = action_event | scoped | atom
    fn parse_primary(&mut self) -> Result<Expr, ParseError> {
        match self.peek() {
            Token::Mek | Token::Men => self.parse_action_event(),
            Token::LParen => {
                self.advance(); // (
                let expr = self.parse_expression()?;
                if self.peek() != &Token::RParen {
                    return Err(ParseError::UnexpectedToken("Expected )".to_string()));
                }
                self.advance(); // )
                Ok(expr)
            }
            Token::Sek => {
                self.advance(); // sek
                let expr = self.parse_expression()?;
                if self.peek() != &Token::Sek {
                    return Err(ParseError::UnexpectedToken("Expected closing sek".to_string()));
                }
                self.advance();
                Ok(expr)
            }
            _ => self.parse_atom(),
        }
    }

    /// action_event = (mek | men) unary
    fn parse_action_event(&mut self) -> Result<Expr, ParseError> {
        let verb = match self.advance() {
            Token::Mek => "mek",
            Token::Men => "men",
            _ => unreachable!(),
        };
        let target = self.parse_unary()?;
        Ok(Expr::Action { verb: verb.to_string(), target: Box::new(target) })
    }

    /// atom = base (fusion | inversion | morph_suffix)* intensity?
    fn parse_atom(&mut self) -> Result<Expr, ParseError> {
        let base = self.parse_base()?;
        // Stage 1: ignore morphology suffixes (Stage 4)
        // Intensity markers: also stage 4
        Ok(base)
    }

    /// base = @literal | bool_literal | number | demonstrative | glyph
    fn parse_base(&mut self) -> Result<Expr, ParseError> {
        match self.peek().clone() {
            Token::AtLiteral(name) => { self.advance(); Ok(Expr::Literal(name)) }
            Token::BoolTrue => { self.advance(); Ok(Expr::Bool(true)) }
            Token::BoolFalse => { self.advance(); Ok(Expr::Bool(false)) }
            Token::Undefined => { self.advance(); Ok(Expr::Undefined) }
            Token::Number(s) => {
                self.advance();
                Ok(Expr::Number(s))
            }
            Token::Dia => { self.advance(); Ok(Expr::Demonstrative("dia".to_string())) }
            Token::Doq => { self.advance(); Ok(Expr::Demonstrative("doq".to_string())) }
            Token::Glyph(w) => { self.advance(); Ok(Expr::Glyph(w)) }
            Token::EOF => Err(ParseError::UnexpectedEOF),
            t => Err(ParseError::UnexpectedToken(format!("{:?}", t))),
        }
    }
}

/// Parse a Noe chain string into an AST.
pub fn parse(input: &str) -> Result<Expr, ParseError> {
    let mut parser = Parser::new(input)?;
    parser.parse_chain()
}
