import { Navigate, Route, Routes } from 'react-router'
import { RedirectIfSignedIn, RequireAuth, RequireCapability, RequireGovernor } from './auth/guards'
import { AppShell } from './components/app/AppShell'
import { PublicLayout } from './components/public/PublicLayout'
import AcceptInvitePage from './pages/AcceptInvitePage'
import { AdminLayout } from './pages/admin/AdminLayout'
import SmeDirectoryPage from './pages/admin/SmeDirectoryPage'
import {
  OverviewPage,
  ProgressPage,
  RedistributePage,
  ReportsPage,
  UploadAssignPage,
} from './pages/admin/sections'
import StudentsPage from './pages/admin/StudentsPage'
import EvaluatePage from './pages/EvaluatePage'
import EvaluatedPage from './pages/EvaluatedPage'
import ForgotPasswordPage from './pages/ForgotPasswordPage'
import GovernorPage from './pages/GovernorPage'
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
          {/* Each persona sees what its capabilities open (D177); the server checks again. */}
          <Route element={<RequireCapability capability="author_content" />}>
            <Route path="qna" element={<QnaPage />} />
            <Route path="schema" element={<SchemaPage />} />
          </Route>
          <Route element={<RequireCapability capability="evaluate" />}>
            <Route path="evaluate" element={<EvaluatePage />} />
            <Route path="evaluated" element={<EvaluatedPage />} />
          </Route>
          <Route element={<RequireCapability capability="manage_smes" />}>
            <Route path="admin" element={<AdminLayout />}>
              <Route index element={<OverviewPage />} />
              <Route path="smes" element={<SmeDirectoryPage />} />
              <Route path="upload" element={<UploadAssignPage />} />
              <Route path="upload/students" element={<StudentsPage />} />
              <Route path="progress" element={<ProgressPage />} />
              <Route path="redistribute" element={<RedistributePage />} />
              <Route path="reports" element={<ReportsPage />} />
              <Route path="*" element={<Navigate to="/admin" replace />} />
            </Route>
          </Route>
          <Route element={<RequireGovernor />}>
            <Route path="governor" element={<GovernorPage />} />
          </Route>
        </Route>
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
