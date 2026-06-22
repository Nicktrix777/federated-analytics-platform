import React, { useState, useRef } from "react";

interface UploadResult {
  table_name: string;
  trino_path: string;
  row_count: number;
  columns: string[];
  sample_query: string;
}

interface FileUploadProps {
  onUploadSuccess: (result: UploadResult) => void;
}

const FileUpload: React.FC<FileUploadProps> = ({ onUploadSuccess }) => {
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFile = async (file: File) => {
    if (!file) return;

    const ext = file.name.split(".").pop()?.toLowerCase();
    if (!["csv", "xlsx", "xls"].includes(ext || "")) {
      setError("Please upload a .csv or .xlsx file");
      return;
    }
    if (file.size > 20 * 1024 * 1024) {
      setError("File size must be under 20MB");
      return;
    }

    setIsUploading(true);
    setError(null);
    setUploadResult(null);

    try {
      const formData = new FormData();
      formData.append("file", file);

      const token = import.meta.env.VITE_API_TOKEN || "poc-demo-token-2024";
      const baseURL = import.meta.env.VITE_API_BASE_URL ?? "";

      const response = await fetch(`${baseURL}/api/upload`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: formData,
      });

      // Safely parse body — guard against HTML error pages (nginx/proxy errors)
      const contentType = response.headers.get("content-type") || "";
      let data: Record<string, unknown> = {};
      if (contentType.includes("application/json")) {
        data = await response.json();
      } else {
        const text = await response.text();
        throw new Error(
          response.ok
            ? "Server returned an unexpected response format"
            : `Server error ${response.status}: ${text.slice(0, 120)}`
        );
      }

      if (!response.ok) {
        throw new Error(
          (data.details as string) || (data.error as string) || "Upload failed"
        );
      }

      const result = data.data as UploadResult;
      setUploadResult(result);
      onUploadSuccess(result);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setIsUploading(false);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = () => setIsDragging(false);

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) handleFile(file);
  };

  const reset = () => {
    setUploadResult(null);
    setError(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  return (
    <div className="upload-section">
      {!uploadResult ? (
        <div
          className={`upload-dropzone ${isDragging ? "upload-dropzone--dragging" : ""} ${isUploading ? "upload-dropzone--loading" : ""}`}
          onDrop={handleDrop}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onClick={() => !isUploading && fileInputRef.current?.click()}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept=".csv,.xlsx,.xls"
            onChange={handleInputChange}
            style={{ display: "none" }}
          />

          {isUploading ? (
            <div className="upload-loading">
              <div className="upload-spinner" />
              <span>Processing file…</span>
              <span className="upload-hint">Creating table & registering metadata</span>
            </div>
          ) : (
            <div className="upload-idle">
              <div className="upload-icon">📊</div>
              <div className="upload-label">Drop your spreadsheet here</div>
              <div className="upload-hint">or click to browse</div>
              <div className="upload-formats">.csv &nbsp;·&nbsp; .xlsx &nbsp;·&nbsp; .xls &nbsp;·&nbsp; max 20MB</div>
            </div>
          )}
        </div>
      ) : (
        <div className="upload-success">
          <div className="upload-success-header">
            <span className="upload-success-icon">✅</span>
            <div>
              <div className="upload-success-title">Upload successful!</div>
              <div className="upload-success-subtitle">
                <strong>{uploadResult.table_name}</strong> — {uploadResult.row_count.toLocaleString()} rows, {uploadResult.columns.length} columns
              </div>
            </div>
            <button className="upload-reset-btn" onClick={reset} title="Upload another file">
              ✕
            </button>
          </div>
          <div className="upload-path">
            <code>{uploadResult.trino_path}</code>
          </div>
          <div className="upload-cols">
            {uploadResult.columns.slice(0, 8).map((col) => (
              <span key={col} className="upload-col-badge">{col}</span>
            ))}
            {uploadResult.columns.length > 8 && (
              <span className="upload-col-badge upload-col-badge--more">+{uploadResult.columns.length - 8} more</span>
            )}
          </div>
          <div className="upload-sample-query">
            <span className="upload-sample-label">Try asking:</span>
            <span className="upload-sample-text">
              "Show me the first 10 rows of {uploadResult.table_name}"
            </span>
          </div>
        </div>
      )}

      {error && (
        <div className="upload-error">
          <span>⚠️</span> {error}
        </div>
      )}
    </div>
  );
};

export default FileUpload;
