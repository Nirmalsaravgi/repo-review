"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { gruvboxDark, gruvboxLight } from "react-syntax-highlighter/dist/esm/styles/prism";
import { useState, type ReactNode } from "react";
import { useThemeMode } from "@/lib/useTheme";
import styles from "./Markdown.module.css";

function CodeBlock({ language, value }: { language: string; value: string }) {
  const mode = useThemeMode();
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      /* clipboard unavailable */
    }
  }
  return (
    <div className={styles.codeBlock}>
      <div className={styles.codeHead}>
        <span className={styles.lang}>{language || "code"}</span>
        <button type="button" className={styles.copyBtn} onClick={copy}>
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <SyntaxHighlighter
        language={language || "text"}
        style={mode === "dark" ? gruvboxDark : gruvboxLight}
        customStyle={{
          margin: 0,
          background: "transparent",
          padding: "0.85rem 1rem",
          fontSize: "0.82rem",
          lineHeight: 1.55,
        }}
        codeTagProps={{ style: { fontFamily: "var(--mono)" } }}
      >
        {value}
      </SyntaxHighlighter>
    </div>
  );
}

export function Markdown({ children }: { children: string }) {
  return (
    <div className={styles.md}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code({ inline, className, children, ...props }: {
            inline?: boolean;
            className?: string;
            children?: ReactNode;
          }) {
            const match = /language-(\w+)/.exec(className || "");
            const value = String(children).replace(/\n$/, "");
            if (!inline && (match || value.includes("\n"))) {
              return <CodeBlock language={match?.[1] || ""} value={value} />;
            }
            return (
              <code className={styles.inlineCode} {...props}>
                {children}
              </code>
            );
          },
          a({ children, href }) {
            return (
              <a href={href} target="_blank" rel="noreferrer">
                {children}
              </a>
            );
          },
          table({ children }) {
            return (
              <div className={styles.tableWrap}>
                <table>{children}</table>
              </div>
            );
          },
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
