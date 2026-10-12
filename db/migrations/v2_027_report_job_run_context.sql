-- Store the dashboard run context used when a report job is created.
--
-- Older jobs only recorded analysis_run_id, and the runner fell back to hard
-- coded run 10 / viability 4 defaults. That breaks reports generated from
-- newer cluster runs because cluster ids are run-scoped.

ALTER TABLE report_jobs
    ADD COLUMN IF NOT EXISTS viability_run_id INT,
    ADD COLUMN IF NOT EXISTS audience_classifier_version TEXT;

UPDATE report_jobs
SET analysis_run_id = 10
WHERE analysis_run_id IS NULL;

UPDATE report_jobs
SET viability_run_id = 4
WHERE viability_run_id IS NULL;

UPDATE report_jobs
SET audience_classifier_version = 'v1'
WHERE audience_classifier_version IS NULL;

CREATE INDEX IF NOT EXISTS idx_report_jobs_cluster_context
    ON report_jobs (
        cluster_id,
        analysis_run_id,
        viability_run_id,
        audience_classifier_version,
        created_at DESC,
        id DESC
    );
