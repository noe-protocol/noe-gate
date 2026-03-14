import json
from dataclasses import dataclass
import hashlib
import traceback
import copy
import os
import threading  # Thread safety for AST cache
from typing import Dict, Any, Union, Optional
from collections import OrderedDict
from collections.abc import Mapping
from arpeggio import ParserPython, PTNodeVisitor, visit_parse_tree, ZeroOrMore, Optional as PEGOptional, EOF
from arpeggio import RegExMatch as _
# Note: Import loop resolution.
# noe_validator imports pi_safe (context_projection)
# context_projection imports nothing from here.
# This local import ensures noe_validator is fully initialized before we run validation.
from .noe_validator import (
    validate_chain,
    compute_context_hashes,
    validate_context_strict,
    DEFAULT_CONTEXT_PARTIAL,
    check_grounding,
    validate_ast_safety
)
from .context_requirements import CONTEXT_REQUIREMENTS
from .provenance import compute_action_hash, OUTCOME_FIELDS
from .canonical import canonical_json, canonical_literal_key, canonical_bytes

# ==========================================
# PERFORMANCE: AST CACHING
# ==========================================
# Cache parsed ASTs to avoid re-parsing identical chains
# Expected speedup: -220µs (parse time) for repeated chains

# Thread-safe cache with lock
_AST_CACHE = OrderedDict()
_AST_CACHE_MAX_SIZE = 1000
_AST_CACHE_LOCK = threading.Lock()
_PARSER_LOCK = threading.Lock()

# Grammar version tracking for cache invalidation
GRAMMAR_VERSION = "1.0.0"  # Increment when grammar changes

# Global parser instance for cache safety
# Reusing same parser ensures cache keys are consistent
_GLOBAL_PARSER = None
_GRAMMAR_HASH = None

def _get_or_create_parser():
    """Get global parser instance, creating it if needed."""
    global _GLOBAL_PARSER, _GRAMMAR_HASH
    # Thread-safe parser creation
    with _PARSER_LOCK:
        if _GLOBAL_PARSER is None:
            # Direct reference to chain() function (defined later in this module)
            # No self-import needed - Python allows forward references
            _GLOBAL_PARSER = ParserPython(chain, ignore_case=False)
            
            # Compute grammar hash for cache key combining version and file contents
            # This ensures any changes to grammar functions automatically invalidate cache
            try:
                with open(__file__, "rb") as f:
                    file_content = f.read()
            except Exception:
                try:
                    import inspect
                    # Hash the actual parser logic instead of the whole file if __file__ is unavailable or zipped
                    file_content = inspect.getsource(chain).encode('utf-8')
                except Exception:
                    file_content = b""
            
            hasher = hashlib.sha256(GRAMMAR_VERSION.encode())
            hasher.update(file_content)
            _GRAMMAR_HASH = hasher.hexdigest()[:8]
    return _GLOBAL_PARSER

def _get_cached_ast(parser, chain_text):
    """Get cached AST or parse and cache (thread-safe, grammar-versioned)."""
    # CRITICAL: Include grammar hash in cache key
    # Fail-fast if grammar hash unset
    if _GRAMMAR_HASH is None:
        raise RuntimeError("Grammar hash not initialized. Call _get_or_create_parser() first.")
    
    # Do not cache pathologically large chains to prevent memory exhaustion DoS
    # Also drop extremely long generated chains that could thrash the LRU
    if len(chain_text) > 2048:
        with _PARSER_LOCK:
            return parser.parse(chain_text)
    
    # Cache key only includes grammar hash and chain_text (ignore_case invariant is False)
    cache_key = f"{_GRAMMAR_HASH}:{chain_text}"
    
    # Check cache first with lock
    with _AST_CACHE_LOCK:
        if cache_key in _AST_CACHE:
            # Deterministic LRU: move to end on access
            _AST_CACHE.move_to_end(cache_key)
            # FIX: AST nodes are treated as immutable, no need for expensive deepcopy
            return _AST_CACHE[cache_key]
            
    # Parse INSIDE the lock (Arpeggio state safety)
    # Even if parser object is reused, we must serialize access
    with _PARSER_LOCK:
        ast = parser.parse(chain_text)
    
    # Insert with lock (re-check in case another thread inserted)
    with _AST_CACHE_LOCK:
        if cache_key in _AST_CACHE:
             _AST_CACHE.move_to_end(cache_key)
             return _AST_CACHE[cache_key]
             
        if len(_AST_CACHE) >= _AST_CACHE_MAX_SIZE:
             # FIFO/LRU eviction: pop first item (last=False)
             _AST_CACHE.popitem(last=False)
             
        _AST_CACHE[cache_key] = ast
        # Return directly - visitor must not mutate the AST
        return ast

# ==========================================
# DEBUGGING INSTRUMENTATION
# ==========================================
# Disable debug prints by setting NOE_DEBUG=0 (Default is now 0 for release)
_DEBUG_ENABLED = os.getenv("NOE_DEBUG", "0") == "1"
_EMPTY_DICT = {}

# ==========================================
# UNDEFINED SENTINEL (v1.0 Safety Kernel)
# ==========================================
# Internal undefined representation: use sentinel object for unambiguous checking
# Domain wrapping happens at boundaries only
_U = object()  # Undefined sentinel

def is_undef(x):
    """Check if value is undefined (handles all representations)."""
    if x is _U:
        return True
    if x == "undefined":
        return True
    if isinstance(x, dict) and x.get("domain") == "undefined":
        return True
    return False

def _debug_print(*args, **kwargs):
    """Print only if debug is enabled."""
    if _DEBUG_ENABLED:
        print(*args, **kwargs)

def _ctx_has(ctx, path):
    """
    Check if a dot-separated path exists in the context.
    Supports both flattened and structured (root/domain/local) contexts.
    """
    # Check for structured context
    if isinstance(ctx, dict) and "local" in ctx and "domain" in ctx and "root" in ctx:
        if _ctx_has(ctx["local"], path): return True
        if _ctx_has(ctx["domain"], path): return True
        if _ctx_has(ctx["root"], path): return True
        return False

    parts = path.split(".")
    curr = ctx
    for p in parts:
        if isinstance(curr, dict) and p in curr:
            curr = curr[p]
        else:
            return False
    return True

def _deep_merge_ctx(base, overlay):
    """Simple deep merge for context layers."""
    if not isinstance(base, dict) or not isinstance(overlay, dict):
        return copy.deepcopy(overlay)
    res = copy.deepcopy(base)
    for k, v in overlay.items():
        if k in res and isinstance(res[k], dict) and isinstance(v, dict):
            res[k] = _deep_merge_ctx(res[k], v)
        else:
            res[k] = copy.deepcopy(v)
    return res

