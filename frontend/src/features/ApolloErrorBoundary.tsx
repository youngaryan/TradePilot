import { Component, type ErrorInfo, type ReactNode } from "react";

/**
 * Catches render errors so one broken screen shows a recoverable message
 * instead of blanking the whole app.
 */
export class ApolloErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Keep the details in the console for debugging; never surface a raw stack
    // to the user.
    console.error("Apollo render error:", error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div
        style={{
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: "32px",
          boxSizing: "border-box",
          background: "oklch(97.5% 0.003 255)",
          fontFamily: "'Inter', -apple-system, sans-serif",
          color: "oklch(20% 0.012 255)",
        }}
      >
        <div
          style={{
            maxWidth: "460px",
            width: "100%",
            background: "oklch(100% 0 0)",
            border: "1px solid oklch(91% 0.005 255)",
            borderRadius: "10px",
            padding: "32px",
          }}
        >
          <div style={{ fontFamily: "'Archivo Black', 'Space Grotesk', sans-serif", fontSize: "18px", letterSpacing: ".02em" }}>APOLLO</div>
          <h1 style={{ fontSize: "18px", fontWeight: 700, margin: "16px 0 0 0" }}>This screen hit an error</h1>
          <p style={{ fontSize: "13px", lineHeight: 1.55, color: "oklch(52% 0.012 255)", marginTop: "8px" }}>
            Nothing was lost — your workspace, strategies, and runs are stored on the server. Reloading usually clears it.
          </p>
          <p style={{ fontSize: "11.5px", lineHeight: 1.5, color: "oklch(52% 0.012 255)", marginTop: "10px", fontFamily: "'JetBrains Mono', monospace", wordBreak: "break-word" }}>
            {error.message || "Unknown error"}
          </p>
          <div style={{ display: "flex", gap: "10px", marginTop: "20px", flexWrap: "wrap" }}>
            <button
              onClick={() => this.setState({ error: null })}
              style={{
                background: "oklch(20% 0.012 255)",
                color: "oklch(100% 0 0)",
                border: "none",
                borderRadius: "6px",
                padding: "0 18px",
                height: "40px",
                fontSize: "13px",
                fontWeight: 600,
                fontFamily: "inherit",
                cursor: "pointer",
              }}
            >
              Try again
            </button>
            <button
              onClick={() => window.location.reload()}
              style={{
                background: "transparent",
                color: "oklch(20% 0.012 255)",
                border: "1px solid oklch(91% 0.005 255)",
                borderRadius: "6px",
                padding: "0 18px",
                height: "40px",
                fontSize: "13px",
                fontWeight: 600,
                fontFamily: "inherit",
                cursor: "pointer",
              }}
            >
              Reload Apollo
            </button>
          </div>
        </div>
      </div>
    );
  }
}

export default ApolloErrorBoundary;
