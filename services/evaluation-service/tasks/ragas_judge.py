"""
tasks/ragas_judge.py
-----------------------
Full RAGAS scoring pass — ALL metrics that make sense for live production
RAG traffic, wired in and ON by default alongside the existing custom
judge (not optional anymore — evaluate_batch.py calls both).

REWRITTEN FOR ragas==0.4.x (collections-based API)
----------------------------------------------------
The previous version of this file targeted ragas's OLD API
(ragas<=~0.2.x): `from ragas import evaluate`, `from ragas.metrics import
faithfulness` (lower-case instances), `from datasets import Dataset`.
That API was REMOVED in ragas 0.4 (see the official migration guide:
https://docs.ragas.io/en/stable/howtos/migrations/migrate_from_v03_to_v04/).
Running old-API code against the new package didn't just warn — it
triggered import-order issues deep inside `datasets`/`ragas` that
surfaced as a confusing circular-import AttributeError, because the old
code path doesn't exist as written anymore.

This file now uses the CURRENT ragas==0.4.x API end to end:
  - Metrics are classes from `ragas.metrics.collections`, instantiated
    once with an LLM (`Faithfulness(llm=llm)`), not free-standing
    pre-built instances imported from `ragas.metrics`.
  - Scoring is `await metric.ascore(**kwargs)` with plain keyword
    arguments (user_input / response / retrieved_contexts / reference),
    not a `Dataset` object passed into a global `evaluate()` call.
  - Each call returns a `MetricResult` (`.value` is the float score,
    `.reason` is an optional explanation) instead of a raw float.
  - The LLM is built with `ragas.llms.llm_factory(model, provider=...,
    client=...)`, which replaces the old `instructor_llm_factory` /
    `LiteLLMStructuredLLM` wiring. For Groq specifically, llm_factory
    auto-selects the Instructor adapter when given a native `groq.Groq`
    client — no LiteLLM indirection needed for the LLM side.
  - `AnswerSimilarity` was removed in 0.4 in favor of `SemanticSimilarity`
    (same concept: embedding-based semantic similarity vs. a reference).
  - `ContextPrecision` ships as two explicit variants in 0.4:
    `ContextPrecisionWithReference` (needs a reference answer) and
    `ContextPrecisionWithoutReference`. This project only ever has a
    reference for Group B rows, so `ContextPrecisionWithReference` is
    the correct one to use here.

No `evaluate()` / `Dataset` / `experiment()` machinery is used at all —
each row is scored directly via per-metric `ascore()` calls, which maps
much more naturally onto "one row from rag_query_logs at a time" than
building a HF Dataset of size 1 ever did.

METRICS INCLUDED — split into two groups (unchanged from before)
--------------------------------------------------------------------
Group A — work WITHOUT a ground-truth answer (reference). These run on
every sampled production row, since live traffic never has a
pre-written "correct answer" to compare against:

  - faithfulness     : is the answer actually supported by the
                        retrieved context, or did the LLM make
                        something up?
  - answer_relevancy  : does the answer actually address the
                        question asked, or does it wander off-topic?

Group B — REQUIRE a ground-truth reference answer to compute. These are
included here and WILL run, but only on rows where a reference is
available (e.g. a curated test set, or a query where a human reviewer
already supplied the correct answer). On live traffic with no reference,
these are skipped automatically and saved as None rather than erroring:

  - context_precision    : of the chunks retrieved, how many were
                           actually relevant/needed?
  - context_recall        : did retrieval pull in everything needed to
                           answer the question, or did it miss chunks?
  - context_entity_recall : did retrieval capture the key named
                           entities (people, places, terms) the
                           reference answer needed?
  - answer_correctness    : how factually correct is the answer
                           compared to the reference?
  - answer_similarity      : how semantically similar is the answer to
                           the reference, independent of exact wording?
                           (backed by SemanticSimilarity in 0.4 — see
                           note above)

LLM AND EMBEDDINGS USED
------------------------
LLM: native `groq.Groq` client wrapped via `ragas.llms.llm_factory(...,
provider="groq")`. Same Groq account/model already used elsewhere in
this project (GROQ_API_KEY / GROQ_MODEL) — no second LLM provider
account needed. Falls back to nothing fancy: if GROQ_API_KEY isn't set,
building the LLM raises clearly at call time rather than silently
returning a dummy judge.

Embeddings: HuggingFaceEmbeddings running intfloat/multilingual-e5-small
locally — the SAME model worker-service already uses for chunk
embedding (tasks/embed.py), so no new model needs to be downloaded. The
embeddings constructor itself is unchanged from before — `ragas 0.4`
keeps `ragas.embeddings.huggingface_provider.HuggingFaceEmbeddings` with
the same `model` / `use_api` signature.

Requires: pip install ragas==0.4.3 groq instructor sentence-transformers
(see requirements.txt for exact pins).

WINDOWS SSL FIX (must run before any ragas/datasets/aiohttp import)
----------------------------------------------------------------------
On some Windows machines, Python's ssl.create_default_context() crashes
with `ssl.SSLError: [ASN1: NOT_ENOUGH_DATA] not enough data` while
enumerating the Windows Certificate Store — a single malformed/legacy
certificate in that store is enough to break the whole load. This is a
known CPython bug (https://github.com/python/cpython/issues/104135),
unrelated to this project's code. It surfaces here specifically because
`aiohttp` (pulled in transitively through `datasets`, which `ragas`
imports) builds its default SSL context at IMPORT time, not at request
time — so the crash happens the moment `import ragas` runs, before any
of this file's own code executes. (This is also why the earlier
"partially initialized module 'datasets'" error was misleading: the
REAL failure was this SSL error interrupting `datasets`' import
part-way through; a later, unrelated import of `datasets` then saw a
half-initialized module and reported a circular import instead of the
original SSL error.)

FIRST ATTEMPT THAT DIDN'T WORK — setting SSL_CERT_FILE: the standard
fix for "Python won't trust my CA" problems is pointing the SSL_CERT_FILE
environment variable at a known-good CA bundle (e.g. certifi's). That
does NOT help here, though, confirmed by testing on the actual affected
machine: `aiohttp/connector.py` calls `ssl.create_default_context()`
with NO arguments at all (no cafile/cadata). On Windows, when
create_default_context() is called with no cafile/cadata/capath,
SSLContext.load_default_certs() unconditionally calls
_load_windows_store_certs() — it does NOT consult SSL_CERT_FILE at all
on that code path. SSL_CERT_FILE only gets honored by libraries that
explicitly read it themselves (certifi, requests, httpx) — the stdlib
ssl module's own Windows store loader ignores it entirely. So setting
the env var changes nothing for aiohttp's specific call pattern.

ACTUAL FIX: monkeypatch `ssl.SSLContext.load_default_certs` itself,
before `aiohttp` (or anything that imports it, like `datasets`/`ragas`)
gets imported. The patched version uses certifi's bundle via
`load_verify_locations(cafile=...)` instead of walking the Windows
store, so the malformed certificate is never visited at all. This is
the same root-cause workaround documented for this exact CPython bug
(see https://github.com/python/cpython/issues/104135 and
https://github.com/agentscope-ai/QwenPaw/issues/5086, which hits the
identical aiohttp.connector._make_ssl_context() call site). Must happen
before any `aiohttp`/`datasets`/`ragas` import anywhere in this
process — hence doing it here, at the very top of this module, before
any local imports below.
"""
from __future__ import annotations

