import { ShieldAlert } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { PendingConfirmation } from "../types";
import { UnifiedDiff } from "./ToolCallCard";

interface ConfirmationDialogProps {
  confirmation: PendingConfirmation;
  busy: boolean;
  onResolve: (approved: boolean, waive: boolean) => void;
}

const KIND_LABEL: Record<string, string> = {
  create: "cria",
  replace: "substitui",
  edit: "edita",
};

export function ConfirmationDialog({
  confirmation,
  busy,
  onResolve,
}: ConfirmationDialogProps) {
  const [waive, setWaive] = useState(false);
  const dialog = useRef<HTMLDivElement>(null);
  const deny = useRef<HTMLButtonElement>(null);
  const tainted = confirmation.reason_code.startsWith("web_taint");
  // Sob taint o mesmo diálogo cobre escrita e saída para a rede: perguntar
  // "aprovar escrita" sobre um web_search descreve a decisão errada.
  const writes = confirmation.tool_calls.some((call) =>
    call.effects?.includes("workspace_write"),
  );
  const grants = confirmation.reason_code === "write_grant_required";
  const webGrant = confirmation.reason_code === "web_access_grant_required";
  // Só o waiver de escrita existe no servidor: oferecê-lo aqui para o grant de
  // web prometeria algo que a política não guarda.
  const waivable = !tainted && !webGrant;

  useEffect(() => {
    deny.current?.focus();
  }, [confirmation.id]);

  // The Turn is parked until this is answered, so the dialog keeps the focus.
  // Escape deliberately does nothing: dismissing by reflex would deny the write
  // and end the Turn.
  const trapFocus = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "Tab") return;
    const focusable = [
      ...(dialog.current?.querySelectorAll<HTMLElement>(
        "button:not([disabled]), input:not([disabled])",
      ) ?? []),
    ];
    const first = focusable.at(0);
    const last = focusable.at(-1);
    if (!first || !last) return;
    const edge = event.shiftKey ? first : last;
    if (document.activeElement !== edge) return;
    event.preventDefault();
    (event.shiftKey ? last : first).focus();
  };

  return (
    <div className="modal-scrim">
      <div
        className="confirmation-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirmation-title"
        ref={dialog}
        onKeyDown={trapFocus}
      >
        <header>
          <span className="confirmation-mark" aria-hidden="true">
            <ShieldAlert size={15} />
          </span>
          <h2 id="confirmation-title">
            {tainted
              ? writes
                ? "Escrita com dado da web"
                : "Novo acesso à web com dado da web"
              : webGrant
                ? "Permitir acesso à web nesta conversa"
                : grants
                  ? "Permitir escrita nesta conversa"
                  : "Confirmar escrita"}
          </h2>
        </header>
        <p>
          {tainted
            ? writes
              ? "O Turn leu conteúdo da web e agora quer escrever no Workspace. Aprovar vale só para esta chamada: não cria grant nem amplia acesso."
              : "O Turn leu conteúdo da web e agora quer sair para a rede de novo, levando o que leu. Aprovar vale só para esta chamada: não cria grant nem amplia acesso."
            : webGrant
              ? "A conversa ainda não pode acessar a web. Aprovar concede o WebAccessGrant e segue com esta chamada sem reenviar o prompt; o grant fica visível no cabeçalho e pode ser revogado a qualquer momento."
              : grants
                ? "A conversa ainda não pode escrever. Aprovar concede o WriteGrant e executa esta escrita; o grant fica visível no cabeçalho e pode ser revogado a qualquer momento."
                : "O modelo quer escrever no Workspace. Aprovar vale só para esta chamada: não cria grant nem amplia acesso."}
        </p>

        <div className="confirmation-body">
          {confirmation.tool_calls.map((call) => {
            const preview = confirmation.previews.find(
              (item) => item.tool_call_id === call.id,
            );
            return (
              <section className="confirmation-call" key={call.id}>
                <h3>
                  <code>{call.name}</code>
                  {preview && (
                    <span className="confirmation-target">
                      {KIND_LABEL[preview.kind] ?? preview.kind} <b>{preview.path}</b>
                    </span>
                  )}
                </h3>
                {preview ? (
                  <>
                    <UnifiedDiff diff={preview.diff} />
                    {preview.truncated && (
                      <p className="confirmation-note">
                        Diff cortado: a mudança é maior do que o mostrado aqui.
                      </p>
                    )}
                  </>
                ) : (
                  <pre className="confirmation-arguments">
                    {JSON.stringify(call.arguments, null, 2)}
                  </pre>
                )}
              </section>
            );
          })}
        </div>

        <div className="confirmation-footer">
          {waivable && (
            <label className="confirmation-waiver">
              <input
                type="checkbox"
                checked={waive}
                disabled={busy}
                onChange={(event) => setWaive(event.target.checked)}
              />
              Não perguntar mais nesta conversa
            </label>
          )}
          <div className="confirmation-actions">
            <button
              type="button"
              className="secondary-button"
              onClick={() => onResolve(false, false)}
              disabled={busy}
              ref={deny}
            >
              Negar
            </button>
            <button
              type="button"
              className="primary-button"
              onClick={() => onResolve(true, waive)}
              disabled={busy}
            >
              {webGrant
                ? "Permitir acesso"
                : grants
                  ? "Permitir escrita"
                  : tainted && !writes
                    ? "Aprovar acesso"
                    : "Aprovar escrita"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
