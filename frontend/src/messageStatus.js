export function messageDeliveryLabel(channel, direction, status) {
  if (channel === 'web' && direction === 'outbound' && (status === 'stored' || status === 'queued')) {
    return 'Saved in web chat'
  }
  const labels = {
    received: 'Received',
    queued: 'Queued for delivery',
    sent: 'Sent',
    delivered: 'Delivered',
    failed: 'Failed',
  }
  return labels[status] || status
}
