-- v2_023: Dashboard user authentication system.
--
-- Stores dashboard user accounts with bcrypt-hashed passwords.
-- The application seeds a default admin account on startup when the table is empty.

CREATE TABLE IF NOT EXISTS dashboard_users (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_dashboard_users_username
    ON dashboard_users (username);

CREATE OR REPLACE FUNCTION touch_dashboard_user_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_dashboard_user_updated_at ON dashboard_users;
CREATE TRIGGER trg_dashboard_user_updated_at
    BEFORE UPDATE ON dashboard_users
    FOR EACH ROW
    EXECUTE FUNCTION touch_dashboard_user_updated_at();
