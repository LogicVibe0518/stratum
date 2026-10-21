import * as Tooltip from "@radix-ui/react-tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Toaster } from "sonner";
import { App } from "./App";
import { SignedOut } from "./lib/api";
import "./app.css";

const queries = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: (count, error) => !(error instanceof SignedOut) && count < 2,
    },
  },
});

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <QueryClientProvider client={queries}>
        <Tooltip.Provider delayDuration={400} skipDelayDuration={200}>
          <App />
          <Toaster position="bottom-center" theme="system" closeButton toastOptions={{ duration: 4000 }} />
        </Tooltip.Provider>
      </QueryClientProvider>
    </StrictMode>,
  );
}
