import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import type { PendingRequest } from "../types";
import { PendingQueue } from "./PendingQueue";

const initialRequests: PendingRequest[] = [
  {
    id: "pending-test",
    prompt: "Revisar o schema",
    createdAt: "agora",
    files: [],
    mode: "queued",
  },
];

function QueueHarness() {
  const [requests, setRequests] = useState(initialRequests);
  return <PendingQueue requests={requests} onChange={setRequests} />;
}

describe("PendingQueue", () => {
  it("permite editar uma solicitação pendente", async () => {
    const user = userEvent.setup();
    render(<QueueHarness />);

    await user.click(screen.getByRole("button", { name: "Editar solicitação 1" }));
    const editor = screen.getByRole("textbox", { name: "Editar solicitação 1" });
    await user.clear(editor);
    await user.type(editor, "Revisar o contrato público");
    await user.click(screen.getByRole("button", { name: "Salvar solicitação 1" }));

    expect(screen.getByText("Revisar o contrato público")).toBeInTheDocument();
  });

  it("permite cancelar uma solicitação pendente", async () => {
    const user = userEvent.setup();
    render(<QueueHarness />);

    await user.click(screen.getByRole("button", { name: "Cancelar solicitação 1" }));

    expect(screen.queryByText("Revisar o schema")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /Na fila/ })).not.toBeInTheDocument();
  });
});
