import React from "react";

// Catches render/runtime errors so one bad component can't white-screen the whole
// dashboard. Shows the actual error + stack so the problem is visible, not blank.
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // Surface it in the console too.
    // eslint-disable-next-line no-console
    console.error("localdeck render error:", error, info);
  }

  render() {
    if (this.state.error) {
      const err = this.state.error;
      return (
        <div style={{ padding: "28px", fontFamily: "-apple-system, system-ui, sans-serif", color: "#23211d" }}>
          <h2 style={{ marginTop: 0 }}>localdeck hit a rendering error</h2>
          <p style={{ color: "#6b6862" }}>
            The dashboard caught an error instead of showing a blank page. Details are below; please
            include them if you open an issue.
          </p>
          <pre
            style={{
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              background: "#1b1b19",
              color: "#ffd4cf",
              padding: "14px 16px",
              borderRadius: "10px",
              fontSize: "12.5px",
              lineHeight: 1.5,
              maxHeight: "60vh",
              overflow: "auto",
            }}
          >
            {String(err && (err.stack || err.message || err))}
          </pre>
          <button
            type="button"
            onClick={() => window.location.reload()}
            style={{
              marginTop: "12px",
              padding: "9px 16px",
              borderRadius: "9px",
              border: "1px solid #d6d1c4",
              background: "#c96442",
              color: "#fff",
              cursor: "pointer",
            }}
          >
            Reload
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
