import React, { useState, useEffect, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { X, Search, FileText, Folder, FolderOpen, File, Copy, Check, Loader2, AlertCircle, HardDrive, Maximize2, Minimize2, ChevronRight, ChevronDown, Download } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';

interface ArchiveFileInfo {
  path: string;
  size: number;
  mtime?: string | null;
  mode?: string | null;
  is_dir: boolean;
}

interface TreeNode {
  name: string;
  full_path: string;
  is_dir: boolean;
  size: number;
  mtime?: string | null;
  mode?: string | null;
  children: TreeNode[];
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
  const [isMaximized, setIsMaximized] = useState<boolean>(false);
  const [expandedPaths, setExpandedPaths] = useState<Set<string>>(new Set());

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
    setExpandedPaths(new Set());

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

  const handleDownload = (filePath: string, isDir: boolean = false) => {
    if (!historyId || !filePath) return;
    const encodedPath = encodeURIComponent(filePath);
    const downloadUrl = `/api/nodes/history/${historyId}/download-file?path=${encodedPath}&is_dir=${isDir}`;
    const link = document.createElement('a');
    link.href = downloadUrl;
    const defaultName = filePath.split('/').pop() || (isDir ? 'folder' : 'file');
    link.download = isDir ? `${defaultName}.zip` : defaultName;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
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

  const toggleExpand = (path: string) => {
    setExpandedPaths((prev) => {
      const next = new Set(prev);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  };

  // Build tree hierarchy and compute cumulative directory sizes
  const { treeRoots, pathToNodeMap } = useMemo(() => {
    const nodeMap = new Map<string, TreeNode>();
    const roots: TreeNode[] = [];

    const getOrCreateNode = (
      path: string,
      is_dir: boolean,
      size: number = 0,
      mtime: string | null = null,
      mode: string | null = null
    ): TreeNode => {
      const cleanPath = path.replace(/\/$/, '');
      if (nodeMap.has(cleanPath)) {
        const existing = nodeMap.get(cleanPath)!;
        if (!existing.is_dir && is_dir) existing.is_dir = true;
        if (size > 0 && !existing.is_dir) existing.size = size;
        return existing;
      }

      const parts = cleanPath.split('/');
      const name = parts[parts.length - 1];

      const newNode: TreeNode = {
        name,
        full_path: cleanPath,
        is_dir,
        size: is_dir ? 0 : size,
        mtime,
        mode,
        children: [],
      };
      nodeMap.set(cleanPath, newNode);

      if (parts.length === 1) {
        roots.push(newNode);
      } else {
        const parentPath = parts.slice(0, -1).join('/');
        const parentNode = getOrCreateNode(parentPath, true);
        parentNode.children.push(newNode);
      }

      return newNode;
    };

    files.forEach((f) => {
      getOrCreateNode(f.path, f.is_dir, f.size || 0, f.mtime || null, f.mode || null);
    });

    // Calculate cumulative folder sizes recursively
    const calcCumulativeSize = (node: TreeNode): number => {
      if (!node.is_dir) {
        return node.size;
      }
      let total = 0;
      for (const child of node.children) {
        total += calcCumulativeSize(child);
      }
      node.size = total;
      return total;
    };

    roots.forEach((root) => calcCumulativeSize(root));

    // Sort: directories first, then files alphabetically
    const sortNodes = (nodes: TreeNode[]) => {
      nodes.sort((a, b) => {
        if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
        return a.name.localeCompare(b.name);
      });
      nodes.forEach((n) => {
        if (n.children.length > 0) sortNodes(n.children);
      });
    };

    sortNodes(roots);

    return { treeRoots: roots, pathToNodeMap: nodeMap };
  }, [files]);

  // Flatten visible nodes for rendering
  const visibleTreeNodes = useMemo(() => {
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      const matches: { node: TreeNode; depth: number }[] = [];
      files.forEach((f) => {
        if (f.path.toLowerCase().includes(q)) {
          const clean = f.path.replace(/\/$/, '');
          const node = pathToNodeMap.get(clean) || {
            name: clean.split('/').pop() || clean,
            full_path: clean,
            is_dir: f.is_dir,
            size: f.size || 0,
            children: [],
          };
          matches.push({ node, depth: 0 });
        }
      });
      return matches;
    }

    const result: { node: TreeNode; depth: number }[] = [];
    const traverse = (nodes: TreeNode[], depth: number) => {
      for (const node of nodes) {
        result.push({ node, depth });
        if (node.is_dir && expandedPaths.has(node.full_path)) {
          traverse(node.children, depth + 1);
        }
      }
    };

    traverse(treeRoots, 0);
    return result;
  }, [treeRoots, expandedPaths, searchQuery, files, pathToNodeMap]);

  const displayedFiles = useMemo(() => {
    return visibleTreeNodes.slice(0, 400);
  }, [visibleTreeNodes]);

  if (!historyId) return null;

  return createPortal(
    <div className={`fixed inset-0 z-[100] flex items-center justify-center bg-black/85 animate-fade-in ${isMaximized ? 'p-0' : 'p-4'}`}>
      <div className={`bg-slate-900 border border-slate-800 shadow-2xl flex flex-col overflow-hidden ${
        isMaximized ? 'w-full h-full rounded-none' : 'w-full max-w-6xl h-[85vh] rounded-xl animate-modal-in'
      }`}>
        
        {/* Header */}
        <div className="px-6 py-4 border-b border-slate-800 flex items-center justify-between bg-slate-900">
          <div className="flex items-center space-x-3">
            <div className="p-2 bg-indigo-500/10 border border-indigo-500/20 rounded-lg text-indigo-400">
              <HardDrive className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-slate-100 flex items-center space-x-2">
                <span>{t('viewArchiveFiles')}</span>
              </h2>
              <p className="text-xs text-slate-400 font-mono mt-0.5" title={archiveName}>{archiveName}</p>
            </div>
          </div>

          <div className="flex items-center space-x-1.5">
            <button
              onClick={() => setIsMaximized(!isMaximized)}
              className="p-1.5 text-slate-400 hover:text-slate-200 hover:bg-slate-800 rounded-lg transition-colors"
              title={isMaximized ? "Restore window" : "Maximize to fullscreen"}
            >
              {isMaximized ? <Minimize2 className="w-5 h-5" /> : <Maximize2 className="w-5 h-5" />}
            </button>
            <button
              onClick={onClose}
              className="p-1.5 text-slate-400 hover:text-slate-200 hover:bg-slate-800 rounded-lg transition-colors"
              title="Close"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Content Body */}
        <div className="flex-1 flex overflow-hidden">
          
          {/* Left Pane: File Tree / Search List */}
          <div className="w-1/2 border-r border-slate-800 flex flex-col bg-slate-950/40">
            {/* Search Input */}
            <div className="p-3 border-b border-slate-800 flex items-center justify-between gap-2">
              <div className="relative flex-1">
                <Search className="w-4 h-4 absolute left-3 top-2.5 text-slate-400" />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder={t('searchFiles')}
                  className="w-full bg-slate-900 border border-slate-800 rounded-lg pl-9 pr-3 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500/50"
                />
              </div>
              {visibleTreeNodes.length > 400 && (
                <span className="text-[10px] text-slate-500 font-mono shrink-0">
                  Showing 400 of {visibleTreeNodes.length}
                </span>
              )}
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
              ) : visibleTreeNodes.length === 0 ? (
                <div className="text-center py-12 text-xs text-slate-500">
                  {t('noFilesFound')}
                </div>
              ) : (
                <>
                  {displayedFiles.map(({ node, depth }, idx) => {
                    const isExpanded = expandedPaths.has(node.full_path);
                    const isSelected = selectedFile?.path === node.full_path;

                    return (
                      <div
                        key={node.full_path || idx}
                        onClick={() => {
                          if (node.is_dir) {
                            toggleExpand(node.full_path);
                          } else {
                            handleSelectFile({
                              path: node.full_path,
                              size: node.size,
                              is_dir: false,
                              mtime: node.mtime,
                              mode: node.mode,
                            });
                          }
                        }}
                        title={node.full_path}
                        style={{ paddingLeft: searchQuery.trim() ? '0.75rem' : `${depth * 1.25 + 0.75}rem` }}
                        className={`group flex items-center justify-between pr-3 py-1.5 rounded-lg text-xs font-mono transition-colors cursor-pointer select-none ${
                          node.is_dir
                            ? 'text-slate-200 hover:bg-slate-800/80 font-semibold'
                            : isSelected
                            ? 'bg-indigo-600/20 text-indigo-300 border border-indigo-500/30 font-medium'
                            : 'text-slate-300 hover:bg-slate-800/60'
                        }`}
                      >
                        <div className="flex items-center space-x-1.5 truncate pr-2 min-w-0 flex-1">
                          {node.is_dir ? (
                            <>
                              {isExpanded ? (
                                <ChevronDown className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                              ) : (
                                <ChevronRight className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                              )}
                              {isExpanded ? (
                                <FolderOpen className="w-4 h-4 text-amber-400 shrink-0" />
                              ) : (
                                <Folder className="w-4 h-4 text-amber-400 shrink-0" />
                              )}
                            </>
                          ) : (
                            <>
                              <span className="w-3.5 shrink-0" />
                              <File className="w-4 h-4 text-slate-400 shrink-0" />
                            </>
                          )}
                          <span className="truncate" title={node.full_path}>
                            {searchQuery.trim() ? node.full_path : node.name}
                          </span>
                        </div>
                        <div className="flex items-center space-x-2 shrink-0">
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              handleDownload(node.full_path, node.is_dir);
                            }}
                            className="p-1 text-slate-400 hover:text-indigo-300 hover:bg-slate-800 rounded opacity-0 group-hover:opacity-100 transition-opacity"
                            title={node.is_dir ? "Download Folder as ZIP" : "Download File"}
                          >
                            <Download className="w-3.5 h-3.5" />
                          </button>
                          <span className="text-[11px] text-slate-500 font-normal">
                            {formatSize(node.size)}
                          </span>
                        </div>
                      </div>
                    );
                  })}
                  {visibleTreeNodes.length > 400 && (
                    <div className="text-center py-3 text-[11px] text-amber-400/80 font-mono bg-amber-500/5 border border-amber-500/10 rounded-lg my-1">
                      Showing first 400 matching items. Expand folders or refine search to see more.
                    </div>
                  )}
                </>
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
                    <span className="text-xs font-mono font-medium text-slate-200 truncate" title={selectedFile.path}>
                      {selectedFile.path}
                    </span>
                    <span className="text-[10px] bg-slate-800 text-slate-400 px-1.5 py-0.5 rounded">
                      {formatSize(selectedFile.size)}
                    </span>
                  </div>

                  <div className="flex items-center space-x-2">
                    <button
                      onClick={() => handleDownload(selectedFile.path)}
                      className="flex items-center space-x-1.5 px-2.5 py-1 text-xs bg-indigo-600/20 hover:bg-indigo-600/40 text-indigo-300 rounded-md transition-colors border border-indigo-500/30"
                      title="Download File"
                    >
                      <Download className="w-3.5 h-3.5" />
                      <span>{t('download') || 'Download'}</span>
                    </button>

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
                      <button
                        onClick={() => handleDownload(selectedFile.path)}
                        className="flex items-center space-x-2 px-4 py-2 text-xs bg-indigo-600 hover:bg-indigo-500 text-white font-medium rounded-lg shadow-lg transition-colors cursor-pointer"
                      >
                        <Download className="w-4 h-4" />
                        <span>{t('downloadFile') || 'Download File'}</span>
                      </button>
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
    </div>,
    document.body
  );
}