import os

if os.name == "nt":
    import ssl as _ssl

    if not getattr(_ssl.SSLContext, "_certifi_patched", False):
        try:
            import certifi

            _certifi_cafile = certifi.where()
            _original_load_default_certs = _ssl.SSLContext.load_default_certs

            def _patched_load_default_certs(self, purpose=_ssl.Purpose.SERVER_AUTH):
                # Skip the Windows-store walk entirely — load certifi's
                # known-good bundle instead. This is what
                # ssl.create_default_context() ultimately needs filled
                # in when called with no cafile/cadata/capath (exactly
                # how aiohttp.connector calls it).
                self.load_verify_locations(cafile=_certifi_cafile)

            _ssl.SSLContext.load_default_certs = _patched_load_default_certs
            _ssl.SSLContext._certifi_patched = True

            # Also set these for the libraries that DO read them
            # directly (certifi/requests/httpx) so everything points at
            # the same known-good bundle consistently.
            os.environ["SSL_CERT_FILE"] = _certifi_cafile
            os.environ["REQUESTS_CA_BUNDLE"] = _certifi_cafile
        except ImportError:
            # certifi ships as a dependency of requests/aiohttp, so this
            # should always be importable here — but never let a
            # missing certifi block the rest of the module from loading.
            pass

    try:
        import safetensors as _st

        if not getattr(_st, "_mmap_patched", False):
            _orig_safe_open = _st.safe_open

            class _SafeOpenInMemory:
                _DTYPE_MAP = None

                def __init__(self, filename, framework="pt", device="cpu"):
                    import json
                    import struct
                    import torch

                    if _SafeOpenInMemory._DTYPE_MAP is None:
                        _SafeOpenInMemory._DTYPE_MAP = {
                            "F64": torch.float64, "F32": torch.float32,
                            "F16": torch.float16, "BF16": torch.bfloat16,
                            "I64": torch.int64, "I32": torch.int32,
                            "I16": torch.int16, "I8": torch.int8,
                            "U8": torch.uint8, "BOOL": torch.bool,
                        }

                    self._tensors = {}
                    self._metadata = {}

                    with open(str(filename), "rb") as f:
                        header_size = struct.unpack("<Q", f.read(8))[0]
                        header = json.loads(f.read(header_size))
                        data_base = 8 + header_size

                        if "__metadata__" in header:
                            self._metadata = header.pop("__metadata__")

                        for name, info in header.items():
                            start, end = info["data_offsets"]
                            f.seek(data_base + start)
                            raw = bytearray(f.read(end - start))
                            dtype = self._DTYPE_MAP.get(info["dtype"], torch.float32)
                            tensor = torch.frombuffer(raw, dtype=dtype).reshape(info["shape"])
                            if device != "cpu":
                                tensor = tensor.to(device)
                            self._tensors[name] = tensor

                def keys(self):
                    return self._tensors.keys()

                def get_tensor(self, name):
                    return self._tensors[name]

                def metadata(self):
                    return self._metadata

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    pass

            def _safe_open_no_mmap(*args, **kwargs):
                kwargs.setdefault("disable_mmap", True)
                try:
                    return _orig_safe_open(*args, **kwargs)
                except TypeError:
                    kwargs.pop("disable_mmap", None)
                    try:
                        return _orig_safe_open(*args, **kwargs)
                    except OSError:
                        return _SafeOpenInMemory(*args, **kwargs)

            _st.safe_open = _safe_open_no_mmap
            _st._mmap_patched = True
    except ImportError:
        pass

