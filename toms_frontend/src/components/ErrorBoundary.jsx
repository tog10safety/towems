import { Component } from "react";

/**
 * Keep one broken screen from blanking the whole operational application.
 *
 * A route-level render error still leaves the user with a clear recovery path
 * instead of a white page. The error is logged locally for the browser console;
 * server-side monitoring can be attached here later without changing callers.
 */
export default class ErrorBoundary extends Component {
  state = { error: null };

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error("Unhandled application error", error, info);
  }

  render() {
    if (!this.state.error) return this.props.children;

    return (
      <main className="splash" role="alert">
        <div className="state">
          <h1>Something went wrong</h1>
          <p>
            This screen could not be displayed. Your saved records are not
            affected.
          </p>
          <div className="inline-row">
            <button
              type="button"
              className="btn btn-primary btn-md"
              onClick={() => window.location.reload()}
            >
              Reload page
            </button>
            <a className="btn btn-ghost btn-md" href="/">
              Back to dashboard
            </a>
          </div>
        </div>
      </main>
    );
  }
}
