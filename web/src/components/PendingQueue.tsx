import { FileText, Pencil, Save, Trash2 } from "lucide-react";
import { useState } from "react";
import type { PendingRequest } from "../types";

interface PendingQueueProps {
  requests: PendingRequest[];
  onChange?: (requests: PendingRequest[]) => void;
  onEdit?: (requestId: string, prompt: string) => Promise<void> | void;
  onCancel?: (requestId: string) => Promise<void> | void;
}

export function PendingQueue({
  requests,
  onChange,
  onEdit,
  onCancel,
}: PendingQueueProps) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (requests.length === 0) {
    return null;
  }

  const startEditing = (request: PendingRequest) => {
    setEditingId(request.id);
    setDraft(request.prompt);
  };

  const save = async (request: PendingRequest) => {
    const prompt = draft.trim();
    if (!prompt) return;

    setBusyId(request.id);
    setError(null);
    try {
      if (onEdit) await onEdit(request.id, prompt);
      else {
        onChange?.(
          requests.map((item) =>
            item.id === request.id ? { ...item, prompt } : item,
          ),
        );
      }
      setEditingId(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Falha ao editar a solicitação.");
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (id: string) => {
    setBusyId(id);
    setError(null);
    try {
      if (onCancel) await onCancel(id);
      else onChange?.(requests.filter((request) => request.id !== id));
      if (editingId === id) setEditingId(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Falha ao cancelar a solicitação.");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="pending-queue" aria-labelledby="pending-title">
      <div className="pending-heading">
        <div>
          <span className="eyebrow">PendingRequest</span>
          <h2 id="pending-title">Na fila · {requests.length}</h2>
        </div>
        <span className="pending-note">editável enquanto aguarda</span>
      </div>
      <div className="pending-list">
        {requests.map((request, index) => (
          <article className="pending-item" key={request.id}>
            <span className="pending-number">{index + 1}</span>
            <div className="pending-content">
              {editingId === request.id ? (
                <textarea
                  aria-label={`Editar solicitação ${index + 1}`}
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  rows={2}
                  autoFocus
                />
              ) : (
                <p>{request.prompt}</p>
              )}
              <div className="pending-meta">
                <span>{request.createdAt}</span>
                {(request.files ?? []).map((file) => (
                  <span className="file-pill" key={file}>
                    <FileText size={11} /> {file}
                  </span>
                ))}
              </div>
            </div>
            <div className="pending-actions">
              {editingId === request.id ? (
                <button
                  className="icon-button"
                  type="button"
                  onClick={() => save(request)}
                  disabled={busyId === request.id}
                  aria-label={`Salvar solicitação ${index + 1}`}
                >
                  <Save size={15} />
                </button>
              ) : (
                <button
                  className="icon-button"
                  type="button"
                  onClick={() => startEditing(request)}
                  disabled={busyId === request.id}
                  aria-label={`Editar solicitação ${index + 1}`}
                >
                  <Pencil size={15} />
                </button>
              )}
              <button
                className="icon-button icon-button--danger"
                type="button"
                onClick={() => remove(request.id)}
                disabled={busyId === request.id}
                aria-label={`Cancelar solicitação ${index + 1}`}
              >
                <Trash2 size={15} />
              </button>
            </div>
          </article>
        ))}
      </div>
      {error && <p className="inline-error" role="alert">{error}</p>}
    </section>
  );
}
