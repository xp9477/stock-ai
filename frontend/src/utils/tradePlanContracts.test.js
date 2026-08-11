import { describe, expect, it, vi } from 'vitest'
import {
  buildApprovalPayload,
  canApprovePlan,
  canValidatePlanPrice,
  createApprovalKeyStore,
  executionIntentLabel,
} from './tradePlanContracts.js'

function plan(overrides = {}) {
  return {
    id: 17,
    lock_version: 4,
    status: 'awaiting_approval',
    gates: [
      { gate_type: 'preopen_information', outcome: 'pass' },
      { gate_type: 'pretrade_quote', outcome: 'pass' },
    ],
    ...overrides,
  }
}

describe('trade plan approval contract', () => {
  it('reuses one idempotency key for retries of the same plan lock version', () => {
    const random = vi.fn()
      .mockReturnValueOnce('retry-token-0001')
      .mockReturnValueOnce('next-version-0002')
    const getKey = createApprovalKeyStore(random)

    const first = getKey(plan())
    const retry = getKey(plan())
    const nextVersion = getKey(plan({ lock_version: 5 }))

    expect(retry).toBe(first)
    expect(nextVersion).not.toBe(first)
    expect(random).toHaveBeenCalledTimes(2)
  })

  it('sends the current optimistic lock and both explicit confirmations', () => {
    expect(buildApprovalPayload(plan(), 'approve-key-0001')).toEqual({
      expected_lock_version: 4,
      idempotency_key: 'approve-key-0001',
      confirmed: true,
      human_official_confirmed: true,
    })
  })

  it('rejects approval until both latest gates pass and official disclosure is confirmed', () => {
    expect(canApprovePlan(plan(), true)).toBe(true)
    expect(canApprovePlan(plan(), false)).toBe(false)
    expect(canApprovePlan(plan({
      gates: [
        { gate_type: 'preopen_information', outcome: 'pass' },
        { gate_type: 'pretrade_quote', outcome: 'pass' },
        { gate_type: 'preopen_information', outcome: 'review_required' },
      ],
    }), true)).toBe(false)
    expect(canApprovePlan(plan({ status: 'candidate' }), true)).toBe(false)
  })

  it('allows price validation only after the latest information gate passes', () => {
    expect(canValidatePlanPrice(plan())).toBe(true)
    expect(canValidatePlanPrice(plan({ status: 'ticket_ready' }))).toBe(false)
    expect(canValidatePlanPrice(plan({
      gates: [
        { gate_type: 'preopen_information', outcome: 'pass' },
        { gate_type: 'preopen_information', outcome: 'blocked_information' },
      ],
    }))).toBe(false)
  })

  it('never labels a ready execution intent as a fill', () => {
    expect(executionIntentLabel('ticket_ready')).toBe('票据就绪 · 尚未成交')
    expect(executionIntentLabel('ticket_ready')).not.toContain('已成交')
  })
})
