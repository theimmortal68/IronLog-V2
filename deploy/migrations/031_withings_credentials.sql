CREATE TABLE IF NOT EXISTS withingscredentials (
    id INTEGER NOT NULL,
    access_token VARCHAR NOT NULL,
    refresh_token VARCHAR NOT NULL,
    token_expires_at DATETIME NOT NULL,
    last_synced_at DATETIME,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (id)
);
