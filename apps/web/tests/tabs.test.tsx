import { StrictMode } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { sectionOf, TabBar, useHashTab } from "../src/tabs";

afterEach(() => { cleanup(); window.history.replaceState(null, "", "/"); });

function Navigation() {
  const [view, select] = useHashTab();
  const section = sectionOf(view);
  return <><TabBar tab={section} onSelect={select} /><div role="tabpanel" aria-labelledby={`tab-${section}`} data-view={view}>{section}</div></>;
}

function expectSection(name: string, section: string) {
  expect(screen.getByRole("tab", { name }).getAttribute("aria-selected")).toBe("true");
  expect(screen.getAllByRole("tab").filter((tab) => tab.getAttribute("aria-selected") === "true")).toHaveLength(1);
  expect(screen.getByRole("tabpanel").textContent).toBe(section);
}

test("each main tab changes its panel and highlight on the first click, including repeated fast switches", async () => {
  window.history.replaceState(null, "", "#research");
  render(<StrictMode><Navigation /></StrictMode>);
  for (const [name, section] of [["บอตและพอร์ต", "bots"], ["ระบบ", "system"], ["ตั้งค่า", "settings"], ["ภาพรวม", "overview"], ["งานวิจัย", "research"], ["ระบบ", "system"], ["บอตและพอร์ต", "bots"]]) {
    fireEvent.click(screen.getByRole("tab", { name }));
    expect(window.location.hash).toBe(`#${section}`);
    expectSection(name, section);
  }
  // An older queued browser event must not restore a previous selection.
  act(() => window.dispatchEvent(new HashChangeEvent("hashchange", { oldURL: "http://localhost/#research", newURL: "http://localhost/#system" })));
  await waitFor(() => expectSection("บอตและพอร์ต", "bots"));
});

test("back and forward keep the panel and selected tab in sync", async () => {
  window.history.replaceState(null, "", "#overview");
  render(<Navigation />);
  fireEvent.click(screen.getByRole("tab", { name: "บอตและพอร์ต" }));
  fireEvent.click(screen.getByRole("tab", { name: "ระบบ" }));
  act(() => window.history.back());
  await waitFor(() => expectSection("บอตและพอร์ต", "bots"));
  act(() => window.history.forward());
  await waitFor(() => expectSection("ระบบ", "system"));
});

test("legacy subview links and keyboard navigation select the correct parent section", async () => {
  window.history.replaceState(null, "", "#asset");
  render(<Navigation />);
  expectSection("บอตและพอร์ต", "bots");
  act(() => { window.location.hash = "worker"; });
  await waitFor(() => expectSection("ระบบ", "system"));
  fireEvent.keyDown(screen.getByRole("tab", { name: "ระบบ" }), { key: "Home" });
  expectSection("ภาพรวม", "overview");
  expect(document.activeElement).toBe(screen.getByRole("tab", { name: "ภาพรวม" }));
  act(() => { window.location.hash = "strategies"; });
  await waitFor(() => expectSection("ตั้งค่า", "settings"));
  fireEvent.keyDown(screen.getByRole("tab", { name: "ตั้งค่า" }), { key: "ArrowRight" });
  expectSection("ภาพรวม", "overview");
});
