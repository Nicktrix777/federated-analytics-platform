import React, { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { dashboardsApi } from "../api/client";
import type { Dashboard, CreateDashboardPayload } from "../types";

export default function DashboardsListPage() {
  const navigate = useNavigate();
  const [dashboards, setDashboards] = useState<Dashboard[]>([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [formName, setFormName] = useState("");
  const [formDesc, setFormDesc] = useState("");
  const [creating, setCreating] = useState(false);

  const loadDashboards = useCallback(async () => {
    try {
      setLoading(true);
      const data = await dashboardsApi.list();
      setDashboards(data ?? []);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadDashboards();
  }, [loadDashboards]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreating(true);
    try {
      const payload: CreateDashboardPayload = {
        name: formName,
        description: formDesc,
      };
      const created = await dashboardsApi.create(payload);
      navigate(`/dashboards/${created.id}`);
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (id: number, name: string) => {
    if (!confirm(`Delete dashboard "${name}"? This will remove all widgets.`)) return;
    await dashboardsApi.delete(id);
    await loadDashboards();
  };

  const getWidgetCount = (d: Dashboard) => d.widgets?.length ?? 0;

  return (
    <div className="dl-page">
      <div className="dl-header">
        <div>
          <h1 className="dl-title">Dashboards</h1>
          <p className="dl-subtitle">
            Build custom analytics views from your federated data.
          </p>
        </div>
        <button className="btn btn-primary" onClick={() => setShowModal(true)}>
          + New Dashboard
        </button>
      </div>

      {loading ? (
        <div className="ds-loading">
          <div className="spinner" />
          <span>Loading dashboards...</span>
        </div>
      ) : dashboards.length === 0 ? (
        <div className="ds-empty">
          <div className="ds-empty-icon">📊</div>
          <h3>No dashboards yet</h3>
          <p>Create your first dashboard to start building custom analytics views.</p>
          <button className="btn btn-primary" onClick={() => setShowModal(true)}>
            Create Dashboard
          </button>
        </div>
      ) : (
        <div className="dl-grid">
          {dashboards.map((d) => (
            <div
              key={d.id}
              className="dl-card"
              onClick={() => navigate(`/dashboards/${d.id}`)}
            >
              <div className="dl-card-icon">📊</div>
              <div className="dl-card-body">
                <div className="dl-card-name">{d.name}</div>
                {d.description && (
                  <div className="dl-card-desc">{d.description}</div>
                )}
                <div className="dl-card-meta">
                  <span>{getWidgetCount(d)} widgets</span>
                  <span>Updated {new Date(d.updated_at).toLocaleDateString()}</span>
                </div>
              </div>
              <div className="dl-card-actions">
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={(e) => {
                    e.stopPropagation();
                    navigate(`/dashboards/${d.id}`);
                  }}
                >
                  Open →
                </button>
                <button
                  className="btn btn-danger btn-sm"
                  onClick={(e) => {
                    e.stopPropagation();
                    handleDelete(d.id, d.name);
                  }}
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {showModal && (
        <div className="modal-overlay" onClick={() => setShowModal(false)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>New Dashboard</h2>
              <button className="modal-close" onClick={() => setShowModal(false)}>✕</button>
            </div>
            <form onSubmit={handleCreate} className="modal-form">
              <div className="form-group">
                <label>Dashboard Name *</label>
                <input
                  type="text"
                  className="form-input"
                  placeholder="e.g. Sales Overview"
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                  required
                  autoFocus
                />
              </div>
              <div className="form-group">
                <label>Description</label>
                <input
                  type="text"
                  className="form-input"
                  placeholder="Optional description"
                  value={formDesc}
                  onChange={(e) => setFormDesc(e.target.value)}
                />
              </div>
              <div className="modal-footer">
                <button type="button" className="btn btn-ghost" onClick={() => setShowModal(false)}>
                  Cancel
                </button>
                <button type="submit" className="btn btn-primary" disabled={creating}>
                  {creating ? "Creating..." : "Create Dashboard"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
