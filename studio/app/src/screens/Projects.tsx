import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowRight, FolderKanban, Loader2, Plus, X } from 'lucide-react';
import { api } from '../lib/api';
import { kebab, prettyName, projectMembers } from '../lib/model';

function NewProjectModal({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');

  const create = useMutation({
    mutationFn: () =>
      api.createProject({
        namespace: kebab(name),
        name: kebab(name),
        description: description || undefined,
        required_resources: [],
      }),
    onSuccess: (manifest) => {
      queryClient.invalidateQueries({ queryKey: ['projects'] });
      navigate(`/projects/${manifest.namespace}/${manifest.name}`);
    },
  });

  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
          <h2 style={{ fontSize: 16 }}>New project</h2>
          <button className="kebab" onClick={onClose} aria-label="Close">
            <X size={16} />
          </button>
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) create.mutate();
          }}
          style={{ display: 'flex', flexDirection: 'column', gap: 14 }}
        >
          <div className="field">
            <label>Name</label>
            <input className="input" placeholder="e.g. Support triage" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
          </div>
          <div className="field">
            <label>What is it for? (optional)</label>
            <input className="input" value={description} onChange={(e) => setDescription(e.target.value)} />
          </div>
          {create.isError && <div className="error-box">{(create.error as Error).message}</div>}
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
            <button type="button" className="btn btn-secondary" onClick={onClose}>
              Cancel
            </button>
            <button className="btn btn-primary" disabled={!name.trim() || create.isPending}>
              {create.isPending ? <Loader2 size={14} className="spin" /> : 'Create project'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export function Projects() {
  const [showCreate, setShowCreate] = useState(false);
  const { data: projects, isLoading, isError, error } = useQuery({
    queryKey: ['projects'],
    queryFn: api.listProjects,
  });

  return (
    <div className="page" style={{ width: '100%' }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12, marginBottom: 22 }}>
        <div>
          <h1 style={{ fontSize: 22 }}>Projects</h1>
          <p style={{ color: 'var(--muted)', margin: '4px 0 0', fontSize: 13.5 }}>
            Each project groups the assistants and workflows that make up one solution.
          </p>
        </div>
        <button className="btn btn-primary" onClick={() => setShowCreate(true)}>
          <Plus size={15} /> New project
        </button>
      </div>

      {isLoading ? (
        <div className="empty">
          <Loader2 size={20} className="spin" style={{ margin: '0 auto 8px', display: 'block' }} /> Loading…
        </div>
      ) : isError ? (
        <div className="error-box">Couldn't load projects: {(error as Error).message}</div>
      ) : !projects || projects.length === 0 ? (
        <div className="card empty">
          <FolderKanban size={30} style={{ margin: '0 auto 10px', display: 'block', color: 'var(--faint)' }} />
          <p style={{ margin: 0 }}>No projects yet — create the first one.</p>
        </div>
      ) : (
        <div style={{ display: 'grid', gap: 14, gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))' }}>
          {projects.map((p) => {
            const members = projectMembers(p);
            return (
              <Link key={p.id} to={`/projects/${p.namespace}/${p.name}`} className="card" style={{ padding: 18, display: 'flex', flexDirection: 'column', gap: 8 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span className="row-logo" style={{ background: 'var(--accent-soft)', color: 'var(--accent-ink)' }}>
                    <FolderKanban size={16} />
                  </span>
                  <span style={{ fontWeight: 700, color: 'var(--ink)', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {prettyName(p.name)}
                  </span>
                  <ArrowRight size={14} style={{ color: 'var(--faint)' }} />
                </div>
                <p style={{ margin: 0, fontSize: 12.5, color: 'var(--muted)', minHeight: 34 }}>
                  {p.description || 'No description yet.'}
                </p>
                <p style={{ margin: 0, fontSize: 11.5, color: 'var(--faint)' }}>
                  {members.assistants.length} assistant{members.assistants.length === 1 ? '' : 's'} ·{' '}
                  {members.workflows.length} workflow{members.workflows.length === 1 ? '' : 's'}
                </p>
              </Link>
            );
          })}
        </div>
      )}

      {showCreate && <NewProjectModal onClose={() => setShowCreate(false)} />}
    </div>
  );
}
