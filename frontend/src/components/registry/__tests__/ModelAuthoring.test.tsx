/**
 * Model authoring in the assumption registry: register, revise, review.
 *
 * Preconditions: rendered inside a QueryClientProvider; modelsApi is the only
 * way to the backend, mocked with ModelVersions causal_decision produced
 * (src/test-fixtures/kernel.ts, src/test-fixtures/registry.ts).
 *
 * Postconditions (backend/src/api/models.py):
 *   - registering posts {name, graph, why} -- CreateModel's fields, as written;
 *   - a revision starts from the head's graph as text, and posts {parent_digest,
 *     graph, why} with parent_digest = the head's digest;
 *   - a review posts {parent_digest = head digest, reviews: [{edge, status,
 *     reason}]} for the edges given a ruling, and only once each has a reason
 *     the registry accepts (5+ characters);
 *   - the new version is shown as the backend returned it; a refusal (403 own
 *     edge, 409 stale head, 422) is shown as the backend worded it.
 *
 * Undefined here: layout.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const { modelsApiMock } = vi.hoisted(() => ({
  modelsApiMock: { create: vi.fn(), revise: vi.fn(), review: vi.fn() },
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, modelsApi: modelsApiMock };
});

import { RegisterModelForm, ReviewEdgesForm, ReviseModelForm } from "../ModelAuthoring";
import { CausalApiError } from "@/lib/api";
import { MODEL_V1, MODEL_V2 } from "@/test-fixtures/kernel";
import { MODEL_V3_REVIEWED, SELF_REVIEW_DETAIL } from "@/test-fixtures/registry";

function wrap(ui: React.ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

beforeEach(() => vi.clearAllMocks());

describe("register a model", () => {
  it("posts the name, graph and why as written, and shows the version the registry made", async () => {
    const user = userEvent.setup();
    const onRegistered = vi.fn();
    modelsApiMock.create.mockResolvedValue(MODEL_V1);
    wrap(<RegisterModelForm onRegistered={onRegistered} />);

    const submit = screen.getByRole("button", { name: /^register$/i });
    expect(submit).toBeDisabled();
    await user.type(screen.getByRole("textbox", { name: /name/i }), "incident-minutes");
    await user.type(screen.getByRole("textbox", { name: /graph/i }), "load -> playbook; load -> minutes; playbook -> minutes");
    await user.type(screen.getByRole("textbox", { name: /why/i }), MODEL_V1.why);
    await user.click(submit);

    expect(modelsApiMock.create).toHaveBeenCalledWith({
      name: "incident-minutes",
      graph: "load -> playbook; load -> minutes; playbook -> minutes",
      why: MODEL_V1.why,
    });
    expect(await screen.findByTestId("authoring-result")).toHaveTextContent(`Registered incident-minutes v1 (${MODEL_V1.digest})`);
    expect(onRegistered).toHaveBeenCalledWith("incident-minutes");
  });

  it("shows the registry's refusal as worded", async () => {
    const user = userEvent.setup();
    modelsApiMock.create.mockRejectedValue(
      new CausalApiError(422, "HTTP_ERROR", "model name 'Bad Name' must be lowercase letters, digits, '.', '_' or '-'")
    );
    wrap(<RegisterModelForm />);
    await user.type(screen.getByRole("textbox", { name: /name/i }), "Bad Name");
    await user.type(screen.getByRole("textbox", { name: /graph/i }), "a -> b");
    await user.type(screen.getByRole("textbox", { name: /why/i }), "because");
    await user.click(screen.getByRole("button", { name: /^register$/i }));
    expect(await screen.findByTestId("authoring-error")).toHaveTextContent("Not registered: model name 'Bad Name' must be lowercase");
  });
});

describe("revise a model", () => {
  it("starts from the head's graph and builds on the head's digest", async () => {
    const user = userEvent.setup();
    modelsApiMock.revise.mockResolvedValue(MODEL_V2);
    wrap(<ReviseModelForm head={MODEL_V1} />);

    const graph = screen.getByRole("textbox", { name: /graph/i });
    expect(graph).toHaveValue("load -> minutes\nload -> playbook\nplaybook -> minutes");
    await user.type(graph, "\nteam -> playbook\nteam -> minutes");
    await user.type(screen.getByRole("textbox", { name: /why this revision/i }), MODEL_V2.why);
    await user.click(screen.getByRole("button", { name: /save as a new version/i }));

    expect(modelsApiMock.revise).toHaveBeenCalledWith("incident-minutes", {
      parent_digest: MODEL_V1.digest,
      graph: "load -> minutes\nload -> playbook\nplaybook -> minutes\nteam -> playbook\nteam -> minutes",
      why: MODEL_V2.why,
    });
    expect(await screen.findByTestId("authoring-result")).toHaveTextContent("Saved incident-minutes v2");
  });

  it("shows a stale-head refusal as the backend worded it", async () => {
    const user = userEvent.setup();
    const detail = `this revision was built on ${MODEL_V1.digest.slice(0, 19)}..., but the head is v2 (${MODEL_V2.digest.slice(0, 19)}...): rebuild on the head`;
    modelsApiMock.revise.mockRejectedValue(new CausalApiError(409, "HTTP_ERROR", detail));
    wrap(<ReviseModelForm head={MODEL_V1} />);
    await user.type(screen.getByRole("textbox", { name: /graph/i }), "\nteam -> minutes");
    await user.type(screen.getByRole("textbox", { name: /why this revision/i }), "team size matters");
    await user.click(screen.getByRole("button", { name: /save as a new version/i }));
    expect(await screen.findByTestId("authoring-error")).toHaveTextContent(`Not revised: ${detail}`);
  });
});

describe("review edges", () => {
  it("posts the rulings given, each with its reason, against the head's digest", async () => {
    const user = userEvent.setup();
    modelsApiMock.review.mockResolvedValue(MODEL_V3_REVIEWED);
    wrap(<ReviewEdgesForm head={MODEL_V2} />);

    expect(screen.getAllByTestId("review-row")).toHaveLength(MODEL_V2.graph.edges.length);
    const submit = screen.getByRole("button", { name: /submit 0 rulings/i });
    expect(submit).toBeDisabled();

    await user.selectOptions(screen.getByRole("combobox", { name: "Ruling on team -> playbook" }), "approved");
    // A ruling without a 5-character reason is not sent.
    expect(screen.getByRole("button", { name: /submit 1 ruling/i })).toBeDisabled();
    await user.type(
      screen.getByRole("textbox", { name: "Reason for team -> playbook" }),
      "team size is recorded before the playbook is chosen"
    );
    await user.click(screen.getByRole("button", { name: /submit 1 ruling/i }));

    expect(modelsApiMock.review).toHaveBeenCalledWith("incident-minutes", {
      parent_digest: MODEL_V2.digest,
      reviews: [{ edge: "team -> playbook", status: "approved", reason: "team size is recorded before the playbook is chosen" }],
    });
    expect(await screen.findByTestId("authoring-result")).toHaveTextContent("Reviewed into incident-minutes v3");
  });

  it("shows the registry's refusal to let an author review their own edge", async () => {
    const user = userEvent.setup();
    modelsApiMock.review.mockRejectedValue(new CausalApiError(403, "HTTP_ERROR", SELF_REVIEW_DETAIL));
    wrap(<ReviewEdgesForm head={MODEL_V2} />);
    await user.selectOptions(screen.getByRole("combobox", { name: "Ruling on team -> playbook" }), "approved");
    await user.type(screen.getByRole("textbox", { name: "Reason for team -> playbook" }), "my own edge, approved");
    await user.click(screen.getByRole("button", { name: /submit 1 ruling/i }));
    expect(await screen.findByTestId("authoring-error")).toHaveTextContent(`Review not recorded: ${SELF_REVIEW_DETAIL}`);
  });
});
