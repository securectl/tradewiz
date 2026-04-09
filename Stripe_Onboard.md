# Stripe Setup Guide

## 1. Create Stripe Account

1. Go to [https://dashboard.stripe.com/register](https://dashboard.stripe.com/register)
2. Complete business verification
3. Enable **Test mode** (toggle in top-right of dashboard) for development

## 2. Get API Keys

1. Go to **Developers > API Keys**
2. Copy your **Secret key** (starts with `sk_test_` in test mode, `sk_live_` in production)
3. Set in your `.env`:
   ```
   STRIPE_SECRET_KEY=sk_test_your_key_here
   ```

## 3. Create Subscription Products & Prices

### Starter Plan ($19/month)
1. Go to **Products > Add Product**
2. Name: `Starter Plan`
3. Description: `30 AI calls/day, Advanced Screener, Research, IPO Tracker`
4. Click **Add Price**: $19.00 / month (recurring)
5. Copy the **Price ID** (starts with `price_`)
6. Set in `.env`:
   ```
   STRIPE_PRICE_STARTER=price_your_starter_id
   ```

### Pro Plan ($39/month)
1. Add another product: `Pro Plan`
2. Description: `100 AI calls/day, Trump Indicator, Congress Trades, Markets, Priority AI`
3. Price: $39.00 / month (recurring)
4. Copy **Price ID** and set:
   ```
   STRIPE_PRICE_PRO=price_your_pro_id
   ```

## 4. Configure 7-Day Free Trial

The app automatically adds a 7-day trial at the **application level** (not Stripe-level) when users sign up. This means:

- New users get Pro tier for 7 days without entering payment info
- After 7 days, they're reverted to Free unless they subscribe via Stripe
- To also use Stripe-level trials (collects card upfront), set `trial_days=7` when calling `create_checkout_session()`

### Stripe-Level Trial (optional, collects payment upfront)
If you want Stripe to handle the trial:
1. Go to **Products > Pro Plan > Edit Price**
2. Under **Free trial**, set to 7 days
3. Or leave it off and the app handles it locally (recommended for frictionless signup)

## 5. Set Up Webhook

1. Go to **Developers > Webhooks**
2. Click **Add endpoint**
3. URL: `https://yourdomain.com/billing/webhook`
4. Select events:
   - `checkout.session.completed`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
   - `invoice.payment_failed`
5. Copy the **Webhook signing secret** (starts with `whsec_`)
6. Set in `.env`:
   ```
   STRIPE_WEBHOOK_SECRET=whsec_your_secret_here
   ```

### Test Webhook Locally
```bash
# Install Stripe CLI
brew install stripe/stripe-cli/stripe   # macOS
# or download from https://stripe.com/docs/stripe-cli

# Login
stripe login

# Forward webhooks to local server
stripe listen --forward-to localhost:5000/billing/webhook

# The CLI will print a webhook signing secret — use it for local testing
```

## 6. Customer Portal

The app uses Stripe's Customer Portal for subscription management (upgrade, downgrade, cancel).

1. Go to **Settings > Customer Portal**
2. Enable:
   - Subscription cancellation
   - Subscription updates (switching plans)
   - Payment method updates
   - Invoice history
3. Under **Products**, add both Starter and Pro plans
4. Save

## 7. Environment Variables Summary

```env
# Stripe (required for billing)
STRIPE_SECRET_KEY=sk_test_...          # or sk_live_... for production
STRIPE_WEBHOOK_SECRET=whsec_...        # From webhook endpoint setup
STRIPE_PRICE_STARTER=price_...         # Starter plan price ID
STRIPE_PRICE_PRO=price_...             # Pro plan price ID
```

## 8. Test the Flow

### Test Cards (Stripe test mode)
| Card Number | Scenario |
|---|---|
| `4242 4242 4242 4242` | Successful payment |
| `4000 0025 0000 3155` | Requires 3D Secure |
| `4000 0000 0000 9995` | Payment declined |

### Test Flow
1. Sign up with a new account
2. You should get a 7-day Pro trial automatically
3. Click "Upgrade to Pro" in the pricing modal
4. Use test card `4242 4242 4242 4242`, any future expiry, any CVC
5. After checkout, verify:
   - `user_subscriptions.tier = 'pro'`
   - `user_subscriptions.stripe_subscription_id` is set
   - `user_subscriptions.trial_status = 'converted'`

## 9. Go Live

1. Toggle off **Test mode** in Stripe dashboard
2. Create production products/prices (same steps as above)
3. Update `.env` with `sk_live_` key and production price IDs
4. Update webhook URL to production domain
5. Copy new webhook signing secret
6. Rebuild: `docker compose up -d --build`

## 10. Trial Fraud Prevention

The app tracks:
- **IP address** of each trial signup
- **Geo-location** (country, region, city) via ip-api.com
- **Device fingerprint** (hash of IP + User-Agent + email domain)
- **Email domain** (blocks known disposable email providers)
- **Subnet abuse** (blocks >3 trials from same /24 IP range)

Fraud data is stored in the `trial_fingerprints` table. Review via:
```sql
SELECT tf.*, u.email, us.trial_status
FROM trial_fingerprints tf
JOIN users u ON tf.user_id = u.id
JOIN user_subscriptions us ON tf.user_id = us.user_id
WHERE tf.fraud_score > 0
ORDER BY tf.created_at DESC;
```
