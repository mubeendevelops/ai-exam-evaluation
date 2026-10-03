import { Navigate, Route, Routes } from 'react-router'
import { RedirectIfSignedIn, RequireAdmin, RequireAuth } from './auth/guards'
import { AppShell } from './components/app/AppShell'
import { PublicLayout } from './components/public/PublicLayout'
import AcceptInvitePage from './pages/AcceptInvitePage'
import AdminPage from './pages/AdminPage'
import EvaluatePage from './pages/EvaluatePage'
import ForgotPasswordPage from './pages/ForgotPasswordPage'
import LandingPage from './pages/LandingPage'
import QnaPage from './pages/QnaPage'
import RecoverPage from './pages/RecoverPage'
import ResetPasswordPage from './pages/ResetPasswordPage'
import SchemaPage from './pages/SchemaPage'
import VerifyEmailPage from './pages/VerifyEmailPage'

/**
 * Public pages (MainLogin.html look) and the signed-in app (project_idea.html look).
 * Links in emails: /verify-email, /reset-password, /accept-invite (all `?token=`).
 */
export default function App() {
  return (
    <Routes>
      <Route element={<PublicLayout />}>
        <Route element={<RedirectIfSignedIn />}>
          <Route index element={<LandingPage />} />
          <Route path="forgot-password" element={<ForgotPasswordPage />} />
          <Route path="recover" element={<RecoverPage />} />
        </Route>
        <Route path="reset-password" element={<ResetPasswordPage />} />
        <Route path="verify-email" element={<VerifyEmailPage />} />
        <Route path="accept-invite" element={<AcceptInvitePage />} />
      </Route>

      <Route element={<RequireAuth />}>
        <Route element={<AppShell />}>
          <Route path="qna" element={<QnaPage />} />
          <Route path="evaluate" element={<EvaluatePage />} />
          <Route path="schema" element={<SchemaPage />} />
          <Route element={<RequireAdmin />}>
            <Route path="admin" element={<AdminPage />} />
          </Route>
        </Route>
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
