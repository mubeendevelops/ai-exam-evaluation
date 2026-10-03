import type { Page, Route } from '@playwright/test'

interface Tenant {
  institutionId: string
  collegeName: string
  adminName: string
  email: string
  password: string
  status: 'PENDING_VERIFICATION' | 'PENDING_APPROVAL' | 'ACTIVE'
  verified: boolean
  approved: boolean
}

interface Signed {
  tenant: Tenant
  role: 'admin' | 'teacher'
}

/**
 * Just enough of the API for the browser tests, answering in the browser (`page.route`). It
 * follows the real contract (docs/api/openapi.json): availability, registration, e-mail
 * verification, sign-in with a refresh cookie, /auth/me, logout, forgot/reset password.
 */
export class FakeApi {
  tenants = new Map<string, Tenant>()
  /** Stands in for the HttpOnly refresh cookie. */
  private signed: Signed | null = null
  forgotRequests: { institution_id: string; email: string }[] = []
  resetTokens = new Map<string, string>() // token -> institution id
  verifyTokens = new Map<string, string>()
  refreshCalls = 0

  constructor() {
    this.addTenant({
      institutionId: 'SYNTH_COLLEGE',
      collegeName: 'Synthetic College',
      adminName: 'Dr. Admin',
      email: 'admin@synthetic.test',
      password: 'correct horse battery staple',
      status: 'ACTIVE',
      verified: true,
      approved: true,
    })
  }

  addTenant(tenant: Tenant) {
    this.tenants.set(tenant.institutionId, tenant)
  }

  /** The operator's `tarn tenants approve`, stubbed. */
  approve(institutionId: string) {
    const tenant = this.tenants.get(institutionId)
    if (!tenant) throw new Error(`no tenant ${institutionId}`)
    tenant.approved = true
    tenant.status = tenant.verified ? 'ACTIVE' : 'PENDING_VERIFICATION'
  }

  async install(page: Page) {
    await page.route('**/api/v1/**', (route) => this.handle(route))
  }

  private user(signed: Signed) {
    return {
      id: '22222222-2222-4222-8222-222222222222',
      college_id: '11111111-1111-4111-8111-111111111111',
      display_name: signed.tenant.adminName,
      email: signed.tenant.email,
      role: signed.role,
      active: true,
    }
  }

  private async handle(route: Route) {
    const request = route.request()
    const url = new URL(request.url())
    const key = `${request.method()} ${url.pathname.replace('/api/v1', '')}`
    const body = request.postData() ? JSON.parse(request.postData() as string) : undefined
    const json = (status: number, data: unknown) =>
      route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) })
    const empty = (status: number) => route.fulfill({ status })

    switch (key) {
      case 'GET /health':
        return json(200, {
          status: 'ok',
          version: '0.0.0',
          environment: 'test',
          device: { kind: 'cpu', name: 'cpu', detail: 'CPU selected' },
          worker: { status: 'up', detail: 'Worker running' },
        })

      case 'GET /auth/password-bloom': {
        // An empty TBF1 filter: m = 64 bits, k = 1.
        const bytes = Buffer.alloc(9 + 8)
        bytes.write('TBF1', 0, 'ascii')
        bytes.writeUInt32BE(64, 4)
        bytes[8] = 1
        return route.fulfill({ status: 200, contentType: 'application/octet-stream', body: bytes })
      }

      case 'GET /registrations/availability': {
        const id = url.searchParams.get('institution_id') ?? ''
        const valid = /^[A-Z0-9_\-*&]{1,20}$/.test(id)
        return json(200, {
          institution_id: id,
          valid,
          available: valid && !this.tenants.has(id),
          problem: valid ? null : 'Invalid Institution ID.',
        })
      }

      case 'POST /registrations': {
        if (this.tenants.has(body.institution_id)) {
          return json(409, { detail: 'That Institution ID is taken.' })
        }
        this.addTenant({
          institutionId: body.institution_id,
          collegeName: body.institution_name ?? body.institution_id,
          adminName: body.admin_name,
          email: body.email,
          password: body.password,
          status: 'PENDING_VERIFICATION',
          verified: false,
          approved: false,
        })
        this.verifyTokens.set(`college.verify-${body.institution_id}`, body.institution_id)
        return json(202, {
          institution_id: body.institution_id,
          status: 'PENDING_VERIFICATION',
          approval_required: true,
        })
      }

      case 'POST /registrations/verify-email': {
        const id = this.verifyTokens.get(body.token)
        const tenant = id ? this.tenants.get(id) : undefined
        if (!tenant) return json(400, { detail: 'Invalid or used link.' })
        this.verifyTokens.delete(body.token)
        tenant.verified = true
        tenant.status = tenant.approved ? 'ACTIVE' : 'PENDING_APPROVAL'
        return json(200, { institution_id: tenant.institutionId, status: tenant.status })
      }

      case 'POST /auth/login': {
        const tenant = this.tenants.get(String(body.institution_id).trim().toUpperCase())
        if (
          !tenant ||
          tenant.status !== 'ACTIVE' ||
          tenant.email !== body.email ||
          tenant.password !== body.password
        ) {
          return json(401, {
            detail: 'Sign-in failed. Check the Institution ID, email and password.',
          })
        }
        this.signed = { tenant, role: 'admin' }
        return json(200, {
          status: 'signed_in',
          access_token: 'access-token',
          token_type: 'bearer',
          expires_in: 900,
          user: this.user(this.signed),
        })
      }

      case 'POST /auth/refresh': {
        this.refreshCalls++
        if (!this.signed) return json(401, { detail: 'Sign in again.' })
        return json(200, {
          status: 'signed_in',
          access_token: 'access-token',
          token_type: 'bearer',
          expires_in: 900,
          user: this.user(this.signed),
        })
      }

      case 'GET /auth/me':
        if (!this.signed) return json(401, { detail: 'Not signed in.' })
        return json(200, {
          user: this.user(this.signed),
          institution_id: this.signed.tenant.institutionId,
          college_name: this.signed.tenant.collegeName,
          recovery_codes_left: 0,
        })

      case 'POST /auth/logout':
        this.signed = null
        return empty(204)

      case 'POST /auth/password/forgot': {
        this.forgotRequests.push(body)
        const tenant = this.tenants.get(String(body.institution_id).toUpperCase())
        if (tenant && tenant.email === body.email)
          this.resetTokens.set('college.reset-token', tenant.institutionId)
        return json(202, {}) // the same answer whether or not the account exists
      }

      case 'POST /auth/password/reset': {
        const id = this.resetTokens.get(body.token)
        const tenant = id ? this.tenants.get(id) : undefined
        if (!tenant) return json(400, { detail: 'Invalid or used link.' })
        this.resetTokens.delete(body.token)
        tenant.password = body.new_password
        return empty(204)
      }

      case 'GET /accounts':
        return json(200, [])
      case 'GET /students':
        return json(200, [])

      default:
        return json(599, { detail: `fake API has no ${key}` })
    }
  }
}
