"""
context_manager_v2.py

Structured Context Manager for Noe Runtime v1.0
-----------------------------------------------

Implements the three-level context model:

    C_root   : global, immutable invariants
    C_domain : domain-specific configuration / calibration
    C_local  : fast-changing, per-tick state

C_total = deep_merge(C_root, C_domain, C_local)

Hashes:
    H_root   = hash(C_root)
    H_domain = hash(C_domain)
    H_local  = hash(C_local)
    H_total  = H(H_root || H_domain || H_local)

Designed for:
    - NIP-009 (Context Serialization Standard)
    - NIP-011 (Reference Interpreter & Test Suite)
    - NoeRuntime (snapshot + staleness + provenance hashing)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Callable
import copy
import hashlib
import threading
import time


# -------------------------------------------------------------------------
# Exceptions
# -------------------------------------------------------------------------


class ContextError(Exception):
    """Base class for context-related errors."""


class ContextStaleError(ContextError):
    """Raised when a context snapshot is considered stale."""


class BadContextError(ContextError):
    """Raised when the context structure is malformed or incomplete."""


class ContextConflictError(ContextError):
    """Raised when a merge/update attempt detects an internal conflict."""


class ContextTooLargeError(ContextError):
    """
    Raised when a context shard exceeds the defined size limit.
    
    Noe Principle: Context is CONTROL PLANE, not DATA PLANE.
    Large blobs (images, point clouds) must be passed by reference (hash/ID),
    never by value.
    """


# -------------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------------


from .canonical import canonical_json


def _deep_freeze(obj: Any) -> Any:
    """
    Recursively freeze an object into an immutable form.
    
    - dict → MappingProxyType (immutable dict view)
    - list/tuple → tuple (recursively frozen)
    - set → REJECTED (sets are not valid JSON types; raises BadContextError)
    - primitives → unchanged
    
    This enables safe caching of hashes while preventing all mutation vectors,
    including nested lists that would otherwise remain mutable.
    
    Used internally to guarantee root/domain immutability.
    """
    if isinstance(obj, dict):
        from types import MappingProxyType
        return MappingProxyType({k: _deep_freeze(v) for k, v in obj.items()})
    elif isinstance(obj, (list, tuple)):
        return tuple(_deep_freeze(x) for x in obj)
    elif isinstance(obj, set):
        raise BadContextError("Sets are not pure JSON types and are unsupported in Noe context layers.")
    else:
        # Primitives (int, str, float, bool, None) are already immutable
        return obj


def _deep_unfreeze(obj: Any) -> Any:
    """
    Convert frozen immutable tree back to plain mutable dicts/lists/sets.
    
    Used when exposing data across public API boundary for JSON compatibility
    and to avoid surprising downstream code with MappingProxyType/frozenset.
    
    This is a one-way conversion - the returned mutable data is safe to use
    because it's a fresh copy with no references to internal frozen state.
    """
    from types import MappingProxyType
    
    if isinstance(obj, MappingProxyType):
        return {k: _deep_unfreeze(v) for k, v in obj.items()}
    elif isinstance(obj, tuple):
        # Convert back to list (tuples were used to freeze lists)
        return [_deep_unfreeze(x) for x in obj]
    else:
        return obj





def _hash_json_digest(obj: Any, max_size: int = 0) -> tuple[bytes, str]:
    """
    Hash a JSON-serializable object to SHA-256, returning both digest and hex.
    
    CRITICAL: Used for composing total hash from byte digests (matches validator).
    
    Args:
        obj: Object to hash
        max_size: Optional size limit in bytes for canonical JSON
    
    Returns:
        (digest_bytes, hex_string) tuple
    
    Raises:
        ContextTooLargeError: If canonical JSON exceeds max_size
    """
    s = canonical_json(obj).encode("utf-8")
    if max_size > 0 and len(s) > max_size:
        raise ContextTooLargeError(
            f"Context shard size {len(s)} exceeds limit {max_size}"
        )
    digest = hashlib.sha256(s).digest()
    return digest, digest.hex()


def _deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """
    Optimized immutable deep merge.
    O(N) instead of O(N * Depth) by shallow copying unless recursion is needed.
    """
    if not isinstance(base, dict) or not isinstance(overlay, dict):
        return copy.deepcopy(overlay)
    
    # Shallow copy base (O(1) pointer checks)
    result = base.copy()
    
    for k, v in overlay.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            # Recurse only on overlapping sub-dictionaries
            result[k] = _deep_merge(result[k], v)
        else:
            # Overwrite with deep copy to ensure immutability of new value
            result[k] = copy.deepcopy(v)
            
    return result


# -------------------------------------------------------------------------
# Snapshot
# -------------------------------------------------------------------------


@dataclass(frozen=True)
class ContextSnapshot:
    """
    Immutable snapshot of the three-level context.
    
    V1.0 Design:
        - local: Deep-copied volatile layer (safe, mutable copy)
        - c_merged: Materialized merged context (root < domain < local) for evaluation
        - structured: Full three-layer structure (for hashing/export)
        - *_hash: Cryptographic hashes for provenance
        - composite_hash: Composite hash of all 3 raw shards (NOT H_safe)
        - timestamp_ms, local_layer_age_stale: Metadata
    
    Immutability Guarantee:
        - The snapshot dataclass itself is frozen.
        - top-level dicts (local, c_merged, structured) are deep-copied.
        - Notice: Inner elements of these dict elements are STILL MUTABLE.
          If true immutability is needed at the boundary, downstream consumers
          must wrap them in read-only proxies or deeply clone.
    
    Performance Note:
        Creating c_merged costs O(|root| + |domain| + |local|) due to dict materialization.
        With typical 50KB contexts, expect 4-5ms per snapshot in Python.
        For <1ms performance, see v1.1 ChainMap/Mapping view approach.
    """
    
    # Core evaluation data (hot path)
    local: Dict[str, Any]       # Deep copy of volatile layer
    c_merged: Dict[str, Any]       # Materialized merged context
    structured: Dict[str, Any]   # {"root": ..., "domain": ..., "local": ...}
    
    # Provenance hashes
    root_hash: str
    domain_hash: str
    local_hash: str
    composite_hash: str
    
    # Metadata
    timestamp_ms: int
    local_layer_age_stale: bool


# -------------------------------------------------------------------------
# Context Manager v2
# -------------------------------------------------------------------------


class ContextManager:
    """
    Structured Context Manager for Noe.

    **Scope:** This manager owns raw-layer context only. It does NOT construct
    C_safe and does NOT compute H_safe; those are validator responsibilities
    (NIP-009 § π_safe, NIP-015 § evaluation semantics). Downstream code must
    never treat snapshot.c_merged or snapshot.composite_hash as a substitute
    for C_safe or H_safe.

    Responsibilities:
        - Maintain C_root, C_domain, C_local in memory.
        - Provide immutable snapshots with provenance hashes.
        - Enforce a coarse staleness window over C_local (manager heuristic only;
          normative per-literal staleness is enforced by the validator).

    Performance Characteristics:
        - snapshot() materializes merged dict: O(|root| + |domain| + |local|)
        - With typical 50KB contexts: 4-5ms per snapshot (Python dict overhead)
        - Layered hashing: Only hashes local (~1KB), reuses cached root/domain digests
        - Best for: 10-20Hz control loops with small contexts
        - For sub-ms performance: See v1.1 ChainMap/Mapping view approach
        - For higher rates or larger contexts, consider caching snapshots at caller level.

    Public API (used by NoeRuntime):

        cm = ContextManager(root, domain, local, staleness_ms=100)

        snap = cm.snapshot()
        cm.update_local(delta)
        cm.replace_local(new_local)
        cm.update_domain(delta)   # optional
        cm.replace_domain(new_domain)  # optional

    Notes:
        - root is treated as immutable (no public mutator by default).
        - domain changes are expected to be rare (calibration, config changes).
        - local is updated per tick by sensor fusion and controller logic.

    Immutability Guarantee:
        Snapshots deep-copy all three context layers (root, domain, local).
        This prevents external mutations from invalidating provenance hashes.
        
    Hash Provenance:
        snapshot.composite_hash is a composition of the raw shard digests only.
        It is NOT H_safe. The validator must compute H_safe after projecting C_safe.
    """

    def __init__(
        self, 
        root: Optional[Dict[str, Any]], 
        domain: Optional[Dict[str, Any]], 
        local: Optional[Dict[str, Any]],
        staleness_ms: int = 1000,
        max_shard_size: int = 256 * 1024,
        time_fn: Callable[[], float] = time.time
    ):
        """
        Initialize the context manager.

        Args:
            root:   C_root, global invariants. Must be a dict. Explicit None is rejected.
            domain: C_domain, environment/model config. Must be a dict. Explicit None is rejected.
            local:  C_local, mutable runtime state. Must be a dict. Explicit None is rejected.
            staleness_ms: maximum allowed age for C_local snapshots in strict mode.
            max_shard_size: maximum serialized size (bytes) for any single shard. 0 = unlimited.
                            Defaults to 256KB to enforce Control vs Data plane separation.
            time_fn: injectable time source (seconds), used for tests or simulation.
        """
        if not isinstance(root, dict) or not isinstance(domain, dict) or not isinstance(local, dict):
            raise BadContextError("Context layers (root, domain, local) must be dictionaries.")

        self._lock = threading.RLock()
        self._max_shard_size = max_shard_size
        
        # PERFORMANCE OPTIMIZATION: Deep-freeze static layers.
        # We freeze internal root/domain to prevent mutation and safely cache hashes.
        # Snapshots expose plain dict copies for API compatibility.
        
        self._root_frozen = _deep_freeze(copy.deepcopy(root))
        self._domain_frozen = _deep_freeze(copy.deepcopy(domain))
        
        # Local is the volatile layer - kept mutable, updated frequently. Guaranteed to be a dict.
        self._local: Dict[str, Any] = copy.deepcopy(local)

        self._staleness_ms = max(int(staleness_ms), 0)
        self._time_fn = time_fn

        # Track last update time for local context (in milliseconds)
        now_ms = self._now_ms()
        self._last_local_update_ms: int = now_ms
        
        # PERFORMANCE: Cache unfrozen dict versions (computed once, reused per snapshot)
        # This avoids O(N) unfreeze cost on every snapshot - only pay when root/domain change
        self._root_dict_cached = _deep_unfreeze(self._root_frozen)
        self._domain_dict_cached = _deep_unfreeze(self._domain_frozen)
        
        # CACHED DIGESTS: Cache digests of frozen static layers
        # Since root/domain are frozen, these digests are valid until explicit update
        self._root_digest, self._root_hash = _hash_json_digest(self._root_dict_cached, self._max_shard_size)
        self._domain_digest, self._domain_hash = _hash_json_digest(self._domain_dict_cached, self._max_shard_size)
        
        # NOTE: _base_merged optimization removed for v1.0 to enforce strict deep copy correctness.

        # Sanity check: ensure minimal structure where required
        self._validate_initial()

    # ------------------------------------------------------------------
    # Internal utilities
    # ------------------------------------------------------------------

    def _now_ms(self) -> int:
        return int(self._time_fn() * 1000)

    def _validate_initial(self) -> None:
        """
        Minimal sanity checks on the initial context structure.
        We keep this deliberately light; schema-level validation belongs in NIP-009 tooling.
        """
        # root_frozen and domain_frozen are always valid (frozen at init)
        # Just check local structure
        if not isinstance(self._local, dict):
            raise BadContextError("C_local must be a dict")



    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def root(self) -> Dict[str, Any]:
        """Return cached unfrozen copy of C_root (public API compatibility)."""
        return copy.deepcopy(self._root_dict_cached)

    @property
    def domain(self) -> Dict[str, Any]:
        """Return cached unfrozen copy of C_domain (public API compatibility)."""
        return copy.deepcopy(self._domain_dict_cached)

    @property
    def local(self) -> Dict[str, Any]:
        """Return a deep copy of C_local."""
        return copy.deepcopy(self._local)

    def snapshot(self) -> ContextSnapshot:
        """
        Thread-safe context snapshot with guaranteed provenance integrity.
        
        V1.0 HARDENING (Ghost-Write safe):
        1. Full Deep Copy: Aliasing is completely eliminated.
           - root/domain/local are deep copied before use.
           - This prevents 'snapshot corruption' where mutating the snapshot 
             retroactively affects the manager's state.
        2. Fresh Merge: Rebuilds merged view from scratch.
           - Ensures 'merged' perfectly reflects 'structured'.
        
        Performance:
        - Cost: O(Size(Root) + Size(Domain) + Size(Local))
        - Latency: ~2-5ms for 50KB contexts (Python)
        - Worth it for v1.0 guarantees: "Visual Hash == Execution State"
        """
        now_ms = self._now_ms()

        with self._lock:
            # Check staleness
            time_since_update = now_ms - self._last_local_update_ms
            is_stale = (self._staleness_ms > 0) and (time_since_update > self._staleness_ms)
            
            # DE-ALIASING GUARANTEE:
            # Create fresh separate copies of all layers.
            # This isolates the snapshot from the manager and from future mutations.
            root_copy = copy.deepcopy(self._root_dict_cached)
            domain_copy = copy.deepcopy(self._domain_dict_cached)
            local_copy = copy.deepcopy(self._local)
            
            # RE-MERGE GUARANTEE:
            # Rebuild merged context from these specific copies.
            merged = _deep_merge({}, root_copy)
            merged = _deep_merge(merged, domain_copy)
            merged = _deep_merge(merged, local_copy)
            
            # Compute/Retrieve Hashes
            # CACHING INVARIANT: Since root/domain are structurally frozen, their cached 
            # digests remain stable. They are only recalculated under lock during 
            # explicit update/replace operations. This avoids O(N) re-hashing per snapshot.
            
            # Since local is volatile, we calculate its digest fresh on every snapshot.
            local_digest, local_hash = _hash_json_digest(local_copy, self._max_shard_size)
            
            # Compose total from BYTE DIGESTS
            total_hash = hashlib.sha256(
                self._root_digest + self._domain_digest + local_digest
            ).hexdigest()
            
            # Build structured context
            structured = {
                "root": root_copy,
                "domain": domain_copy,
                "local": local_copy,
            }
            
            snap = ContextSnapshot(
                local=local_copy,
                c_merged=merged, 
                structured=structured,
                root_hash=self._root_hash,
                domain_hash=self._domain_hash,
                local_hash=local_hash,
                composite_hash=total_hash,
                timestamp_ms=now_ms,
                local_layer_age_stale=is_stale,
            )
            return snap

    # ------------------------------------------------------------------
    # Update operations
    # ------------------------------------------------------------------

    def update_local(self, delta: Dict[str, Any]) -> None:
        """
        Patch C_local with a shallow or nested dict.

        Example:
            cm.update_local({"sensors": {"vision": {"human_score": 0.9}}})

        This performs a deep merge into the existing local context.
        
        Raises:
            BadContextError: If delta is not a dict
            ContextTooLargeError: If resulting local shard exceeds max_shard_size
        """
        if not isinstance(delta, dict):
            raise BadContextError("update_local expects a dict delta")

        with self._lock:
            # Deep merge into C_local
            new_local = _deep_merge(self._local, delta)
            
            # SECURITY: Enforce size limit immediately (not just at snapshot)
            # This prevents DoS via large blob injection
            if self._max_shard_size > 0:
                serialized = canonical_json(new_local).encode("utf-8")
                if len(serialized) > self._max_shard_size:
                    raise ContextTooLargeError(
                        f"Local context size {len(serialized)} exceeds limit {self._max_shard_size}"
                    )
            
            self._local = new_local
            self._last_local_update_ms = self._now_ms()
            # No need to recompute - snapshots compute fresh hashes

    def replace_local(self, new_local: Dict[str, Any]) -> None:
        """Replace C_local entirely with new_local."""
        if not isinstance(new_local, dict):
            raise BadContextError("replace_local expects a dict")

        with self._lock:
            # SECURITY: Enforce size limit immediately (not just at snapshot)
            if self._max_shard_size > 0:
                serialized = canonical_json(new_local).encode("utf-8")
                if len(serialized) > self._max_shard_size:
                    raise ContextTooLargeError(
                        f"Local context size {len(serialized)} exceeds limit {self._max_shard_size}"
                    )
            
            self._local = copy.deepcopy(new_local)
            self._last_local_update_ms = self._now_ms()
            # No need to recompute - snapshots compute fresh hashes

    def update_domain(self, delta: Dict[str, Any]) -> None:
        """Patch C_domain (rare config changes). Invalidates cached digest + base merge."""
        if not isinstance(delta, dict):
            raise BadContextError("update_domain expects a dict delta")

        with self._lock:
            # Unfreeze cached dict, merge, deep-copy to prevent aliasing, re-freeze
            domain_dict = _deep_merge(self._domain_dict_cached, delta)
            
            # Verify size before mutating internal state
            new_digest, new_hash = _hash_json_digest(domain_dict, self._max_shard_size)
            
            domain_dict = copy.deepcopy(domain_dict)
            self._domain_frozen = _deep_freeze(domain_dict)
            
            # Update cached dict version
            self._domain_dict_cached = domain_dict
            
            # Update cached digest
            self._domain_digest, self._domain_hash = new_digest, new_hash

    def replace_domain(self, new_domain: Dict[str, Any]) -> None:
        """Replace C_domain entirely. Invalidates cached digest + base merge."""
        if not isinstance(new_domain, dict):
            raise BadContextError("replace_domain expects a dict")

        with self._lock:
            # Deep copy to prevent aliasing
            domain_dict = copy.deepcopy(new_domain)
            
            # Verify size before mutating internal state
            new_digest, new_hash = _hash_json_digest(domain_dict, self._max_shard_size)
            
            self._domain_frozen = _deep_freeze(domain_dict)
            
            # Update cached dict version
            self._domain_dict_cached = domain_dict
            
            # Update cached digest
            self._domain_digest, self._domain_hash = new_digest, new_hash

    def unsafe_replace_root(self, new_root: Dict[str, Any]) -> None:
        """Replace C_root. Invalidates cached digest + base merge."""
        if not isinstance(new_root, dict):
            raise BadContextError("unsafe_replace_root expects a dict")

        with self._lock:
            # Deep copy to prevent aliasing
            root_dict = copy.deepcopy(new_root)
            
            # Verify size before mutating internal state
            new_digest, new_hash = _hash_json_digest(root_dict, self._max_shard_size)
            
            self._root_frozen = _deep_freeze(root_dict)
            
            # Update cached dict version
            self._root_dict_cached = root_dict
            
            # Update cached digest
            self._root_digest, self._root_hash = new_digest, new_hash

    # ------------------------------------------------------------------
    # Staleness & conflict helpers
    # ------------------------------------------------------------------

    def assert_fresh(self) -> None:
        """
        Raise ContextStaleError if the local layer has not been updated recently.

        MANAGER HEURISTIC ONLY — not spec-normative.

        This check is based solely on the wall-clock age of the last
        update_local() / replace_local() call (self._last_local_update_ms).
        It is a coarse freshness guard for the manager, not the validator's
        per-field staleness enforcement required by NIP-015.

        A fresh local-layer update does NOT guarantee that individual sensor
        entries within that update are themselves fresh. Per-literal timestamp
        staleness is enforced by the validator (ERR_STALE_CONTEXT), not here.

        Use snapshot().local_layer_age_stale for the equivalent inline check.
        """
        now_ms = self._now_ms()
        age_ms = now_ms - self._last_local_update_ms
        if self._staleness_ms > 0 and age_ms > self._staleness_ms:
            raise ContextStaleError(
                f"Local context stale: age={age_ms} ms > {self._staleness_ms} ms"
            )

    def compare_hashes(self, other_snapshot: ContextSnapshot) -> bool:
        """
        Compare the current context hash with another snapshot.

        Returns True if hashes are equal, False otherwise.
        """
        snap = self.snapshot()
        return snap.composite_hash == other_snapshot.composite_hash