import logging
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# Load .env from the project root (two levels up from this file's directory)
# so GROQ_API_KEY and other vars are available regardless of which working
# directory the Celery worker was started from.
load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False)

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Lazy singletons — built once, reused across every call in this process,
# same caching pattern paddle_engine.py already uses for its OCR models.
# ----------------------------------------------------------------------
_ragas_llm = None
_ragas_embeddings = None


def _get_ragas_llm():
    """
    Builds (once) the LLM wrapper RAGAS metrics use to do their own
    internal reasoning (e.g. faithfulness breaks the answer into
    statements and checks each one against the context — that step
    itself calls an LLM).

    Uses a native AsyncGroq client patched via instructor.from_groq() —
    the officially documented way to wire Instructor's structured-output
    support onto Groq (https://python.useinstructor.com/integrations/groq/)
    — then passes the already-patched client into ragas.llms.llm_factory()
    with adapter="instructor" explicit.

    MUST be AsyncGroq, not the sync Groq client: ragas 0.4.x's metric API
    is async-only (ascore() has no sync counterpart — see this module's
    top-level docstring). instructor.from_groq() preserves whatever
    sync/async-ness the raw client passed into it already has, so a sync
    Groq() here produces a sync-patched instructor client; ragas then
    fails at call time with "Cannot use agenerate() with a synchronous
    client. Use generate() instead." the moment any metric calls
    llm.agenerate() internally.

    WHY NOT plain llm_factory(model, provider="groq", client=AsyncGroq(...))
    with auto-detection: tried that first, and it failed with
        Failed to initialize groq client with instructor adapter.
        Error: Failed to patch groq client with Instructor:
        'AsyncGroq' object has no attribute 'messages'
    ragas's own internal instructor-adapter setup (when given a raw,
    un-patched groq client) misidentifies it and tries to access an
    Anthropic-style `.messages` attribute instead of `.chat.completions`.
    Patching the client ourselves via instructor.from_groq() first avoids
    ragas's internal client-type detection entirely — by the time
    llm_factory sees the client, it's already a working Instructor-patched
    async client, so llm_factory's job is just to wrap it, not to also
    figure out how to patch it.

    Model + API key come from env vars already used elsewhere in this
    project (GROQ_API_KEY) — no new credential needed.
    """
    global _ragas_llm
    if _ragas_llm is not None:
        return _ragas_llm

    import instructor
    from groq import AsyncGroq
    from ragas.llms import llm_factory

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY is not set — the RAGAS judge needs it to build "
            "its LLM (same key used elsewhere in this project). Set it in "
            ".env, or skip RAGAS scoring for this row."
        )

    # Strip the "groq/" prefix some configs carry over from the old
    # LiteLLM-style model naming (e.g. RAGAS_JUDGE_MODEL=groq/llama-3.3-
    # 70b-versatile) — llm_factory wants the bare model name plus an
    # explicit provider="groq".
    raw_model = os.getenv("RAGAS_JUDGE_MODEL", "llama-3.3-70b-versatile")
    model = raw_model.split("/", 1)[1] if raw_model.startswith("groq/") else raw_model

    # MUST be AsyncGroq, not Groq — ragas 0.4.x metrics are async-only
    # (ascore() has no sync counterpart, see this module's docstring).
    # instructor.from_groq() preserves whatever sync/async-ness the raw
    # client has; patching a sync Groq() here produces a sync-patched
    # client, and ragas's llm_factory wrapper then fails at call time
    # with "Cannot use agenerate() with a synchronous client" the moment
    # a metric calls llm.agenerate(). Using AsyncGroq from the start
    # makes instructor.from_groq() return an async-patched client that
    # actually has agenerate() available.
    #
    # base_url is pinned explicitly to the bare host, NOT taken from
    # GROQ_BASE_URL. This project's .env sets
    #   GROQ_BASE_URL=https://api.groq.com/openai/v1
    # for code elsewhere that hands that value straight to an
    # OpenAI-compatible client (openai.OpenAI(base_url=...)), which is
    # correct there. But the groq-python SDK's OWN default base_url is
    # already "https://api.groq.com/openai/v1" internally, and
    # ragas.llms.llm_factory(provider="groq", ...) builds request paths
    # assuming it owns the "/openai/v1" segment too. If AsyncGroq() picks
    # up GROQ_BASE_URL from the environment (the groq SDK reads that var
    # itself if base_url isn't passed explicitly), the "/openai/v1" ends
    # up baked into the client's base_url AND re-added by llm_factory's
    # request building, producing the observed
    #   POST /openai/v1/openai/v1/chat/completions -> 404 unknown_url
    # Passing base_url="https://api.groq.com" here overrides the env var
    # and gives llm_factory a clean host to add its own "/openai/v1" to,
    # exactly once.
    raw_client = AsyncGroq(api_key=api_key, base_url="https://api.groq.com")
    patched_client = instructor.from_groq(raw_client)
    _ragas_llm = llm_factory(
        model, provider="groq", client=patched_client, adapter="instructor",
    )
    logger.info("RAGAS LLM judge initialized (provider=groq, model=%s)", model)
    return _ragas_llm


