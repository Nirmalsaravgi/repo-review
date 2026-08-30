"use client";

import { useEffect, useState } from "react";

export type Mode = "light" | "dark";

function currentMode(): Mode {
  if (typeof document === "undefined") return "dark";
  const attr = document.documentElement.dataset.theme;
  if (attr === "light" || attr === "dark") return attr;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** Reactively track the active theme (explicit override or system preference). */
export function useThemeMode(): Mode {
  const [mode, setMode] = useState<Mode>("dark");
  useEffect(() => {
    setMode(currentMode());
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const sync = () => setMode(currentMode());
    mq.addEventListener("change", sync);
    const obs = new MutationObserver(sync);
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => {
      mq.removeEventListener("change", sync);
      obs.disconnect();
    };
  }, []);
  return mode;
}

export function setThemeMode(mode: Mode) {
  document.documentElement.dataset.theme = mode;
  try {
    localStorage.setItem("theme", mode);
  } catch {
    /* storage unavailable */
  }
}
