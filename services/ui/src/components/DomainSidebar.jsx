/**
 * DomainSidebar.jsx — left panel listing available domains + create form
 */
import { useState } from "react";
import { Plus, Database, RefreshCw, ChevronRight } from "lucide-react";
import clsx from "clsx";
import { Spinner, Badge } from "./ui.jsx";
import { useAuth } from "@/lib/auth";

export function DomainSidebar({ domains, loading, activeDomainId, onSelect, onRefresh, onCreate }) {
  const { user } = useAuth();
  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [creating, setCreating] = useState(false);

  const handleCreate = async (e) => {
    e.preventDefault();
    if (!name.trim()) return;
    setCreating(true);
    try {
      await onCreate({ name: name.trim(), description: desc.trim() || undefined });
      setName(""); setDesc(""); setShowCreate(false);
    } finally {
      setCreating(false);
    }
  };

  const active = domains.find((d) => d.id === activeDomainId);

  return (
    <aside className="flex flex-col w-56 min-w-[14rem] h-full bg-surface-2 border-r border-surface-4">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-surface-4">
        <span className="text-xs font-semibold text-text-secondary uppercase tracking-widest">
          Domains
        </span>
        <div className="flex items-center gap-1">
          <button
            onClick={onRefresh}
            className="btn-ghost p-1.5 rounded"
            title="Refresh domains"
          >
            <RefreshCw size={13} />
          </button>
          {user?.is_system_admin && (
            <button
              onClick={() => setShowCreate((s) => !s)}
              className="btn-ghost p-1.5 rounded text-accent"
              title="Create domain"
            >
              <Plus size={13} />
            </button>
          )}
        </div>
      </div>

      {/* Create form */}
      {showCreate && (
        <form
          onSubmit={handleCreate}
          className="px-4 py-3 border-b border-surface-4 space-y-2 animate-slide-up"
        >
          <input
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Domain name"
            className="input text-xs py-1.5"
            required
          />
          <input
            value={desc}
            onChange={(e) => setDesc(e.target.value)}
            placeholder="Description (optional)"
            className="input text-xs py-1.5"
          />
          <div className="flex gap-2">
            <button
              type="submit"
              disabled={creating || !name.trim()}
              className="btn-primary text-xs py-1.5 flex-1 flex items-center justify-center gap-1"
            >
              {creating ? <Spinner size="sm" /> : "Create"}
            </button>
            <button
              type="button"
              onClick={() => setShowCreate(false)}
              className="btn-ghost text-xs py-1.5"
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      {/* List */}
      <div className="flex-1 overflow-y-auto py-2">
        {loading ? (
          <div className="flex justify-center py-6">
            <Spinner />
          </div>
        ) : domains.length === 0 ? (
          <p className="text-xs text-text-muted text-center px-4 py-6">
            No domains yet.
            {user?.is_system_admin && " Click + to create one."}
          </p>
        ) : (
          domains.map((d) => (
            <button
              key={d.id}
              onClick={() => onSelect(d.id)}
              className={clsx(
                "w-full flex items-center gap-2.5 px-4 py-2.5 text-left transition-colors text-sm",
                d.id === activeDomainId
                  ? "bg-accent/10 text-accent border-r-2 border-accent"
                  : "text-text-secondary hover:bg-surface-3 hover:text-text-primary",
              )}
            >
              <Database size={13} className="flex-shrink-0 opacity-70" />
              <span className="flex-1 truncate">{d.name}</span>
              {d.status === "archived" && (
                <Badge variant="default" className="text-[10px]">archived</Badge>
              )}
              {d.id === activeDomainId && (
                <ChevronRight size={12} className="flex-shrink-0 opacity-60" />
              )}
            </button>
          ))
        )}
      </div>

      {/* Active domain footer */}
      {active && (
        <div className="px-4 py-2.5 border-t border-surface-4">
          <p className="text-[10px] text-text-muted truncate font-mono">{active.id}</p>
        </div>
      )}
    </aside>
  );
}
