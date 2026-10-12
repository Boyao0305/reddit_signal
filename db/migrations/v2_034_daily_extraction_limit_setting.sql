-- v2_034: Configurable cap for daily extraction work.
--
-- The default keeps a daily run bounded while still covering normal daily
-- increments. Set value to 0 for an unlimited catch-up run.

INSERT INTO system_settings (key, value, is_secret, description, updated_by)
VALUES (
    'DAILY_EXTRACTION_LIMIT',
    '1000',
    FALSE,
    'Maximum signal bundles processed by each daily extraction run. Use 0 for unlimited.',
    'migration'
)
ON CONFLICT (key) DO UPDATE SET
    is_secret = EXCLUDED.is_secret,
    description = EXCLUDED.description
WHERE system_settings.is_secret IS DISTINCT FROM EXCLUDED.is_secret
   OR system_settings.description IS DISTINCT FROM EXCLUDED.description;