def _get_ragas_embeddings():
    """
    Builds (once) the embedding model RAGAS metrics use for anything
    based on semantic similarity (answer_relevancy compares the answer's
    embedding back against the question; answer_similarity and
    context_entity_recall need embeddings too).

    Reuses intfloat/multilingual-e5-small — same model already loaded by
    worker-service's tasks/embed.py — so this doesn't add a new model
    family to the project, just a second loaded copy in this process.
    Unchanged from the pre-0.4 version of this file — this part of the
    API didn't move.
    """
    global _ragas_embeddings
    if _ragas_embeddings is not None:
        return _ragas_embeddings

    from ragas.embeddings.huggingface_provider import HuggingFaceEmbeddings

    model_name = os.getenv("RAGAS_EMBEDDING_MODEL") or os.getenv("EMBEDDING_MODEL") or "intfloat/multilingual-e5-small"

    _ragas_embeddings = HuggingFaceEmbeddings(
        model=model_name,
        use_api=False,   # runs locally, no HF API key needed
    )
    logger.info("RAGAS embeddings initialized (model=%s)", model_name)
    return _ragas_embeddings


async def _ascore_metric(metric, **kwargs) -> tuple[Optional[float], Optional[str]]:
    """
    Calls metric.ascore(**kwargs) and unpacks the MetricResult into a
    plain (value, reason) tuple. Centralized here so every metric call
    below handles the MetricResult -> float conversion the same way, and
    so a metric raising doesn't need a try/except duplicated at every
    call site (callers of this helper still wrap it themselves, since
    "this one metric failed" should not be silently swallowed at this
    layer — see score_with_ragas() below).
    """
    result = await metric.ascore(**kwargs)
    value = float(result.value) if result.value is not None else None
    reason = getattr(result, "reason", None)
    return value, reason


