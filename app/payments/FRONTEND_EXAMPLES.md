# Frontend Integration Examples

This document provides frontend code examples for integrating with the payment system, including ModemPay.

## ModemPay Payment Integration

### JavaScript/TypeScript Example

```javascript
/**
 * Initiate a ModemPay payment
 * @param {number} orderId - Order ID
 * @param {number} amount - Payment amount
 * @param {string} phone - Customer phone number
 * @param {string} provider - Payment provider: 'wave', 'qmoney', 'afrimoney', 'ecobank', or 'card'
 */
async function initiateModemPayPayment(orderId, amount, phone, provider) {
    try {
        const response = await fetch('/payments/modempay/pay', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                // Add your authentication header here
                'Authorization': `Bearer ${getAuthToken()}`
            },
            body: JSON.stringify({
                order_id: orderId,
                amount: amount,
                phone: phone,
                provider: provider  // 'wave', 'qmoney', 'afrimoney', 'ecobank', or 'card'
            })
        });

        const data = await response.json();

        if (data.success) {
            // Redirect to payment URL
            if (data.data.payment_url) {
                window.location.href = data.data.payment_url;
            } else {
                console.log('Payment initiated:', data);
                // Handle payment URL display or redirect
            }
        } else {
            console.error('Payment initiation failed:', data.message);
            alert('Payment failed: ' + data.message);
        }
    } catch (error) {
        console.error('Error initiating payment:', error);
        alert('An error occurred while initiating payment');
    }
}

// Example usage
document.getElementById('pay-button').addEventListener('click', async () => {
    const orderId = 123;
    const amount = 100.00;
    const phone = '+2201234567';
    const provider = document.getElementById('provider-select').value; // 'wave', 'qmoney', etc.
    
    await initiateModemPayPayment(orderId, amount, phone, provider);
});
```

### React Example

```jsx
import React, { useState } from 'react';

function ModemPayCheckout({ orderId, amount }) {
    const [phone, setPhone] = useState('');
    const [provider, setProvider] = useState('wave');
    const [loading, setLoading] = useState(false);

    const handlePayment = async (e) => {
        e.preventDefault();
        setLoading(true);

        try {
            const response = await fetch('/payments/modempay/pay', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${localStorage.getItem('token')}`
                },
                body: JSON.stringify({
                    order_id: orderId,
                    amount: amount,
                    phone: phone,
                    provider: provider
                })
            });

            const data = await response.json();

            if (data.success && data.data.payment_url) {
                // Redirect to payment URL
                window.location.href = data.data.payment_url;
            } else {
                alert('Payment failed: ' + data.message);
            }
        } catch (error) {
            console.error('Payment error:', error);
            alert('An error occurred while processing payment');
        } finally {
            setLoading(false);
        }
    };

    return (
        <form onSubmit={handlePayment}>
            <div>
                <label>Phone Number:</label>
                <input
                    type="tel"
                    value={phone}
                    onChange={(e) => setPhone(e.target.value)}
                    placeholder="+2201234567"
                    required
                />
            </div>

            <div>
                <label>Payment Provider:</label>
                <select value={provider} onChange={(e) => setProvider(e.target.value)}>
                    <option value="wave">Wave Money</option>
                    <option value="qmoney">QMoney</option>
                    <option value="afrimoney">AfriMoney</option>
                    <option value="ecobank">ECOBANK Mobile</option>
                    <option value="card">Card Payment</option>
                </select>
            </div>

            <button type="submit" disabled={loading}>
                {loading ? 'Processing...' : 'Pay Now'}
            </button>
        </form>
    );
}

export default ModemPayCheckout;
```

### HTML Form Example

```html
<!DOCTYPE html>
<html>
<head>
    <title>ModemPay Payment</title>
