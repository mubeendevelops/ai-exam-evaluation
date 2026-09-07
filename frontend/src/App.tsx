import { QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import { queryClient } from "./app/queryClient";
import { Layout } from "./app/Layout";
import { AuthProvider } from "./auth/AuthProvider";
import { RequireAuth } from "./auth/RequireAuth";
import { RequireCollegeUser } from "./auth/RequireCollegeUser";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { BookletSummaries } from "./routes/BookletSummaries";
import { Classes } from "./routes/Classes";
import { Home } from "./routes/Home";
import { Login } from "./routes/Login";
import { PaperGeneration } from "./routes/PaperGeneration";
import { QuestionBank } from "./routes/QuestionBank";
import { ResultDetail } from "./routes/ResultDetail";
import { Results } from "./routes/Results";
import { Upload } from "./routes/Upload";

function App() {
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AuthProvider>
            <Routes>
              <Route path="/login" element={<Login />} />

              <Route element={<RequireAuth />}>
                <Route element={<Layout />}>
                  <Route path="/" element={<Home />} />
                  <Route element={<RequireCollegeUser />}>
                    <Route path="/upload" element={<Upload />} />
                    <Route path="/results" element={<Results />} />
                    <Route path="/results/summary" element={<BookletSummaries />} />
                    <Route path="/results/:answerId" element={<ResultDetail />} />
                    <Route path="/questions" element={<QuestionBank />} />
                    <Route path="/papers" element={<PaperGeneration />} />
                    <Route path="/classes" element={<Classes />} />
                  </Route>
                </Route>
              </Route>
            </Routes>
          </AuthProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  );
}

export default App;
