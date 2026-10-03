/** Same rule as the server and MainLogin.html: A-Z, 0-9, _ - * &, 1 to 20 characters. */
export const INSTITUTION_ID_MAX = 20
const FORMAT = /^[A-Z0-9_\-*&]{1,20}$/

export function institutionIdProblem(value: string): string | null {
  if (value === '') return null
  if (!FORMAT.test(value)) {
    return 'Invalid Format: Upper case, numbers, _, -, *, & only. Max 20 chars. No spaces.'
  }
  return null
}
