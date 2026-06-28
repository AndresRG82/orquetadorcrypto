-- Migration 001: Add columns for Fase 0 signal tracking and fee transparency
-- Applied 2026-06-27

ALTER TABLE trades ADD COLUMN IF NOT EXISTS signal_id TEXT DEFAULT '';
ALTER TABLE trades ADD COLUMN IF NOT EXISTS slippage_usd DOUBLE PRECISION DEFAULT 0;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS funding_usd DOUBLE PRECISION DEFAULT 0;
