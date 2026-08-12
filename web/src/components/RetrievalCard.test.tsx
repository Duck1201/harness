import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "../App";
import { MockHarnessClient } from "../client/MockHarnessClient";
import type { Retrieval } from "../types";
import { RetrievalCard } from "./RetrievalCard";

const retrieval: Retrieval = {
  corpus: "Manual do servidor",
  status: "completed",
  searchQuery: "proxy port",
  detail: null,
  passages: [
    {
      marker: 1,
      document: "Manual do servidor",
      location: "Manual do servidor > 4. Rede > 4.1 Proxy",
      origin: "upload",
      source: "manual.pdf",
      untrusted: false,
      text: "O proxy escuta na porta 8899.",
    },
    {
      marker: 2,
      document: "Wiki",
      location: "Wiki > Chefes",
      origin: "scrape",
      source: "https://wiki.test/chefes",
      untrusted: true,
      text: "O chefe final tem 320 pontos de vida.",
    },
  ],
};

describe("RetrievalCard", () => {
  it("mostra a passagem literal com o endereço e marca o que veio da web", () => {
    render(<RetrievalCard retrieval={retrieval} />);

    expect(screen.getByText("2 passagens recuperadas de")).toBeTruthy();
    expect(screen.getByText("O proxy escuta na porta 8899.")).toBeTruthy();
    expect(screen.getByText("Manual do servidor > 4. Rede > 4.1 Proxy")).toBeTruthy();
    // A passagem coletada é a única marcada: upload do Operator não carrega taint.
    expect(screen.getAllByTitle(/não confiável/)).toHaveLength(1);
  });

  it("diz que nada foi encontrado em vez de mostrar a passagem menos ruim", () => {
    render(<RetrievalCard retrieval={{ ...retrieval, passages: [] }} />);

    expect(screen.getByText(/Nenhuma passagem relevante/)).toBeTruthy();
  });

  it("distingue o Corpus vazio do Corpus que não pôde ser consultado", () => {
    render(
      <RetrievalCard
        retrieval={{ ...retrieval, passages: [], status: "failed", detail: "embedder fora" }}
      />,
    );

    expect(screen.getByText(/não foi consultado neste turno: embedder fora/)).toBeTruthy();
  });
});

describe("marcadores de citação", () => {
  it("liga o marcador existente à passagem e deixa o inventado como texto", async () => {
    const client = new MockHarnessClient();
    client.setMessages([
      {
        id: "message-1",
        turnId: "turn-1",
        role: "assistant",
        content: "A porta é 8899 [1], e o chefe tem 320 de vida [9].",
        createdAt: "2026-08-10T10:43:00Z",
        retrieval: { ...retrieval, passages: [retrieval.passages[0]!] },
      },
    ]);

    render(<App client={client} />);

    const valid = await screen.findByRole("link", { name: "[1]" });
    expect(valid.getAttribute("href")).toBe("#passagem-1");
    // Fora da faixa: o modelo citou a nona passagem de uma só. Sem link.
    expect(screen.queryByRole("link", { name: "[9]" })).toBeNull();
    expect(screen.getByText(/\[9\]/)).toBeTruthy();
  });
});
