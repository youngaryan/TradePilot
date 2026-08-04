import { useCallback, useEffect, useId, useRef, type ReactNode } from "react";
import { X } from "lucide-react";

import { Button, IconButton, cx } from "./primitives";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Modal focus management: focus moves into the panel on open, is trapped while
 * open, Escape closes, and focus returns to the trigger on close.
 */
function useModalFocus(open: boolean, onClose: () => void) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const restoreRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return undefined;
    restoreRef.current = document.activeElement as HTMLElement | null;
    const panel = panelRef.current;
    const first = panel?.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? panel)?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !panelRef.current) return;
      const nodes = Array.from(panelRef.current.querySelectorAll<HTMLElement>(FOCUSABLE))
        .filter((node) => node.offsetParent !== null || node === document.activeElement);
      if (!nodes.length) return;
      const firstNode = nodes[0];
      const lastNode = nodes[nodes.length - 1];
      if (event.shiftKey && document.activeElement === firstNode) {
        event.preventDefault();
        lastNode.focus();
      } else if (!event.shiftKey && document.activeElement === lastNode) {
        event.preventDefault();
        firstNode.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown, true);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      document.body.style.overflow = previousOverflow;
      restoreRef.current?.focus?.();
    };
  }, [open, onClose]);

  return panelRef;
}

export function Dialog({ open, onClose, title, description, children, footer, wide }: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  const panelRef = useModalFocus(open, onClose);
  const titleId = useId();
  if (!open) return null;
  return (
    <>
      <div className="ui-scrim" onClick={onClose} aria-hidden="true" />
      <div className="ui-dialog">
        <div
          ref={panelRef}
          className={cx("ui-dialog__panel", wide && "ui-dialog__panel--wide")}
          role="dialog"
          aria-modal="true"
          aria-labelledby={titleId}
          tabIndex={-1}
        >
          <div className="ui-dialog__head">
            <h2 id={titleId}>{title}</h2>
            <IconButton label="Close dialog" onClick={onClose}>
              <X size={16} />
            </IconButton>
          </div>
          <div className="ui-dialog__body">
            {description}
            {children}
          </div>
          {footer ? <div className="ui-dialog__foot">{footer}</div> : null}
        </div>
      </div>
    </>
  );
}

/**
 * Destructive-action confirmation. Requires an explicit, labelled confirm and
 * spells out what will happen — used for account deletion, archival, disabling
 * strategies, and platform-admin actions.
 */
export function ConfirmDialog({ open, onClose, onConfirm, title, body, confirmLabel, cancelLabel = "Cancel", destructive, busy }: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  body: ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  destructive?: boolean;
  busy?: boolean;
}) {
  const confirm = useCallback(() => {
    onConfirm();
  }, [onConfirm]);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {cancelLabel}
          </Button>
          <Button variant={destructive ? "danger" : "primary"} onClick={confirm} disabled={busy}>
            {busy ? "Working…" : confirmLabel}
          </Button>
        </>
      }
    >
      {body}
    </Dialog>
  );
}

export function Drawer({ open, onClose, title, children }: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
}) {
  const panelRef = useModalFocus(open, onClose);
  const titleId = useId();
  if (!open) return null;
  return (
    <>
      <div className="ui-scrim" onClick={onClose} aria-hidden="true" />
      <div
        ref={panelRef}
        className="ui-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <span className="ui-sr-only" id={titleId}>{title}</span>
        {children}
      </div>
    </>
  );
}

/** Closes a popover on outside click or Escape. */
export function useDismissable(open: boolean, onClose: () => void) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) onClose();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, onClose]);
  return ref;
}
