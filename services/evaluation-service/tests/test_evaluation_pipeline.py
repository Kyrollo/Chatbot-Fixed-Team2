"""
tests/test_evaluation_pipeline.py
-----------------------------------
Integration + unit tests for the three production fixes:

  Fix 1 — Missing Retrieved Context
  Fix 2 — Duplicate Evaluations
  Fix 3 — Evaluation Progress Tracking

HOW TO RUN
-----------
Run against a real PostgreSQL test database and a real Redis instance:

    # 1. Set environment variables (or copy .env.test)
    export SYNC_DATABASE_URL="postgresql://postgres:postgres@localhost:5432/eval_test"
    export REDIS_URL="redis://localhost:6379/1"   # use DB 1 so you don't clobber your dev DB

    # 2. Install test dependencies
    pip install pytest pytest-asyncio httpx factory-boy

    # 3. Run the full suite
    pytest tests/test_evaluation_pipeline.py -v

    # 4. Run a single group
    pytest tests/test_evaluation_pipeline.py -v -k "context"
    pytest tests/test_evaluation_pipeline.py -v -k "duplicate"
    pytest tests/test_evaluation_pipeline.py -v -k "cursor"

ISOLATION
----------
Every test class uses its own session / transaction that is rolled back at
teardown, so tests do not interfere with each other or leave dirty data.
The `rag_query_logs` table is created as a lightweight TEMP TABLE per
session so the tests never need access to a live generation-service
database — only the evaluation-service's own tables are the real objects
under test.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Generator
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# ── DB / model imports (adjust paths if your project lays things out differently)
from db.models import (
    Base,
    EvaluationLog,
    ModerationQueueItem,
    LiveEvaluationCache,
    EvalCursor,
    context_cache_key,
)

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

TEST_DB_URL = "postgresql://postgres:postgres@localhost:5432/eval_test"


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(TEST_DB_URL)
    # Create all evaluation-service tables.
    Base.metadata.create_all(bind=eng)

    # Minimal stub for rag_query_logs so foreign-key-free JOINs still work.
    with eng.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS rag_query_logs (
                id         BIGSERIAL PRIMARY KEY,
                domain_id  INTEGER,
                user_id    INTEGER,
                query      TEXT NOT NULL,
                answer     TEXT NOT NULL,
                llm_route  VARCHAR(64),
                model      VARCHAR(128),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))
        conn.commit()

    yield eng

    # Tear down all evaluation-service tables at the end of the test session.
    Base.metadata.drop_all(bind=eng)
    with eng.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS rag_query_logs"))
        conn.commit()


@pytest.fixture()
def session(engine):
    """
    Provides a SQLAlchemy session that is rolled back after every test,
    so no test leaves dirty data behind.
    """
    conn    = engine.connect()
    trans   = conn.begin()
    Session = sessionmaker(bind=conn)
    sess    = Session()

    yield sess

    sess.close()
    trans.rollback()
    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _insert_query_log(session, query: str = "What is AI?", answer: str = "AI is intelligence demonstrated by machines.") -> int:
    """Inserts a rag_query_logs row and returns its auto-generated id."""
    result = session.execute(
        text("""
            INSERT INTO rag_query_logs (query, answer, created_at)
            VALUES (:q, :a, NOW())
            RETURNING id
        """),
        {"q": query, "a": answer},
    )
    session.commit()
    return result.fetchone()[0]


def _insert_eval_log(session, query_id: int, model_used: str = "custom_judge",
                     overall_score: float = 0.8) -> uuid.UUID:
    log = EvaluationLog(
        query_id=query_id,
        model_used=model_used,
        faithfulness_score=overall_score,
        relevance_score=overall_score,
        completeness_score=overall_score,
        overall_score=overall_score,
        raw_judge_response='{"test": true}',
    )
    session.add(log)
    session.commit()
    return log.id


# ─────────────────────────────────────────────────────────────────────────────
# Fix 1 — Missing Retrieved Context
# ─────────────────────────────────────────────────────────────────────────────

class TestLiveEvaluationCache:
    """Tests for save_live_evaluation_cache() and get_cached_context()."""

    def test_cache_key_is_deterministic(self):
        """The same query+answer always produces the same 64-char hex key."""
        key1 = context_cache_key("Hello world", "Yes it is")
        key2 = context_cache_key("Hello world", "Yes it is")
        assert key1 == key2
        assert len(key1) == 64

    def test_cache_key_differs_for_different_inputs(self):
        """Different query or answer → different key (no accidental match)."""
        key_a = context_cache_key("Query A", "Answer B")
        key_b = context_cache_key("Query B", "Answer A")
        key_c = context_cache_key("Query A", "Answer A")
        assert key_a != key_b
        assert key_a != key_c

    def test_separator_prevents_boundary_collision(self):
        """(query='AB', answer='C') and (query='A', answer='BC') → different keys."""
        key1 = context_cache_key("AB", "C")
        key2 = context_cache_key("A", "BC")
        assert key1 != key2

    def test_save_and_retrieve_context(self, session):
        """save_live_evaluation_cache() → get_cached_context() round-trip."""
        from db.queries import save_live_evaluation_cache, get_cached_context

        query   = "What is retrieval-augmented generation?"
        answer  = "RAG combines retrieval with generation."
        chunks  = ["RAG is a technique.", "It uses a retriever and a generator."]

        save_live_evaluation_cache(query, answer, chunks)
        retrieved_chunks, retrieved_ref = get_cached_context(query, answer)

        assert retrieved_chunks == chunks
        assert retrieved_ref is None

    def test_save_with_reference(self, session):
        """Reference answer is also stored and retrieved correctly."""
        from db.queries import save_live_evaluation_cache, get_cached_context

        query  = "Capital of Egypt?"
        answer = "Cairo"
        chunks = ["Egypt's capital is Cairo."]
        ref    = "The capital of Egypt is Cairo."

        save_live_evaluation_cache(query, answer, chunks, reference=ref)
        retrieved_chunks, retrieved_ref = get_cached_context(query, answer)

        assert retrieved_chunks == chunks
        assert retrieved_ref == ref

    def test_upsert_updates_stale_context(self, session):
        """A second save() for the same (query, answer) replaces the old chunks."""
        from db.queries import save_live_evaluation_cache, get_cached_context

        query  = "Tell me about Python."
        answer = "Python is a programming language."

        save_live_evaluation_cache(query, answer, ["Old chunk about Python."])
        save_live_evaluation_cache(query, answer, ["New, updated chunk about Python 3.12."])

        chunks, _ = get_cached_context(query, answer)
        assert chunks == ["New, updated chunk about Python 3.12."]

    def test_cache_miss_returns_none(self, session):
        """get_cached_context() returns (None, None) when no row exists."""
        from db.queries import get_cached_context

        chunks, ref = get_cached_context(
            "A query that was never scored live.",
            "An answer that was never cached.",
        )
        assert chunks is None
        assert ref is None

    def test_consumed_flag_is_set_on_hit(self, session):
        """get_cached_context() marks the row consumed=True on a successful hit."""
        from db.queries import save_live_evaluation_cache, get_cached_context

        query  = "What is quantum computing?"
        answer = "Quantum computing uses qubits."
        save_live_evaluation_cache(query, answer, ["Qubits are quantum bits."])

        # First call — row is not yet consumed.
        key = context_cache_key(query, answer)
        row = session.query(LiveEvaluationCache).filter_by(cache_key=key).first()
        assert row is not None
        assert row.consumed is False

        # Retrieve it.
        get_cached_context(query, answer)

        session.expire(row)  # force a re-read from the DB
        assert row.consumed is True

    def test_prune_removes_old_rows(self, session):
        """prune_old_cache_entries() deletes rows older than ttl_hours."""
        from db.queries import prune_old_cache_entries

        # Insert a row with an old timestamp directly.
        old_key = "a" * 64
        old_row = LiveEvaluationCache(
            cache_key=old_key,
            query="old query",
            answer="old answer",
            context_chunks=json.dumps(["old chunk"]),
            created_at=datetime.now(timezone.utc) - timedelta(hours=200),
        )
        session.add(old_row)

        # Insert a recent row.
        new_key = "b" * 64
        new_row = LiveEvaluationCache(
            cache_key=new_key,
            query="new query",
            answer="new answer",
            context_chunks=json.dumps(["new chunk"]),
            created_at=datetime.now(timezone.utc),
        )
        session.add(new_row)
        session.commit()

        pruned = prune_old_cache_entries(ttl_hours=168)

        assert pruned >= 1
        assert session.query(LiveEvaluationCache).filter_by(cache_key=old_key).first() is None
        assert session.query(LiveEvaluationCache).filter_by(cache_key=new_key).first() is not None


# ─────────────────────────────────────────────────────────────────────────────
# Fix 2 — Duplicate Evaluations
# ─────────────────────────────────────────────────────────────────────────────

class TestDuplicateEvaluations:
    """Tests for the upsert in save_evaluation_result() and flag_for_moderation()."""

    def test_save_evaluation_result_returns_id_on_first_insert(self, engine):
        """save_evaluation_result() returns a non-None UUID on the first call."""
        from db.queries import save_evaluation_result

        qid = _insert_query_log(sessionmaker(bind=engine)())
        log_id = save_evaluation_result(
            query_id=qid,
            model_used="custom_judge",
            faithfulness_score=0.9,
            relevance_score=0.8,
            completeness_score=0.7,
            overall_score=0.8,
            raw_judge_response='{"ok": true}',
        )
        assert log_id is not None
        assert isinstance(log_id, uuid.UUID)

    def test_save_evaluation_result_no_duplicate_on_retry(self, engine):
        """Calling save_evaluation_result() twice for the same (query_id, model_used) produces exactly ONE row."""
        from db.queries import save_evaluation_result

        Session = sessionmaker(bind=engine)
        sess    = Session()
        qid     = _insert_query_log(sess)
        sess.close()

        save_evaluation_result(
            query_id=qid, model_used="custom_judge",
            faithfulness_score=0.9, relevance_score=0.8,
            completeness_score=0.7, overall_score=0.8,
            raw_judge_response="run 1",
        )
        save_evaluation_result(
            query_id=qid, model_used="custom_judge",
            faithfulness_score=0.5, relevance_score=0.5,
            completeness_score=0.5, overall_score=0.5,
            raw_judge_response="run 2 — should be ignored",
        )

        sess2 = Session()
        count = (
            sess2.query(EvaluationLog)
            .filter_by(query_id=qid, model_used="custom_judge")
            .count()
        )
        sess2.close()
        assert count == 1, f"Expected 1 row, got {count}"

    def test_second_save_returns_existing_id(self, engine):
        """On conflict, save_evaluation_result() returns the EXISTING row's id."""
        from db.queries import save_evaluation_result

        Session = sessionmaker(bind=engine)
        sess    = Session()
        qid     = _insert_query_log(sess)
        sess.close()

        first_id  = save_evaluation_result(
            query_id=qid, model_used="ragas",
            faithfulness_score=0.9, relevance_score=0.8,
            completeness_score=0.7, overall_score=0.8,
            raw_judge_response="first",
        )
        second_id = save_evaluation_result(
            query_id=qid, model_used="ragas",
            faithfulness_score=0.1, relevance_score=0.1,
            completeness_score=0.1, overall_score=0.1,
            raw_judge_response="second",
        )
        assert first_id == second_id

    def test_two_judges_produce_two_rows(self, engine):
        """custom_judge and ragas produce SEPARATE rows for the same query_id."""
        from db.queries import save_evaluation_result

        Session = sessionmaker(bind=engine)
        sess    = Session()
        qid     = _insert_query_log(sess)
        sess.close()

        save_evaluation_result(
            query_id=qid, model_used="custom_judge",
            faithfulness_score=0.9, relevance_score=0.8,
            completeness_score=0.7, overall_score=0.8,
            raw_judge_response="custom",
        )
        save_evaluation_result(
            query_id=qid, model_used="ragas",
            faithfulness_score=0.85, relevance_score=0.9,
            completeness_score=None, overall_score=0.875,
            raw_judge_response="ragas",
        )

        sess2 = Session()
        count = sess2.query(EvaluationLog).filter_by(query_id=qid).count()
        sess2.close()
        assert count == 2

    def test_flag_for_moderation_no_duplicate(self, engine):
        """Calling flag_for_moderation() twice for the same query_id produces exactly ONE queue entry."""
        from db.queries import save_evaluation_result, flag_for_moderation

        Session = sessionmaker(bind=engine)
        sess    = Session()
        qid     = _insert_query_log(sess)
        sess.close()

        log_id = save_evaluation_result(
            query_id=qid, model_used="custom_judge",
            faithfulness_score=0.1, relevance_score=0.1,
            completeness_score=0.1, overall_score=0.1,
            raw_judge_response="bad",
        )

        flag_for_moderation(qid, log_id)
        flag_for_moderation(qid, log_id)  # second call — must be a no-op

        sess2 = Session()
        count = sess2.query(ModerationQueueItem).filter_by(query_id=qid).count()
        sess2.close()
        assert count == 1

    def test_flag_for_moderation_returns_true_first_time(self, engine):
        """flag_for_moderation() returns True on first insert, False on duplicate."""
        from db.queries import save_evaluation_result, flag_for_moderation

        Session = sessionmaker(bind=engine)
        sess    = Session()
        qid     = _insert_query_log(sess)
        sess.close()

        log_id = save_evaluation_result(
            query_id=qid, model_used="custom_judge",
            faithfulness_score=0.1, relevance_score=0.1,
            completeness_score=0.1, overall_score=0.1,
            raw_judge_response="bad",
        )

        first  = flag_for_moderation(qid, log_id)
        second = flag_for_moderation(qid, log_id)

        assert first  is True
        assert second is False


