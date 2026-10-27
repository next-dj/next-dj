import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Presence } from "./component";

const ada = { name: "Ada Lovelace", initials: "AL" };
const grace = { name: "Grace Hopper", initials: "GH" };

describe("Presence", () => {
  it("renders nothing before the first snapshot", () => {
    render(<Presence />);
    expect(document.querySelector("[data-kanban-presence]")).toBeNull();
  });

  it("shows one avatar per member with their initials and name", () => {
    render(<Presence count={2} members={[ada, grace]} />);
    const region = screen.getByRole("region", { name: "Watching this board" });
    const avatars = region.querySelectorAll("[data-kanban-member]");
    expect([...avatars].map((avatar) => avatar.title)).toEqual([
      "Ada Lovelace",
      "Grace Hopper",
    ]);
    expect(avatars[0]).toHaveTextContent("AL");
    expect(region.querySelector("[data-kanban-guests]")).toBeNull();
  });

  it("keeps two members who share a name apart by their key", () => {
    render(
      <Presence
        count={2}
        members={[
          { ...ada, key: "first" },
          { ...ada, key: "second" },
        ]}
      />,
    );
    expect(document.querySelectorAll("[data-kanban-member]")).toHaveLength(2);
  });

  it("adds the guests the count holds beyond the members", () => {
    render(<Presence count={4} members={[ada]} />);
    expect(document.querySelector("[data-kanban-guests]")).toHaveTextContent(
      "+3 guests",
    );
  });

  it("counts a lone guest without a plus", () => {
    render(<Presence count={1} />);
    expect(document.querySelector("[data-kanban-guests]")).toHaveTextContent("1 guest");
    expect(document.querySelector("[data-kanban-guests]")).not.toHaveTextContent("+");
  });
});