</head>
<body>
    <form id="payment-form">
        <div>
            <label for="phone">Phone Number:</label>
            <input type="tel" id="phone" name="phone" placeholder="+2201234567" required>
        </div>

        <div>
            <label for="provider">Payment Provider:</label>
            <select id="provider" name="provider" required>
                <option value="wave">Wave Money</option>
                <option value="qmoney">QMoney</option>
                <option value="afrimoney">AfriMoney</option>
                <option value="ecobank">ECOBANK Mobile</option>
                <option value="card">Card Payment</option>
            </select>
        </div>

        <button type="submit">Pay Now</button>
    </form>

    <script>
        document.getElementById('payment-form').addEventListener('submit', async (e) => {
            e.preventDefault();

            const formData = {
                order_id: 123,  // Get from your order
                amount: 100.00,  // Get from your order
                phone: document.getElementById('phone').value,
                provider: document.getElementById('provider').value
            };

            try {
                const response = await fetch('/payments/modempay/pay', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${localStorage.getItem('token')}`
                    },
                    body: JSON.stringify(formData)
                });

                const data = await response.json();

                if (data.success && data.data.payment_url) {
                    // Redirect to payment URL
                    window.location.href = data.data.payment_url;
                } else {
                    alert('Payment failed: ' + data.message);
                }
            } catch (error) {
                console.error('Error:', error);
                alert('An error occurred while processing payment');
            }
        });
    </script>
</body>
</html>
```

### Vue.js Example

```vue
<template>
    <div class="payment-form">
        <form @submit.prevent="handlePayment">
            <div>
                <label>Phone Number:</label>
                <input v-model="phone" type="tel" placeholder="+2201234567" required>
            </div>

            <div>
                <label>Payment Provider:</label>
                <select v-model="provider" required>
                    <option value="wave">Wave Money</option>
                    <option value="qmoney">QMoney</option>
                    <option value="afrimoney">AfriMoney</option>
                    <option value="ecobank">ECOBANK Mobile</option>
                    <option value="card">Card Payment</option>
                </select>
            </div>

            <button type="submit" :disabled="loading">
                {{ loading ? 'Processing...' : 'Pay Now' }}
            </button>
        </form>
    </div>
</template>

<script>
export default {
    name: 'ModemPayCheckout',
    props: {
        orderId: {
            type: Number,
            required: true
        },
        amount: {
            type: Number,
            required: true
        }
    },
    data() {
        return {
            phone: '',
            provider: 'wave',
            loading: false
        };
    },
    methods: {
        async handlePayment() {
            this.loading = true;

            try {
                const response = await fetch('/payments/modempay/pay', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${this.$store.state.authToken}`
                    },
                    body: JSON.stringify({
                        order_id: this.orderId,
                        amount: this.amount,
                        phone: this.phone,
                        provider: this.provider
                    })
                });

                const data = await response.json();

                if (data.success && data.data.payment_url) {
                    window.location.href = data.data.payment_url;
                } else {
                    alert('Payment failed: ' + data.message);
                }
            } catch (error) {
                console.error('Payment error:', error);
                alert('An error occurred while processing payment');
            } finally {
                this.loading = false;
            }
        }
    }
};
</script>
```

## Payment Status Check

### Check Payment Status

```javascript
/**
 * Check payment status
 * @param {number} paymentId - Payment ID
 */
async function checkPaymentStatus(paymentId) {
    try {
        const response = await fetch(`/payments/status/${paymentId}`, {
            headers: {
                'Authorization': `Bearer ${getAuthToken()}`
            }
        });

        const data = await response.json();

        if (data.success) {
            const payment = data.data.payment;
            console.log('Payment status:', payment.status);
            
            if (payment.status === 'completed') {
                // Payment successful
                return true;
            } else if (payment.status === 'failed') {
                // Payment failed
                return false;
            } else {
                // Payment pending
                return null;
            }
        }
    } catch (error) {
        console.error('Error checking payment status:', error);
    }
}
```

## Error Handling

```javascript
async function handlePaymentError(error, response) {
    if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        
        switch (response.status) {
            case 400:
                // Bad request - validation error
                console.error('Validation error:', data.message);
                break;
            case 401:
                // Unauthorized
                console.error('Authentication required');
                break;
            case 403:
                // Forbidden
                console.error('Access denied');
                break;
            case 404:
                // Not found
                console.error('Order or payment not found');
                break;
            case 503:
                // Service unavailable - gateway not configured
                console.error('Payment gateway not configured');
                break;
            default:
                console.error('Payment error:', data.message || 'Unknown error');
        }
    }
}
```

## Notes

- Always validate phone numbers and amounts on the frontend before sending
- Store payment URLs securely and redirect users appropriately
- Implement proper error handling and user feedback
- Use HTTPS in production for secure payment processing
- Handle payment callbacks and webhooks appropriately
- Test with ModemPay sandbox/test mode before going live

