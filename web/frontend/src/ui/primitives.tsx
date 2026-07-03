import type { InputHTMLAttributes, ReactNode } from "react";

import { chromeBaseClass, chromeTextClass, readingTextClass } from "../design/theme";
import { cx } from "./classNames";

type ButtonVariant = "orange" | "dark" | "ghost";

type ButtonProps = {
  children: ReactNode;
  variant?: ButtonVariant;
  type?: "button" | "submit";
  disabled?: boolean;
  onClick?: () => void;
};

export function Button({
  children,
  variant = "ghost",
  type = "button",
  disabled = false,
  onClick,
}: ButtonProps) {
  return (
    <button
      className={cx(
        chromeBaseClass,
        "rounded-[2px] px-4 py-2 text-[11px] font-medium transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-50",
        variant === "orange" && "bg-signal-orange text-pure-white",
        variant === "dark" && "bg-ink-black text-cream-paper",
        variant === "ghost" &&
          "border border-hairline bg-transparent text-ink-black",
      )}
      disabled={disabled}
      onClick={onClick}
      type={type}
    >
      {children}
    </button>
  );
}

type InputProps = InputHTMLAttributes<HTMLInputElement> & {
  label: string;
};

export function Input({ label, className, id, ...props }: InputProps) {
  const inputId = id ?? label.toLowerCase();
  return (
    <label className="grid gap-2 rounded-[2px]" htmlFor={inputId}>
      <span className={cx(chromeTextClass, "text-[11px]")}>{label}</span>
      <input
        {...props}
        className={cx(
          "rounded-[2px] border border-hairline bg-cream-paper px-3 py-2",
          "font-ftsystemmono text-[13px] text-ink-black outline-none",
          "focus:border-schematic-blue",
          className,
        )}
        id={inputId}
      />
    </label>
  );
}

export function Tag({ children }: { children: ReactNode }) {
  return (
    <span
      className={cx(
        chromeTextClass,
        "inline-flex rounded-[2px] border border-hairline bg-fog px-2 py-1 text-[10px]",
      )}
    >
      {children}
    </span>
  );
}

export function Card({ children }: { children: ReactNode }) {
  return (
    <section
      className={cx(
        "rounded-[2px] border border-hairline bg-cream-paper p-6 shadow-subtle",
        readingTextClass,
      )}
    >
      {children}
    </section>
  );
}