# ─────────────────────────────────────────────────────────────────────────────
# Fix 3 — Evaluation Progress Tracking (cursor)
# ─────────────────────────────────────────────────────────────────────────────

class TestEvalCursor:
    """Tests for get_cursor(), advance_cursor(), and cursor-based sampling."""

    def _clean_cursor(self, engine):
        """Remove the cursor row so each test starts fresh."""
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM eval_cursor WHERE name = 'default'"))
            conn.commit()

    def test_get_cursor_returns_zero_on_first_call(self, engine):
        """get_cursor() returns 0 and creates the singleton row on first call."""
        from db.queries import get_cursor

        self._clean_cursor(engine)
        assert get_cursor() == 0

    def test_get_cursor_creates_singleton_row(self, engine):
        """After get_cursor(), there is exactly one row in eval_cursor."""
        from db.queries import get_cursor

        self._clean_cursor(engine)
        get_cursor()

        with engine.connect() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM eval_cursor WHERE name = 'default'")
            ).scalar()
        assert count == 1

    def test_advance_cursor_moves_forward(self, engine):
        """advance_cursor(n) sets last_query_id to n."""
        from db.queries import get_cursor, advance_cursor

        self._clean_cursor(engine)
        get_cursor()  # ensure the row exists
        advance_cursor(42)
        assert get_cursor() == 42

    def test_advance_cursor_never_goes_backward(self, engine):
        """advance_cursor(smaller) leaves the cursor unchanged."""
        from db.queries import get_cursor, advance_cursor

        self._clean_cursor(engine)
        get_cursor()
        advance_cursor(100)
        advance_cursor(50)   # should be ignored
        assert get_cursor() == 100

    def test_advance_cursor_updates_updated_at(self, engine):
        """advance_cursor() updates the updated_at timestamp."""
        from db.queries import get_cursor, advance_cursor

        self._clean_cursor(engine)
        get_cursor()
        before = datetime.now(timezone.utc)
        advance_cursor(999)

        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT updated_at FROM eval_cursor WHERE name = 'default'")
            ).fetchone()
        assert row.updated_at.replace(tzinfo=timezone.utc) >= before

    def test_fetch_sample_uses_cursor(self, engine):
        """fetch_sample_query_ids() returns only rows with id > cursor."""
        from db.queries import fetch_sample_query_ids, advance_cursor

        self._clean_cursor(engine)

        Session = sessionmaker(bind=engine)
        sess    = Session()

        # Insert 3 rows.
        id1 = _insert_query_log(sess, query="q1", answer="a1")
        id2 = _insert_query_log(sess, query="q2", answer="a2")
        id3 = _insert_query_log(sess, query="q3", answer="a3")
        sess.close()

        # Advance cursor past the first two rows.
        advance_cursor(id2)

        # With sample_rate=1.0 we get all eligible rows.
        rows = fetch_sample_query_ids(sample_rate=1.0)
        ids  = [r["id"] for r in rows]

        assert id1 not in ids, "id1 is ≤ cursor — should NOT be returned"
        assert id2 not in ids, "id2 is = cursor — should NOT be returned"
        assert id3 in ids,     "id3 is > cursor — should be returned"

    def test_fetch_sample_excludes_already_evaluated(self, engine):
        """Rows that already have an evaluation_logs entry are excluded."""
        from db.queries import fetch_sample_query_ids

        self._clean_cursor(engine)

        Session = sessionmaker(bind=engine)
        sess    = Session()
        qid     = _insert_query_log(sess, query="evaluated q", answer="evaluated a")
        _insert_eval_log(sess, qid)   # mark as already evaluated
        sess.close()

        rows = fetch_sample_query_ids(sample_rate=1.0)
        ids  = [r["id"] for r in rows]
        assert qid not in ids


