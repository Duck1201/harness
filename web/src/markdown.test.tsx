import { render, screen } from "@testing-library/react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { describe, expect, it } from "vitest";

describe("markdown de resposta do modelo", () => {
  it("renderiza tabela GFM como <table>, não como texto com pipes", () => {
    const content = [
      "| Ferramenta | Descrição |",
      "|------------|------------|",
      "| `read_file` | Lê um intervalo de linhas |",
    ].join("\n");

    render(<ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>);

    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Ferramenta" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "read_file" })).toBeInTheDocument();
    expect(screen.queryByText(/^\|/)).toBeNull();
  });
});
