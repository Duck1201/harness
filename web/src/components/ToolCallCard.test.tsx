import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ToolCallCard } from "./ToolCallCard";
import type { ToolCall } from "../types";

const baseTool: ToolCall = {
  id: "tool-test",
  name: "read_file",
  label: "Ler arquivo",
  status: "success",
  arguments: { path: "README.md" },
  summary: "Arquivo lido",
  result: "conteúdo",
};

describe("ToolCallCard", () => {
  it("mantém sucessos fechados por padrão", () => {
    const { container } = render(<ToolCallCard tool={baseTool} />);

    expect(screen.getByText("Concluída")).toBeInTheDocument();
    expect(container.querySelector("details")).not.toHaveAttribute("open");
  });

  it("abre erros por padrão e expõe a causa", () => {
    const tool: ToolCall = {
      ...baseTool,
      status: "error",
      summary: "Caminho recusado",
      error: "path_outside_workspace",
    };
    const { container } = render(<ToolCallCard tool={tool} />);

    expect(container.querySelector("details")).toHaveAttribute("open");
    expect(screen.getByText("path_outside_workspace")).toBeInTheDocument();
  });
});