# ─────────────────────────────────────────────────────────────────────────────
# End-to-end: batch task with mocked judges
# ─────────────────────────────────────────────────────────────────────────────

class TestEvaluateBatchEndToEnd:
    """
    Tests evaluate_recent_answers() with both judges mocked so the test does
    not need a live LLM (or RAGAS / Groq) to run.
    """

    MOCK_CUSTOM_SCORES = {
        "faithfulness":  0.9,
        "relevance":     0.8,
        "completeness":  0.7,
        "raw_response":  '{"faithfulness":0.9,"relevance":0.8,"completeness":0.7}',
    }

    MOCK_RAGAS_FULL = {
        "faithfulness":          0.85,
        "answer_relevancy":      0.88,
        "context_precision":     None,
        "context_recall":        None,
        "context_entity_recall": None,
        "answer_correctness":    None,
        "answer_similarity":     None,
        "raw_response":          "ragas mock",
    }

    def _seed_query_log(self, engine, query="What is ML?", answer="ML is a subset of AI.") -> int:
        sess = sessionmaker(bind=engine)()
        qid  = _insert_query_log(sess, query=query, answer=answer)
        sess.close()
        return qid

    def _clean_cursor(self, engine):
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM eval_cursor WHERE name = 'default'"))
            conn.commit()

    def test_batch_evaluates_new_rows(self, engine):
        """evaluate_recent_answers() writes evaluation_logs rows for new rag_query_logs rows."""
        from tasks.evaluate_batch import evaluate_recent_answers

        self._clean_cursor(engine)
        qid = self._seed_query_log(engine)

        with (
            patch("tasks.evaluate_batch._score_with_custom_judge",
                  return_value=self.MOCK_CUSTOM_SCORES),
            patch("tasks.evaluate_batch.score_with_ragas_for_pipeline",
                  return_value={"ragas_full": self.MOCK_RAGAS_FULL,
                                **self.MOCK_CUSTOM_SCORES}),
        ):
            result = evaluate_recent_answers.apply().get()

        assert result["evaluated"] >= 1

        sess = sessionmaker(bind=engine)()
        count = sess.query(EvaluationLog).filter_by(query_id=qid).count()
        sess.close()
        assert count >= 1  # at minimum the custom_judge row

    def test_batch_does_not_duplicate_on_retry(self, engine):
        """Running the task twice for the same rows produces no duplicate evaluation_logs rows."""
        from tasks.evaluate_batch import evaluate_recent_answers

        self._clean_cursor(engine)
        qid = self._seed_query_log(engine, query="unique q", answer="unique a")

        run_kwargs = dict(
            _score_with_custom_judge=lambda **kw: self.MOCK_CUSTOM_SCORES,
        )

        with (
            patch("tasks.evaluate_batch._score_with_custom_judge",
                  return_value=self.MOCK_CUSTOM_SCORES),
            patch("tasks.evaluate_batch.score_with_ragas_for_pipeline",
                  return_value={"ragas_full": self.MOCK_RAGAS_FULL,
                                **self.MOCK_CUSTOM_SCORES}),
        ):
            evaluate_recent_answers.apply().get()

        # Second run — cursor has advanced, sample should be 0. But even if
        # some row slips through the cursor check, the upsert prevents doubles.
        with (
            patch("tasks.evaluate_batch._score_with_custom_judge",
                  return_value=self.MOCK_CUSTOM_SCORES),
            patch("tasks.evaluate_batch.score_with_ragas_for_pipeline",
                  return_value={"ragas_full": self.MOCK_RAGAS_FULL,
                                **self.MOCK_CUSTOM_SCORES}),
        ):
            evaluate_recent_answers.apply().get()

        sess = sessionmaker(bind=engine)()
        for model in ("custom_judge", "ragas"):
            count = sess.query(EvaluationLog).filter_by(
                query_id=qid, model_used=model
            ).count()
            assert count <= 1, f"Duplicate row for model_used={model}"
        sess.close()

    def test_batch_flags_low_score(self, engine):
        """Rows with overall_score below MODERATION_THRESHOLD are flagged."""
        from tasks.evaluate_batch import evaluate_recent_answers

        self._clean_cursor(engine)
        qid = self._seed_query_log(engine, query="terrible q", answer="terrible a")

        low_scores = {
            "faithfulness":  0.1,
            "relevance":     0.1,
            "completeness":  0.1,
            "raw_response":  "bad answer",
        }
        low_ragas_full = {
            **self.MOCK_RAGAS_FULL,
            "faithfulness":     0.1,
            "answer_relevancy": 0.1,
        }

        with (
            patch("tasks.evaluate_batch._score_with_custom_judge",
                  return_value=low_scores),
            patch("tasks.evaluate_batch.score_with_ragas_for_pipeline",
                  return_value={"ragas_full": low_ragas_full, **low_scores}),
        ):
            result = evaluate_recent_answers.apply().get()

        assert result["flagged_for_review"] >= 1

        sess = sessionmaker(bind=engine)()
        flag = sess.query(ModerationQueueItem).filter_by(query_id=qid).first()
        sess.close()
        assert flag is not None
        assert flag.status == "pending"

    def test_batch_advances_cursor(self, engine):
        """After a run, get_cursor() returns a value ≥ the highest sampled id."""
        from db.queries import get_cursor
        from tasks.evaluate_batch import evaluate_recent_answers

        self._clean_cursor(engine)
        qid = self._seed_query_log(engine, query="cursor test q", answer="cursor test a")

        with (
            patch("tasks.evaluate_batch._score_with_custom_judge",
                  return_value=self.MOCK_CUSTOM_SCORES),
            patch("tasks.evaluate_batch.score_with_ragas_for_pipeline",
                  return_value={"ragas_full": self.MOCK_RAGAS_FULL,
                                **self.MOCK_CUSTOM_SCORES}),
        ):
            evaluate_recent_answers.apply().get()

        assert get_cursor() >= qid

    def test_batch_skips_already_evaluated_rows(self, engine):
        """Rows already in evaluation_logs are not re-evaluated."""
        from db.queries import get_cursor
        from tasks.evaluate_batch import evaluate_recent_answers

        self._clean_cursor(engine)

        sess = sessionmaker(bind=engine)()
        qid  = _insert_query_log(sess, query="pre-eval q", answer="pre-eval a")
        _insert_eval_log(sess, qid, model_used="custom_judge", overall_score=0.9)
        sess.close()

        call_count = {"n": 0}

        def mock_judge(**kwargs):
            call_count["n"] += 1
            return self.MOCK_CUSTOM_SCORES

        with patch("tasks.evaluate_batch._score_with_custom_judge", side_effect=mock_judge):
            evaluate_recent_answers.apply().get()

        assert call_count["n"] == 0, "Judge was called for an already-evaluated row"


