#!/bin/bash
# TradeWiz — Google Cloud Platform Deployment Script
# Usage: ./deploy-gcp.sh [setup|deploy|status]
#
# Prerequisites:
#   1. gcloud CLI installed and authenticated
#   2. GCP project created
#   3. .env.gcp file with secrets

set -e

PROJECT_ID="${GCP_PROJECT_ID:-tradewiz-prod}"
REGION="${GCP_REGION:-us-central1}"
DB_INSTANCE="tradewiz-db"
DB_NAME="tradewiz"
DB_USER="tradewiz"
WEB_SERVICE="tradewiz-web"

echo "======================================="
echo "TradeWiz GCP Deployment"
echo "Project: $PROJECT_ID | Region: $REGION"
echo "======================================="

case "${1:-deploy}" in

  setup)
    echo ""
    echo ">>> Step 1: Setting project..."
    gcloud config set project "$PROJECT_ID"
    gcloud config set run/region "$REGION"

    echo ""
    echo ">>> Step 2: Enabling APIs..."
    gcloud services enable \
      run.googleapis.com \
      sqladmin.googleapis.com \
      aiplatform.googleapis.com \
      cloudbuild.googleapis.com \
      secretmanager.googleapis.com \
      artifactregistry.googleapis.com

    echo ""
    echo ">>> Step 3: Creating Cloud SQL instance..."
    gcloud sql instances create "$DB_INSTANCE" \
      --database-version=POSTGRES_16 \
      --tier=db-f1-micro \
      --region="$REGION" \
      --storage-size=10GB \
      --storage-auto-increase \
      --backup-start-time=03:00 \
      || echo "Instance may already exist"

    echo ""
    echo ">>> Step 4: Creating database and user..."
    gcloud sql databases create "$DB_NAME" --instance="$DB_INSTANCE" || echo "DB may exist"

    echo ""
    echo ">>> Enter a strong password for the database user:"
    read -s DB_PASS
    gcloud sql users create "$DB_USER" --instance="$DB_INSTANCE" --password="$DB_PASS" || echo "User may exist"

    echo ""
    echo ">>> Step 5: Storing secrets..."
    echo -n "$DB_PASS" | gcloud secrets create db-password --data-file=- 2>/dev/null || echo "Secret exists"

    echo ""
    echo ">>> Step 6: Granting Vertex AI access..."
    PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
      --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
      --role="roles/aiplatform.user" \
      --quiet

    echo ""
    echo "======================================="
    echo "SETUP COMPLETE!"
    echo ""
    echo "Next steps:"
    echo "  1. Store your secrets in Secret Manager:"
    echo "     echo -n 'your-key' | gcloud secrets create flask-secret-key --data-file=-"
    echo "     echo -n 'your-key' | gcloud secrets create stripe-secret-key --data-file=-"
    echo "     echo -n 'your-key' | gcloud secrets create encryption-key --data-file=-"
    echo ""
    echo "  2. Enable Claude in Vertex AI Model Garden:"
    echo "     https://console.cloud.google.com/vertex-ai/model-garden"
    echo ""
    echo "  3. Run: ./deploy-gcp.sh deploy"
    echo "======================================="
    ;;

  deploy)
    echo ""
    echo ">>> Building and deploying to Cloud Run..."
    CONNECTION="${PROJECT_ID}:${REGION}:${DB_INSTANCE}"

    gcloud run deploy "$WEB_SERVICE" \
      --source . \
      --dockerfile Dockerfile.cloudrun \
      --region "$REGION" \
      --allow-unauthenticated \
      --set-env-vars "USE_VERTEX_AI=1,GCP_PROJECT_ID=${PROJECT_ID},GCP_REGION=${REGION}" \
      --set-secrets "SECRET_KEY=flask-secret-key:latest" \
      --set-secrets "STRIPE_SECRET_KEY=stripe-secret-key:latest" \
      --set-secrets "ENCRYPTION_KEY=encryption-key:latest" \
      --add-cloudsql-instances "$CONNECTION" \
      --min-instances 0 \
      --max-instances 10 \
      --memory 512Mi \
      --cpu 1 \
      --timeout 300 \
      --concurrency 80

    echo ""
    echo ">>> Deployment complete!"
    gcloud run services describe "$WEB_SERVICE" --region "$REGION" --format="value(status.url)"
    ;;

  domain)
    echo ""
    echo ">>> Mapping custom domain..."
    echo "Enter your domain (e.g. tradewiz.market):"
    read DOMAIN
    gcloud run domain-mappings create \
      --service "$WEB_SERVICE" \
      --domain "$DOMAIN" \
      --region "$REGION"
    echo ""
    echo "Add the DNS records shown above to your domain registrar."
    ;;

  status)
    echo ""
    echo ">>> Service status:"
    gcloud run services describe "$WEB_SERVICE" --region "$REGION" \
      --format="table(status.url, status.conditions[0].status, spec.template.spec.containers[0].resources)"
    echo ""
    echo ">>> Recent revisions:"
    gcloud run revisions list --service "$WEB_SERVICE" --region "$REGION" --limit 5
    echo ""
    echo ">>> Cloud SQL:"
    gcloud sql instances describe "$DB_INSTANCE" --format="table(state, settings.tier, settings.dataDiskSizeGb)"
    ;;

  logs)
    echo ">>> Streaming logs..."
    gcloud run services logs tail "$WEB_SERVICE" --region "$REGION"
    ;;

  *)
    echo "Usage: $0 [setup|deploy|domain|status|logs]"
    echo ""
    echo "  setup   — First-time GCP infrastructure setup"
    echo "  deploy  — Build and deploy to Cloud Run"
    echo "  domain  — Map custom domain"
    echo "  status  — Check service status"
    echo "  logs    — Stream live logs"
    ;;
esac
