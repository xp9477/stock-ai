export const INFORMATION_GATE_TYPE = 'preopen_information'
export const PRICE_GATE_TYPE = 'pretrade_quote'

export const TERMINAL_PLAN_STATUSES = new Set([
  'expired',
  'invalidated_price',
  'invalidated_condition',
  'rejected',
  'superseded',
  'executed',
  'cancelled',
])

export function latestGate(gates, gateType) {
  return [...(gates || [])].reverse().find((gate) => gate.gate_type === gateType)
}

export function isPlanMutable(plan) {
  return Boolean(plan)
    && plan.status !== 'ticket_ready'
    && !TERMINAL_PLAN_STATUSES.has(plan.status)
}

export function canValidatePlanPrice(plan) {
  return isPlanMutable(plan)
    && latestGate(plan?.gates, INFORMATION_GATE_TYPE)?.outcome === 'pass'
}

export function canApprovePlan(plan, officialConfirmed) {
  return Boolean(officialConfirmed)
    && plan?.status === 'awaiting_approval'
    && latestGate(plan?.gates, INFORMATION_GATE_TYPE)?.outcome === 'pass'
    && latestGate(plan?.gates, PRICE_GATE_TYPE)?.outcome === 'pass'
}

function defaultRandomToken() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`
}

export function createApprovalKeyStore(randomToken = defaultRandomToken) {
  const keys = new Map()
  return (plan) => {
    if (!Number.isInteger(plan?.lock_version) || plan.lock_version < 1 || plan?.id == null) {
      throw new Error('计划缺少有效的 id 或 lock_version')
    }
    const slot = `${plan.id}:${plan.lock_version}`
    if (!keys.has(slot)) {
      const key = `approve-plan-${plan.id}-v${plan.lock_version}-${randomToken()}`
      if (key.length < 8 || key.length > 128) throw new Error('审批幂等键长度无效')
      keys.set(slot, key)
    }
    return keys.get(slot)
  }
}

export function buildApprovalPayload(plan, idempotencyKey) {
  if (!Number.isInteger(plan?.lock_version) || plan.lock_version < 1) {
    throw new Error('计划缺少有效的 lock_version')
  }
  if (typeof idempotencyKey !== 'string' || idempotencyKey.length < 8 || idempotencyKey.length > 128) {
    throw new Error('审批幂等键无效')
  }
  return {
    expected_lock_version: plan.lock_version,
    idempotency_key: idempotencyKey,
    confirmed: true,
    human_official_confirmed: true,
  }
}

export function executionIntentLabel(status) {
  return status === 'ticket_ready' ? '票据就绪 · 尚未成交' : status || '未知'
}
