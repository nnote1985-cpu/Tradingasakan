import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { ModeBadge } from "../src/ModeBadge";

test("LIVE is shown in red, unknown modes too", () => {
  render(<ModeBadge mode="LIVE" />);
  expect(screen.getByTestId("mode-badge").className).toContain("bg-red-600");
});

test("SHADOW is neutral", () => {
  render(<ModeBadge mode="SHADOW" />);
  expect(screen.getAllByTestId("mode-badge")[1].className).toContain("bg-slate-200");
});
