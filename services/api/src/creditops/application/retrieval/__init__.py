"""Graph-guided hybrid retrieval application layer (master design sections 12,
12.2, 12.3).

``RunRetrieval`` orchestrates the read-only section 12.2 pipeline -- authorized
seed nodes -> bounded graph traversal -> lexical + vector ranking over ORIGINAL
passages -> source hydration -> deterministic token packing -> trace persistence
-- honouring the section 12.3 priority ladder (SQL/graph before retrieval, LLM
last).  It returns a ``RetrievalResult`` and records every exclusion; it can
never confirm a fact, satisfy a gate, or record a credit decision.  A missing
model gateway degrades the run to LEXICAL-ONLY (fail closed), never a fabricated
query embedding.
"""
