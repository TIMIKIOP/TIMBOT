import assert from 'node:assert/strict'
import { formatInrFromPaise, parseInrToPaise } from '../src/currency.js'
import { messageDeliveryLabel } from '../src/messageStatus.js'

assert.equal(formatInrFromPaise(179900), '₹1,799.00')
assert.equal(formatInrFromPaise(5), '₹0.05')
assert.equal(formatInrFromPaise(null), 'Not set')

assert.equal(parseInrToPaise('1799'), 179900)
assert.equal(parseInrToPaise('1799.50'), 179950)
assert.equal(parseInrToPaise('0.05'), 5)
assert.equal(parseInrToPaise(''), null)
assert.throws(() => parseInrToPaise('12.345'), RangeError)
console.log('INR price formatting and conversion checks passed.')

assert.equal(messageDeliveryLabel('web', 'outbound', 'stored'), 'Saved in web chat')
assert.equal(messageDeliveryLabel('web', 'outbound', 'queued'), 'Saved in web chat')
assert.equal(messageDeliveryLabel('messenger', 'outbound', 'queued'), 'Queued for delivery')
assert.equal(messageDeliveryLabel('web', 'inbound', 'received'), 'Received')
console.log('Local and external message status labels passed.')
