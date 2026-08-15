/** Decimal places / stepper for settings number inputs.

Hard-coding 2 places would collapse A-share fee rates such as
0.00025 (万 2.5) or 0.0001 (万一) to 0.00.
*/

function fractionalDigits(value) {
  if (value == null || Number.isNaN(Number(value))) return 0
  const text = String(value)
  if (/e-/i.test(text)) {
    const exp = Number(text.split(/e-/i)[1])
    return Number.isFinite(exp) ? exp : 0
  }
  const frac = text.includes('.') ? text.split('.')[1].replace(/0+$/, '') : ''
  return frac.length
}

export function numberPrecision(item) {
  if (!item || item.type === 'int') return 0
  if (Number.isInteger(item.precision) && item.precision >= 0) {
    return item.precision
  }
  const fromDefault = fractionalDigits(item.default)
  if (item.type === 'percent') return Math.max(fromDefault, 4)
  return Math.max(fromDefault, 2)
}

export function numberStep(item) {
  if (!item || item.type === 'int') return 1
  const step = Number(item.step)
  if (Number.isFinite(step) && step > 0) return step
  if (item.type === 'percent') return 0.01
  const precision = numberPrecision(item)
  return precision <= 2 ? 0.1 : Number(`1e-${precision}`)
}

export function formatNumber(value, precision) {
  if (value == null || Number.isNaN(Number(value))) return '—'
  const digits = Number.isInteger(precision) && precision >= 0 ? precision : 2
  const text = Number(value).toFixed(digits)
  if (digits === 0) return text
  return text.replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '')
}
