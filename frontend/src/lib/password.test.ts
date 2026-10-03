import { describe, expect, it } from 'vitest'
import { institutionIdProblem } from './institutionId'
import { strengthOf } from './password'

describe('password strength hint', () => {
  it('follows the server policy: 12 characters minimum, no composition rules', () => {
    expect(strengthOf('', false)).toEqual({ level: 'idle' })
    expect(strengthOf('Sh0rt!', false)).toEqual({ level: 'weak', reason: 'short' })
    expect(strengthOf('elevenchars', false)).toEqual({ level: 'weak', reason: 'short' })
    expect(strengthOf('twelve chars', false)).toEqual({ level: 'acceptable' })
    expect(strengthOf('a long passphrase of words', false)).toEqual({ level: 'strong' })
  })

  it('flags common passwords whatever their length', () => {
    expect(strengthOf('password12345678', true)).toEqual({ level: 'weak', reason: 'common' })
  })
})

describe('Institution ID format', () => {
  it.each(['TARN_INST_01', 'A', 'A-B*C&D', 'X'.repeat(20)])('accepts %s', (value) => {
    expect(institutionIdProblem(value)).toBeNull()
  })
  it.each(['lower', 'HAS SPACE', 'BAD!', 'X'.repeat(21)])('rejects %s', (value) => {
    expect(institutionIdProblem(value)).toMatch(/Invalid Format/)
  })
  it('says nothing about an empty field', () => {
    expect(institutionIdProblem('')).toBeNull()
  })
})
