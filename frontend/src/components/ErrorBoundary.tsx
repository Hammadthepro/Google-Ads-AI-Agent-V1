import { Component, type ReactNode } from "react";

/** Keeps a page usable when one section cannot render its data: the section shows what went wrong instead of blanking the page. */
export class ErrorBoundary extends Component<{ children: ReactNode; label?: string }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  render() {
    if (this.state.error) {
      return (
        <div role="alert" className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          {this.props.label ?? "This section"} could not be displayed: {this.state.error.message}. The raw data is still available below.
        </div>);
    }
    return this.props.children;
  }
}