async def score_with_ragas(query: str, answer: str, contexts: list[str],
                            reference: Optional[str] = None) -> dict:
    """
    Runs the RAGAS metric suite on a single query/answer/contexts triple
    (optionally with a reference answer for Group B metrics).

    IMPORTANT — context availability in THIS project:
    rag_query_logs (the real table) has no context/retrieved_chunks
    column — see db/queries.py's module docstring. generation-service
    uses retrieved context only in-memory, at answer time, then never
    persists it. So on every row coming from the scheduled batch job,
    `contexts` will be an empty list UNLESS it was recovered from
    live_evaluation_cache (see db/queries.get_cached_context).

    faithfulness specifically checks whether the answer is supported by
    the retrieved context — it is meaningless with an empty context
    list, so it is SKIPPED whenever `contexts` is empty, exactly the same
    way Group B metrics are skipped whenever there's no reference.
    answer_relevancy still runs fine with just query+answer, since it
    only compares the answer back against the question, not against
    context.

    NOTE: this function is now `async def` (the underlying 0.4 metric
    API is async-only — `ascore()` has no sync counterpart). Callers
    must await it. evaluate_batch.py's Celery task runs this through
    asyncio.run() at the call site — see score_with_ragas_for_pipeline().

    Returns a flat dict of scores, all in [0, 1] or None if that metric
    couldn't be computed for this row:
        {
            "faithfulness":          0.0-1.0 or None (None if no context),
            "answer_relevancy":      0.0-1.0,
            "context_precision":     0.0-1.0 or None,
            "context_recall":        0.0-1.0 or None,
            "context_entity_recall": 0.0-1.0 or None,
            "answer_correctness":    0.0-1.0 or None,
            "answer_similarity":     0.0-1.0 or None,
            "raw_response":          "<dict of all scores + reasons, as a string>",
        }
    """
    from ragas.metrics.collections import (
        Faithfulness,
        AnswerRelevancy,
        ContextPrecisionWithReference,
        ContextRecall,
        ContextEntityRecall,
        AnswerCorrectness,
        SemanticSimilarity,  # replaces the removed AnswerSimilarity
    )

    llm        = _get_ragas_llm()
    embeddings = _get_ragas_embeddings()

    has_context   = bool(contexts)
    has_reference = bool(reference)

    output = {
        "faithfulness":          None,
        "answer_relevancy":      None,
        "context_precision":     None,
        "context_recall":        None,
        "context_entity_recall": None,
        "answer_correctness":    None,
        "answer_similarity":     None,
    }
    reasons = {}

    # ── Group A — no reference needed ─────────────────────────────────

    # answer_relevancy always runs.
    # vibrantlabsai/ragas 0.4.x uses keyword arg 'contexts' (not
    # 'retrieved_contexts'). We try with contexts first; on TypeError we
    # fall back to query+answer only so the metric never silently returns 0.0.
    try:
        relevancy = AnswerRelevancy(llm=llm, embeddings=embeddings)
        relevancy_contexts = contexts if has_context else [answer]
        try:
            value, reason = await _ascore_metric(
                relevancy,
                user_input=query,
                response=answer,
                contexts=relevancy_contexts,
            )
        except TypeError:
            value, reason = await _ascore_metric(
                relevancy, user_input=query, response=answer,
            )
        output["answer_relevancy"] = value
        reasons["answer_relevancy"] = reason
    except Exception as exc:
        logger.warning("RAGAS answer_relevancy failed: %s", exc)

    # faithfulness only runs when real context exists — without context
    # there is nothing for it to check the answer against.
    if has_context:
        try:
            faithfulness = Faithfulness(llm=llm)
            value, reason = await _ascore_metric(
                faithfulness,
                user_input=query,
                response=answer,
                retrieved_contexts=contexts,
            )
            output["faithfulness"] = value
            reasons["faithfulness"] = reason
        except Exception as exc:
            logger.warning("RAGAS faithfulness failed: %s", exc)
    else:
        logger.debug(
            "No retrieved context for this row — skipping faithfulness "
            "(rag_query_logs has no context column in this project's "
            "schema; see db/queries.py). Only answer_relevancy will be "
            "computed for this row's Group A metrics."
        )

    # ── Group B — only meaningful with a reference answer ─────────────
    if has_reference:
        try:
            context_precision = ContextPrecisionWithReference(llm=llm)
            value, reason = await _ascore_metric(
                context_precision,
                user_input=query,
                retrieved_contexts=contexts,
                reference=reference,
            )
            output["context_precision"] = value
            reasons["context_precision"] = reason
        except Exception as exc:
            logger.warning("RAGAS context_precision failed: %s", exc)

        try:
            context_recall = ContextRecall(llm=llm)
            value, reason = await _ascore_metric(
                context_recall,
                user_input=query,
                retrieved_contexts=contexts,
                reference=reference,
            )
            output["context_recall"] = value
            reasons["context_recall"] = reason
        except Exception as exc:
            logger.warning("RAGAS context_recall failed: %s", exc)

        try:
            context_entity_recall = ContextEntityRecall(llm=llm)
            value, reason = await _ascore_metric(
                context_entity_recall,
                retrieved_contexts=contexts,
                reference=reference,
            )
            output["context_entity_recall"] = value
            reasons["context_entity_recall"] = reason
        except Exception as exc:
            logger.warning("RAGAS context_entity_recall failed: %s", exc)

        try:
            answer_correctness = AnswerCorrectness(llm=llm, embeddings=embeddings)
            value, reason = await _ascore_metric(
                answer_correctness,
                user_input=query,
                response=answer,
                reference=reference,
            )
            output["answer_correctness"] = value
            reasons["answer_correctness"] = reason
        except Exception as exc:
            logger.warning("RAGAS answer_correctness failed: %s", exc)

        try:
            # SemanticSimilarity is 0.4's replacement for the removed
            # AnswerSimilarity metric — same embedding-based comparison
            # between the answer and the reference.
            semantic_similarity = SemanticSimilarity(embeddings=embeddings)
            value, reason = await _ascore_metric(
                semantic_similarity,
                response=answer,
                reference=reference,
            )
            output["answer_similarity"] = value
            reasons["answer_similarity"] = reason
        except Exception as exc:
            logger.warning("RAGAS answer_similarity failed: %s", exc)
    else:
        logger.debug(
            "No reference answer for this row — skipping context_precision, "
            "context_recall, context_entity_recall, answer_correctness, "
            "answer_similarity (Group B metrics)."
        )

    output["raw_response"] = str({"scores": output, "reasons": reasons})
    return output


