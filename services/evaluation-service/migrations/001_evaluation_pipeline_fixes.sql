-- ==========================================================================
-- Fix 1: live_evaluation_cache
-- ==========================================================================

CREATE TABLE IF NOT EXISTS live_evaluation_cache (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    cache_key       VARCHAR(64) NOT NULL,
    query           TEXT        NOT NULL,
    answer          TEXT        NOT NULL,
    context_chunks  TEXT,           -- JSON-encoded list[str]
    reference       TEXT,           -- optional ground-truth answer
    consumed        BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_live_evaluation_cache_key
    ON live_evaluation_cache (cache_key);

CREATE INDEX IF NOT EXISTS ix_live_evaluation_cache_created_at
    ON live_evaluation_cache (created_at);


-- ==========================================================================
-- Fix 2a: UniqueConstraint on evaluation_logs(query_id, model_used)
-- ==========================================================================

WITH duplicates AS (
    SELECT id,
           ROW_NUMBER() OVER (
               PARTITION BY query_id, model_used
               ORDER BY evaluated_at ASC
           ) AS rn
    FROM   evaluation_logs
)
DELETE FROM evaluation_logs
WHERE  id IN (
    SELECT id FROM duplicates WHERE rn > 1
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE  conrelid = 'evaluation_logs'::regclass
          AND  conname  = 'uq_evaluation_logs_query_judge'
    ) THEN
        ALTER TABLE evaluation_logs
            ADD CONSTRAINT uq_evaluation_logs_query_judge
            UNIQUE (query_id, model_used);
    END IF;
END$$;

CREATE INDEX IF NOT EXISTS ix_evaluation_logs_query_model
    ON evaluation_logs (query_id, model_used);

ALTER TABLE evaluation_logs
    ADD COLUMN IF NOT EXISTS ragas_context_precision      FLOAT,
    ADD COLUMN IF NOT EXISTS ragas_context_recall          FLOAT,
    ADD COLUMN IF NOT EXISTS ragas_context_entity_recall   FLOAT,
    ADD COLUMN IF NOT EXISTS ragas_answer_correctness      FLOAT,
    ADD COLUMN IF NOT EXISTS ragas_answer_similarity       FLOAT;


-- ==========================================================================
-- Fix 2b: UniqueConstraint on moderation_queue(query_id)
-- ==========================================================================

WITH duplicates AS (
    SELECT id,
           ROW_NUMBER() OVER (
               PARTITION BY query_id
               ORDER BY created_at ASC
           ) AS rn
    FROM   moderation_queue
)
DELETE FROM moderation_queue
WHERE  id IN (
    SELECT id FROM duplicates WHERE rn > 1
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE  conrelid = 'moderation_queue'::regclass
          AND  conname  = 'uq_moderation_queue_query_id'
    ) THEN
        ALTER TABLE moderation_queue
            ADD CONSTRAINT uq_moderation_queue_query_id
            UNIQUE (query_id);
    END IF;
END$$;


-- ==========================================================================
-- Fix 3: eval_cursor
-- ==========================================================================

CREATE TABLE IF NOT EXISTS eval_cursor (
    name            VARCHAR(64) PRIMARY KEY DEFAULT 'default',
    last_query_id   BIGINT      NOT NULL DEFAULT 0,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO eval_cursor (name, last_query_id, updated_at)
VALUES ('default', 0, NOW())
ON CONFLICT (name) DO NOTHING;