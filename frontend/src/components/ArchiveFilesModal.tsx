import React, { useState, useEffect } from 'react';
import { X, Search, FileText, Folder, File, Copy, Check, Loader2, AlertCircle, HardDrive } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';

interface ArchiveFileInfo {
  path: string;
  size: number;
  mtime?: string | null;
  mode?: string | null;
  is_dir: boolean;
}

interface ArchiveFilesModalProps {
  historyId: number | null;
  archiveName: string;
  onClose: () => void;
}

export default function ArchiveFilesModal({ historyId, archiveName, onClose }: ArchiveFilesModalProps) {
  const { t } = useTranslation();
  const [files, setFiles] = useState<ArchiveFileInfo[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState<string>('');

  const [selectedFile, setSelectedFile] = useState<ArchiveFileInfo | null>(null);
  const [contentLoading, setContentLoading] = useState<boolean>(false);
  const [fileContent, setFileContent] = useState<string | null>(null);
  const [contentMessage, setContentMessage] = useState<string | null>(null);
  const [isText, setIsText] = useState<boolean>(true);
  const [copied, setCopied] = useState<boolean>(false);

  useEffect(() => {
    if (!historyId) return;

    setLoading(true);
    setError(null);
    setSelectedFile(null);
    setFileContent(null);

    fetch(`/api/nodes/history/${historyId}/files`)
      .then(async (res) => {
        if (!res.ok) {
          const errData = await res.json().catch(() => ({}));
          throw new Error(errData.detail || `Server returned ${res.status}`);
        }
        return res.json();
      })
      .then((data) => {
        setFiles(data.files || []);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message || 'Failed to load archive file list');
        setLoading(false);
      });
  }, [historyId]);

  const handleSelectFile = (file: ArchiveFileInfo) => {
    if (file.is_dir || !historyId) return;

    setSelectedFile(file);
    setContentLoading(true);
    setFileContent(null);
    setContentMessage(null);
    setCopied(false);

    const encodedPath = encodeURIComponent(file.path);
    fetch(`/api/nodes/history/${historyId}/file-content?path=${encodedPath}`)
      .then(async (res) => {
        if (!res.ok) {
          const errData = await res.json().catch(() => ({}));
          throw new Error(errData.detail || `Server returned ${res.status}`);
        }
        return res.json();
      })
      .then((data) => {
        setIsText(data.is_text);
        if (data.is_text) {
          setFileContent(data.content || '');
        } else {
          setContentMessage(data.message || t('binaryFileWarning'));
        }
        setContentLoading(false);
      })
      .catch((err) => {
        setContentMessage(err.message || 'Failed to extract file content');
        setContentLoading(false);
      });
  };

  const formatSize = (bytes: number) => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
  };

  const handleCopy = () => {
    if (!fileContent) return;
    navigator.clipboard.writeText(fileContent);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const filteredFiles = files.filter((f) =>
    f.path.toLowerCase().includes(searchQuery.toLowerCase())
  );

  if (!historyId) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/75 backdrop-blur-md p-4 animate-fade-in">
      <div className="bg-slate-900 border border-slate-800 rounded-xl shadow-2xl w-full max-w-6xl h-[85vh] flex flex-col overflow-hidden animate-modal-in">
        
        {/* Header */}
        <div className="px-6 py-4 border-b border-slate-800 flex items-center justify-between bg-slate-900/80">
          <div className="flex items-center space-x-3">
            <div className="p-2 bg-indigo-500/10 border border-indigo-500/20 rounded-lg text-indigo-400">
              <HardDrive className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-slate-100 flex items-center space-x-2">
                <span>{t('viewArchiveFiles')}</span>
              </h2>
              <p className="text-xs text-slate-400 font-mono mt-0.5">{archiveName}</p>
            </div>
          </div>

          <button
            onClick={onClose}
            className="p-1.5 text-slate-400 hover:text-slate-200 hover:bg-slate-800 rounded-lg transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content Body */}
        <div className="flex-1 flex overflow-hidden">
          
          {/* Left Pane: File Tree / Search List */}
          <div className="w-1/2 border-r border-slate-800 flex flex-col bg-slate-950/40">
            {/* Search Input */}
            <div className="p-3 border-b border-slate-800">
              <div className="relative">
                <Search className="w-4 h-4 absolute left-3 top-2.5 text-slate-400" />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder={t('searchFiles')}
                  className="w-full bg-slate-900 border border-slate-800 rounded-lg pl-9 pr-3 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500/50"
                />
              </div>
            </div>

            {/* File List */}
            <div className="flex-1 overflow-y-auto p-2 space-y-0.5">
              {loading ? (
                <div className="flex flex-col items-center justify-center h-48 space-y-2 text-slate-400">
                  <Loader2 className="w-6 h-6 animate-spin text-indigo-400" />
                  <span className="text-xs">{t('loadingFiles')}</span>
                </div>
              ) : error ? (
                <div className="p-4 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-xs flex items-center space-x-2">
                  <AlertCircle className="w-4 h-4 shrink-0" />
                  <span>{error}</span>
                </div>
              ) : filteredFiles.length === 0 ? (
                <div className="text-center py-12 text-xs text-slate-500">
                  {t('noFilesFound')}
                </div>
              ) : (
                filteredFiles.map((file, idx) => {
                  const isSelected = selectedFile?.path === file.path;
                  return (
                    <div
                      key={idx}
                      onClick={() => handleSelectFile(file)}
                      className={`flex items-center justify-between px-3 py-2 rounded-lg text-xs font-mono transition-colors ${
                        file.is_dir
                          ? 'text-slate-400 cursor-default hover:bg-slate-900/50'
                          : isSelected
                          ? 'bg-indigo-600/20 text-indigo-300 border border-indigo-500/30 font-medium cursor-pointer'
                          : 'text-slate-300 hover:bg-slate-800/60 cursor-pointer'
                      }`}
                    >
                      <div className="flex items-center space-x-2 truncate pr-2">
                        {file.is_dir ? (
                          <Folder className="w-4 h-4 text-amber-400 shrink-0" />
                        ) : (
                          <File className="w-4 h-4 text-slate-400 shrink-0" />
                        )}
                        <span className="truncate">{file.path}</span>
                      </div>
                      {!file.is_dir && (
                        <span className="text-[11px] text-slate-500 shrink-0">
                          {formatSize(file.size)}
                        </span>
                      )}
                    </div>
                  );
                })
              )}
            </div>
          </div>

          {/* Right Pane: Config & File Content Reader */}
          <div className="w-1/2 flex flex-col bg-slate-900/50">
            {selectedFile ? (
              <div className="flex-1 flex flex-col overflow-hidden">
                {/* Reader Header */}
                <div className="px-4 py-2.5 border-b border-slate-800 flex items-center justify-between bg-slate-950/60">
                  <div className="flex items-center space-x-2 truncate">
                    <FileText className="w-4 h-4 text-indigo-400 shrink-0" />
                    <span className="text-xs font-mono font-medium text-slate-200 truncate">
                      {selectedFile.path}
                    </span>
                    <span className="text-[10px] bg-slate-800 text-slate-400 px-1.5 py-0.5 rounded">
                      {formatSize(selectedFile.size)}
                    </span>
                  </div>

                  {isText && fileContent !== null && (
                    <button
                      onClick={handleCopy}
                      className="flex items-center space-x-1.5 px-2.5 py-1 text-xs bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-md transition-colors border border-slate-700"
                    >
                      {copied ? (
                        <>
                          <Check className="w-3.5 h-3.5 text-emerald-400" />
                          <span className="text-emerald-400">{t('copied')}</span>
                        </>
                      ) : (
                        <>
                          <Copy className="w-3.5 h-3.5" />
                          <span>{t('copyContent')}</span>
                        </>
                      )}
                    </button>
                  )}
                </div>

                {/* Reader Body */}
                <div className="flex-1 overflow-y-auto p-4 bg-slate-950 font-mono text-xs text-slate-200">
                  {contentLoading ? (
                    <div className="flex flex-col items-center justify-center h-full space-y-2 text-slate-400">
                      <Loader2 className="w-6 h-6 animate-spin text-indigo-400" />
                      <span className="text-xs">{t('loadingFiles')}</span>
                    </div>
                  ) : !isText || contentMessage ? (
                    <div className="flex flex-col items-center justify-center h-full text-center p-6 space-y-3">
                      <AlertCircle className="w-8 h-8 text-amber-400" />
                      <p className="text-xs text-slate-300 max-w-sm">
                        {contentMessage || t('binaryFileWarning')}
                      </p>
                    </div>
                  ) : (
                    <pre className="whitespace-pre-wrap break-words leading-relaxed text-slate-300 font-mono text-[11px]">
                      {fileContent}
                    </pre>
                  )}
                </div>
              </div>
            ) : (
              <div className="flex-1 flex flex-col items-center justify-center text-center p-6 text-slate-500 space-y-2">
                <FileText className="w-10 h-10 text-slate-700" />
                <span className="text-xs">{t('selectFileToView')}</span>
              </div>
            )}
          </div>

        </div>

      </div>
    </div>
  );
}