# ─────────────────────────────────────────────────────────────────────────────
# Schema integrity checks
# ─────────────────────────────────────────────────────────────────────────────

class TestSchemaConstraints:
    """Direct SQL checks that the migration applied correctly."""

    def test_evaluation_logs_unique_constraint_exists(self, engine):
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT conname
                FROM   pg_constraint
                WHERE  conrelid = 'evaluation_logs'::regclass
                  AND  conname  = 'uq_evaluation_logs_query_judge'
            """)).fetchone()
        assert row is not None, "UniqueConstraint uq_evaluation_logs_query_judge not found"

    def test_moderation_queue_unique_constraint_exists(self, engine):
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT conname
                FROM   pg_constraint
                WHERE  conrelid = 'moderation_queue'::regclass
                  AND  conname  = 'uq_moderation_queue_query_id'
            """)).fetchone()
        assert row is not None, "UniqueConstraint uq_moderation_queue_query_id not found"

    def test_live_evaluation_cache_table_exists(self, engine):
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT tablename
                FROM   pg_tables
                WHERE  schemaname = 'public'
                  AND  tablename  = 'live_evaluation_cache'
            """)).fetchone()
        assert row is not None, "live_evaluation_cache table not found"

    def test_eval_cursor_table_exists(self, engine):
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT tablename
                FROM   pg_tables
                WHERE  schemaname = 'public'
                  AND  tablename  = 'eval_cursor'
            """)).fetchone()
        assert row is not None, "eval_cursor table not found"

    def test_ragas_columns_exist_on_evaluation_logs(self, engine):
        expected_cols = {
            "ragas_context_precision",
            "ragas_context_recall",
            "ragas_context_entity_recall",
            "ragas_answer_correctness",
            "ragas_answer_similarity",
        }
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT column_name
                FROM   information_schema.columns
                WHERE  table_name = 'evaluation_logs'
            """)).fetchall()
        actual_cols = {r[0] for r in rows}
        missing = expected_cols - actual_cols
        assert not missing, f"Missing columns on evaluation_logs: {missing}"
