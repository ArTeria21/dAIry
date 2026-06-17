import { describe, expect, it } from "vitest";

import {
  chromeTextClass,
  fontImportCss,
  readingTextClass,
  themeCss,
} from "./design/theme";
import { Button, Input, Tag } from "./ui/primitives";

describe("Phase 2 frontend foundation tokens", () => {
  it("AC-1: exposes Tailwind v4 theme tokens from design.md", () => {
    expect(fontImportCss).toContain("Source+Serif+4");
    expect(fontImportCss).toContain("IBM+Plex+Mono");
    expect(themeCss).toContain("--color-cream-paper: #f5f4f1");
    expect(themeCss).toContain("--color-schematic-blue: #0d6ea5");
    expect(themeCss).toContain("--color-signal-orange: #fb631b");
    expect(themeCss).toContain("--color-hairline: #e5e5e5");
    expect(themeCss).toContain("--font-gerstnerprogramm: 'Source Serif 4'");
    expect(themeCss).toContain("--font-ftsystemmono: 'IBM Plex Mono'");
    expect(themeCss).toContain("--radius-sm: 2px");
    expect(themeCss).not.toContain("border-radius: 9999px");
  });

  it("AC-1: separates reading serif text from uppercase mono chrome", () => {
    expect(readingTextClass).toContain("font-gerstnerprogramm");
    expect(readingTextClass).not.toContain("uppercase");
    expect(chromeTextClass).toContain("font-ftsystemmono");
    expect(chromeTextClass).toContain("uppercase");
    expect(chromeTextClass).toContain("tracking-[0.015em]");
  });

  it("AC-1: base primitives use 2px rectangles and no capsule classes", () => {
    expect(Button({ children: "LOG IN", variant: "orange" }).props.className).toContain(
      "rounded-[2px]",
    );
    expect(Input({ label: "USERNAME", value: "", onChange: () => undefined }).props.className).toContain(
      "rounded-[2px]",
    );
    expect(Tag({ children: "CALM" }).props.className).toContain("rounded-[2px]");
    expect(Tag({ children: "CALM" }).props.className).not.toContain("rounded-full");
  });
});
