-- Capital — canonical SQLite schema (reference DDL)
--
-- This file documents the schema that SQLAlchemy creates from models/__init__.py.
-- tests/test_database.py asserts that this DDL and the ORM metadata describe the
-- same tables and columns, so the two can never drift apart silently.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- ---------------- dimension: stocks ----------------
CREATE TABLE IF NOT EXISTS stocks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol        VARCHAR(64)  NOT NULL UNIQUE,
    name          VARCHAR(200),
    series        VARCHAR(16),
    first_date    DATE,
    last_date     DATE,
    record_count  INTEGER      NOT NULL DEFAULT 0,
    created_at    DATETIME     NOT NULL,
    updated_at    DATETIME     NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_stocks_symbol ON stocks (symbol);

-- ---------------- import history ----------------
CREATE TABLE IF NOT EXISTS import_batches (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file      TEXT        NOT NULL,
    source_hash      VARCHAR(64),
    sheet_name       VARCHAR(120),
    duplicate_policy VARCHAR(16) NOT NULL DEFAULT 'skip',
    status           VARCHAR(16) NOT NULL DEFAULT 'running',
    started_at       DATETIME    NOT NULL,
    finished_at      DATETIME,
    rows_read        INTEGER     DEFAULT 0,
    rows_inserted    INTEGER     DEFAULT 0,
    rows_updated     INTEGER     DEFAULT 0,
    rows_skipped     INTEGER     DEFAULT 0,
    rows_rejected    INTEGER     DEFAULT 0,
    error_count      INTEGER     DEFAULT 0,
    notes            TEXT,
    column_mapping   JSON,
    created_at       DATETIME    NOT NULL,
    updated_at       DATETIME    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_import_batches_source_hash ON import_batches (source_hash);

-- ---------------- fact: prices ----------------
CREATE TABLE IF NOT EXISTS prices (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id     INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    trade_date   DATE    NOT NULL,
    open         FLOAT,
    high         FLOAT,
    low          FLOAT,
    close        FLOAT,
    last         FLOAT,
    prevclose    FLOAT,
    tottrdqty    FLOAT,
    tottrdval    FLOAT,
    totaltrades  FLOAT,
    delivery_pct FLOAT,
    batch_id     INTEGER REFERENCES import_batches(id) ON DELETE SET NULL,
    source_row   INTEGER,
    created_at   DATETIME NOT NULL,
    updated_at   DATETIME NOT NULL,
    CONSTRAINT uq_prices_stock_date UNIQUE (stock_id, trade_date),
    CONSTRAINT ck_prices_qty_nonneg CHECK (tottrdqty IS NULL OR tottrdqty >= 0)
);
CREATE INDEX IF NOT EXISTS ix_prices_stock_date ON prices (stock_id, trade_date);
CREATE INDEX IF NOT EXISTS ix_prices_date      ON prices (trade_date);

-- ---------------- unmapped source columns ----------------
CREATE TABLE IF NOT EXISTS price_extras (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    price_id    INTEGER REFERENCES prices(id) ON DELETE CASCADE,
    batch_id    INTEGER REFERENCES import_batches(id) ON DELETE SET NULL,
    symbol      VARCHAR(64) NOT NULL,
    trade_date  DATE,
    column_name VARCHAR(200) NOT NULL,
    value_text  TEXT
);
CREATE INDEX IF NOT EXISTS ix_price_extras_lookup ON price_extras (symbol, trade_date);

-- ---------------- validation trail ----------------
CREATE TABLE IF NOT EXISTS import_errors (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id    INTEGER NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
    row_number  INTEGER,
    column_name VARCHAR(120),
    severity    VARCHAR(16) NOT NULL DEFAULT 'error',
    message     TEXT NOT NULL,
    raw_value   TEXT
);

-- ---------------- Formula Lab ----------------
CREATE TABLE IF NOT EXISTS formulas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            VARCHAR(120) NOT NULL UNIQUE,
    description     TEXT,
    category        VARCHAR(60)  NOT NULL DEFAULT 'custom',
    output_column   VARCHAR(120) NOT NULL,
    expression      TEXT         NOT NULL,
    data_type       VARCHAR(20)  NOT NULL DEFAULT 'float',
    null_policy     VARCHAR(16)  NOT NULL DEFAULT 'blank',
    display_format  VARCHAR(40),
    parameters      JSON,
    validation_rules JSON,
    enabled         BOOLEAN      NOT NULL DEFAULT 1,
    builtin         BOOLEAN      NOT NULL DEFAULT 0,
    current_version INTEGER      NOT NULL DEFAULT 1,
    created_at      DATETIME     NOT NULL,
    updated_at      DATETIME     NOT NULL
);

CREATE TABLE IF NOT EXISTS formula_versions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    formula_id  INTEGER NOT NULL REFERENCES formulas(id) ON DELETE CASCADE,
    version     INTEGER NOT NULL,
    expression  TEXT    NOT NULL,
    snapshot    JSON,
    change_note TEXT,
    created_at  DATETIME NOT NULL,
    CONSTRAINT uq_formula_version UNIQUE (formula_id, version)
);

-- ---------------- customisation ----------------
CREATE TABLE IF NOT EXISTS dashboard_components (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    layout_name  VARCHAR(80)  NOT NULL DEFAULT 'Default',
    position     INTEGER      NOT NULL DEFAULT 0,
    kind         VARCHAR(24)  NOT NULL,
    title        VARCHAR(160) NOT NULL,
    metric       VARCHAR(120) NOT NULL,
    dimension    VARCHAR(120),
    aggregation  VARCHAR(24)  NOT NULL DEFAULT 'sum',
    source       VARCHAR(24)  NOT NULL DEFAULT 'prices',
    filters      JSON,
    options      JSON,
    visible      BOOLEAN      NOT NULL DEFAULT 1,
    created_at   DATETIME     NOT NULL,
    updated_at   DATETIME     NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_dashboard_layout ON dashboard_components (layout_name);

CREATE TABLE IF NOT EXISTS filter_presets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       VARCHAR(120) NOT NULL UNIQUE,
    scope      VARCHAR(40)  NOT NULL DEFAULT 'explorer',
    payload    JSON         NOT NULL,
    created_at DATETIME     NOT NULL,
    updated_at DATETIME     NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
    key        VARCHAR(120) PRIMARY KEY,
    value      JSON         NOT NULL,
    created_at DATETIME     NOT NULL,
    updated_at DATETIME     NOT NULL
);

-- ---------------- audit log ----------------
CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at DATETIME NOT NULL,
    category   VARCHAR(40)  NOT NULL,
    severity   VARCHAR(16)  NOT NULL DEFAULT 'info',
    entity     VARCHAR(160),
    message    TEXT         NOT NULL,
    payload    JSON
);
CREATE INDEX IF NOT EXISTS ix_audit_category ON audit_log (category, created_at);
