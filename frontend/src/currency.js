export function formatInrFromPaise(paise) {
  if (paise == null) return 'Not set'
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(paise / 100)
}

export function parseInrToPaise(value) {
  const input = String(value).trim()
  if (!input) return null
  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(input)
  if (!match) throw new RangeError('Enter a valid INR amount with up to two decimal places.')
  const rupees = Number(match[1])
  const paise = Number((match[2] || '').padEnd(2, '0'))
  const total = rupees * 100 + paise
  if (!Number.isSafeInteger(total)) throw new RangeError('INR amount is too large.')
  return total
}
