import React, { useState, useEffect, useCallback } from "react";
import { dataSourcesApi } from "../api/client";
import type { DataSource, CreateDataSourcePayload } from "../types";
import { Button, Modal, Banner, EmptyState, SkeletonGrid, ConfirmDialog } from "../components/ui";
import { useToast } from "../components/ui/Toast";
import { Icon, sourceIcon } from "../components/ui/Icon";

// Connectable source types, in the order they appear in the picker. Icons come
// from the shared registry so this page and the schema panel can never
// disagree about what a MongoDB source looks like.
const SOURCE_TYPES = ["postgresql", "mongodb", "elasticsearch", "mysql", "trino"] as const;

const DEFAULT_PORTS: Record<string, number> = {
  postgresql: 5432,
  mongodb: 27017,
  elasticsearch: 9200,
  mysql: 3306,
  trino: 8080,
};

interface FormState {
  name: string;
  source_type: string;
  host: string;
  port: number;
  database_name: string;
  username: string;
  password: string;
  trino_catalog: string;
}

const DEFAULT_FORM: FormState = {
  name: "",
  source_type: "postgresql",
  host: "",
  port: 5432,
  database_name: "",
  username: "",
  password: "",
  trino_catalog: "",
};

export default function DataSourcesPage() {
  const toast = useToast();
  const [sources, setSources] = useState<DataSource[]>([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [form, setForm] = useState<FormState>(DEFAULT_FORM);
  const [saving, setSaving] = useState(false);
  const [refreshingId, setRefreshingId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expandedSchemaId, setExpandedSchemaId] = useState<number | null>(null);
  const [syncing, setSyncing] = useState(false);
  // ConfirmDialog state
  const [confirmDelete, setConfirmDelete] = useState<{ id: number; name: string } | null>(null);

  const loadSources = useCallback(async () => {
    try {
      setLoading(true);
      const data = await dataSourcesApi.list();
      setSources(data ?? []);
    } catch (e) {
      setError("Failed to load data sources");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSources();
  }, [loadSources]);

  const handleTypeChange = (type: string) => {
    setForm((f) => ({
      ...f,
      source_type: type,
      port: DEFAULT_PORTS[type] ?? 5432,
    }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const payload: CreateDataSourcePayload = {
        name: form.name,
        source_type: form.source_type,
        host: form.host,
        port: form.port,
        database_name: form.database_name,
        username: form.username || undefined,
        password: form.password || undefined,
        trino_catalog: form.trino_catalog,
      };
      await dataSourcesApi.create(payload);
      setShowModal(false);
      setForm(DEFAULT_FORM);
      await loadSources();
      toast.success("Data source connected successfully");
    } catch (e: unknown) {
      const err = e as { response?: { data?: { details?: string } }; message?: string };
      setError(err.response?.data?.details || err.message || "Failed to create data source");
    } finally {
      setSaving(false);
    }
  };

  const handleRefresh = async (id: number) => {
    setRefreshingId(id);
    try {
      await dataSourcesApi.refreshSchema(id);
      await loadSources();
      toast.success("Schema refreshed");
    } catch (e: unknown) {
      const err = e as { message?: string };
      toast.error(err.message || "Schema refresh failed");
    } finally {
      setRefreshingId(null);
    }
  };

  const handleSync = async () => {
    setSyncing(true);
    try {
      const result = await dataSourcesApi.syncCatalogs();
      await loadSources();
      const parts: string[] = [];
      if (result.new_sources.length > 0) {
        parts.push(`${result.new_sources.length} new source(s): ${result.new_sources.join(", ")}`);
      }
      if (result.new_datasets.length > 0) {
        parts.push(`${result.new_datasets.length} new dataset(s): ${result.new_datasets.join(", ")}`);
      }
      toast.success(parts.length > 0 ? parts.join(" — ") : "No new catalogs or tables found");
    } catch (e: unknown) {
      const err = e as { response?: { data?: { details?: string } }; message?: string };
      toast.error(err.response?.data?.details || err.message || "Catalog sync failed");
    } finally {
      setSyncing(false);
    }
  };

  const handleDeleteConfirmed = async () => {
    if (!confirmDelete) return;
    try {
      await dataSourcesApi.delete(confirmDelete.id);
      await loadSources();
      toast.success(`"${confirmDelete.name}" removed`);
    } catch {
      toast.error("Failed to remove data source");
    } finally {
      setConfirmDelete(null);
    }
  };

  const parseSchemaCache = (raw?: string) => {
    if (!raw || raw === "{}") return null;
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  };

  return (
    <div className="ds-page">
      <div className="ds-header">
        <div>
          <h1 className="ds-title">Data Sources</h1>
          <p className="ds-subtitle">
            Register external databases, clusters, and indices. Schemas are
            automatically fetched and made available to the AI query planner.
          </p>
        </div>
        <div className="ds-header-actions">
          <Button
            variant="secondary"
            onClick={handleSync}
            busy={syncing}
            busyLabel="Syncing…"
            title="Discover new Trino catalogs and tables/indices (e.g. a newly added Elasticsearch index) without a manual setup step"
          >
            <Icon name="sync" size={14} /> Sync Catalogs
          </Button>
          <Button variant="primary" onClick={() => setShowModal(true)}>
            <span>+</span> Connect Source
          </Button>
        </div>
      </div>

      {error && (
        <Banner kind="error" message={error} onDismiss={() => setError(null)} />
      )}

      {loading ? (
        <SkeletonGrid count={3} />
      ) : sources.length === 0 ? (
        <EmptyState
          icon={<Icon name="database" size={26} />}
          title="No data sources registered"
          description="Connect your first database, Elasticsearch cluster, or MongoDB instance to start building federated queries."
          action={{ label: "Connect Your First Source", onClick: () => setShowModal(true) }}
        />
      ) : (
        <div className="ds-grid">
          {sources.map((src, index) => {
            const schema = parseSchemaCache(src.schema_cache);
            const tableCount = schema?.tables
              ? Object.keys(schema.tables).length
              : null;
            const isExpanded = expandedSchemaId === src.id;

            return (
              <div 
                key={src.id} 
                className="ds-card"
                style={{ animationDelay: `${index * 30}ms` }}
              >
                <div className="ds-card-header">
                  <div className="ds-card-icon">
                    <Icon name={sourceIcon(src.source_type)} size={20} />
                  </div>
                  <div className="ds-card-info">
                    <div className="ds-card-name">{src.name}</div>
                    <div className="ds-card-type">
                      <span className={`ds-badge ds-badge-${src.source_type}`}>
                        {src.source_type}
                      </span>
                    </div>
                  </div>
                  <div
                    className={`ds-status-dot ${src.is_active ? "active" : "inactive"}`}
                    title={src.is_active ? "Active" : "Inactive"}
                  />
                </div>

                <div className="ds-card-details">
                  <div className="ds-detail-row">
                    <span className="ds-detail-label">Host</span>
                    <span className="ds-detail-value">
                      {src.host}:{src.port}
                    </span>
                  </div>
                  {src.database_name && (
                    <div className="ds-detail-row">
                      <span className="ds-detail-label">Database</span>
                      <span className="ds-detail-value">{src.database_name}</span>
                    </div>
                  )}
                  <div className="ds-detail-row">
                    <span className="ds-detail-label">Trino Catalog</span>
                    <code className="ds-code">{src.trino_catalog}</code>
                  </div>
                  {tableCount !== null && (
                    <div className="ds-detail-row">
                      <span className="ds-detail-label">Tables/Indices</span>
                      <span className="ds-detail-value ds-highlight">
                        {tableCount}
                      </span>
                    </div>
                  )}
                  {src.last_schema_refresh && (
                    <div className="ds-detail-row">
                      <span className="ds-detail-label">Last Refresh</span>
                      <span className="ds-detail-value ds-muted">
                        {new Date(src.last_schema_refresh).toLocaleString()}
                      </span>
                    </div>
                  )}
                </div>

                {schema?.tables && Object.keys(schema.tables).length > 0 && (
                  <div className="ds-schema-section">
                    <button
                      className="ds-schema-toggle"
                      onClick={() =>
                        setExpandedSchemaId(isExpanded ? null : src.id)
                      }
                    >
                      <Icon name={isExpanded ? "chevron-down" : "chevron-right"} size={12} />
                      Schema Preview
                    </button>
                    {isExpanded && (
                      <div className="ds-schema-list">
                        {Object.entries(schema.tables)
                          .slice(0, 10)
                          .map(([path, tbl]: [string, unknown]) => {
                            const table = tbl as { columns?: { name: string; type: string }[] };
                            return (
                              <div key={path} className="ds-schema-table">
                                <code className="ds-table-path">{path}</code>
                                <span className="ds-col-count">
                                  {table.columns?.length ?? 0} columns
                                </span>
                              </div>
                            );
                          })}
                        {Object.keys(schema.tables).length > 10 && (
                          <div className="ds-schema-more">
                            +{Object.keys(schema.tables).length - 10} more
                            tables
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}

                <div className="ds-card-actions">
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => handleRefresh(src.id)}
                    busy={refreshingId === src.id}
                    busyLabel="Refreshing…"
                    title="Fetch live schema from this source via Trino"
                  >
                    <Icon name="refresh" size={13} /> Refresh Schema
                  </Button>
                  <Button
                    variant="danger"
                    size="sm"
                    onClick={() => setConfirmDelete({ id: src.id, name: src.name })}
                    title="Remove this data source"
                  >
                    Remove
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* ── Add Data Source Modal ─────────────────────────────── */}
      <Modal open={showModal} onClose={() => setShowModal(false)}>
        <div className="modal-header">
          <h2>Connect Data Source</h2>
          <button
            className="modal-close"
            onClick={() => setShowModal(false)}
            aria-label="Close"
          >
            <Icon name="close" size={16} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="modal-form">
          <div className="form-group">
            <label>Source Type</label>
            <div className="source-type-grid">
              {SOURCE_TYPES.map((type) => (
                <button
                  key={type}
                  type="button"
                  className={`source-type-btn ${form.source_type === type ? "selected" : ""}`}
                  onClick={() => handleTypeChange(type)}
                >
                  <span className="source-type-icon">
                    <Icon name={sourceIcon(type)} size={20} />
                  </span>
                  <span className="source-type-label">{type}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="form-row">
            <div className="form-group">
              <label>Connection Name *</label>
              <input
                type="text"
                className="form-input"
                placeholder="e.g. Production Postgres"
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                required
              />
            </div>
            <div className="form-group">
              <label>Trino Catalog Name *</label>
              <input
                type="text"
                className="form-input"
                placeholder="e.g. elasticsearch"
                value={form.trino_catalog}
                onChange={(e) => setForm((f) => ({ ...f, trino_catalog: e.target.value }))}
                required
              />
            </div>
          </div>

          <div className="form-row">
            <div className="form-group flex-2">
              <label>Host *</label>
              <input
                type="text"
                className="form-input"
                placeholder="localhost"
                value={form.host}
                onChange={(e) => setForm((f) => ({ ...f, host: e.target.value }))}
                required
              />
            </div>
            <div className="form-group">
              <label>Port *</label>
              <input
                type="number"
                className="form-input"
                value={form.port}
                onChange={(e) => setForm((f) => ({ ...f, port: Number(e.target.value) }))}
                required
              />
            </div>
          </div>

          <div className="form-row">
            <div className="form-group">
              <label>Database / Index</label>
              <input
                type="text"
                className="form-input"
                placeholder={
                  form.source_type === "elasticsearch"
                    ? "Index name (optional)"
                    : "Database name"
                }
                value={form.database_name}
                onChange={(e) => setForm((f) => ({ ...f, database_name: e.target.value }))}
              />
            </div>
          </div>

          <div className="form-row">
            <div className="form-group">
              <label>Username</label>
              <input
                type="text"
                className="form-input"
                placeholder="Optional"
                value={form.username}
                onChange={(e) => setForm((f) => ({ ...f, username: e.target.value }))}
              />
            </div>
            <div className="form-group">
              <label>Password</label>
              <input
                type="password"
                className="form-input"
                placeholder="Optional"
                value={form.password}
                onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))}
              />
            </div>
          </div>

          {error && <div className="form-error">{error}</div>}

          <div className="modal-footer">
            <Button
              type="button"
              variant="ghost"
              onClick={() => setShowModal(false)}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              busy={saving}
              busyLabel="Connecting…"
            >
              Connect &amp; Fetch Schema
            </Button>
          </div>
        </form>
      </Modal>

      {/* ── Delete Confirm ─────────────────────────────────────── */}
      <ConfirmDialog
        open={confirmDelete !== null}
        title="Remove data source?"
        message={`Remove "${confirmDelete?.name ?? ""}" from registered sources? This cannot be undone.`}
        confirmLabel="Remove"
        onConfirm={handleDeleteConfirmed}
        onCancel={() => setConfirmDelete(null)}
      />
    </div>
  );
}
