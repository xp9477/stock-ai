// @vitest-environment node
import { describe, expect, it } from 'vitest'
import { formatNumber, numberPrecision, numberStep } from './numberInput.js'

describe('numberPrecision', () => {
  it('keeps A-share fee rates past two decimals', () => {
    expect(numberPrecision({
      type: 'float', default: 0.00025, precision: 6,
    })).toBe(6)
    expect(numberPrecision({ type: 'float', default: 0.00025 })).toBe(5)
    expect(numberPrecision({ type: 'float', default: 0.00001 })).toBe(5)
  })

  it('uses integers for int fields and at least two places otherwise', () => {
    expect(numberPrecision({ type: 'int', default: 30 })).toBe(0)
    expect(numberPrecision({ type: 'float', default: 5 })).toBe(2)
  })
})

describe('numberStep', () => {
  it('steps fee rates by 万分之 0.1 when told to', () => {
    expect(numberStep({
      type: 'float', default: 0.00025, step: 0.00001, precision: 6,
    })).toBe(0.00001)
  })

  it('falls back from the inferred precision', () => {
    expect(numberStep({ type: 'int' })).toBe(1)
    expect(numberStep({ type: 'float', default: 0.7 })).toBe(0.1)
    expect(numberStep({ type: 'percent', default: 0.15 })).toBe(0.01)
  })
})

describe('formatNumber', () => {
  it('shows 万一-scale defaults without rounding to 0.00', () => {
    expect(formatNumber(0.00025, 6)).toBe('0.00025')
    expect(formatNumber(0.0001, 6)).toBe('0.0001')
    expect(formatNumber(5, 2)).toBe('5')
  })
})
