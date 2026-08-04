import { useCallback, useEffect, useState } from "react";

import { trackTelemetryEvent } from "../api/client";

/**
 * Persisted UI preferences.
 *
 * Storage keys are backend/product contracts and are intentionally unchanged by
 * the redesign (`quantops.*`), so existing sessions keep their theme and
 * telemetry choice.
 */
export const THEME_STORAGE_KEY = "quantops.theme";
export const TELEMETRY_STORAGE_KEY = "quantops.telemetry_consent";
export const NAV_STORAGE_KEY = "quantops.nav_collapsed";

export type ThemeMode = "light" | "dark" | "system";
export type TelemetryConsent = "granted" | "denied";

export function resolveTheme(mode: ThemeMode): "light" | "dark" {
  if (mode !== "system") return mode;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function useThemeMode() {
  const [mode, setMode] = useState<ThemeMode>(() => {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY) as ThemeMode | null;
    return stored === "light" || stored === "dark" || stored === "system" ? stored : "light";
  });

  useEffect(() => {
    const apply = () => {
      const resolved = resolveTheme(mode);
      document.documentElement.dataset.theme = resolved;
      document.documentElement.dataset.themeMode = mode;
      document.documentElement.style.colorScheme = resolved;
    };
    apply();
    window.localStorage.setItem(THEME_STORAGE_KEY, mode);
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    media?.addEventListener("change", apply);
    return () => media?.removeEventListener("change", apply);
  }, [mode]);

  return { themeMode: mode, resolvedTheme: resolveTheme(mode), setThemeMode: setMode };
}

export function useTelemetryConsent() {
  const [consent, setConsent] = useState<TelemetryConsent>(() =>
    window.localStorage.getItem(TELEMETRY_STORAGE_KEY) === "denied" ? "denied" : "granted",
  );

  useEffect(() => {
    window.localStorage.setItem(TELEMETRY_STORAGE_KEY, consent);
  }, [consent]);

  const update = useCallback((next: TelemetryConsent) => {
    setConsent(next);
    void trackTelemetryEvent({
      name: "telemetry_consent_changed",
      category: "product",
      properties: { consent: next },
      consent: next,
    }).catch(() => undefined);
  }, []);

  return { telemetryConsent: consent, setTelemetryConsent: update };
}

export function useNavCollapsed() {
  const [collapsed, setCollapsed] = useState(() => window.localStorage.getItem(NAV_STORAGE_KEY) === "1");
  useEffect(() => {
    window.localStorage.setItem(NAV_STORAGE_KEY, collapsed ? "1" : "0");
  }, [collapsed]);
  return { navCollapsed: collapsed, setNavCollapsed: setCollapsed };
}
