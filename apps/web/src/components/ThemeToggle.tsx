"use client";

import { motion } from "framer-motion";
import { useThemeMode, setThemeMode } from "@/lib/useTheme";
import styles from "./ThemeToggle.module.css";

export function ThemeToggle() {
  const mode = useThemeMode();
  const dark = mode === "dark";
  return (
    <button
      type="button"
      className={styles.toggle}
      data-mode={mode}
      onClick={() => setThemeMode(dark ? "light" : "dark")}
      aria-label={`Switch to ${dark ? "light" : "dark"} theme`}
      title={`Switch to ${dark ? "light" : "dark"} theme`}
    >
      <motion.span
        className={styles.knob}
        layout
        transition={{ type: "spring", stiffness: 500, damping: 32 }}
      >
        {dark ? "☾" : "☀"}
      </motion.span>
    </button>
  );
}
