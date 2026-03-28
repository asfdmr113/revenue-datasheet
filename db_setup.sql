-- Warung cashflow: transactions table (PostgreSQL / Supabase)
-- Run this once in the SQL editor or via psql.

CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL,
    type VARCHAR(20) NOT NULL CHECK (type IN ('Sale', 'Expense')),
    category VARCHAR(100),
    amount NUMERIC(15, 2) NOT NULL CHECK (amount >= 0),
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions (date);
CREATE INDEX IF NOT EXISTS idx_transactions_type_date ON transactions (type, date);
