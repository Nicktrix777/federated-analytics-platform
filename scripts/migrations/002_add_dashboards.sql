-- ======================================================
-- Migration 002 — dashboards + dashboard_widgets
--
-- Adds the dashboard-builder tables used by
-- core-api/services/dashboard_service.go (feature/dashboards).
--
-- Idempotent: safe to run multiple times.
-- ======================================================

CREATE TABLE IF NOT EXISTS dashboards (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,
    description TEXT DEFAULT '',
    layout      JSONB DEFAULT '{}',
    is_active   BOOLEAN DEFAULT true,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS dashboard_widgets (
    id              SERIAL PRIMARY KEY,
    dashboard_id    INTEGER NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
    title           VARCHAR(255) NOT NULL,
    query_sql       TEXT NOT NULL,
    chart_type      VARCHAR(50) DEFAULT 'table'
                        CHECK (chart_type IN ('table', 'bar', 'line', 'pie', 'area', 'scatter', 'number', 'gauge')),
    chart_config    JSONB DEFAULT '{}',
    grid_position   JSONB DEFAULT '{"x":0,"y":0,"w":6,"h":4}',
    refresh_rate_ms INTEGER DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dashboards_active ON dashboards(is_active);
CREATE INDEX IF NOT EXISTS idx_dashboard_widgets_dashboard ON dashboard_widgets(dashboard_id);

DROP TRIGGER IF EXISTS update_dashboards_updated_at ON dashboards;
CREATE TRIGGER update_dashboards_updated_at
    BEFORE UPDATE ON dashboards
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_dashboard_widgets_updated_at ON dashboard_widgets;
CREATE TRIGGER update_dashboard_widgets_updated_at
    BEFORE UPDATE ON dashboard_widgets
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
