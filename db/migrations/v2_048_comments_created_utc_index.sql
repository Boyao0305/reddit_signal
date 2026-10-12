-- migrate: no-transaction
-- The daily projection (sources/reddit_adapter.py::_project_comments) selects comments by created_utc
-- window. Without this index the 136 GB comments table is walked in id order with a random heap read per
-- row: job 110 (2026-10-12) spent over two hours in its first FETCH. posts already has idx_posts_created_utc.
-- Built CONCURRENTLY so ingestion is not blocked.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_comments_created_utc
    ON comments (created_utc);
