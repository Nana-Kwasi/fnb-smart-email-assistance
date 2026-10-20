import { Component, type ErrorInfo, type ReactNode } from "react";

/** Shows a friendly message instead of a blank page if a screen crashes. */
export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Screen crashed:", error, info.componentStack);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="center-page">
        <div className="card login-card" role="alert">
          <h1>Something went wrong</h1>
          <p className="card-sub">
            This page could not be shown. If the app was just updated, the server may need restarting. Reloading
            usually fixes it.
          </p>
          <button className="btn btn-primary" onClick={() => window.location.reload()}>
            Reload
          </button>
        </div>
      </div>
    );
  }
}
