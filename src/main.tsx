import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import "./styles.css";

type ErrorBoundaryState = {
  error: Error | null;
};

class ErrorBoundary extends React.Component<React.PropsWithChildren, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("RedFolio UI crashed", error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <main className="root-error">
          <section className="root-error-panel">
            <div className="brand-mark">R</div>
            <h1>程序遇到错误</h1>
            <p>界面组件发生异常，请重新加载应用。若问题持续出现，请保留日志并重启 RedFolio。</p>
            <pre>{this.state.error.message}</pre>
            <button className="primary-button" type="button" onClick={() => window.location.reload()}>
              重新加载
            </button>
          </section>
        </main>
      );
    }

    return this.props.children;
  }
}

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>
);
