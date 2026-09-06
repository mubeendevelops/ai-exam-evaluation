// src/components/ErrorBoundary.tsx — the app's one render-error boundary.
//
// Wraps the whole app (src/App.tsx) so a bug anywhere in the tree shows a
// plain-language message instead of a blank page or a raw stack trace. A
// stack trace is a debugging tool for the person who wrote the code, not
// something a teacher can act on, and it can leak implementation details
// (component names, file paths) to whoever is looking at the screen.
import { Component, type ErrorInfo, type ReactNode } from "react";

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // The stack trace goes to the browser console (for whoever is debugging
    // with devtools open), never into the rendered page.
    console.error("Unhandled error in the app:", error, info.componentStack);
  }

  render(): ReactNode {
    if (this.state.hasError) {
      return (
        <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-slate-50 px-4 text-center">
          <h1 className="text-lg font-semibold text-slate-900">Something went wrong</h1>
          <p className="max-w-sm text-sm text-slate-500">
            This page ran into a problem. Reloading usually fixes it; if it keeps happening, let your admin know.
          </p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700"
          >
            Reload the page
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
