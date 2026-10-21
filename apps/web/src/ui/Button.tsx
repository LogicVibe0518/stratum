import * as Tooltip from "@radix-ui/react-tooltip";
import { type ButtonHTMLAttributes, forwardRef, type ReactNode } from "react";

type Variant = "primary" | "secondary" | "ghost";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: "md" | "sm";
  busy?: boolean;
  icon?: boolean;
}

/** Press feedback at once (scale), a spinner inside when busy, width never jumps. */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", busy = false, icon = false, className, children, disabled, ...rest },
  ref,
) {
  const classes = [
    "btn",
    `btn-${variant}`,
    size === "sm" ? "btn-sm" : "",
    icon ? "btn-icon" : "",
    className ?? "",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <button
      ref={ref}
      type="button"
      className={classes}
      disabled={disabled || busy}
      aria-busy={busy}
      {...rest}
    >
      {busy ? <span className="spinner btn-spinner" aria-hidden /> : null}
      <span className="btn-label" data-busy={busy || undefined}>
        {children}
      </span>
    </button>
  );
});

export interface IconButtonProps extends ButtonProps {
  label: string;
  shortcut?: string;
  side?: "top" | "bottom" | "left" | "right";
  children: ReactNode;
}

/** An icon with its name on hover (and to screen readers), and its shortcut. */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, shortcut, side = "bottom", variant = "ghost", children, ...rest },
  ref,
) {
  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>
        <Button ref={ref} variant={variant} icon aria-label={label} {...rest}>
          {children}
        </Button>
      </Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content className="tooltip" side={side} sideOffset={6}>
          {label}
          {shortcut ? <kbd>{shortcut}</kbd> : null}
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
});

/** A hint on hover for any control. */
export function Hint({
  label,
  shortcut,
  children,
}: {
  label: string;
  shortcut?: string;
  children: ReactNode;
}) {
  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>{children}</Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content className="tooltip" sideOffset={6}>
          {label}
          {shortcut ? <kbd>{shortcut}</kbd> : null}
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}
