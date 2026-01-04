# Manual Payment Methods Implementation Plan

## Overview
This document outlines the implementation of manual payment methods (Bank Transfer, Western Union, Ria, Wave, MoneyGram) with receipt upload and admin approval.

## Payment Methods to Implement

1. **Bank Transfer**
2. **Western Union**
3. **Ria Money Transfer**
4. **Wave** (manual, not via ModemPay API)
5. **MoneyGram**

## Payment Details (to be stored in config/settings)

### Bank Transfer
- Account Holder Name: ABDOUKADIR JABBI
- Bank Name: State Bank of India (SBI)
- Branch Name: Surajpur Greater Noida
- Account Number: 60541424234
- IFSC Code: SBIN0014022

### Wave Payment
- Receiver Name: Foday M J
- Wave Number: 5427090

### Western Union / MoneyGram / Ria
- Receiver Name: Abdoukadir Jabbi
- Country: India
- Phone: +91 93190 38312

## Database Model

Added `ManualPayment` model to `app/payments/models.py` with fields:
- pending_payment_id, user_id, order_id
- payment_method, amount
- receipt_url, receipt_public_id
- status (pending/approved/rejected)
- approved_by, approved_at, rejection_reason
- timestamps

## Implementation Steps

### 1. Payment Details Configuration
- Store payment details in AppSettings or create PaymentDetails model
- For now, hardcode in templates (can be moved to settings later)

### 2. Checkout Page Updates
- Add payment method selection (radio buttons or dropdown)
- Show payment details based on selected method
- Add receipt upload field for manual methods
- Hide receipt upload for ModemPay

### 3. Manual Payment Submission Route
- `/payments/manual/submit` - POST endpoint
- Accepts: pending_payment_id, payment_method, receipt file
- Creates ManualPayment record with status='pending'
- Redirects to approval/waiting page

### 4. User Approval/Waiting Page
- `/payments/manual/<int:manual_payment_id>/status`
- Shows payment status (pending/approved/rejected)
- Displays receipt if uploaded
- Shows approval message if approved
- Shows rejection reason if rejected

### 5. Admin Approval Page
- `/admin/manual-payments` - List all pending manual payments
- `/admin/manual-payment/<int:payment_id>` - View details and approve/reject
- Shows: User info, payment method, amount, receipt image
- Actions: Approve (creates order), Reject (with reason)

### 6. Integration with Orders
- When admin approves: Create order from pending_payment
- Link manual_payment to order
- Show payment info in order details

### 7. Notifications
- Add badge/notification for pending payments in top bar
- Update when payment is approved/rejected

## Files to Create/Modify

1. `app/payments/models.py` - ✅ Added ManualPayment model
2. `app/templates/checkout.html` - Add payment method selection
3. `app/payments/routes.py` - Add manual payment routes
4. `app/__init__.py` - Add admin routes for manual payments
5. `app/templates/payments/manual_payment_status.html` - User status page
6. `app/templates/admin/admin/manual_payments.html` - Admin list page
7. `app/templates/admin/admin/manual_payment_detail.html` - Admin detail page