def score_with_ragas_for_pipeline(query: str, answer: str, context: Optional[str],
                                   reference: Optional[str] = None) -> dict:
    """
    Adapter matching the same return shape evaluate_batch.py's
    _score_with_custom_judge expects (faithfulness/relevance/completeness/
    raw_response), so evaluate_batch.py can call either judge
    interchangeably without branching logic.

    This is a SYNC function (evaluate_batch.py calls it directly, with no
    `await`), but the underlying 0.4 metric API is async-only. The
    asyncio.run() call below bridges that gap — safe here because this
    function is called from a plain Celery task (evaluate_recent_answers),
    not from inside an already-running event loop. If this ever gets
    called from async code, switch to awaiting score_with_ragas()
    directly instead of going through this sync wrapper.

    `context` may be None (rag_query_logs has no context column — see
    evaluate_batch.py's module docstring) or a recovered context string
    from live_evaluation_cache (see db/queries.get_cached_context, used
    by evaluate_batch.py before calling this function). When `context` is
    None, this sends an EMPTY contexts list to RAGAS, which means
    faithfulness is skipped entirely for that row (see score_with_ragas's
    docstring) rather than scored against nothing.
    """
    import asyncio

    full = asyncio.run(
        score_with_ragas(
            query=query,
            answer=answer,
            contexts=[context] if context else [],
            reference=reference,
        )
    )
    return {
        "faithfulness": full["faithfulness"],
        "relevance":    full["answer_relevancy"],
        "completeness": full["answer_correctness"],  # closest Group-B analogue; None on live traffic without a reference
        "raw_response": full["raw_response"],
        # Full metric set also returned for callers that want everything,
        # not just the 3-field shape the custom judge uses:
        "ragas_full": full,
    }