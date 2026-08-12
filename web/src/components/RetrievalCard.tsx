import { ChevronDown, Library, ShieldAlert } from "lucide-react";

import type { Retrieval } from "../types";

/**
 * O que o Corpus devolveu neste turno, recolhido.
 *
 * A passagem aparece literal, com o endereço de onde saiu, porque conferir uma
 * resposta é comparar o que ela afirma com o que a fonte diz — e um resumo da
 * fonte não serve para isso.
 */
export function RetrievalCard({ retrieval }: { retrieval: Retrieval }) {
  const { passages, corpus, status } = retrieval;
  if (status === "failed" || status === "blocked") {
    return (
      <div className="retrieval-card retrieval-card-failed" role="status">
        <Library size={15} />
        <span>
          O Corpus não foi consultado neste turno
          {retrieval.detail ? `: ${retrieval.detail}` : "."}
        </span>
      </div>
    );
  }

  if (passages.length === 0) {
    return (
      <div className="retrieval-card retrieval-card-empty" role="status">
        <Library size={15} />
        <span>
          Nenhuma passagem relevante em <strong>{corpus}</strong>
          {retrieval.searchQuery ? ` para “${retrieval.searchQuery}”` : ""}.
        </span>
      </div>
    );
  }

  return (
    <details className="retrieval-card">
      <summary>
        <Library size={15} />
        <span>
          {passages.length}{" "}
          {passages.length === 1 ? "passagem recuperada de" : "passagens recuperadas de"}{" "}
          <strong>{corpus}</strong>
        </span>
        <ChevronDown size={14} className="summary-chevron" />
      </summary>
      <ol className="retrieval-passages">
        {passages.map((passage) => (
          <li key={passage.marker} id={passageAnchor(passage.marker)}>
            <div className="retrieval-address">
              <span className="retrieval-marker">[{passage.marker}]</span>
              <span className="retrieval-location">{passage.location}</span>
              {passage.untrusted && (
                <span className="retrieval-taint" title="Coletada da web: conteúdo não confiável">
                  <ShieldAlert size={13} /> web
                </span>
              )}
            </div>
            <blockquote>{passage.text}</blockquote>
            <span className="retrieval-source">{passage.source}</span>
          </li>
        ))}
      </ol>
    </details>
  );
}

export function passageAnchor(marker: number): string {
  return `passagem-${marker}`;
}
