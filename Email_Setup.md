# Email Setup Guide

The app uses SMTP for transactional emails (trial notifications, support). No third-party SDK required.

## Email Types Sent

| Email | Trigger | When |
|---|---|---|
| Trial Welcome | User signs up | Immediately on signup |
| Trial 3-Day Warning | Scheduler | Daily at 8 AM CST |
| Trial Expired | Scheduler | Daily at 8 AM CST |
| Support Request | User submits form | Immediately |

## Option 1: Gmail SMTP (Simplest)

Best for low-volume (<500 emails/day).

### Setup
1. Use a Google account (ideally a dedicated `noreply@yourdomain.com`)
2. Enable 2-Factor Authentication on the Google account
3. Generate an App Password:
   - Go to [https://myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
   - Select **Mail** and **Other (Custom name)**: `TradeWiz`
   - Copy the 16-character password
4. Set in `.env`:
   ```env
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USER=noreply@yourdomain.com
   SMTP_PASS=xxxx xxxx xxxx xxxx    # App password (with spaces)
   ```

### Limits
- 500 emails/day per Gmail account
- 100 recipients per message

## Option 2: Custom Domain SMTP (Google Workspace)

Best for professional branding.

### Setup
1. Set up Google Workspace for your domain
2. Create `noreply@tradewiz.market`
3. Enable App Password (same as above)
4. Set in `.env`:
   ```env
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USER=noreply@tradewiz.market
   SMTP_PASS=xxxx xxxx xxxx xxxx
   ```

## Option 3: Amazon SES (High Volume)

Best for >500 emails/day.

### Setup
1. Go to AWS Console > SES > Verified Identities
2. Verify your domain (add DNS TXT/CNAME records)
3. Create SMTP credentials:
   - SES > SMTP Settings > Create SMTP Credentials
   - Save the username and password
4. Request production access (SES starts in sandbox mode)
5. Set in `.env`:
   ```env
   SMTP_HOST=email-smtp.us-east-1.amazonaws.com
   SMTP_PORT=587
   SMTP_USER=AKIA...your_smtp_username
   SMTP_PASS=your_smtp_password
   ```

### Limits
- Sandbox: 200 emails/day, verified recipients only
- Production: 50,000+ emails/day

## Option 4: SendGrid

### Setup
1. Create account at [https://sendgrid.com](https://sendgrid.com)
2. Go to Settings > API Keys > Create API Key (Full Access)
3. Set in `.env`:
   ```env
   SMTP_HOST=smtp.sendgrid.net
   SMTP_PORT=587
   SMTP_USER=apikey
   SMTP_PASS=SG.your_api_key_here
   ```

### Limits
- Free: 100 emails/day
- Essentials ($19.95/mo): 50,000 emails/mo

## Option 5: Mailgun

### Setup
1. Create account at [https://mailgun.com](https://mailgun.com)
2. Verify your domain
3. Go to Sending > Domain Settings > SMTP Credentials
4. Set in `.env`:
   ```env
   SMTP_HOST=smtp.mailgun.org
   SMTP_PORT=587
   SMTP_USER=postmaster@mg.yourdomain.com
   SMTP_PASS=your_mailgun_smtp_password
   ```

## Environment Variables

```env
# Email (required for trial notifications)
SMTP_HOST=smtp.gmail.com           # SMTP server hostname
SMTP_PORT=587                      # SMTP port (587 for TLS)
SMTP_USER=noreply@tradewiz.market  # From address
SMTP_PASS=xxxx xxxx xxxx xxxx      # SMTP password or app password

# Optional
APP_NAME=TradeWiz                  # Used in email subject/body
APP_URL=https://tradewiz.market    # Used in email links
SUPPORT_EMAIL=support@tradewiz.market  # Reply-to address
```

## Testing

### Verify SMTP Connection
```bash
docker compose exec app python3 -c "
from trial_manager import _send_email
result = _send_email('your@email.com', 'Test Email', '<h1>It works!</h1><p>SMTP is configured correctly.</p>')
print('Sent!' if result else 'Failed - check logs')
"
```

### Test Trial Welcome Email
```bash
docker compose exec app python3 -c "
from trial_manager import _send_trial_welcome
from datetime import datetime, timedelta
_send_trial_welcome(1, datetime.now() + timedelta(days=7))  # user_id=1
print('Done')
"
```

### Check Email Logs
```bash
docker compose logs app 2>&1 | grep -i "email\|smtp\|trial"
```

## DNS Records (for custom domain)

To prevent emails from going to spam, add these DNS records:

### SPF Record
```
Type: TXT
Name: @
Value: v=spf1 include:_spf.google.com ~all
```
(Adjust `include:` for your SMTP provider: `amazonses.com`, `sendgrid.net`, etc.)

### DKIM Record
Follow your SMTP provider's DKIM setup guide. Each provider generates unique DKIM keys.

### DMARC Record
```
Type: TXT
Name: _dmarc
Value: v=DMARC1; p=quarantine; rua=mailto:dmarc@tradewiz.market
```

## Scheduler

Trial emails are sent by the APScheduler job at **8:00 AM CST daily**:
- Checks all active trials
- Expires trials past their end date (reverts to free tier)
- Sends 3-day warning emails for trials expiring within 3 days
- Sends expired notification emails

The scheduler runs in one gunicorn worker (locked via `/tmp/scheduler.lock`).
