-- FRTB IMA Risk Monitor - PostgreSQL schema
-- Database: frtb_monitor (PostgreSQL 18)
--
-- Run order:
--   1. Create the database (handled by the bootstrap routine in db_utils.create_database).
--   2. Run this file against the frtb_monitor database to create all tables.
--
-- This script is idempotent: it can be re-run safely. Tables are only created
-- if they do not already exist, so existing data is preserved.

-- ---------------------------------------------------------------------------
-- Raw market data: one row per asset per trading day.
-- Not in the original 4-table spec, but required so risk calculations and the
-- NMRF gap checker can read a persistent return history straight from the DB.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS price_history (
    id            SERIAL PRIMARY KEY,
    date          DATE         NOT NULL,
    ticker        VARCHAR(10)  NOT NULL,
    adj_close     NUMERIC(16,6),
    daily_return  NUMERIC(12,8),
    created_at    TIMESTAMP    DEFAULT NOW(),
    UNIQUE (date, ticker)
);

CREATE INDEX IF NOT EXISTS idx_price_history_ticker_date
    ON price_history (ticker, date);

-- ---------------------------------------------------------------------------
-- Table 1: daily_risk_metrics - one row per trading day (portfolio level).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS daily_risk_metrics (
    id                     SERIAL PRIMARY KEY,
    date                   DATE NOT NULL UNIQUE,
    var_975                NUMERIC(10,6),   -- 97.5% Historical Simulation VaR
    es_975                 NUMERIC(10,6),   -- 97.5% Expected Shortfall
    es_stressed            NUMERIC(10,6),   -- Stress-calibrated ES
    liquidity_adjusted_es  NUMERIC(10,6),   -- ES scaled by FRTB liquidity horizons
    volatility_regime      VARCHAR(20),     -- 'normal', 'elevated', 'stressed'
    created_at             TIMESTAMP DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Table 2: asset_risk - one row per asset per trading day.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_risk (
    id                SERIAL PRIMARY KEY,
    date              DATE NOT NULL,
    ticker            VARCHAR(10) NOT NULL,
    daily_return      NUMERIC(10,6),
    var_contribution  NUMERIC(10,6),
    es_contribution   NUMERIC(10,6),
    liquidity_horizon INTEGER,
    is_nmrf           BOOLEAN DEFAULT FALSE,
    created_at        TIMESTAMP DEFAULT NOW(),
    UNIQUE (date, ticker)
);

CREATE INDEX IF NOT EXISTS idx_asset_risk_date ON asset_risk (date);

-- ---------------------------------------------------------------------------
-- Table 3: backtest_results - one row per weekly backtest (Friday).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS backtest_results (
    id                       SERIAL PRIMARY KEY,
    week_ending              DATE NOT NULL UNIQUE,
    exceptions_count         INTEGER,           -- VaR breaches that week
    acerbi_szekely_statistic NUMERIC(10,6),
    pass_fail                VARCHAR(10),        -- 'PASS' or 'FAIL'
    regime                   VARCHAR(20),
    created_at               TIMESTAMP DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Table 4: narrative_log - one row per generated narrative.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS narrative_log (
    id             SERIAL PRIMARY KEY,
    week_ending    DATE NOT NULL,
    daily_summary  TEXT,   -- One-line daily post text
    weekly_summary TEXT,   -- Full weekly LinkedIn narrative
    key_movers     TEXT,   -- JSON string of top moving assets
    created_at     TIMESTAMP DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Table 5: events - notable daily signals worth surfacing (drives which days
-- become LinkedIn posts). One row per (date, event_type), upserted on re-run.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS events (
    id          SERIAL PRIMARY KEY,
    date        DATE         NOT NULL,
    event_type  VARCHAR(40)  NOT NULL,   -- regime_change, es_spike, es_high, backtest_breach
    severity    VARCHAR(10),             -- info, warning, alert
    headline    TEXT,
    detail      TEXT,
    created_at  TIMESTAMP    DEFAULT NOW(),
    UNIQUE (date, event_type)
);

CREATE INDEX IF NOT EXISTS idx_events_date ON events (date);
