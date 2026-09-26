import { useState } from 'react';
import { api } from '@/api/client';
import { toast } from 'sonner';
import { Upload } from 'lucide-react';
import styles from './Knowledge.module.css';

interface KnowledgeUploadProps {
  onUploadSuccess: () => void;
}

export function KnowledgeUpload({ onUploadSuccess }: KnowledgeUploadProps) {
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = () => {
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);

    const files = Array.from(e.dataTransfer.files).filter(
      (f) => f.type === 'application/pdf'
    );
    if (files.length > 0) {
      handleFiles(files);
    } else {
      toast.error('Please drop PDF files only');
    }
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.currentTarget.files || []);
    handleFiles(files);
  };

  const handleFiles = async (files: File[]) => {
    setIsUploading(true);

    for (const file of files) {
      try {
        await api.uploadDocument(file);
        toast.success(`Uploaded: ${file.name}`);
      } catch (error) {
        toast.error(`Failed to upload: ${file.name}`);
      }
    }

    setIsUploading(false);
    onUploadSuccess();
  };

  return (
    <div className={styles.panel}>
      <div className={styles.panelHeader}>
        <h3 className={styles.panelTitle}>Upload Documents</h3>
      </div>

      <div
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={`${styles.dropzone} ${
          isDragging
            ? styles.dropzoneActive
            : styles.dropzoneIdle
        }`}
      >
        <label className={styles.dropLabel}>
          <Upload size={20} className={styles.muted} />
          <p className={styles.dropTitle}>Drop PDFs here or click to upload</p>
          <p className={styles.dropSubtitle}>Supports multiple files</p>
          <input
            type="file"
            accept=".pdf"
            multiple
            onChange={handleFileSelect}
            disabled={isUploading}
            className={styles.hiddenInput}
          />
        </label>
      </div>

      {isUploading && (
        <div className={styles.muted}>Uploading...</div>
      )}
    </div>
  );
}
