import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { IncrementalState } from "./components";

describe("IncrementalState", () => {
  it("renders a section-local loading placeholder", () => {
    const html = renderToStaticMarkup(<IncrementalState label="model performance" />);

    expect(html).toContain("Loading model performance");
    expect(html).not.toContain("Loading dashboard");
  });

  it("keeps a failed section recoverable without replacing the page", () => {
    const html = renderToStaticMarkup(
      <IncrementalState label="issue signals" error={new Error("locked")} onRetry={vi.fn()} />,
    );

    expect(html).toContain("issue signals is temporarily unavailable");
    expect(html).toContain("Other dashboard sections remain usable");
    expect(html).toContain("Retry now");
  });
});
