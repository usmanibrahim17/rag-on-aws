# Eval results

One row per run (newest last). Server settings are what was passed to run_evals.py, not read from the API.

| Date | Collection | Embedding model | Generation model | Chunk size/overlap | top_k | Threshold | Questions (ans/unans) | hit@1 | hit@3 | hit@5 | MRR | Correct refusals | Wrong refusals | p50 ms | p95 ms | Avg in/out tokens | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-11-24 | all (fastapi docs only at the time) | gemini-embedding-001 | gemini-3.5-flash-lite | 1000/150 | 5 | 0.6 | 32/8 | 96.9% | 100.0% | 100.0% | 0.984 | 8/8 (threshold 0, LLM 8) | 0/32 | 1375 | 13268 | 945/101 | Baseline, all 40 docs indexed. Threshold 0.6 never fired; the model refused every unanswerable question (row corrected after fixing the refusal classifier: q39 was first missed, and refusals were first mislabeled "threshold") |
| 2026-11-24 | all (fastapi docs only at the time) | gemini-embedding-001 | gemini-3.5-flash-lite | 1000/150 | 5 | 0.4 | 32/8 | 96.9% | 100.0% | 100.0% | 0.984 | 8/8 (threshold 3, LLM 5) | 0/32 | 1161 | 14551 | 913/107 | After: threshold lowered 0.6 -> 0.4 (from the baseline sweep: answerable max top distance 0.357, off-topic 0.47-0.50). The 3 off-topic questions are now refused before the LLM (~200 ms, 0 tokens); the 5 near-misses still depend on the model |