def merge_layers_for_validation(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    Flatten a structured context (root/domain/local) into a single effective context
    for validation, applying NIP-009 precedence (local > domain > root).
    
    Use recursive _deep_merge_ctx for deep nested context preservation.
    """
    # Safety: if ctx is not a dict (e.g. NoneType test case), return as is
    if not isinstance(ctx, dict):
        return ctx

    if "root" not in ctx and "domain" not in ctx and "local" not in ctx:
        return ctx
    
    # FIX: Reject explicitly passed None values instead of silently masking them with {}
    # If the user passed None, it means the layer is invalid/missing, and we shouldn't "fix" it for them
    # Because this function returns the merged context, we signify invalid structured contexts
    # by returning the unmerged original context, letting downstream strict validators fail it.
    
    if "root" in ctx and ctx["root"] is None:
        return ctx
    if "domain" in ctx and ctx["domain"] is None:
        return ctx
    if "local" in ctx and ctx["local"] is None:
        return ctx
        
    c_root = ctx.get("root", {})
    c_domain = ctx.get("domain", {})
    c_local = ctx.get("local", {})
    
    if not isinstance(c_root, dict): return ctx
    if not isinstance(c_domain, dict): return ctx
    if not isinstance(c_local, dict): return ctx

    # Recursive deep merge with strict precedence: local > domain > root
    merged = {}
    merged = _deep_merge_ctx(merged, c_root)
    merged = _deep_merge_ctx(merged, c_domain)
    merged = _deep_merge_ctx(merged, c_local)
    return merged

# ==========================================
# 1. LOAD REGISTRY (NIP-001)
# ==========================================
# (json already imported at top)

# Load glyph registry
_REGISTRY_PATH = os.path.join(os.path.dirname(__file__), "registry.json")
_REGISTRY = {}

try:
    with open(_REGISTRY_PATH, "r", encoding="utf-8") as f:
        _REGISTRY = json.load(f)
except (FileNotFoundError, json.JSONDecodeError, IOError):
    # Safe fallback if registry missing or malformed
    _REGISTRY = {"glyphs": []}

# Defensive GLYPH_MAP construction
glyphs = (_REGISTRY.get("glyphs") or []) if isinstance(_REGISTRY, dict) else []
GLYPH_MAP = {
    g.get("phonetic"): g 
    for g in glyphs 
    if isinstance(g, dict) and g.get("phonetic")
}

KNOWN_GLYPHS = set(GLYPH_MAP.keys())

# ==========================================
# 3. HASHING & PROVENANCE (NIP-003)
# ==========================================

# v1.0 Outcome Fields (Complete Allow-List)
# These fields affect event_hash but NOT action_hash
# OUTCOME_FIELDS and action hashing logic moved to noe.provenance
# for Single Source of Truth


# ==========================================
# SAFETY KERNEL FOUNDATION (v1.0)
# ==========================================

def _finalize_action_static(action_obj, ctx_hash, source, dag, mode="strict", now_ms=None):
    """
    Finalize an action by computing hashes, attaching provenance, and registering in DAG.
    
    This is the ONLY choke point for action creation in v1.0.
    
    Args:
        action_obj: Minimal action dict with at least {type, verb, target}
        ctx_hash: Context hash (total) for provenance
        source: Source chain text for provenance
        dag: Action DAG dict (will be mutated)
        mode: "strict" or "partial"
        now_ms: Optional current timestamp in milliseconds
        
    Returns:
        Finalized action dict with action_hash, event_hash, provenance, registered in DAG
    """
    # 1. Validate minimal schema
    if not isinstance(action_obj, dict):
        return action_obj  # Pass through non-actions
    
    if action_obj.get("type") != "action":
        return action_obj
    
    if "verb" not in action_obj or "target" not in action_obj:
        if mode == "strict":
            return {
                "domain": "error",
                "code": "ERR_INVALID_ACTION",
                "value": "Action missing required fields (verb, target)"
            }
        return _U
    
    # Strict Mode: Undefined target -> ERR_UNDEFINED_TARGET
    target = action_obj.get("target")
    is_target_undefined = (
        target == "undefined" or 
        (isinstance(target, dict) and target.get("domain") == "undefined")
    )
    
    if is_target_undefined:
        if mode == "strict":
            return {
                "domain": "error",
                "code": "ERR_UNDEFINED_TARGET",
                "value": "Action target is undefined"
            }
        return _U

    # 2. Compute action_hash (proposal identity)
    action_hash = compute_action_hash(action_obj)
    action_obj["action_hash"] = action_hash
    
    # 3. Compute event_hash (proposal + outcomes)
    has_outcomes = any(field in action_obj for field in OUTCOME_FIELDS)
    
    if has_outcomes:
        # Temporarily flag to include outcomes in normalization
        action_obj["_include_outcome_in_hash"] = True
        try:
            event_hash = compute_action_hash(action_obj)
        finally:
            del action_obj["_include_outcome_in_hash"]
    else:
        # No outcomes -> event_hash = action_hash
        event_hash = action_hash
    
    action_obj["event_hash"] = event_hash
    
    # 4. Register in DAG and check cycles
    edges = dag.setdefault(action_hash, [])
    
    # Add edge if target is an action
    target = action_obj.get("target")
    if isinstance(target, dict) and target.get("type") == "action":
        child_hash = target.get("action_hash")
        if child_hash and child_hash not in edges:
            edges.append(child_hash)
    
    # Cycle check using proper DFS with recursion stack
    def _has_cycle(node, rec_stack, visited):
        if node in rec_stack:
            return True  # Back edge = cycle
        if node in visited:
            return False  # Already explored
        
        visited.add(node)
        rec_stack.add(node)
        
        for child in dag.get(node, []):
            if _has_cycle(child, rec_stack, visited):
                return True
        
        rec_stack.remove(node)
        return False
    
    if _has_cycle(action_hash, set(), set()):
        if mode == "strict":
            return {
                "domain": "error",
                "code": "ERR_ACTION_CYCLE",
                "value": "Action cycle detected in proposal DAG"
            }
        else:
            return _U
    
    # 5. Attach provenance
    provenance = {
        "action_hash": action_hash,
        "event_hash": event_hash,
        "context_hash": ctx_hash,
        "source": source
    }
    
    # Optional: observed_at_ms for delivery/observation actions
    if now_ms and action_obj.get("kind") in ("delivery", "observation"):
        provenance["observed_at_ms"] = now_ms
    
    action_obj["provenance"] = provenance
    
    # 6. Strip internal keys
    for key in list(action_obj.keys()):
        if key.startswith("_"):
            del action_obj[key]
    
    return action_obj


def compute_question_hash(
    chain_text,
    context_hash,
    timestamp,
    question_type=None,
    audience=None,
    to=None,
):
    """
    Compute deterministic SHA-256 hash for a question.
    
    H_Q = SHA256("Q" || canonical_chain || context_hash || timestamp_ms)
    
    Canonicalize chain text and use integer timestamp (ms) for deterministic hashing
    to ensure cross-implementation hash agreement.
    
    Args:
        chain_text: Original Noe chain text (will be canonicalized)
        context_hash: SHA-256 hash of context
        timestamp: Unix epoch (float or int) - will be converted to int milliseconds
        question_type: Optional "soi"|"fek"|"kru"
        audience: Optional addressing hint
        to: Optional logical target id
    
    Returns:
        Hex string SHA-256 hash
    """
    import unicodedata
    
    # CRITICAL: Canonicalize chain text (NFKC + collapse whitespace)
    canonical_chain = unicodedata.normalize('NFKC', chain_text)
    canonical_chain = ' '.join(canonical_chain.split())  # Collapse whitespace
    
    # Use integer milliseconds for timestamp determinism (never fall back to 0)
    if isinstance(timestamp, (int, float)):
        # Detect if timestamp is in seconds (< 10 billion) or milliseconds
        timestamp_ms = int(timestamp * 1000) if timestamp < 10_000_000_000 else int(timestamp)
    elif isinstance(timestamp, str):
        # Parse ISO-8601 to unix milliseconds
        from datetime import datetime
        try:
            dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            timestamp_ms = int(dt.timestamp() * 1000)
        except ValueError:
            raise ValueError(f"Invalid ISO-8601 timestamp: {timestamp}")
    else:
        raise TypeError(f"Timestamp must be int/float/str, got {type(timestamp)}")
    
    # CHANGED: Use JSON structure instead of raw concatenation
    payload_list = [
        "noe.question.v1",
        canonical_chain,
        context_hash,
        timestamp_ms
    ]
    if question_type: payload_list.append(question_type)
    if audience: payload_list.append(audience)
    if to: payload_list.append(to)
    
    payload = canonical_bytes(payload_list)
    return hashlib.sha256(payload).hexdigest()


def compute_answer_hash(parent_question_hash, answer_payload, context_hash, timestamp, answerer_id=None):
    """
    Compute deterministic SHA-256 hash for an answer.
    
    H_A = SHA256("A" || H_Q || canonical_answer_payload || C_A || T_A)
    
    Args:
        parent_question_hash: Hash of the question being answered
        answer_payload: Dict with "domain" and "value" (must be JSON-safe)
        context_hash: SHA-256 hash of context
        timestamp: Unix epoch (int/float) or ISO-8601 string
        answerer_id: Optional agent/system identifier
    
    Returns:
        Hex string SHA-256 hash
    """
    # Use integer milliseconds consistent with question hash computation
    if isinstance(timestamp, (int, float)):
        timestamp_ms = int(timestamp * 1000) if timestamp < 10_000_000_000 else int(timestamp)
    elif isinstance(timestamp, str):
        from datetime import datetime
        try:
            dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            timestamp_ms = int(dt.timestamp() * 1000)
        except ValueError:
            raise ValueError(f"Invalid ISO-8601 timestamp: {timestamp}")
    else:
        raise TypeError(f"Timestamp must be int/float/str, got {type(timestamp)}")
    
    
    
    payload_list = [
        "noe.answer.v1",
        parent_question_hash,
        answer_payload,
        context_hash,
        timestamp_ms
    ]
    if answerer_id: payload_list.append(answerer_id)
        
    payload = canonical_bytes(payload_list)
    return hashlib.sha256(payload).hexdigest()




# ==========================================
# 3. GRAMMAR LAYER (NIP-004 PEG)
# ==========================================

# Precedence (tightest -> loosest):
#  1. primary (literal, glyph, scoped)
#  2. unary_op (vus, vel, nai, ret, tri, nau, yel, ...)
#  3. action_event (mek, men)
#  4. conjunction_op (<=, >=, <, >, =, el, an, kos, til, nel, tel, xel, kra, tra, fra, noq)
#  5. disjunction 'ur'
#  6. guard 'khi'

def glyph():
    # Phonetic atoms (identifiers).
    # Exclude keywords to prevent consumption during implicit juxtaposition.
    keywords = (
        r"an|ur|nai|nex|shi|vek|sha|tor|da|nau|ret|tri|qer|eni|sem|mun|fiu|"
        r"khi|kra|sek|mek|men|nel|tel|xel|kos|til|qua|soi|fek|kru|nek|true|false|undefined|"
        r"ko|dia|doq|en|tra|fra|noq|lef|rai|sup|bel|fai|ban|rel" 
    )
    return _(r'(?!({})\b)[a-z]+'.format(keywords))


def literal():
    # Locked to ASCII-only lowercase as per Strict Contract
    return _(r'@[a-z0-9_]+')


def bool_literal():
    # Lowercase-only boolean keywords
    return _(r'(true|false|undefined)\b')


def demonstrative():
    # Demonstratives: dia (proximal), doq (distal)
    return _(r'(dia|doq)\b')


def intensity():
    return ["°", '"', "'"]


def fusion():
    # Lexical fusion: fel·hum
    # Must be followed by a glyph (not a keyword/operator)
    return _(r'·'), glyph

def inversion():
    # Inversion suffix: ·nei
    return _(r'·nei\b')

def morph_suffix():
    # Other suffixes: tok, al, mek
    # Note: tok/al are technically prefixes in NIP-003 but legacy grammar has them here.
    # We keep them here for now to avoid breaking changes, but fusion/inversion are separate.
    return _(r'tok\b|al\b')

def morphology():
    # Legacy rule for backward compatibility if needed, but atom uses specific parts now
    return _(r'[*·]|tok\b|al\b|mek\b')


def atom():
    # base + (fusion | inversion | suffix)*
    return [literal, bool_literal, number, demonstrative, glyph], ZeroOrMore([fusion, inversion, morph_suffix]), PEGOptional(intensity)


def scoped():
    # Scoped expression: sek Expr sek. 'nek' is chain-level only.
    return [(_(r'sek\b'), expression, _(r'sek\b')), ("(", expression, ")")]


def action_event():
    # Delivery / action verbs: mek (do), men (audit)
    return _(r'mek\b|men\b'), unary


def number():
    # Matches integers and floats: 123, 0.5, 42.0
    return _(r'[+-]?\d+(\.\d+)?([eE][+-]?\d+)?')


# IMPORTANT: action_event first, so "mek @halt" binds as a unit
def primary():
    # bool_literal BEFORE atom to prevent "true"/"false" being parsed as glyphs.
    return [action_event, scoped, atom]


def unary_op():
    return _(r'\b(nai|nex|shi|vek|sha|tor|da|nau|ret|tri|qer|eni|sem|mun|fiu|vus|vel)\b')


def unary():
    # Allow stacked unary ops (e.g., nai nai @t).
    return ZeroOrMore(unary_op), primary


def conjunction_op():
    # Binary operators at conjunction precedence
    # Add 'rel' to make it reachable
    return _(
        r"(an|kos|til|nel|tel|xel|en|kra|tra|fra|noq|lef|rai|sup|bel|fai|ban|rel|<|>|<=|>=|=)\b"
    )

def conjunction():
    # unary (op unary)* for high-precedence operators.
    # Implicit juxtaposition (unary unary) is treated as list/structural.
    return unary, ZeroOrMore([(conjunction_op, unary), unary])


def disjunction():
    # Lower precedence: ur (OR).
    # conjunction (ur conjunction)*
    return conjunction, ZeroOrMore(_(r'\bur\b'), conjunction)


def sek_scope():
    # Strict scope: sek Expr sek (no parentheses allowed for khi)
    return _(r'sek\b'), expression, _(r'sek\b')

def conditional():
    # OrExpr ('khi' SekScope)?
    # Strict grammar: khi must be followed by sek ... sek
    return disjunction, PEGOptional((_(r'khi\b'), sek_scope))


def expression():
    # Highest level: either a conditional guard or a plain expression
    return conditional


def termination():
    return _(r'nek\b')


def question_type():
    # soi / fek / kru
    return _(r'soi\b|fek\b|kru\b')


def question_body():
    # QuestionType? Expr
    return PEGOptional(question_type), expression


def question_chain():
    # qua QuestionBody nek
    return _(r'qua\b'), question_body, termination, EOF


def chain():
    # question_chain / (Expr nek?)
    return [question_chain, (expression, PEGOptional(termination), EOF)]

# ==========================================
# 4. DOMAIN HELPERS
# ==========================================
def wrap_domain(value):
    """
    Map raw Python values into Noe typed domains.

    Truth:       {'domain': 'truth', 'value': bool}
    Numeric:     {'domain': 'numeric', 'value': float}
    Undefined:   {'domain': 'undefined', 'value': 'undefined'}
    Action:      {'domain': 'action', 'value': dict}
    List:        {'domain': 'list', 'value': list}
    Structural:  {'domain': 'structural', 'value': ...} (default fallback)

    Structural values (strings, dicts, etc) are wrapped in a structural domain.
    """
    # Handle _U sentinel
    if value is _U:
        return {"domain": "undefined", "value": "undefined"}
    
    # Pass through existing domain objects (e.g. error)
    if isinstance(value, dict) and "domain" in value:
        return value

    # Action objects
    if isinstance(value, dict) and value.get("type") == "action":
        return {"domain": "action", "value": value}

    # Undefined (legacy string representation)
    if value == "undefined":
        return {"domain": "undefined", "value": "undefined"}

    # Truth domain
    if isinstance(value, bool):
        return {"domain": "truth", "value": value}

    # Numeric domain
    if isinstance(value, (int, float)):
        return {"domain": "numeric", "value": float(value)}

    # Structural lists
    if isinstance(value, list):
        return {"domain": "list", "value": value}

    # Structural or future domains
    return {"domain": "structural", "value": value}


# ==========================================
# 5. SEMANTIC EVALUATOR (NIP-005 + numeric slice of NIP-007)
# ==========================================
class NoeEvaluator(PTNodeVisitor):
    def __init__(self, context, mode="strict", debug=False, source=None, context_hash=None, context_hashes=None):
        """
        Args:
            mode: 'strict' (no inference) or 'partial' (minimal defaults).
            source: Original chain text (for provenance hashing).
            context_hash: Pre-computed context hash from snapshot (CRITICAL: prevents mismatch)
            context_hashes: Pre-computed shard hashes {root, domain, local, total}
        """
        super().__init__(debug=debug)
        self.ctx = context or {}
        self.mode = mode
        self.debug = debug
        self.source = source
        self._action_dag = {}  # Isolate DAG state to evaluator instance
        
        # Use passed context_hash instead of recomputing
        # Ensures provenance hash matches meta.context_hash returned to caller
        if context_hash is not None:
            self._ctx_hash = context_hash
        else:
            # Fallback for backward compatibility (but will cause mismatch!)
            # Fallback for backward compatibility (but will cause mismatch!)
            # v1.0 Update: Use unified hashing (ignoring partial defaults)
            self._ctx_hash = compute_context_hashes(self.ctx)["total"]
        
        self._ctx_hashes = context_hashes or {}
        self.context_manager = None
        
        if debug:
            print("DEBUG NoeEvaluator instantiated")
            print(f"DEBUG NoeEvaluator methods: {[m for m in dir(self) if m.startswith('visit_')]}")

    # def __getattribute__(self, name):
    #     if name.startswith('visit_'):
    #         print(f"DEBUG GETATTRIBUTE: {name}")
    #     return super().__getattribute__(name)

    def _ensure_context_for_op(self, op: str) -> bool:
        """
        Returns True if context is complete for this operator,
        False if required fields are missing.
        """
        reqs = CONTEXT_REQUIREMENTS.get(op, [])
        if not reqs:
            return True  # no special context needed

        for path in reqs:
            if not _ctx_has(self.ctx, path):
                # In partial mode, check if default context has it
                if self.mode == "partial" and _ctx_has(DEFAULT_CONTEXT_PARTIAL, path):
                    continue
                return False
        return True

    def _resolve_audit_status(self, target):
        """
        Resolve the audit status for a men target.

        Resolve audit status for 'men' targets.

        Rules:
        1. Literal '@x': Lookup C.audit.files['@x']. Return status string or None.
        2. Action dict: Return 'audit_status' if present.
           Fallback: Map boolean 'verified' -> 'verified'/'failed'.
        3. Otherwise: None.
        """
        # Case 1: literal like '@file_ok'
        if isinstance(target, str) and target.startswith("@"):
            audit = self._get_context_field("audit")
            if not isinstance(audit, dict):
                return None
            files = audit.get("files")
            if not isinstance(files, dict):
                return None
            status = files.get(target)
            if isinstance(status, str):
                return status
            return None

        # Case 2: nested action from previous men
        if isinstance(target, dict) and target.get("type") == "action":
            # Prefer explicit audit_status if present
            if "audit_status" in target and isinstance(target["audit_status"], str):
                return target["audit_status"]

            # Backwards-compat: map a boolean 'verified' to a status
            if "verified" in target and isinstance(target["verified"], bool):
                return "verified" if target["verified"] else "failed"

        # Otherwise: unknown / not audited
        return None

    # --- Intensity / Scalar Operators (NIP-006 minimal) ---

    def _apply_intensity(self, op, val):
        # Undefined case
        if val == "undefined":
            return "undefined"

        # Truth domain: intensity does NOT change truth value
        if isinstance(val, bool):
            return val

        # Numeric domain: apply scale factor
        if isinstance(val, (int, float)):
            if op == "'":
                return float(val) * 0.5
            if op == '"':
                return float(val) * 1.0
            if op == "°":
                return float(val) * 2.0
            return "undefined"

        # Disallowed: literals, actions, lists, and non-numeric glyphs
        if isinstance(val, str) and val.startswith("@"):
            return "undefined"

        if isinstance(val, dict) and val.get("type") == "action":
            return "undefined"

        if isinstance(val, list):
            return "undefined"

        # Catch-all
        return "undefined"

    # -------------------------
    # Context helper
    # -------------------------
    def _get_context_field(self, key, default=None):
        # Trace context lookup

        # Check for structured context (root/domain/local)
        if "local" in self.ctx and "domain" in self.ctx and "root" in self.ctx:
            local_val = self.ctx["local"].get(key)
            domain_val = self.ctx["domain"].get(key)
            root_val = self.ctx["root"].get(key)
            
            # If we found something in any layer
            if local_val is not None or domain_val is not None or root_val is not None:
                # If they are dicts, return a merged view
                maps = []
                if isinstance(local_val, dict): maps.append(local_val)
                if isinstance(domain_val, dict): maps.append(domain_val)
                if isinstance(root_val, dict): maps.append(root_val)
                
                if maps:
                    # Merge explicitly (local > domain > root)
                    merged_val = {}
                    for m in reversed(maps):
                        merged_val.update(m)
                    return merged_val
                
                # Otherwise return the most specific value
                if local_val is not None: return local_val
                if domain_val is not None: return domain_val
                return root_val

        # Standard flattened context lookup
        if key in self.ctx:
            val = self.ctx[key]
            # print(f"DEBUG: Found {key} in flat context: {type(val)}")
            return val

        if self.mode == "partial":
            if key in DEFAULT_CONTEXT_PARTIAL:
                # print(f"DEBUG: _get_context_field({key}) returning default partial")
                return DEFAULT_CONTEXT_PARTIAL[key]
            return default

        # Not found
        # print(f"DEBUG: {key} NOT FOUND in context")
        return None

    # -------------------------
    # 3-valued logic helper
    # -------------------------
    @staticmethod
    def _to_trit(v):
        """
        Map a value to a 3-valued logic atom:

        Returns:
          - True  for truthy / 1
          - False for falsy / 0
          - None  for 'undefined' sentinel or non-boolean types (treated as U)
        """
        # 1. Undefined check (Unified)
        if is_undef(v):
            return None

        # 2. Domain Unwrapping (Truth domain)
        if isinstance(v, dict) and v.get("domain") == "truth":
            return NoeEvaluator._to_trit(v.get("value"))

        # 3. Standard checks
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            import math
            if not math.isfinite(v):
                return None
            return (v != 0)
        
        return None
    
    # [DELETED] _attach_provenance: Superseded by _finalize_action
    # [DELETED] _register_action_node: Superseded by _finalize_action

    # --- Atoms and literals ---

    def visit_question_type(self, node, children):
        # soi / fek / kru (or older noi/voi/soi)
        # For regex matches, the value is in node.value
        return node.value if node.value else None

    def visit_question_body(self, node, children):
        # QuestionBody: QuestionType? Expr
        # children: [question_type?, expr_result]
        if len(children) == 1:
            qtype = None
            body = children[0]
        else:
            qtype, body = children
        return {"type": qtype, "body": body}

    def visit_question_chain(self, node, children):
        # question_chain: 'qua' QuestionBody 'nek' EOF
        # children: ['qua', question_body_result, 'nek']
        # Note: EOF doesn't create a child node
        _, qbody, _ = children  # qua, question_body, nek
        return {
            "domain": "question",
            "value": qbody,
        }







    def visit_glyph(self, node, children):
        name = node.value

        # Demonstratives: resolve via C.demonstratives
        # dia  -> proximal
        # doq  -> distal
        # Removed duplicate demonstrative handling
        # dia/doq are keywords excluded from glyph grammar, so this branch is dead code
        # Single source of truth: visit_demonstrative() handles all demonstrative resolution
        
        # All glyphs pass through as structural atoms for now
        return name

    def visit_literal(self, node, children):
        raw_key = node.value
        key = canonical_literal_key(raw_key)

        literals_map = self._get_context_field("literals", default={})
        if literals_map is None:
            # In strict mode, missing context is an error
            if self.mode == "strict":
                 return {
                     "domain": "error", 
                     "code": "ERR_CONTEXT_INCOMPLETE", 
                     "value": "Missing literals map in strict mode"
                 }
            # Pass through the literal key for delivery operators (partial mode)
            return key

        val = literals_map.get(key, None)
        
        # Fallback for contexts storing keys with @ prefix (matches Validator logic)
        if val is None:
            val = literals_map.get("@" + key, None)
            
        if val is None:
            # NIP-009: Missing literal -> semantic undefined
            return {"domain": "undefined", "value": "undefined"}
        
        # Return a domain object that preserves the key for spatial ops
        return {"domain": "literal", "key": key, "value": val}

    def visit_bool_literal(self, node, children):
        val = node.value
        if val == "true":
            return True
        if val == "false":
            return False
        if val == "undefined":
            return "undefined"
        return val

    def visit_number(self, node, children):
        return float(node.value)

    def visit_demonstrative(self, node, children):
        """
        Handles demonstrative tokens (dia, doq).
        Resolves based on Context.demonstratives → entity bindings
        or falls back to spatial threshold resolution in visit_binary_op.
        """
        dem_type = node.value # "dia" or "doq"
        
        # 1. Get Demonstratives Context
        demonstratives = self._get_context_field("demonstratives")
        if not isinstance(demonstratives, Mapping):
            return "undefined"
            
        # 2. Get Entities Context (needed for resolution)
        entities = self._get_context_field("entities")
        if not isinstance(entities, Mapping):
            return "undefined"

        # Map type to key
        key = "proximal" if dem_type == "dia" else "distal"
        
        # 3. Check Direct Binding
        binding = demonstratives.get(dem_type) or demonstratives.get(key)
        
        # Case A: Explicit Entity ID in binding
        if isinstance(binding, Mapping) and "entity" in binding:
            ent_id = binding["entity"]
            if ent_id in entities:
                return ent_id
            # Stale reference
            return "undefined"
            
        if isinstance(binding, str) and binding.startswith("@"):
            if binding in entities:
                return binding
            return "undefined"

        # 4. Spatial Resolution (if no direct entity binding)
        # Anti-Axiom Security: Do NOT default missing thresholds to {}
        spatial = self._get_context_field("spatial")
        if not isinstance(spatial, Mapping):
            return "undefined"
        
        # Strict: thresholds must exist, no silent defaults
        if "thresholds" not in spatial:
            return "undefined"
            
        thresholds = spatial["thresholds"]
        if not isinstance(thresholds, Mapping):
            return "undefined"
            
        # Get threshold value
        # dia -> near, doq -> far (or > near?)
        # Simplification: dia <= near, doq >= far
        
        candidates = []
        
        if dem_type == "dia":
            limit = thresholds.get("near")
            if not isinstance(limit, (int, float)):
                return "undefined"
                
            for ent_id, ent_data in entities.items():
                if not isinstance(ent_data, Mapping): continue
                dist = ent_data.get("distance")
                if isinstance(dist, (int, float)) and dist <= limit:
                    candidates.append(ent_id)
                    
        elif dem_type == "doq":
            limit = thresholds.get("far")
            if not isinstance(limit, (int, float)):
                return "undefined"
                
            for ent_id, ent_data in entities.items():
                if not isinstance(ent_data, Mapping): continue
                dist = ent_data.get("distance")
                if isinstance(dist, (int, float)) and dist >= limit:
                    candidates.append(ent_id)

        # 5. Uniqueness / Ambiguity Check
        if len(candidates) == 1:
            return candidates[0]
        
        # 0 or >1 matches -> undefined
        return "undefined"

    def _validate_morphology(self, token):
        """
        Enforce NIP-003 morphology rules on a fully reconstructed token string.
        Returns None if valid, or error dict if invalid.
        """
        # 1. Standalone 'nei' is invalid
        if token == "nei":
            return {
                "domain": "error",
                "code": "ERR_MORPHOLOGY",
                "value": "Standalone 'nei' is invalid. Use '·nei' suffix."
            }

        # 2. 'nei' must be a suffix (·nei), never a prefix or mid-fusion
        # Valid: fel·nei
        # Invalid: nei·fel, fel·nei·hum
        if "nei" in token:
            if not token.endswith("·nei"):
                # Check if it's just part of a root (e.g. 'onei')?
                # NIP-003 says 'nei' is the operator.
                # If we have '·nei·', it's invalid.
                if "·nei·" in token or token.startswith("nei·"):
                     return {
                        "domain": "error",
                        "code": "ERR_MORPHOLOGY",
                        "value": f"Invalid placement of 'nei' in '{token}'. Must be final suffix '·nei'."
                    }

        # 3. Double inversion (·nei·nei)
        if "·nei·nei" in token:
             return {
                "domain": "error",
                "code": "ERR_MORPHOLOGY",
                "value": "Double inversion '·nei·nei' is invalid."
            }

        # 4. Syntactic operators in fusion
        # ko, nai, khi, etc. should be blocked by grammar (keywords),
        # but we double check here in case of grammar leaks.
        # 'ko' is now a keyword, so it shouldn't parse as glyph.
        # 'nai' is a keyword.
        
        return None
        # Removed dead code after return (lines 1005-1020 were unreachable)

    def visit_fusion(self, node, children):
        # Fusion: · glyph
        # Return the full string "·glyph"
        return "".join(str(c) for c in children)

    def visit_inversion(self, node, children):
        # Terminal node - use node.value, not children[0]
        # Grammar: _(r'·nei\b') produces terminal with empty children list
        return node.value if hasattr(node, "value") and node.value else (children[0] if children else "·nei")

    def visit_morph_suffix(self, node, children):
        # Terminal node - use node.value, not children[0]
        # Grammar: _(r'tok\b|al\b') produces terminal with empty children list
        return node.value if hasattr(node, "value") and node.value else (children[0] if children else "undefined")

    def visit_intensity(self, node, children):
        # Flatten intensity list
        return children[0]

    def visit_atom(self, node, children):
        """
        children structure:
            [base, (fusion parts | inversion | suffixes)..., optional_intensity]
        """
        if not children:
            return "undefined"

        base = children[0]
        
        # Reconstruct the full token string for validation
        # base is likely a string (glyph/literal) or number
        full_token = str(base)
        
        morph_parts = []
        intensity_op = None

        for child in children[1:]:
            if child in ["°", '"', "'"]:
                intensity_op = child
            else:
                # Append to full token string
                full_token += str(child)
                morph_parts.append(child)

        # Morphology only allowed on plain glyph atoms
        if morph_parts:
            # Check if base is a valid glyph for morphology
            is_invalid_base = (
                not isinstance(base, str) or
                (isinstance(base, str) and base.startswith("@")) or  # literal
                isinstance(base, bool) or
                isinstance(base, (int, float)) or
                isinstance(base, dict)  # structured result
            )
            
            if is_invalid_base and self.mode == "strict":
                return {
                    "domain": "error",
                    "code": "ERR_MORPHOLOGY",
                    "value": f"Morphology only allowed on glyph atoms, not on {type(base).__name__}"
                }
        
        # If no morphology, return base as is (preserves bool/number types)
        if not morph_parts:
             # Apply intensity if present
             if intensity_op and intensity_op != "":
                 return self._apply_intensity(intensity_op, base)
             return base

        # Validate the reconstructed token
        if self.mode == "strict":
            error = self._validate_morphology(full_token)
            if error:
                return error

        # For v1.0, we treat the reconstructed string as the identifier
        return full_token

    def visit_primary(self, node, children):
        return children[0]

    # --- Unary operators (negation, quantifiers, epistemics, temporal, tor) ---
    
    def visit_unary_op(self, node, children):
        """Convert unary operator node to string."""
        return node.value

    def visit_unary(self, node, children):
        if self.debug:
            print(f"DEBUG visit_unary called")
        if len(children) == 1:
            return children[0]

        val = children[-1]
        ops = children[:-1]

        # OPTIONAL: record a simple AST-like descriptor for temporal nodes
        if any(op in ("nau", "ret", "tri", "qer") for op in ops):
            self._last_temporal_node = {
                "kind": "TemporalUnary",
                "ops": [op for op in ops if op in ("nau", "ret", "tri", "qer")],
                "inner": val,
            }

        # We need to access the operand node to get the literal key for 'sha'
        # The operand node is the last child in the Arpeggio node list
        operand_node = node[-1]

        for i, op in enumerate(reversed(ops)):
            # Special handling for epistemic ops & delivery ops: need the literal key
            extra_key = None
            if op in ("sha", "shi", "vek", "vus", "vel") and i == 0:
                # Only if op is the innermost operator
                try:
                    raw_text = operand_node.flat_str().strip()
                    if raw_text.startswith("@"):
                        extra_key = raw_text
                except AttributeError:
                    pass

            val = self._apply_unary_op(op, val, extra_key=extra_key)
            # In strict mode, validation failure IS the result.
            # Return the error object so the caller sees specific codes.
            if isinstance(val, dict) and val.get("domain") == "error":
                return val
            if op == "shi" and self.debug:
                 print(f"DEBUG visit_unary shi: val={val}, extra_key={extra_key}")

        return val

    def _apply_unary_op(self, op, val, extra_key=None):
        # Unwrap literal object
        if isinstance(val, dict) and val.get("domain") == "literal":
             extra_key = val["key"]
             val = val["value"]

        # Enforce context completeness
        if not self._ensure_context_for_op(op):
            return "undefined"

        # Epistemic & normative layer
        if op == "shi":
            """
            shi P (knowledge)
            
            Evaluates to P's truth value ONLY if P is in C.modal.knowledge.
            Otherwise returns "undefined".
            
            # Unlike other operators, 'shi' checks C.modal.knowledge directly,
            so the literal doesn't need to be in C.literals.
            """
            # Extract the key (literal) from val or extra_key
            if not extra_key:
                if isinstance(val, str) and val.startswith("@"):
                    extra_key = val
                elif isinstance(val, dict) and val.get("domain") == "literal":
                    extra_key = val.get("key")
                else:
                    # Can't determine what to check knowledge of
                    return "undefined"
                
            modal = self._get_context_field("modal")
            if not isinstance(modal, Mapping):
                return "undefined"
                
            knowledge_map = modal.get("knowledge", {})
            
            # Check if known
            if extra_key in knowledge_map:
                return knowledge_map[extra_key]
            elif ("@" + extra_key) in knowledge_map:
                return knowledge_map["@" + extra_key]
            else:
                if self.mode == "strict":
                    return {
                        "domain": "error",
                        "code": "ERR_EPISTEMIC_MISMATCH",
                        "value": f"Knowledge check failed for '{extra_key}' in strict mode"
                    }
                return "undefined"

        if op == "sha":
            """
            sha P (certainty)
            
            Key-driven modal lookup consulting C.modal.certainty for sha operator
            even if literal is missing from C.literals. Epistemics are grounded in
            C.modal, not C.literals (which holds world facts).
            
            Evaluates to P's truth value ONLY if certainty(P) >= threshold.
            Otherwise returns "undefined".
            """
            # Extract key from extra_key, val["key"], or @literal string
            # Do NOT short-circuit on val == "undefined"
            key = None
            if extra_key:
                key = extra_key
            elif isinstance(val, dict) and val.get("domain") == "literal":
                key = val.get("key")
            elif isinstance(val, str) and val.startswith("@"):
                key = val
            
            if not key:
                # Can't determine what to check certainty for
                return "undefined"
            
            # Canonicalize key before lookup
            key = canonical_literal_key(key)
            
            modal = self._get_context_field("modal")
            if not isinstance(modal, Mapping):
                return "undefined"
                
            certainty_map = modal.get("certainty", {})
            
            # FIX: No implicit default for certainty_threshold in strict mode
            # If the user doesn't define it, we shouldn't supply a hidden policy that affects outcomes
            if "certainty_threshold" not in modal:
                 if self.mode == "strict":
                      return {
                          "domain": "error",
                          "code": "ERR_EPISTEMIC_MISMATCH",
                          "value": f"Cannot evaluate sha '{key}': certainty_threshold is not defined in modal subsystem (strict mode)"
                      }
                 threshold = 0.8  # Legacy fallback for non-strict
            else:
                 threshold = modal.get("certainty_threshold")
            
            # Fallback lookup for legacy contexts
            cert_level = certainty_map.get(key)
            if cert_level is None:
                cert_level = certainty_map.get("@" + key, 0.0)
            
            if cert_level >= threshold:
                # High certainty - return truth value from modal.knowledge or modal.belief
                knowledge_map = modal.get("knowledge", {})
                if key in knowledge_map:
                    return knowledge_map[key]
                elif ("@" + key) in knowledge_map:
                    return knowledge_map["@" + key]
                
                belief_map = modal.get("belief", {})
                if key in belief_map:
                    return belief_map[key]
                elif ("@" + key) in belief_map:
                    return belief_map["@" + key]
                
                # High certainty but no truth value available
                if self.mode == "strict":
                    return {
                        "domain": "error",
                        "code": "ERR_EPISTEMIC_MISMATCH",
                        "value": f"Certainty check passed for '{key}' but no truth value found in strict mode"
                    }
                return "undefined"

            
            # Below certainty threshold
            if self.mode == "strict":
                return {
                    "domain": "error",
                    "code": "ERR_EPISTEMIC_MISMATCH",
                    "value": f"Certainty check failed for '{key}' (threshold {threshold}) in strict mode"
                }
            return "undefined"

        # Logical negations (Kleene 3-valued)
        if op == "vek":
            """
            vek P (belief)
            
            Key-driven modal lookup consulting C.modal.belief/knowledge for vek/shi operators
            even if literal is missing from C.literals.
            
            Evaluates to P's truth value if P is in C.modal.belief OR C.modal.knowledge.
            (Knowledge implies Belief).
            Otherwise returns "undefined".
            """
            # Extract key from extra_key, val["key"], or @literal string
            # Do NOT short-circuit on val == "undefined"
            key = None
            if extra_key:
                key = extra_key
            elif isinstance(val, dict) and val.get("domain") == "literal":
                key = val.get("key")
            elif isinstance(val, str) and val.startswith("@"):
                key = val
            
            if not key:
                # Can't determine what to check belief for
                return "undefined"
            
            # Canonicalize key before lookup
            key = canonical_literal_key(key)
                
            modal = self._get_context_field("modal")
            if not isinstance(modal, Mapping):
                return "undefined"
                
            belief_map = modal.get("belief", {})
            knowledge_map = modal.get("knowledge", {})
            # Knowledge implies belief (check knowledge first)
            if key in knowledge_map:
                return knowledge_map[key]
            elif ("@" + key) in knowledge_map:
                return knowledge_map["@" + key]
            elif key in belief_map:
                return belief_map[key]
            elif ("@" + key) in belief_map:
                return belief_map["@" + key]
            else:
                return "undefined"

        if op in ("nai", "nex"):
            t = self._to_trit(val)
            if t is None:
                return "undefined"
            return (not t)

        # Quantifiers over lists
        if op in ("eni", "sem", "mun", "fiu"):
            if val == "undefined" or val is None:
                return "undefined"

            # Map to booleans for counting, skip undefined entries
            bools = []
            try:
                # Handle both list objects and implicit list structures
                iterator = val if isinstance(val, (list, tuple)) else [val]
                for x in iterator:
                    t = self._to_trit(x)
                    if t is None:
                        continue
                    bools.append(t)
            except TypeError:
                return "undefined"

            n = len(bools)
            true_count = sum(1 for b in bools if b is True)
            false_count = sum(1 for b in bools if b is False)

            if op == "eni":
                if true_count > 0:
                    return True
                if n == 0:
                    return "undefined"
                return False

            if op == "sem":
                if n == 0:
                    return "undefined"
                if false_count == 0:
                    return True
                return False

            if op == "mun":
                if n == 0:
                    return "undefined"
                theta = 0.4
                return (true_count / n) >= theta

            if op == "fiu":
                if n == 0:
                    return "undefined"
                phi = 0.1
                return (true_count / n) <= phi

        # Temporal operators (v1.1: event-based + action compatibility)
        if op in ("nau", "ret", "tri", "qer"):
            # sys.stderr.write(f"DEBUG: _apply_unary_op op={op} val={val}\n")
            temporal = self._get_context_field("temporal")
            # sys.stderr.write(f"DEBUG: temporal={temporal}\n")
            if not isinstance(temporal, Mapping):
                # sys.stderr.write("DEBUG: temporal is not Mapping\n")
                return "undefined"

            """
            - If `val` is:
                * a literal '@evt_*'      → look up C.temporal.events['@evt_*']
                * an action dict          → if target is '@evt_*', look that up
            - Events are objects like { "ts": <float> } in C.temporal.events.
            - C.temporal.now is the reference time.

            Operators:
                nau E  → ts == now
                ret E  → ts <  now   (past)
                tri E  → ts >  now   (future)
                qer E  → repetition marker (returns same truth as nau/ret/tri would)

            If no matching event can be resolved, we fall back to the
            propositional semantics from v1.0 (with U-safe behavior).
            """

            now = temporal.get("now", None)
            events = temporal.get("events", {})

            # ---------- 1. Resolve `val` to an event record ----------
            event = None

            # Case A: action object from vus/vel/men or mek
            if isinstance(val, dict) and val.get("type") == "action":
                # Prefer explicit event_id if present
                event_id = val.get("event_id")
                target = val.get("target")

                if isinstance(events, dict):
                    if isinstance(event_id, str):
                        event = events.get(event_id)

                    # Fallback: use target literal like '@evt_past'
                    if event is None and isinstance(target, str) and target.startswith("@"):
                        event = events.get(target)

            # Case B: literal like '@evt_past'
            elif isinstance(val, str) and val.startswith("@") and isinstance(events, dict):
                event = events.get(val)

            # ---------- 2. If we have an event, do event-time semantics ----------
            if event is not None and isinstance(event, dict):
                ts = event.get("ts", event.get("time", None))

                if not isinstance(ts, (int, float)) or not isinstance(now, (int, float)):
                    return "undefined"

                if op == "nau":
                    return (ts == now)
                if op == "ret":
                    return (ts < now)
                if op == "tri":
                    return (ts > now)
                if op == "qer":
                    # In v1.1, qer is a repetition marker; we can treat it
                    # as "event exists in model" for now.
                    return True

            # ---------- 3. Fallback: propositional behavior (v1.0) ----------
            if val == "undefined":
                return "undefined"

            t = self._to_trit(val)
            if t is None:
                # Non-propositional type (number, action, entity…) → undefined
                return "undefined"

            # For nau / yel without resolvable event, just pass through truth
            if op in ("nau", "qer"):
                return t

            # For ret / tri without a temporal model or event mapping, we do not guess
            return "undefined"

        # Normative layer: tor (v1.1 normative correctness)
        if op == "tor":
            """
            tor P  (normative correctness)

            Semantics v1.1 (safety-first):

            - If P is already a bare boolean or a truth-domain value,
              tor MUST NOT flip it. It simply returns that truth value.

            # - C.axioms.value_system.accepted / rejected are lists of such keys.
            #
            # - If key ∈ accepted  → True
            #   If key ∈ rejected  → False
            #   Else               → undefined
            """
            # Case 1: P is already a boolean/truth value
            # Use extra_key as the primary operand for unary ops
            t = self._to_trit(val)
            if t is not None:
                return t

            # Case 2: P is a key to check against value system
            axioms = self._get_context_field("axioms")
            if not isinstance(axioms, dict):
                return "undefined"

            vs = axioms.get("value_system")
            if not isinstance(vs, dict):
                return "undefined"

            accepted = vs.get("accepted", [])
            rejected = vs.get("rejected", [])

            # 1) If we already have explicit truth, respect it.
            if isinstance(val, bool):
                return val

            if isinstance(val, dict) and val.get("domain") == "truth":
                inner = val.get("value")
                if isinstance(inner, bool):
                    return inner

            # 2) Extract a normative key (string only).
            key = None

            # Plain strings: literals ('@act_ok') or glyph labels ('ok_policy')
            if isinstance(val, str):
                key = val

            # Typed domain with string value (e.g. structural/other)
            elif isinstance(val, dict) and isinstance(val.get("value"), str):
                key = val["value"]

            # Anything else (numbers, lists, actions, etc.) → undefined
            if key is None:
                return "undefined"

            # 3) Look up in accepted / rejected
            if key in accepted:
                return True
            if key in rejected:
                return False

            # Not mentioned in the value system → normative status undefined
            return "undefined"

        # Delivery semantics (NIP-008 v1.0 structured events + DAG integration)
        if op in ("vus", "vel"):
            # val must be a literal like @pkg, OR we have extra_key from visit_unary
            if extra_key and isinstance(extra_key, str):
                literal_key = extra_key if extra_key.startswith("@") else "@" + extra_key
            elif isinstance(val, str) and val.startswith("@"):
                literal_key = val
            elif isinstance(val, dict) and val.get("domain") == "literal" and isinstance(val.get("key"), str):
                k = val["key"]
                literal_key = k if k.startswith("@") else "@" + k
            else:
                # Anything else (undefined, glyph, numeric) => undefined
                return "undefined"

            # Resolve lookup_id (the ID used in delivery system) -> e.g. "pkg_123"
            lookup_id = literal_key
            
            # Helper to extract content from potentially wrapped value
            val_content = val
            if isinstance(val, dict) and val.get("domain") == "literal":
                val_content = val.get("value")
            
            # Prefer 'id' or 'tracking' fields if available
            if isinstance(val_content, dict):
                if "id" in val_content:
                    lookup_id = val_content["id"]
                elif "tracking" in val_content:
                    lookup_id = val_content["tracking"]

            # Unified Context Lookup (v1.0 Frozen Schema)
            # C.delivery.items["@pkg"] = {status, verified, observed_at_ms, expires_at_ms}
            delivery = self._get_context_field("delivery")
            if not isinstance(delivery, dict):
                # Missing subsystem -> treat as empty
                items = {}
            else:
                items = delivery.get("items", {})
                
                # Fallback to legacy split structure if 'items' not found (Migration path)
                if not items:
                    status_map = delivery.get("status", {})
                    verified_set = delivery.get("verified", [])
                    if isinstance(verified_set, list):
                        verified_set = set(verified_set)
                    else:
                        verified_set = set()
                    
                    # On-the-fly migration using lookup_id
                    if lookup_id in status_map or lookup_id in verified_set:
                        items = {
                            lookup_id: {
                                "status": status_map.get(lookup_id, "unknown"),
                                "verified": lookup_id in verified_set
                            }
                        }

            item = items.get(lookup_id)

            # Behavior Matrix: Missing Item
            if item is None:
                if self.mode == "strict":
                    return "undefined"
                else:
                    # Partial: assume unknown/unverified
                    item = {"status": "unknown", "verified": False}

            # Construct Action Object
            action_obj = {
                "type": "action",
                "kind": "delivery",
                "verb": op,
                "target": literal_key, # Keep input reference (@package)
                "status": item.get("status", "unknown"),
                "verified": item.get("verified", False)
            }

            # Optional outcome fields
            if "observed_at_ms" in item:
                action_obj["observed_at_ms"] = item["observed_at_ms"]
            if "expires_at_ms" in item:
                action_obj["expires_at_ms"] = item["expires_at_ms"]

            # SAFETY KERNEL: Route through finalization
            return self._finalize_action(action_obj)

    # --- Scoped groupings: ( ... ) or sek ... sek ---

    def visit_scoped(self, node, children):
        """
        Scoped groupings:

          - sek X sek
          - ( X )

        For now:

          - Parentheses: behave as pure grouping, return X directly.
          - sek ... sek: return a one element structural list [X].

        This makes sek the canonical list constructor, and keeps lists
        out of boolean / numeric semantics unless explicitly handled.
        """
        # Parentheses: ("(", expression, ")")
        
        # Check if it's a parenthesized group
        if len(children) >= 1 and children[0] == "(":
             # Find the expression inside
             # Usually children = ["(", expr, ")"]
             # But might be flattened?
             # Just filter out parens
             inner = [x for x in children if x != "(" and x != ")"]
             if len(inner) == 1:
                 return inner[0]
             # If empty or multiple, something is weird, but return list?
             # Empty parens () -> undefined? Or empty list?
             if not inner:
                 return "undefined"
             return inner[-1] # Return last item? Or list? Standard grouping returns last item usually.
             
        if len(children) == 1:
            return children[0]

        # sek expression sek -> treat as structural list
        if len(children) >= 3:
            # Filter out 'sek' and 'nek' tokens to find the expression result
            # Note: In some cases Arpeggio might group [expression, termination] into a single list
            # so we need to flatten/filter carefully.
            
            # 1. Flatten children just in case
            flat = []
            for c in children:
                if isinstance(c, list):
                    flat.extend(c)
                else:
                    flat.append(c)
            
            # 2. Filter out keywords
            exprs = [x for x in flat if x != "sek" and x != "nek"]
            
            if len(exprs) == 1:
                return [exprs[0]]
            elif len(exprs) > 1:
                # Should not happen with current grammar (single expression)
                # but if it does, return list of them?
                return exprs
                
            return [] # Empty sek sek?

        return "undefined"

    def visit_sek_scope(self, node, children):
        """
        Strict scope: sek Expr sek
        Returns [Expr] (structural list)
        """
        if self.debug:
            import sys
            sys.stderr.write(f"DEBUG visit_sek_scope called with {children}\n")
        # Filter out 'sek' tokens
        exprs = [x for x in children if x != "sek"]
        
        if len(exprs) == 1:
            return [exprs[0]]
        elif len(exprs) > 1:
            return exprs
            
        return []

       # --- Action events: mek X / men X ---

    # --- Action events: mek X / men X ---

    def visit_action_event(self, node, children):
        verb = children[0]
        target = children[1]

        # Debug: Print target structure
        if self.debug and verb == "mek":
            print(f"DEBUG visit_action_event: verb={verb}, target={target}, type={type(target)}")

        # Unwrap literal object
        # For execution-bearing verbs (mek, men), the hashed target is the SYMBOLIC KEY
        # (e.g., "@release_pallet"), not the resolved literal value blob.
        # This prevents context floats (confidence, timestamps) from leaking into action_hash.
        # Exception: nested action targets use the resolved action (pointer semantics).
        if isinstance(target, dict) and target.get("domain") == "literal":
             val = target["value"]
             if not (isinstance(val, dict) and val.get("type") == "action"):
                 # Symbolic ref: use the literal key with @ prefix
                 # (canonical_literal_key strips @, but we want it for action target identity)
                 key = target.get("key")
                 if key is None:
                     return {
                         "domain": "error",
                         "code": "ERR_BAD_ACTION_TARGET_REF",
                         "value": f"Literal operand for '{verb}' has no 'key' field",
                         "meta": {"verb": verb}
                     }
                 target = "@" + key if not key.startswith("@") else key
             else:
                 # Nested action: unwrap to the action object (pointer semantics)
                 target = val

        # Propagate errors from target (e.g. morphology errors)
        if isinstance(target, dict) and target.get("domain") == "error":
            return target

        # Strict Mode Validation (NIP-010)
        if self.mode == "strict":
            # 1. Undefined Target -> ERR_UNDEFINED_TARGET
            if isinstance(target, dict) and target.get("domain") == "undefined":
                return {
                    "domain": "error",
                    "code": "ERR_UNDEFINED_TARGET",
                    "value": "Action target is undefined in strict mode",
                    "meta": {"verb": verb, "target": target}
                }

            # 2. Invalid Target Type -> ERR_INVALID_ACTION
            # Reject primitives (bool, float) as targets for standard actions
            # unless the verb specifically allows them (none do in v1 core)
            if isinstance(target, (bool, float, int)):
                return {
                    "domain": "error",
                    "code": "ERR_INVALID_ACTION",
                    "value": f"Invalid target type {type(target).__name__} for verb '{verb}'",
                    "meta": {"verb": verb, "target": target}
                }
                
            # 3. Grounding Requirements (v1.0 Missing Shard Check)
            from .noe_validator import check_grounding
            if not check_grounding(verb, tuple([target]), self.ctx):
                return {
                    "domain": "error",
                    "code": "ERR_BAD_CONTEXT",
                    "value": f"Context grounding validation failed for action '{verb}' against context schema.",
                    "meta": {"verb": verb, "target": target}
                }

        # ---------- 1. Action DAG (cycle safety) ----------

        # ---------- 1. Build base action object (Hoisted) ----------

        action_obj = {
            "type": "action",
            "verb": verb,
            "target": target,
        }

        # Tag audit actions
        if verb == "men":
            action_obj["kind"] = "audit"

        # Hoist kind from target
        if isinstance(target, dict) and target.get("type") == "action":
            if "kind" in target:
                action_obj["kind"] = target["kind"]

        # Audit verification lookup (men only)
        if verb == "men" and isinstance(target, str) and target.startswith("@"):
            audit = self._get_context_field("audit")
            if audit and isinstance(audit, dict):
                status_map = audit.get("files")
                if status_map and isinstance(status_map, dict):
                    status = status_map.get(target)
                    if status:
                        action_obj["audit_status"] = status
                        action_obj["verified"] = (status == "verified")

        # SAFETY KERNEL: Route through finalization choke point
        # Handles: action_hash, event_hash, provenance, DAG registration, cycle detection
        return self._finalize_action(action_obj)
    
    def _finalize_action(self, action_obj, now_ms=None):
        """Instance method wrapper for _finalize_action_static."""
        return _finalize_action_static(
            action_obj,
            ctx_hash=self._ctx_hash,
            source=self.source or "",
            dag=self._action_dag,
            mode=self.mode,
            now_ms=now_ms
        )
    
    def _apply_binary_op(self, left, op, right):
        # --- 0. Domain unwrapping -----------------------------------------
        # If operands are domain objects (e.g. literals), extract values
        # This allows operators to work on raw values or metadata if needed
        
        def _extract_key(x):
            if isinstance(x, dict) and x.get("domain") == "literal":
                return x.get("key")
            return None

        left_key = _extract_key(left)
        right_key = _extract_key(right)

        # Explicit safe error propagation
        if isinstance(left, dict) and left.get("domain") == "error":
            return left
        if isinstance(right, dict) and right.get("domain") == "error":
            return right
            
        if (isinstance(left, dict) and left.get("domain") == "undefined") or left == "undefined":
            return "undefined"
        if (isinstance(right, dict) and right.get("domain") == "undefined") or right == "undefined":
            return "undefined"

        def _unwrap(x):
            if isinstance(x, dict) and "value" in x:
                return x["value"]
            return x

        left_val = _unwrap(left)
        right_val = _unwrap(right)
        
        # Pre-check noq RHS type (Priority: Schema > Context)
        if op == "noq" and self.mode == "strict":
            if not (isinstance(right_val, dict) and right_val.get("type") == "action"):
               # Check if it was undefined input (propagate)
               if right_val == "undefined":
                   return "undefined"
               return {"domain": "error", "code": "ERR_ACTION_MISUSE", "value": "noq RHS must be an action"}

        # 1. Grounding check (requires context access)
        # We need to check if the operation is valid in the current context
        # e.g. spatial ops require entities to exist in C.entities
        if not check_grounding(op, (left, right), self.ctx):
            return "undefined"

        # Guarded execution: kra
        #
        # Semantics:
        #   left kra right
        #   - Evaluate left as condition in three-valued logic.
        #   - If left is True  => return right unchanged (action, truth, numeric, etc.).
        #   - If left is False => guard fails, return undefined.
        #   - If left is U     => guard unknown, return undefined.
        if op == "kra":
            tL = self._to_trit(left_val)
            if tL is None or tL is False:
                return "undefined"
            return right_val

        # Logical AND (an) - Strong Kleene (K3) with False short-circuit
        # False AND anything = False (including Undefined)
        # True AND Undefined = Undefined
        # True AND True = True
        if op == "an":
            tL = self._to_trit(left_val)
            tR = self._to_trit(right_val)

            # K3 Short-circuit: False dominates
            if tL is False or tR is False:
                return False
            
            # If either is Undefined (and neither is False), result is Undefined
            if tL is None or tR is None:
                return "undefined"

            # Both are True
            return True

        # Logical OR (ur) - Kleene Strong OR
        if op == "ur":
            tL = self._to_trit(left_val)
            tR = self._to_trit(right_val)

            # Kleene Strong OR: If either is True, result is True
            if tL is True or tR is True:
                return True
            # If both are False, result is False
            if tL is False and tR is False:
                return False
            # Otherwise (one is U, or both U) -> Undefined
            return "undefined"

        # Spatial Operators (nel, tel, xel, en, tra, fra)
        if op in ("nel", "tel", "xel", "en", "tra", "fra"):
            # 1. Resolve both operands to entity keys
            def _as_entity_key(val, explicit_key):
                if explicit_key: return explicit_key
                if isinstance(val, str) and val.startswith("@"): return val
                if isinstance(val, dict) and val.get("kind") == "deixis":
                     return val.get("entity")
                # Glyphs might be entities if in D_e, but for now assume literals/deixis
                return None

            key_L = _as_entity_key(left_val, left_key)
            key_R = _as_entity_key(right_val, right_key)

            if not key_L or not key_R:
                return "undefined"

            # 2. Get Spatial Context
            spatial = self._get_context_field("spatial")
            entities = self._get_context_field("entities")
            if not isinstance(spatial, Mapping) or not isinstance(entities, Mapping):
                return "undefined"
            
            # 3. Resolve Positions
            def _get_pos(k):
                ent = entities.get(k)
                if isinstance(ent, Mapping):
                    p = ent.get("position") or ent.get("pos")
                    if isinstance(p, list) and len(p) >= 2:
                        return p
                return None

            pos_L = _get_pos(key_L)
            pos_R = _get_pos(key_R)

            if not pos_L or not pos_R:
                return "undefined"

            # 4. Compute Metrics
            import math
            dx = pos_R[0] - pos_L[0]
            dy = pos_R[1] - pos_L[1]
            dist = math.sqrt(dx*dx + dy*dy)
            
            # Strict Geometry Checks for tra/fra
            # V1.0 Safe Update: Check v_min and d_min to avoid unsafe "undefined" behavior
            if op in ("tra", "fra"):
                # Get cone parameters
                cone = spatial.get("cone", {})
                v_min = cone.get("v_min", 0.0) # velocity threshold
                d_min = cone.get("d_min", 0.0) # distance threshold (co-located)
                
                # If too close, mechanics are undefined (singularity)
                if dist < d_min:
                    return "undefined"
                
                # Need velocity for tra/fra
                # Assume entities have 'vel' [vx, vy]
                def _get_vel(k):
                    ent = entities.get(k)
                    if isinstance(ent, Mapping):
                        v = ent.get("velocity") or ent.get("vel")
                        if isinstance(v, list) and len(v) >= 2:
                            return v
                    return None
                
                vel_L = _get_vel(key_L)
                if not vel_L:
                     # Stationary or unknown velocity -> undefined directionality
                     return "undefined"
                     
                vx, vy = vel_L[0], vel_L[1]
                speed = math.sqrt(vx*vx + vy*vy)
                
                if speed < v_min:
                    # Not moving fast enough to have "front/behind"
                    return "undefined"
                    
                # Normalize velocity
                nx, ny = vx/speed, vy/speed
                
                # Vector L->R
                rx, ry = dx/dist, dy/dist
                
                # Dot product: cos(theta)
                dot = nx*rx + ny*ry
                
                # Cone check (default 45 deg ~ 0.707)
                limit = cone.get("cos_theta", 0.707)
                
                if op == "tra": # Towards / Front
                    return dot >= limit
                if op == "fra": # Away / Behind (simplified: dot product negative?)
                    # "fra" usually means "front-away" or "facing away"? 
                    # Noe spec: "fra" = derived from "from". 
                    # Often means L is moving away from R, or R is behind L.
                    # Standard interpretation: dot < -limit (behind)
                    return dot <= -limit

            thresholds = spatial.get("thresholds", {})
            
            if op == "nel": # Near
                limit = thresholds.get("near")
                if not isinstance(limit, (int, float)): return "undefined"
                return dist <= limit

            if op == "tel": # Far
                limit = thresholds.get("far")
                if not isinstance(limit, (int, float)): return "undefined"
                return dist >= limit

            if op == "xel": # Aligned (Orientation)
                # Requires orientation context
                orientation = spatial.get("orientation", {})
                target_angle = orientation.get("target")
                tolerance = orientation.get("tolerance")
                if not isinstance(target_angle, (int, float)) or not isinstance(tolerance, (int, float)):
                    return "undefined"
                
                # Compute angle from L to R
                angle = math.degrees(math.atan2(dy, dx))
                diff = abs(angle - target_angle)
                # Normalize diff to [0, 180]
                diff = diff % 360
                if diff > 180: diff = 360 - diff
                
                return diff <= tolerance

            if op == "en": # In Region
                # Treat R as a region defined by its position and a radius?
                # Or check if R is a region in C.regions?
                # For now, let's assume R is an entity defining a circular region with 'radius'
                ent_R = entities.get(key_R, {})
                radius = ent_R.get("radius")
                if not isinstance(radius, (int, float)):
                    # Fallback: check C.regions if R is a region key?
                    # But R is an entity key here.
                    return "undefined"
                return dist <= radius

            # Redundant tra/fra removed (Strict logic applied above)
            pass

        # Request: noq (request action)
        if op == "noq":
            # noq: <entity> noq <action> -> request event in D_action

            def _as_entity_key_noq(val, explicit_key):
                if explicit_key: return explicit_key

                # bool/numeric -> not entity
                if isinstance(val, (bool, int, float)):
                    return None

                # Deixis struct
                if isinstance(val, dict) and val.get("kind") == "deixis":
                    ent = val.get("entity")
                    return ent if isinstance(ent, str) else None

                # String (Literal or Glyph)
                if isinstance(val, str):
                    # Check if it's a known entity glyph
                    if val in GLYPH_MAP:
                        if GLYPH_MAP[val].get("domain") == "D_e":
                            return val
                        return None
                    # Otherwise assume literal or raw entity string
                    return val

                return None

            # 1. Check RHS is action
            is_action = (isinstance(right_val, dict) and right_val.get("type") == "action")
            if not is_action:
                # If undefined, propagate undefined (e.g. ungrounded input)
                if right_val == "undefined":
                    return "undefined"
                
                # In strict mode, mismatched type is an error
                if self.mode == "strict":
                    return {"domain": "error", "code": "ERR_ACTION_MISUSE", "value": "noq RHS must be an action"}
                
                return "undefined"

            # 2. Check LHS is entity
            subj_key = _as_entity_key_noq(left_val, left_key)
            if subj_key is None:
                return "undefined"

            # 3. Construct request action
            # Normalize noq schema (verb="noq", standard target)
            # Use pointer semantics for hashing: child_action_hash determines identity
            child_action_hash = right_val.get("action_hash")
            
            request_action = {
                "type": "action",
                "kind": "request",
                "verb": "noq",
                "subject": subj_key,
                "target": right_val,
                "child_action_hash": child_action_hash
            }

            # SAFETY KERNEL: Route through finalization
            return self._finalize_action(request_action)

        # Numeric comparisons (with deixis-awareness)
        if op in ("<", ">", "<=", ">=", "="):
            if left_val == "undefined" or right_val == "undefined":
                return "undefined"

            def _as_num(x):
                # Plain numeric
                if isinstance(x, (int, float)):
                    return float(x)

                # Deixis struct with distance
                if isinstance(x, dict) and x.get("kind") == "deixis":
                    dist = x.get("distance")
                    if isinstance(dist, (int, float)):
                        return float(dist)
                    return None

                # Everything else not allowed in numeric comparators
                return None

            a = _as_num(left_val)
            b = _as_num(right_val)
            if a is None or b is None:
                return "undefined"

            if op == "<":
                return a < b
            if op == ">":
                return a > b
            if op == "<=":
                return a <= b
            if op == ">=":
                return a >= b
            if op == "=":
                return a == b


        # Relational lookup (NIP-009 + Deixis Entities v1.1)
        if op in ("kos", "til", "rel"):
            rel = self._get_context_field("rel")
            if not isinstance(rel, dict):
                return "undefined"

            rel_table = rel.get(op)
            if not isinstance(rel_table, dict):
                return "undefined"

            def _as_left_key(x):
                """
                Extract entity key from left side:
                  - Deixis struct with entity field → extract entity
                  - Plain string → use as-is
                  - Anything else → str(x)
                """
                # Case 1: Already a valid entity string
                if isinstance(x, str):
                    return x

                # Case 2: Deixis struct (resolved by demonstratives)
                # Our deixis structs have: {"kind": "deixis", "entity": "robot_1", ...}
                if isinstance(x, dict) and x.get("kind") == "deixis":
                    ent = x.get("entity")
                    if isinstance(ent, str):
                        return ent
                    return None

                # Case 3: Fallback to string representation
                return str(x)

            def _as_right_key(x):
                """
                Right side:
                  - if a literal '@home'      → preserve literal
                  - else                      → str(x)
                """
                if isinstance(x, str) and x.startswith("@"):
                    return x
                return str(x)

            L = _as_left_key(left_val)
            R = _as_right_key(right_val)

            if L is None:
                return "undefined"

            row = rel_table.get(L)
            if not isinstance(row, dict):
                return "undefined"

            val = row.get(R)
            if isinstance(val, bool):
                return val

            return "undefined"

        # Removed duplicate tra/fra implementations (lines 2075-2104)
        # Velocity-based implementations at lines 1915-1944 are correct

        # Spatial: Axis-aligned comparisons (lef, rai, sup, bel, fai, ban)
        if op in ("lef", "rai", "sup", "bel", "fai", "ban"):
            # 1. Resolve positions
            def _get_pos(val):
                # If val is a dict with {x,y,z}, use it directly
                if isinstance(val, dict) and "x" in val and "y" in val and "z" in val:
                    return val
                
                # If val is a string (entity/frame ID), look it up
                if isinstance(val, str):
                    # Try local position map first (dynamic entities)
                    # C.local.position = { "robot_1": {x,y,z}, ... }
                    local = self.ctx.get("local", {})
                    pos_map = local.get("position", {})
                    if val in pos_map:
                        return pos_map[val]
                    
                    # Try root spatial frames (static landmarks)
                    # C.root.spatial.frames = { "home": {x,y,z}, ... }
                    root = self.ctx.get("root", {})
                    spatial = root.get("spatial", {})
                    frames = spatial.get("frames", {})
                    if val in frames:
                        return frames[val]
                        
                    # Try entities map
                    # C.entities = { "robot_1": { "position": {x,y,z} } }
                    entities = self._get_context_field("entities")
                    if isinstance(entities, dict) and val in entities:
                        ent = entities[val]
                        if isinstance(ent, dict) and "position" in ent:
                            return ent["position"]
                            
                return None

            p1 = _get_pos(left_val)
            p2 = _get_pos(right_val)
            
            if p1 is None or p2 is None:
                return "undefined"
                
            try:
                x1, y1, z1 = float(p1["x"]), float(p1["y"]), float(p1["z"])
                x2, y2, z2 = float(p2["x"]), float(p2["y"]), float(p2["z"])
            except (KeyError, ValueError, TypeError):
                return "undefined"
                
            # 2. Compare based on axis definitions (from registry)
            # lef/rai: X axis (Horizontal)
            # sup/bel: Z axis (Vertical)
            # fai/ban: Y axis (Depth/Forward)
            
            if op == "lef": return x1 < x2
            if op == "rai": return x1 > x2
            
            if op == "sup": return z1 > z2
            if op == "bel": return z1 < z2
            
            if op == "fai": return y1 > y2
            if op == "ban": return y1 < y2
            
            return "undefined"

        # Fallback structural
        return [left_val, op, right_val]

    # --- Conjunction (high-precedence binary operators) ---

    def visit_conjunction_op(self, node, children):
        """Convert conjunction operator node to string."""
        return node.value

    def visit_conjunction(self, node, children):
        """
        Handles chains like:

            L
            L op R
            L op1 R1 op2 R2 op3 R3 ...

        using left-associative fold:

            (((L op1 R1) op2 R2) op3 R3) ...

        We DO NOT early-return on 'undefined' to preserve associativity
        under Kleene semantics.
        """
        if len(children) == 1:
            return children[0]

        # Semantic Robustness
        # Arpeggio sometimes returns (op, val) tuples for ZeroOrMore/OneOrMore
        # We must flatten the list to [val, op, val, op, val...]
        flat = []
        for x in children:
            if isinstance(x, (list, tuple)):
                flat.extend(x)
            else:
                flat.append(x)

        result = flat[0]
        i = 1
        
        # Operators handled by conjunction
        # Add 'rel' to operator set
        ops = {"an", "kos", "til", "nel", "tel", "xel", "en", "kra", "tra", "fra", "noq", "lef", "rai", "sup", "bel", "fai", "ban", "rel", "<", ">", "<=", ">=", "="}

        while i < len(flat):
            item = flat[i]
            # if self.debug: print(f"DEBUG LOOP: item='{item}', type={type(item)}, in_ops={'noq' in ops}, item_in_ops={item in ops}")


            # Only treat plain string tokens as operators.
            # Lists, dicts, and other structures must never be compared to ops.
            if isinstance(item, str) and item in ops:
                # Binary operator
                if i + 1 >= len(flat):
                    return "undefined"
                rhs = flat[i + 1]
                result = self._apply_binary_op(result, item, rhs)
                i += 2
            else:
                # Implicit juxtaposition -> list construction
                # Unwrap literal object if present
                if isinstance(item, dict) and item.get("domain") == "literal":
                    item = item["value"]

                # If result is already a list, append; else start new list.
                # Note: This is a simple structural list, not a specific domain object yet.
                if isinstance(result, list):
                    result.append(item)
                else:
                    # Unwrap result if it's a literal object (first item)
                    if isinstance(result, dict) and result.get("domain") == "literal":
                        result = result["value"]
                    result = [result, item]
                i += 1
        return result

    def visit_disjunction(self, node, children):
        """
        Handles OR (ur) chains at lower precedence than AND (an).
        """
        if self.debug:
            print(f"DEBUG visit_disjunction called with {len(children)} children")

        if len(children) == 1:
            return children[0]

        # Semantic Robustness - flatten nested shapes
        # Same pattern as visit_conjunction to handle Arpeggio's (op, val) tuples
        flat = []
        for x in children:
            if isinstance(x, (list, tuple)):
                flat.extend(x)
            else:
                flat.append(x)

        if len(flat) % 2 == 0:
            return "undefined"

        result = flat[0]
        i = 1
        while i < len(flat):
            op = flat[i]  # should always be "ur"
            rhs = flat[i + 1]
            result = self._apply_binary_op(result, op, rhs)
            i += 2

        return result

    def visit__default__(self, node, children):
        if self.debug:
            print(f"DEBUG visit__default__: {repr(node.rule_name)}")
        # For Terminal nodes (keywords, operators), return the node value
        # Terminal nodes have no children, so we check if children is empty
        if not children and hasattr(node, 'value'):
            return node.value
        return children

    # --- Top level expression and chain ---

    def visit_expression(self, node, children):
        if self.debug:
            print(f"DEBUG visit_expression called with {len(children)} children")
        return self._handle_conditional(children)

    def visit_conditional(self, node, children):
        if self.debug:
            print(f"DEBUG visit_conditional called with {len(children)} children")
        return self._handle_conditional(children)

    def _handle_conditional(self, children):
        """Handle conditional (khi) expressions with robust child flattening."""
        try:
            if self.debug:
                print(f"DEBUG _handle_conditional: len(children)={len(children)}, children={children}")
            
            # If only one child, return it directly. 
            # This preserves semantic lists returned by disjunction/conjunction (e.g. implicit juxtaposition).
            if len(children) == 1:
                return children[0]
                
            # Flatten children to handle Arpeggio's nested shapes
            # Same pattern as visit_conjunction/visit_disjunction
            flat = []
            for x in children:
                if isinstance(x, (list, tuple)):
                    flat.extend(x)
                else:
                    flat.append(x)
            
            # expression -> conditional -> disjunction ('khi' scoped)?
            # Because 'expression' returns 'conditional' directly, Arpeggio may collapse them.
            # So we handle the logic here.
            
            # Case 1: No khi guard
            if len(flat) == 1:
                return flat[0]
            
            # 2. If we have 'khi', we expect: [condition, 'khi', action_block]
            if len(children) == 3 and children[1] == "khi":
                condition = children[0]
                action_block = children[2]
                if self.debug:
                    print(f"DEBUG _handle_conditional: condition={condition}, action_block={action_block}")

                # Strict mode: Guard must be explicitly boolean (truth domain)
                # We do NOT allow implicit truthiness of numbers or strings here.
                # But we DO allow 'undefined' (None or string) to pass through to the next check
                # so it returns "undefined" instead of an error.
                # We also allow domain objects (undefined/error) which will be handled by _to_trit.
                is_special_obj = isinstance(condition, dict) and condition.get("domain") in ("undefined", "error")
                
                if self.mode == "strict" and condition != "undefined" and condition is not None and not isinstance(condition, bool) and not is_special_obj:
                     return {
                        "domain": "error",
                        "code": "ERR_GUARD_TYPE",
                        "value": f"Guard condition must be boolean, got {type(condition).__name__}"
                    }

                # Evaluate condition (truth domain)
                t = self._to_trit(condition)
                if t is None:
                    # Condition not truth-typed → undefined
                    return "undefined"
                if t:
                    # Guard holds → return the action clause
                    if self.mode == "strict":
                        # Recursive check for action lists (to support nested sek scopes)
                        def is_valid_action_structure(obj):
                            if isinstance(obj, dict):
                                if obj.get("domain") == "error":
                                    return True # Allow inner errors to bubble up
                                if obj.get("type") == "action" or obj.get("domain") == "action":
                                    return True
                                if obj.get("domain") == "list" and isinstance(obj.get("value"), list):
                                    return all(is_valid_action_structure(x) for x in obj["value"])
                            if isinstance(obj, list):
                                return all(is_valid_action_structure(x) for x in obj)
                            return False

                        if not is_valid_action_structure(action_block):
                            # Error: Right-hand side of khi must be an action or list of actions
                            return {
                                "domain": "error",
                                "code": "ERR_GUARD_TYPE",
                                "value": "Right-hand side of 'khi' must be an action or list of actions in strict mode"
                            }

                    return action_block
                else:
                    # Guard fails → no action taken
                    return "undefined"

            # Anything else is malformed
            return "undefined"
        except Exception as e:
            # User Requirement: Quiet on error unless debug
            if self.debug or _DEBUG_ENABLED:
                import traceback
                traceback.print_exc()
            
            return "undefined"

    def visit_chain(self, node, children):
        if self.debug:
            print(f"DEBUG visit_chain children: {children}")
        return wrap_domain(children[0])

# ==========================================
# 5. PUBLIC API
# ==========================================
def run_noe_logic(chain_text, context_object, mode="strict", audience=None, to=None, debug=False, source=None):
    """
    Parse and evaluate a Noe chain against a context object.

    mode:
      - 'strict'  = no inferred context; context MUST conform to NIP-009
      - 'partial' = minimal defaults where missing (non-normative)
    
    audience: optional addressing hint ("broadcast", "unicast", etc.)
    to: optional logical target id (e.g. "agent_42", "sensor_temp_1")

    Returns a dict with at least:
      - 'domain' : 'truth' | 'numeric' | 'action' | 'undefined' | 'error' | 'question'
      - 'value'  : internal value for the domain (or None)
      - 'meta'   : {
            'context_hash': <hex SHA-256 of normalized C>,
            'mode': 'strict' | 'partial'
        }
    """
    # Canonicalize chain text ONCE at entry
    # Use canonical form for: validation, parsing, caching, hashing, provenance
    import unicodedata
    canonical_chain = unicodedata.normalize('NFKC', chain_text)
    canonical_chain = ' '.join(canonical_chain.split())  # Collapse whitespace
    
    # Use canonical chain everywhere from here on
    chain_text = canonical_chain
    
    # Preserve original context layers for hashing, preprocess for backward compatibility
    # Some tests pass structured {root, domain, local} that needs preprocessing
    import copy
    effective_ctx = context_object # Default to original
    hash_ctx = context_object      # Default: hash the original input

    # If input has old-style structured layers WITHOUT shard keys, preprocess
    if isinstance(context_object, dict):
        has_layers = "root" in context_object or "domain" in context_object or "local" in context_object
        shard_keys = {"literals", "entities", "spatial", "temporal", "modal", "axioms"}
        has_shards_top = any(k in context_object for k in shard_keys)
        
        # If structured but no top-level shards, this is old-style layered
        if has_layers and not has_shards_top:
            # Separate hashing context from effective context
            # Keep original layered structure for Merkle hashing
            hash_ctx = context_object
            
            # Flatten for validator/evaluator compatibility
            # (Test suite expects flat context for strict validation)
            def _merge_dicts(base, overlay):
                if not isinstance(overlay, dict):
                    return copy.deepcopy(overlay)
                result = copy.deepcopy(base) if isinstance(base, dict) else {}
                for k, v in overlay.items():
                    if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                        result[k] = _merge_dicts(result[k], v)
                    else:
                        result[k] = copy.deepcopy(v)
                return result
            
            r = context_object.get("root", {})
            d = context_object.get("domain", {})
            l = context_object.get("local", {})
            effective_ctx = _merge_dicts(_merge_dicts(r, d), l)
            
    # Validator and Evaluator see the EFFECTIVE (flat) context
    # STRICT INVALIDATION of flattening. Pass structured context to validator if available.
    val_ctx = hash_ctx
    eval_ctx = effective_ctx
    
    # Hashing (ContextManager) sees the HASH CONTEXT (layered if possible)
    ctx = hash_ctx
    # Initialize hash variables (CRITICAL for non-dict contexts)
    snap = None
    hashes = {"root": "", "domain": "", "local": "", "total": ""}
    ctx_hash = ""
    
    if isinstance(ctx, dict):
        # 1. Detect if context is Layered or Flat
        shard_keys = {"literals", "entities", "spatial", "temporal", "modal", "axioms", "audit", "rel"}
        has_shards = any(k in ctx for k in shard_keys)
        
        # Determine layering
        if not has_shards and ("root" in ctx or "domain" in ctx or "local" in ctx):
            # Layered context
            c_root = ctx.get("root", _EMPTY_DICT)
            c_domain = ctx.get("domain", _EMPTY_DICT)
            c_local = ctx.get("local", _EMPTY_DICT)
        else:
            # Flat context - treat as entirely local
            c_root = _EMPTY_DICT
            c_domain = _EMPTY_DICT
            c_local = ctx

        try:
            # 2. Use ContextManager to compute REAL shard hashes
            import copy
            cm = ContextManager(root=c_root, domain=c_domain, local=c_local)
            snap = cm.snapshot()
            
            # Compute independent flat effective context for validation and evaluation
            # This preserves test semantics (check_grounding expects flat ctx)
            # Use snap.structured because ContextSnapshot stores layers there.
            effective_flat = merge_layers_for_validation(snap.structured)
            
            # 4. Use flat context for validation AND evaluation
            val_ctx = effective_flat
            # Deepcopy eval_ctx to prevent mutation
            eval_ctx = copy.deepcopy(effective_flat)
            
            # 5. Get REAL Merkle hashes from snapshot (computed on actual layers)
            hashes = {
                "root": snap.root_hash,
                "domain": snap.domain_hash,
                "local": snap.local_hash,
                "total": snap.context_hash
            }
            ctx_hash = snap.context_hash
        except Exception as e:
            # If ContextManager fails (e.g. bad types inside dict), fall back to raw context
            # Validator will catch the mess.
            if _DEBUG_ENABLED:
                print(f"DEBUG: ContextManager failure: {e}")
                traceback.print_exc()
            # Ensure we have a context hash for meta/provenance even on failure
            try:
                 # Use plural hashes computation to satisfy validator (needs 'domain' key)
                 hashes = compute_context_hashes(val_ctx)
                 ctx_hash = hashes.get("total", "")
            except Exception:
                 ctx_hash = "" 
                 hashes = {"root": "", "domain": "", "local": "", "total": ""}

    # --- Strict-mode validation via Verified Validator ---
    if mode == "strict":
        v_err = None
        v_result = None

        # val_ctx is already merged/normalized if valid dict
        try:
            v_result = validate_chain(chain_text=chain_text, context_object=val_ctx, mode="strict", context_hashes=hashes)
            # Wrap validation result for consistent error reporting
            if isinstance(v_result, bool):
                v_result = {'ok': v_result, 'context_hashes': {}, 'errors': []}
        except NameError:
            # If validator is not wired, we can't validate properly in strict mode.
            if _DEBUG_ENABLED:
                traceback.print_exc()
            v_result = {"ok": False, "context_error": "ERR_VALIDATOR_MISSING"}
        except Exception as e:
            # Catch unexpected validator crashes
            if _DEBUG_ENABLED:
                traceback.print_exc()
            # Clean strict error return instead of printing traceback and continuing
            return {
                "domain": "error",
                "code": "ERR_INTERNAL",
                "value": "blocked",
                "details": f"ERR_INTERNAL: {type(e).__name__}: {e}",
                "meta": {"mode": mode, "context_hashes": hashes or {}},
            }

        # Process result unconditionally
        if isinstance(v_result, dict):
            if not v_result.get("ok", True):
                # 1. Try to get specific error from 'errors' list
                raw_code = None
                v_err_msg = None
                
                if v_result.get("errors"):
                    first = v_result["errors"][0]
                    if isinstance(first, dict):
                        raw_code = first.get("code")
                        v_err_msg = first.get("detail")
                
                # 2. Fallback to context_error
                if not raw_code:
                    raw_code = v_result.get("context_error")
                
                v_err = raw_code or "Validator reported failure"
                
                # 3. Construct message if not already set
                if not v_err_msg:
                    if "reasons" in v_result and v_result["reasons"]:
                         v_err_msg = f"{v_err}: {'; '.join(v_result['reasons'])}"
                    else:
                         v_err_msg = v_err
        elif v_result:  # non-empty string or other truthy error
            v_err = v_result
            v_err_msg = v_result
            raw_code = v_result if isinstance(v_result, str) and v_result.startswith("ERR_") else None

        # STRICT MODE: Map flags to errors logic to ensure deterministic failing
        if mode == "strict" and isinstance(v_result, dict) and not v_result.get("ok"):
             if not v_result.get("context_error"):
                 flags_map = v_result.get("flags", {})
                 code = None
                 
                 # User Priority Order:
                 # 1. Parse / Invalid Literal (Fundamental)
                 if flags_map.get("invalid_literal"): code = "ERR_INVALID_LITERAL"
                 
                 # 2. Context Schema / Incomplete (Structural)
                 elif flags_map.get("schema_invalid"): code = "ERR_CONTEXT_INCOMPLETE"
                 
                 # 3. Staleness
                 elif flags_map.get("context_stale"): code = "ERR_CONTEXT_STALE"
                 
                 # 3. Operator Grounding (Specifics)
                 elif flags_map.get("epistemic_mismatch"): code = "ERR_EPISTEMIC_MISMATCH"
                 elif flags_map.get("spatial_mismatch"): code = "ERR_SPATIAL_UNGROUNDABLE"
                 elif flags_map.get("demonstrative_ungrounded"): code = "ERR_DEMONSTRATIVE_UNGROUNDED"
                 elif flags_map.get("delivery_mismatch"): code = "ERR_CONTEXT_INCOMPLETE"
                 elif flags_map.get("audit_mismatch"): code = "ERR_CONTEXT_INCOMPLETE"
                 
                 # 4. Action Misuse
                 elif flags_map.get("action_misuse"): code = "ERR_ACTION_MISUSE"
                 
                 # 5. Literal Missing (Lowest Priority as per specific guidance, or check priority)
                 elif flags_map.get("literal_mismatch"): code = "ERR_LITERAL_MISSING"
                 
                 # 5. Literal Missing (Dependency)
                 elif flags_map.get("literal_mismatch"): code = "ERR_LITERAL_MISSING"
                 
                 if code:
                     v_result["context_error"] = code
                     # Update v_err immediately for strict return
                     v_err = code
        
        if debug:
            print(f"DEBUG PARSER: v_result ok={v_result.get('ok')}")
            print(f"DEBUG PARSER: v_result context_error={v_result.get('context_error')}")
            print(f"DEBUG PARSER: v_err calculated={v_err}")

                     
        if v_err:
            if debug: print(f"DEBUG run_noe_logic: returning validation error: {v_err}")
            code = "ERR_BAD_CONTEXT"
            val_msg = v_err_msg if 'v_err_msg' in locals() else str(v_err)
            
            # If we have a raw code that looks like an error code, use it
            if raw_code and isinstance(raw_code, str) and raw_code.startswith("ERR_"):
                code = raw_code
            elif isinstance(v_err, str) and v_err.startswith("ERR_"):
                 # Fallback if v_err itself is the code
                 code = v_err.split(":")[0] # Split just in case
            
            # Special case: If validator returned a specific code in the error dict, use it.
            # This handles cases where validate_chain returns a list of errors.
            if isinstance(v_result, dict) and v_result.get("errors"):
                 first = v_result["errors"][0]
                 if isinstance(first, dict) and first.get("code"):
                     code = first.get("code")

            flags = v_result.get("flags", {}) if isinstance(v_result, dict) else {}
            
            # 4. Construct Error Response
            return {
                "domain": "error",
                "code": code,
                "value": "blocked",  # User Requirement: value='blocked'
                "details": val_msg,  # Preserve message for debug
                "meta": {
                    "context_hash": ctx_hash,
                    "mode": mode,
                    "flags": flags,
                    "context_hashes": hashes,
                }
            }

        # Staleness check (NIP-015) - overrides validation error IF STALE
        if isinstance(val_ctx, dict) and val_ctx.get("stale"):
            if debug: print(f"DEBUG run_noe_logic: detected stale context")
            return {
                "domain": "error",
                "code": "ERR_STALE_CONTEXT",
                "value": "Context is stale (max_skew_ms exceeded).",
                "meta": {
                    "context_hash": ctx_hash,
                    "context_hashes": hashes,
                    "mode": mode,
                }
            }
    
    # --- Check for AST Safety Properties (checking guard grounding, nested epistemics) ---
    # Convert chain text to AST first? Or parse later?
    # We usually parse later. But we can parse now.
    
    # ----------------------------------------------------
    # Execution (Partial or Validated Strict)
    # ----------------------------------------------------

    # Chain already canonicalized at function entry - use directly
    # Use global parser instance for cache safety
    parser = _get_or_create_parser()
    try:
        # Use cached AST if available (cache key uses canonical chain)
        parse_tree = _get_cached_ast(parser, chain_text)
        
        # In strict mode, deep structural validation (recursion depth, etc.) 
        # was already performed by validate_chain() above. 
        # We proceed directly to evaluation.

        # DEBUG: Print parse tree
        # with open("debug_tree.log", "a") as f:
        #     f.write(f"DEBUG: Parse Tree: {parse_tree}\n")
        
        # Pass merged context to evaluator so it can access entities/spatial directly
        # merged_eval_ctx = merge_layers_for_validation(eval_ctx) # Redundant: eval_ctx is already merged
        evaluator = NoeEvaluator(
            context=eval_ctx, 
            mode=mode, 
            debug=debug, 
            source=source if source is not None else chain_text,
            context_hash=ctx_hash,  # Pass precomputed hash
            context_hashes=hashes
        )
        
        # Only write debug files if debug=True
        if debug:
            if "visit_conditional" not in dir(evaluator):
                 with open("debug_visitor.log", "a") as f:
                    f.write("DEBUG: visit_conditional NOT FOUND in evaluator!\n")
            else:
                 with open("debug_visitor.log", "a") as f:
                    f.write("DEBUG: visit_conditional FOUND in evaluator.\n")

        result = visit_parse_tree(
            parse_tree,
            evaluator
        )


        # Note: In strict mode, undefined still stays as domain="undefined"
        # It is a semantic value that blocks execution, not an error.
        # Per NIP-009: undefined = incomplete context or type signature failure
        # This is distinct from structural errors (parse failures, bad context, etc.)


        # If evaluator already returned a domain dict, attach meta
        if isinstance(result, dict) and "domain" in result:
            meta = result.get("meta") or {}
            meta.setdefault("context_hash", ctx_hash)
            meta.setdefault("mode", mode)
            # Attach all Merkle hashes for full provenance
            meta["context_hashes"] = hashes
            result["meta"] = meta
            
            # Attach question provenance for question domain
            if result.get("domain") == "question":
                import time
                timestamp = time.time()
                timestamp_ms = int(timestamp * 1000)  # Store exact ms for hash consistency
                
                q_val = result.get("value", {}) or {}
                question_type = q_val.get("type")
                
                question_hash = compute_question_hash(
                    chain_text=chain_text,  # Already canonical from function entry
                    context_hash=ctx_hash,
                    timestamp=timestamp,
                    question_type=question_type,
                    audience=audience,
                    to=to,
                )
                
                # Attach to meta
                meta["question_hash"] = question_hash
                meta["timestamp"] = timestamp
                meta["timestamp_ms"] = timestamp_ms  # Exact ms for deterministic replay
                meta["chain"] = chain_text  # Already canonical
                if audience is not None:
                    meta["audience"] = audience
                if to is not None:
                    meta["to"] = to
                
                # Build provenance record
                prov = {
                    "kind": "question",
                    "question_hash": question_hash,
                    "chain": chain_text,
                    "context_hash": ctx_hash,
                    "timestamp": timestamp,
                }
                if question_type is not None:
                    prov["question_type"] = question_type
                if audience is not None:
                    prov["audience"] = audience
                if to is not None:
                    prov["to"] = to
                
                meta["provenance"] = prov
            
            return result

        # Otherwise, wrap raw value into a domain and attach meta
        wrapped = wrap_domain(result)
        wrapped["meta"] = {
            "context_hash": ctx_hash,
            "context_hashes": hashes,
            "mode": mode,
        }
        return wrapped

    except Exception as e:
        # User Requirement: Quiet on error unless debug
        if _DEBUG_ENABLED:
             traceback.print_exc()
        return {
            "domain": "error",
            "code": "ERR_PARSE_FAILED",
            "value": f"{e}",
            "meta": {
                "context_hash": ctx_hash,
                "context_hashes": hashes,
                "mode": mode,
            },
        }

def create_answer(parent_question_hash, answer_payload, context_object=None, answerer_id=None):
    """
    Create an answer record linked to a question via parent_question_hash.
    
    Args:
        parent_question_hash: Hash of the question being answered
        answer_payload: Dict with "domain" and "value" keys
        context_object: Optional context for answer (defaults to empty)
        answerer_id: Optional identifier for answering agent/system
    
    Returns:
        Dict with answer record including answer_hash and provenance
    """
    import time
    
    ctx = context_object or {}
    answer_hashes = compute_context_hashes(ctx)
    ctx_hash = answer_hashes["total"]
    timestamp = time.time()
    
    answer_hash = compute_answer_hash(
        parent_question_hash=parent_question_hash,
        answer_payload=answer_payload,
        context_hash=ctx_hash,
        timestamp=timestamp,
        answerer_id=answerer_id
    )
    
    return {
        "kind": "answer",
        "answer_hash": answer_hash,
        "parent_question_hash": parent_question_hash,
        "answer_payload": answer_payload,
        "context_hash": ctx_hash,
        "timestamp": timestamp,
        "answerer_id": answerer_id,
        "provenance": {
            "kind": "answer",
            "answer_hash": answer_hash,
            "parent_question_hash": parent_question_hash,
        }
    }


# ==========================================
# 6. CANONICAL SERIALIZER
# ==========================================
def serialize_noe(node):
    """
    Convert an evaluated Noe node (the value produced by the evaluator)
    back into a canonical Noe-like string.
    """

    # Primitive undefined
    if node == "undefined":
        return "undefined"

    # Typed domains
    if isinstance(node, dict) and "domain" in node and "value" in node:
        dom = node["domain"]
        val = node["value"]
        if dom == "truth":
            return "true" if bool(val) else "false"
        if dom == "numeric":
            return str(val)
        if dom == "undefined":
            return "undefined"
        return serialize_noe(val)

    # Action node
    if isinstance(node, dict) and node.get("type") == "action":
        verb = node["verb"]
        target = node["target"]
        return f"{verb} {serialize_noe(target)}"

    # Lists / tuples
    if isinstance(node, (list, tuple)):
        items = " ".join(serialize_noe(x) for x in node)
        return f"({items})"

    # Literals
    if isinstance(node, str) and node.startswith("@"):
        return node

    # Plain strings
    if isinstance(node, str):
        return node

    # Bare booleans
    if isinstance(node, bool):
        return "true" if node else "false"

    # Numbers
    if isinstance(node, (int, float)):
        return str(node)

    return str(node)
